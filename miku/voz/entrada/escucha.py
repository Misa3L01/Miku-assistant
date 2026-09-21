"""
escucha.py - Reconocimiento de voz (STT) con estructura en hilos.

En el proyecto original la escucha se hacía con SpeechRecognition en
bloque. Acá separamos la captura de audio (en su propio hilo) del
procesamiento para no congelar el bucle principal.

Flujo en modo "voz":
    1. Un hilo captura lo que se dice (con VAD: sabe cuándo empezaste y cuándo terminaste).
    2. Un detector decide si dijiste "Miku" (en la PC o, por defecto, transcribiendo en la nube).
    3. Se toma la orden y se entrega al cerebro.

Quién hace qué: ``vad.py`` decide dónde empieza y termina una frase, ``wake.py`` si esa frase te
dirigía a Miku, y ``transcriptores.py`` la pasa a texto.

También se puede INVOCAR a Miku sin decir la palabra (tecla F13): ``invocar()`` hace que el
hilo de escucha salude y tome el próximo comando directamente.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

# Imports "pesados"/opcionales hechos de forma lazy dentro de los métodos
# para acortar el arranque si no se usa modo voz (ver optimización).
from miku.ajustes import carga as config_mod  # noqa: F401  (accede por config_mod.config)
from miku.servicios import metricas
from miku.voz.entrada import vad as vad_mod, wake
from miku.voz.entrada.transcriptores import Transcriptor, crear_transcriptor
from miku.plataforma.texto import sin_acentos

logger = logging.getLogger("miku.stt")

# Las variantes de "Miku" y su expresión regular viven en ``wake.py`` (las comparten los detectores).
_VARIANTES_MIKU: tuple = wake.VARIANTES_MIKU
_RE_MIKU = wake.RE_MIKU

# Frases que Whisper "alucina" cuando recibe silencio/ruido: no son comandos.
_ALUCINACIONES_WHISPER: tuple = (
    "subtitulos por la comunidad de amara.org", "amara.org",
    "gracias por ver", "suscribete", "subtitulado por",
)

# Duración mínima (segundos) de un audio para gastar una llamada a Whisper.
_MIN_DURACION_AUDIO = 0.4
# Tope de duración de una frase en escucha continua (comando en la misma
# frase que la wake word: "Miku, abrí Brave y poné Discord a la derecha").
_LIMITE_FRASE_WAKE = 8
# Cada cuántos segundos el bucle de escucha revisa si lo invocaron (tecla) aunque nadie hable.
_ESPERA_INVOCACION = 1.0
# Segundos que se espera a que un hilo de escucha que se está deteniendo termine de verdad
# (puede estar en medio de una captura de hasta ~18 s).
_ESPERA_CIERRE_HILO = 20.0
# Cuánto audio se guarda ANTES de detectar voz: sin esto se perdería el arranque de la primera palabra.
_COLCHON_INICIO_SEG = 0.3


def _duracion_audio(audio) -> float:
    """Duración en segundos de un ``AudioData`` (0.0 si no se puede medir)."""
    try:
        return len(audio.frame_data) / float(audio.sample_rate * audio.sample_width)
    except Exception:  # noqa: BLE001
        return 0.0

# Tipo del handler de respuesta de texto final.
CallbackTranscripcion = Callable[[str], None]
CallbackError = Callable[[Exception], None]


class SpeechToText:
    """Motor de reconocimiento de voz.

    Args:
        cfg: Instancia de ``config.Config`` ya cargada.
    """

    def __init__(self, cfg: "config_mod.Config") -> None:
        self.cfg = cfg
        self._reconocedor: Any = None          # reconocedor "lazy"
        self._sr_mod: Any = None               # módulo speech_recognition (lazy)
        self._transcriptor: Optional[Transcriptor] = None   # Groq o local (lazy, ver transcriptores.py)
        self._detector_wake: Any = None        # detector local de la palabra clave (lazy)
        self._wake_resuelto = False
        self._vad: Any = None                  # detector de voz (lazy)
        self._vad_resuelto = False

        # Estado del escucha en segundo plano.
        self._hilo_escucha: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Invocación directa (tecla de invocación / atajo): saluda y toma el PRÓXIMO comando sin
        # exigir la palabra "Miku". La atiende el hilo de escucha.
        self._invocada = threading.Event()

        # Handler cuando se detecta la palabra de activación.
        self.on_wake: Optional[CallbackTranscripcion] = None
        # Handler con el texto del comando final (tras el wake echo).
        self.on_comando: Optional[CallbackTranscripcion] = None
        self.on_error: Optional[CallbackError] = None
        # Hook opcional ``(timeout) -> bool``: espera hasta ``timeout`` s a que Miku termine de
        # hablar y devuelve True si ya está en silencio. Se usa para no grabar su propia voz
        # (p. ej. "¿Sí? Decime.") como si fuera un comando.
        self.esperar_silencio: Optional[Callable[[float], bool]] = None

    # ---------------------------------------------------------------- #
    #            Inicialización / recursos (lazy loading)              #
    # ---------------------------------------------------------------- #
    def _importar_dependencias(self) -> None:
        """Importa SpeechRecognition solo cuando se necesita por primera
        vez (así el arranque en modo texto es muy rápido)."""
        if self._reconocedor is None:
            import speech_recognition as sr  # import tardío y exclusivo

            # Guardamos tanto el módulo como el reconocedor.
            self._sr_mod = sr
            self._reconocedor = sr.Recognizer()
            # Silencio que cierra la frase (el estándar de la librería es 0,8 s): es tiempo que se suma
            # a CADA orden antes de mandar el audio a transcribir.
            pausa = self._pausa_fin()
            self._reconocedor.pause_threshold = pausa
            self._reconocedor.non_speaking_duration = min(self._reconocedor.non_speaking_duration, pausa)

    @property
    def sr(self):
        """Módulo ``speech_recognition`` (se importa la primera vez)."""
        self._importar_dependencias()
        return self._sr_mod

    def _pausa_fin(self) -> float:
        """Segundos de silencio que cierran una frase (``STT_PAUSA_FIN``, acotado a 0,3-1,5)."""
        try:
            valor = float(self.cfg.get("stt_pausa_fin", 0.6))
        except (TypeError, ValueError):
            valor = 0.6
        return min(max(valor, 0.3), 1.5)

    # ---------------------------------------------------------------- #
    #                  Transcripción vía API de Groq                   #
    # ---------------------------------------------------------------- #
    def _llamar_whisper(self, audio, modelo: str, timeout: float) -> str:
        """Transcribe ``audio`` con el transcriptor configurado (Groq o local).

        Es el cuerpo común de ``transcribir_audio`` (comandos) y
        ``_transcribir_wake`` (wake word).

        Returns:
            Texto transcrito, o "" si falló, no hay clave/modelo o Whisper devolvió
            una "alucinación" típica de silencio/ruido.
        """
        self._importar_dependencias()
        if self._transcriptor is None:
            self._transcriptor = crear_transcriptor(self.cfg)
        try:
            wav_bytes = audio.get_wav_data()
        except Exception as e:  # noqa: BLE001
            logger.error("No pude leer el audio capturado: %s", e)
            return ""
        texto = self._transcriptor.transcribir(wav_bytes, modelo, timeout)
        if not texto:
            return ""

        normalizado = sin_acentos(texto)
        if any(a in normalizado for a in _ALUCINACIONES_WHISPER):
            logger.debug("Descarto alucinación de Whisper: %r", texto)
            return ""
        return texto

    def transcribir_audio(self, audio) -> str:
        """Transcribe un ``AudioData`` como COMANDO (modelo de comandos).

        Returns:
            Texto transcrito o "" si falló.
        """
        return self._llamar_whisper(audio, self.cfg.modelo_stt_comando, 15)

    def _transcribir_wake(self, audio) -> str:
        """Transcribe con el modelo rápido de wake word (distinto al de comandos)."""
        return self._llamar_whisper(audio, self.cfg.modelo_stt_wake, 12)

    def _contiene_miku(self, texto: str) -> bool:
        """Devuelve True si `texto` contiene la palabra Miku (palabra completa)."""
        return wake.contiene_miku(texto)

    def _obtener_detector(self) -> Any:
        """Detector LOCAL de la palabra clave, o None si se resuelve en la nube (ver ``wake.py``)."""
        if not self._wake_resuelto:
            self._wake_resuelto = True
            try:
                self._detector_wake = wake.crear_detector_local(self.cfg)
                if self._detector_wake is not None:
                    logger.info("Palabra clave detectada en la PC con '%s'.", self._detector_wake.nombre)
            except Exception:  # noqa: BLE001
                logger.exception("No pude preparar el detector local; uso la nube.")
                self._detector_wake = None
        return self._detector_wake

    def _detectar_wake(self, audio) -> "wake.Deteccion":
        """¿Ese audio te dirigía a Miku?

        Con un detector local el audio NO sale de la PC. Con el de nube se transcribe con Whisper,
        que además devuelve el texto y permite decir la orden en la misma frase ("Miku, qué hora es").
        """
        detector = self._obtener_detector()
        if detector is not None:
            return detector.detectar(audio)
        texto = self._transcribir_wake(audio)
        return wake.Deteccion(self._contiene_miku(texto), texto)

    def _obtener_vad(self) -> Any:
        """Detector de voz para saber cuándo terminás de hablar (None = pausa fija de siempre)."""
        if not self._vad_resuelto:
            self._vad_resuelto = True
            try:
                self._vad = vad_mod.crear_vad(self.cfg)
                if self._vad is not None:
                    logger.info("Fin de frase con VAD '%s'.", self._vad.nombre)
            except Exception:  # noqa: BLE001
                logger.exception("No pude preparar el VAD; uso la pausa fija.")
                self._vad = None
        return self._vad

    def _extraer_comando_en_linea(self, texto: str) -> str:
        """Extrae lo que el usuario dijo DESPUÉS de la wake word, si hay algo.

        Soporta el flujo "todo junto en una sola frase" ("Miku, qué hora es"):
        busca la PRIMERA variante de Miku que aparezca en `texto`, toma el
        resto del string desde el final de esa variante, y limpia separadores
        sueltos al principio (comas, dos puntos, puntos, guiones, espacios).

        Devuelve el comando (limpio) si quedó contenido significativo (>2
        caracteres útiles). Devuelve "" si:
          - no se encontró la wake word, o
          - después de la wake word no quedó nada útil (el usuario dijo solo
            "Miku" / "Miku." / "Miku,"), y en ese caso se sigue con el flujo
            de dos pasos.
        """
        if not texto:
            return ""
        t = texto.strip()
        bajo = t.lower()

        # Primera aparición de la wake word como palabra completa.
        m = _RE_MIKU.search(bajo)
        if m is None:
            return ""
        idx, largo = m.start(), m.end() - m.start()

        resto = t[idx + largo:]
        # Limpiamos separadores/puntuación sueltos al principio.
        resto = resto.lstrip(" \t,.:;-—–¡!¿?\"'")
        resto = resto.strip()
        # Si queda contenido útil (>2 caracteres), es un comando en línea.
        if len(resto) > 2:
            return resto
        return ""

    def _capturar(self, source, timeout: float, limite: float):
        """Graba UNA frase: con VAD si está disponible, si no con la pausa fija de la librería.

        Raises:
            sr.WaitTimeoutError: si nadie habló dentro de ``timeout``.
        """
        detector = self._obtener_vad()
        if detector is None or getattr(source, "stream", None) is None:
            return self._reconocedor.listen(source=source, timeout=timeout, phrase_time_limit=limite)
        return self._capturar_con_vad(source, detector, timeout, limite)

    def _capturar_con_vad(self, source, detector, timeout: float, limite: float):
        """Graba desde que empezás a hablar hasta que se te nota que terminaste.

        A diferencia de la pausa fija, el corte lo decide el VAD: como distingue voz de ruido, la
        pausa puede ser corta sin que el ventilador o un golpe en el teclado corten la frase.
        """
        detector.reiniciar()
        pausa_fin = self._pausa_fin()
        frame_seg = vad_mod.MUESTRAS_FRAME / float(vad_mod.FRECUENCIA)
        colchon = deque(maxlen=max(1, int(_COLCHON_INICIO_SEG / frame_seg)))
        frames: list = []
        hablando, silencio, inicio = False, 0.0, time.monotonic()

        while not self._stop.is_set():
            try:
                frame = source.stream.read(vad_mod.MUESTRAS_FRAME)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"No pude leer del micrófono: {e}") from e
            hay_voz = detector.es_voz(frame)

            if not hablando:
                colchon.append(frame)
                if hay_voz:
                    hablando = True
                    frames.extend(colchon)      # con el colchón no se pierde el inicio de la palabra
                    frames.append(frame)
                elif time.monotonic() - inicio > timeout:
                    raise self.sr.WaitTimeoutError("nadie habló")
                continue

            frames.append(frame)
            silencio = 0.0 if hay_voz else silencio + frame_seg
            if silencio >= pausa_fin:
                break                           # terminó de hablar
            if len(frames) * frame_seg >= limite:
                logger.debug("Corté la frase en el límite de %.0f s.", limite)
                break

        return self.sr.AudioData(b"".join(frames), vad_mod.FRECUENCIA, 2)

    def _notificar_error(self, handler: Optional[CallbackError],
                         error: Exception) -> None:
        """Avisa un error al handler dado o, si no hay, a ``self.on_error``."""
        destino = handler or self.on_error
        if destino is None:
            return
        try:
            destino(error)
        except Exception:  # noqa: BLE001
            logger.exception("Falló el handler de errores del STT.")

    # ---------------------------------------------------------------- #
    #        Modo "voz continua" con wake-word en un hilo              #
    # ---------------------------------------------------------------- #
    def iniciar_escucha_continua(self) -> None:
        """Arranca el hilo que escucha la palabra "Miku" en bucle."""
        if self._hilo_escucha and self._hilo_escucha.is_alive():
            if not self._stop.is_set():
                logger.warning("Ya hay una escucha activa.")
                return
            # Se pidió parar hace poco y el hilo viejo todavía está terminando (puede estar en
            # medio de una captura): hay que esperarlo, si no arrancaríamos y quedaría SIN escucha.
            self._hilo_escucha.join(timeout=_ESPERA_CIERRE_HILO)
            if self._hilo_escucha.is_alive():
                logger.error("La escucha anterior no terminó; no puedo reiniciarla todavía.")
                return

        self._stop.clear()
        self._hilo_escucha = threading.Thread(
            target=self._bucle_escucha_permanente,
            daemon=True,
            name="escucha_voz",
        )
        self._hilo_escucha.start()
        logger.info("Escucha continua iniciada (núcleo STT).")

    def detener_escucha(self) -> None:
        """Pide al hilo de escucha que termine y espera por él (unos segundos como máximo)."""
        self._stop.set()
        if self._hilo_escucha and self._hilo_escucha.is_alive():
            self._hilo_escucha.join(timeout=5.0)

    def invocar(self) -> None:
        """Pide al hilo de escucha que salude y tome el próximo comando SIN la palabra "Miku".

        Es lo que ocurre al apretar la tecla de invocación (o al abrir Miku con su atajo): equivale a haber dicho
        "Miku" y esperar el "¿Sí? Decime.". Es seguro llamarlo desde cualquier hilo.
        """
        self._invocada.set()

    def _esperar_silencio(self) -> None:
        """Bloquea mientras Miku habla (si hay un hook), para no oírse a sí misma.

        Espera de a fracciones de segundo para poder reaccionar enseguida si piden detener la
        escucha (antes podía quedar bloqueado hasta 30 s).
        """
        if self.esperar_silencio is None:
            return
        try:
            while not self._stop.is_set():
                if self.esperar_silencio(0.5):
                    return
        except Exception:  # noqa: BLE001
            logger.debug("Falló el hook esperar_silencio.", exc_info=True)

    def _abrir_microfono(self):
        """Abre el micrófono configurado (o el de por defecto si falla).

        Returns:
            Tupla ``(mic, source)`` con el contexto ya abierto.

        Raises:
            RuntimeError: si no se pudo abrir ningún micrófono.
        """
        sr = self.sr
        indice_mic = self.cfg.microfono_index
        # Con VAD el audio tiene que venir a 16 kHz (es lo único que acepta silero); sin VAD se deja
        # la frecuencia nativa del micrófono, como siempre.
        extra = {"sample_rate": vad_mod.FRECUENCIA} if self._obtener_vad() is not None else {}
        try:
            mic = sr.Microphone(device_index=indice_mic, **extra)
            return mic, mic.__enter__()
        except Exception as e:  # noqa: BLE001
            logger.error("No se pudo abrir el micrófono [%s]: %s", indice_mic, e)
        # Intento de respaldo con el micrófono por defecto del sistema.
        try:
            mic = sr.Microphone(device_index=None, **extra)
            source = mic.__enter__()
            logger.info("Micrófono por defecto en uso.")
            return mic, source
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"No se pudo abrir ningún micrófono: {e2}") from e2

    def _bucle_escucha_permanente(self) -> None:
        """Hilo: captura audio corto y detecta la wake word.

        Cualquier error inesperado se registra y se avisa por ``on_error``: el
        hilo no muere en silencio dejando la app "escuchando" sin oír nada.
        """
        try:
            mic, source = self._abrir_microfono()
        except Exception as e:  # noqa: BLE001
            logger.error("La escucha continua no pudo empezar: %s", e)
            self._notificar_error(None, e)
            return

        try:
            # Calibración al ruido ambiente.
            self._reconocedor.adjust_for_ambient_noise(source, duration=1.0)
            self._escuchar(source)
        except Exception as e:  # noqa: BLE001
            logger.exception("La escucha continua se detuvo por un error.")
            self._notificar_error(None, e)
        finally:
            try:
                mic.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    def _escuchar(self, source) -> None:
        """Bucle principal de escucha: wake word -> comando."""
        sr = self.sr
        while not self._stop.is_set():
            # No escuchar mientras Miku habla (evita oírse a sí misma).
            self._esperar_silencio()
            if self._invocada.is_set():
                self._invocada.clear()
                self._atender_invocacion(source)
                continue
            try:
                # timeout corto: si nadie habla, el bucle vuelve a revisar si lo invocaron.
                audio = self._capturar(source, _ESPERA_INVOCACION, _LIMITE_FRASE_WAKE)
            except Exception as e:  # noqa: BLE001
                if isinstance(e, sr.WaitTimeoutError):  # pragma: no cover
                    continue
                logger.warning("Error al escuchar: %s", e)
                time.sleep(0.5)
                continue

            # Un ruido corto no vale una llamada a Whisper.
            if _duracion_audio(audio) < _MIN_DURACION_AUDIO:
                continue

            deteccion = self._detectar_wake(audio)
            logger.debug("Wake escuchó: %r", deteccion.texto)

            if not deteccion.activo:
                continue  # no era la palabra; se sigue escuchando

            texto_wake = (deteccion.texto or "").lower()
            logger.info("Activación detectada: %r", texto_wake or "(sin transcribir)")
            metricas.nuevo_turno(texto_wake)

            # ¿El usuario dijo TODO JUNTO ("Miku, qué hora es")? Si después
            # de la wake word quedó contenido significativo, lo usamos como
            # comando directo y SALTAMOS la segunda escucha. En este caso NO
            # saludamos con "¿Sí? Decime.": el saludo es solo para el flujo
            # de dos pasos (cuando el usuario dijo "Miku" en solitario).
            comando_en_linea = self._extraer_comando_en_linea(texto_wake)
            if comando_en_linea:
                logger.info("Comando en la MISMA frase del wake: %r",
                            comando_en_linea)
                self._entregar_comando(comando_en_linea)
                continue

            # Flujo de DOS PASOS: el usuario dijo solo "Miku". Saludamos
            # ("¿Sí? Decime.") y pedimos el comando en una toma nueva.
            if self.on_wake:
                try:
                    self.on_wake("activation")
                except Exception:  # noqa: BLE001
                    logger.exception("Error en on_wake.")
            if not self._stop.is_set():
                self._capturar_y_reportar_comando(source)

    def _atender_invocacion(self, source) -> None:
        """Saluda y captura un comando directo (equivale a decir "Miku" y esperar)."""
        logger.info("Invocación directa: escucho un comando sin wake word.")
        metricas.nuevo_turno()
        if self.on_wake:
            try:
                self.on_wake("invocacion")
            except Exception:  # noqa: BLE001
                logger.exception("Error en on_wake.")
        if not self._stop.is_set():
            self._capturar_y_reportar_comando(source)

    def _entregar_comando(self, comando: str) -> None:
        """Entrega ``comando`` a ``on_comando`` sin dejar caer el hilo."""
        if not self.on_comando:
            return
        try:
            self.on_comando(comando)
        except Exception:  # noqa: BLE001
            logger.exception("Error en on_comando.")

    def _capturar_y_reportar_comando(self, source) -> None:
        """Tras el wake echo, captura la frase del usuario y la reporta."""
        # Esperamos a que termine "¿Sí? Decime." antes de abrir la escucha:
        # si no, el micrófono grabaría la voz de Miku como si fuera el comando.
        self._esperar_silencio()
        try:
            with metricas.etapa("escuchar"):
                audio = self._capturar(source, 6, 12)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, self.sr.WaitTimeoutError):
                logger.info("No dijiste nada tras el saludo; vuelvo a esperar.")
                return
            logger.warning("No llegó comando tras el wake: %s", e)
            self._notificar_error(None, e)
            return

        with metricas.etapa("stt"):
            comando = self.transcribir_audio(audio)
        if not comando:
            logger.warning("Comando vacío tras el wake.")
            self._notificar_error(None, RuntimeError("Comando vacío."))
            return

        logger.info("Comando transcrito: %s", comando)
        self._entregar_comando(comando)
