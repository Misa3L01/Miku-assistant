"""
speech_to_text.py - Reconocimiento de voz (STT) con estructura en hilos.

En el proyecto original la escucha se hacía con SpeechRecognition en
bloque. Acá separamos la captura de audio (en su propio hilo) del
procesamiento para no congelar el bucle principal.

Flujo en modo "voz":
    1. Un hilo escucha en segundo plano y transcribe cada chunk corto.
    2. Se detecta la palabra de activación "Miku".
    3. Se emite el evento correspondiente en el EventBus.

También se puede INVOCAR a Miku sin decir la palabra (tecla F22): ``invocar()`` hace que el
hilo de escucha salude y tome el próximo comando directamente.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Optional

# Imports "pesados"/opcionales hechos de forma lazy dentro de los métodos
# para acortar el arranque si no se usa modo voz (ver optimización).
from miku.ajustes import carga as config_mod  # noqa: F401  (accede por config_mod.config)
from miku.plataforma.texto import sin_acentos

logger = logging.getLogger("miku.stt")

# Palabras que el STT puede deformar de "Miku". Se buscan como PALABRA COMPLETA
# (ver ``_RE_MIKU``): por substring, "migo"/"mica"/"iku" activaban a Miku con
# "amigo", "conmigo", "química", "económica"...
_VARIANTES_MIKU: tuple = (
    "miku", "mikú", "mika", "mica", "niku", "mike",
    "miko", "mico", "meco", "meko", "mego", "mecu", "migu",
)
_RE_MIKU = re.compile(r"\b(?:%s)\b" % "|".join(_VARIANTES_MIKU))

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
# Cada cuántos segundos el bucle de escucha revisa si lo invocaron (F22) aunque nadie hable.
_ESPERA_INVOCACION = 1.0
# Segundos que se espera a que un hilo de escucha que se está deteniendo termine de verdad
# (puede estar en medio de una captura de hasta ~18 s).
_ESPERA_CIERRE_HILO = 20.0


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

        # Estado del escucha en segundo plano.
        self._hilo_escucha: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Invocación directa (tecla F22 / atajo): saluda y toma el PRÓXIMO comando sin
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

    @property
    def sr(self):
        """Módulo ``speech_recognition`` (se importa la primera vez)."""
        self._importar_dependencias()
        return self._sr_mod

    @property
    def reconocedor(self):
        """Reconocedor SpeechRecognition inicializado."""
        self._importar_dependencias()
        return self._reconocedor

    # ---------------------------------------------------------------- #
    #                  Transcripción vía API de Groq                   #
    # ---------------------------------------------------------------- #
    def _llamar_whisper(self, audio, modelo: str, timeout: float) -> str:
        """Transcribe ``audio`` con Whisper (Groq) usando ``modelo``.

        Es el cuerpo común de ``transcribir_audio`` (comandos) y
        ``_transcribir_wake`` (wake word).

        Returns:
            Texto transcrito, o "" si falló, no hay API key o Whisper devolvió
            una "alucinación" típica de silencio/ruido.
        """
        self._importar_dependencias()
        if not str(self.cfg.groq_api_key_stt).strip():
            logger.error(
                "Falta la API key para STT. Configurala en GROQ_API_KEY_STT "
                "(entorno) o en config_local.GROQ_API_KEY_STT.")
            return ""
        import requests  # lazy

        try:
            wav_bytes = audio.get_wav_data()
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.cfg.groq_api_key_stt}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model": modelo,
                    "language": "es",
                    "response_format": "json",
                    "temperature": 0,
                },
                timeout=timeout,
            )
            data = resp.json()
            if "text" not in data:
                logger.error("Error transcribiendo con Groq: %s", data)
                return ""
            texto = (data["text"] or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.error("Error llamando a la API de transcripción de Groq: %s", e)
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
        return _RE_MIKU.search((texto or "").lower()) is not None

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

        Es lo que ocurre al apretar F22 (o al abrir Miku con su atajo): equivale a haber dicho
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
        try:
            mic = sr.Microphone(device_index=indice_mic)
            return mic, mic.__enter__()
        except Exception as e:  # noqa: BLE001
            logger.error("No se pudo abrir el micrófono [%s]: %s", indice_mic, e)
        # Intento de respaldo con el micrófono por defecto del sistema.
        try:
            mic = sr.Microphone(device_index=None)
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
                audio = self._reconocedor.listen(
                    source=source, timeout=_ESPERA_INVOCACION,
                    phrase_time_limit=_LIMITE_FRASE_WAKE)
            except Exception as e:  # noqa: BLE001
                if isinstance(e, sr.WaitTimeoutError):  # pragma: no cover
                    continue
                logger.warning("Error al escuchar: %s", e)
                time.sleep(0.5)
                continue

            # Un ruido corto no vale una llamada a Whisper.
            if _duracion_audio(audio) < _MIN_DURACION_AUDIO:
                continue

            texto_wake = self._transcribir_wake(audio).lower()
            logger.debug("Wake escuchó: %r", texto_wake)

            if not texto_wake or not self._contiene_miku(texto_wake):
                continue  # no era la palabra; se sigue escuchando

            logger.info("Activación detectada: %r", texto_wake)

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
            audio = self._reconocedor.listen(source=source, timeout=6,
                                             phrase_time_limit=12)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, self.sr.WaitTimeoutError):
                logger.info("No dijiste nada tras el saludo; vuelvo a esperar.")
                return
            logger.warning("No llegó comando tras el wake: %s", e)
            self._notificar_error(None, e)
            return

        comando = self.transcribir_audio(audio)
        if not comando:
            logger.warning("Comando vacío tras el wake.")
            self._notificar_error(None, RuntimeError("Comando vacío."))
            return

        logger.info("Comando transcrito: %s", comando)
        self._entregar_comando(comando)
