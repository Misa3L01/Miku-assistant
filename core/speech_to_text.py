"""
speech_to_text.py - Reconocimiento de voz (STT) con estructura en hilos.

En el proyecto original la escucha se hacía con SpeechRecognition en
bloque. Acá separamos la captura de audio (en su propio hilo) del
procesamiento para no congelar el bucle principal.

Flujo en modo "voz":
    1. Un hilo escucha en segundo plano y transcribe cada chunk corto.
    2. Se detecta la palabra de activación "Miku".
    3. Se emite el evento correspondiente en el EventBus.

También brindamos captura "push-to-talk" estilo botón (por API externa):
cuando el hilo lanzado con `capture_once` transcribe de una sola toma.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# Imports "pesados"/opcionales hechos de forma lazy dentro de los métodos
# para acortar el arranque si no se usa modo voz (ver optimización).
import config as config_mod  # noqa: F401  (accede por config_mod.config)

logger = logging.getLogger("miku.stt")

# Palabras que el STT puede deformar de "Miku" (del proyecto original).
_VARIANTES_MIKU: tuple = (
    "miku", "mika", "mica", "niku", "iku", "mike",
    "miko", "mico", "migo", "meco", "meko", "mego",
    "mecu", "migu", "mecup",
)

# Tipo del handler de respuesta de texto final.
CallbackTranscripcion = Callable[[str], None]
CallbackError = Callable[[Exception], None]


@dataclass
class _ResultadoEscucha:
    """Contenedor de una toma de audio transcrita."""

    texto: str = ""
    exito: bool = False
    error: Optional[Exception] = None


class SpeechToText:
    """Motor de reconocimiento de voz.

    Args:
        cfg: Instancia de ``config.Config`` ya cargada.
        detector: Función opcional que devuelve True cuando el botón de
            push-to-talk está presionado (se inyecta desde main para no
            acoplar el hardware aquí).
    """

    def __init__(self, cfg: "config_mod.Config",
                 detector: Optional[Callable[[], bool]] = None) -> None:
        self.cfg = cfg
        self._detector = detector              # para push-to-talk
        self._reconocedor = None               # reconocedor "lazy"
        self._microfono_abierto = False

        # Estado del escucha en segundo plano.
        self._hilo_escucha: Optional[threading.Thread] = None
        self._ejecutando = False
        self._stop = threading.Event()

        # Handler cuando se detecta la palabra de activación.
        self.on_wake: Optional[CallbackTranscripcion] = None
        # Handler con el texto del comando final (tras el wake echo).
        self.on_comando: Optional[CallbackTranscripcion] = None
        self.on_error: Optional[CallbackError] = None

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
    def sr(self):  # noqa: D102
        return self._sr_mod

    @property
    def reconocedor(self):
        """Reconocedor SpeechRecognition inicializado."""
        self._importar_dependencias()
        return self._reconocedor

    # ---------------------------------------------------------------- #
    #                  Transcripción vía API de Groq                   #
    # ---------------------------------------------------------------- #
    def transcribir_audio(self, audio) -> str:
        """Envía un objeto AudioData a la API Whisper de Groq.

        Returns:
            Texto transcrito o "" si falló.
        """
        self._importar_dependencias()
        import requests  # lazy

        try:
            wav_bytes = audio.get_wav_data()
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.cfg.groq_api_key_stt}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model": self.cfg.modelo_stt_comando,
                    "language": "es",
                    "response_format": "json",
                    "temperature": 0,
                },
                timeout=15,
            )
            data = resp.json()
            if "text" not in data:
                logger.error("Error transcribiendo con Groq: %s", data)
                return ""
            return (data["text"] or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.error("Error llamando a la API de transcripción de Groq: %s", e)
            return ""

    def _transcribir_wake(self, audio) -> str:
        """Transcribe enfocado a detectar la palabra de activación.

        Usa el modelo configurado para wake (fast), distinto del comando.
        """
        self._importar_dependencias()
        import requests  # lazy

        try:
            wav_bytes = audio.get_wav_data()
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.cfg.groq_api_key_stt}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model": self.cfg.modelo_stt_wake,
                    "language": "es",
                    "response_format": "json",
                    "temperature": 0,
                },
                timeout=12,
            )
            data = resp.json()
            return (data.get("text") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.error("Error en transcripción de wake: %s", e)
            return ""

    def _contiene_miku(self, texto: str) -> bool:
        """Devuelve True si `texto` parece contener la palabra Miku."""
        t = texto.lower()
        return any(v in t for v in _VARIANTES_MIKU)

    # ---------------------------------------------------------------- #
    #             API pública: capturas que no bloquean                #
    # ---------------------------------------------------------------- #
    def capturar_comando(self, callback: CallbackTranscripcion,
                         on_error: Optional[CallbackError] = None) -> None:
        """Captura una sola vez el audio del micrófono (push-to-talk corto)
        y entrega el texto transcrito por `callback`. Corre en un hilo."""
        hilo = threading.Thread(
            target=self._run_captura_unica,
            args=(callback, on_error),
            daemon=True,
            name="captura_una_vez",
        )
        hilo.start()

    def _run_captura_unica(self, callback: CallbackTranscripcion,
                           on_error: Optional[CallbackError]) -> None:
        """Hilo interno para una captura única sin bloquear al llamador."""
        resultado = self.capturar_una_vez_sync()
        if resultado.exito and resultado.texto:
            callback(resultado.texto)
        elif not resultado.exito and (on_error or self.on_error):
            handler = on_error or self.on_error
            handler(resultado.error or RuntimeError("No se pudo capturar audio."))

    def capturar_una_vez_sync(self) -> _ResultadoEscucha:
        """Captura una toma de audio del micrófono y la transcribe.

        Este método ES bloqueante (por eso llamarlo siempre dentro de un
        hilo o del main si se quiere pausar).
        """
        self._importar_dependencias()
        sr = self._sr_mod
        index_mic = self.cfg.microfono_index
        mic = sr.Microphone(device_index=index_mic)

        try:
            source = mic.__enter__()  # mantiene la sesión abierta
        except Exception as e:  # noqa: BLE001
            return _ResultadoEscucha(exito=False, error=e)

        try:
            self._reconocedor.adjust_for_ambient_noise(source, duration=0.5)
            audio = self._reconocedor.listen(source=source, timeout=6,
                                             phrase_time_limit=12)
            texto = self.transcribir_audio(audio)
            return _ResultadoEscucha(texto=texto, exito=bool(texto))
        except Exception as e:  # noqa: BLE001
            logger.warning("Error capturando audio: %s", e)
            return _ResultadoEscucha(exito=False, error=e)
        finally:
            try:
                mic.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- #
    #        Modo "voz continua" con wake-word en un hilo              #
    # ---------------------------------------------------------------- #
    def iniciar_escucha_continua(self) -> None:
        """Arranca el hilo que escucha la palabra "Miku" en bucle."""
        if self._hilo_escucha and self._hilo_escucha.is_alive():
            logger.warning("Ya hay una escucha activa.")
            return

        self._ejecutando = True
        self._stop.clear()
        self._hilo_escucha = threading.Thread(
            target=self._bucle_escucha_permanente,
            daemon=True,
            name="escucha_voz",
        )
        self._hilo_escucha.start()
        logger.info("Escucha continua iniciada (núcleo STT).")

    def detener_escucha(self) -> None:
        """Pide al hilo de escucha que termine y espera por él."""
        self._stop.set()
        if self._hilo_escucha and self._hilo_escucha.is_alive():
            self._hilo_escucha.join(timeout=2.0)
        self._ejecutando = False

    def _bucle_escucha_permanente(self) -> None:
        """Hilo: captura audio corto y detecta la wake word."""
        self._importar_dependencias()
        sr = self._sr_mod
        indice_mic = self.cfg.microfono_index

        mic = sr.Microphone(device_index=indice_mic)
        # Abrimos una sola vez (más estable con USB / BT).
        try:
            source = mic.__enter__()
        except Exception as e:  # noqa: BLE001
            logger.error("No se pudo abrir el micrófono [%s]: %s", indice_mic, e)
            # Intento de respaldo con el micrófono por defecto del sistema.
            try:
                mic = sr.Microphone(device_index=None)
                source = mic.__enter__()
                logger.info("Micrófono por defecto en uso.")
            except Exception as e2:  # noqa: BLE001
                logger.error("También falló el micrófono default: %s", e2)
                return

        try:
            # Calibración al ruido ambiente.
            self._reconocedor.adjust_for_ambient_noise(source, duration=1.0)

            while not self._stop.is_set():
                try:
                    audio = self._reconocedor.listen(
                        source=source, timeout=None, phrase_time_limit=4
                    )
                except Exception as e:  # noqa: BLE001
                    if isinstance(e, sr.WaitTimeoutError):  # pragma: no cover
                        continue
                    logger.warning("Error al escuchar: %s", e)
                    time.sleep(0.5)
                    continue

                texto_wake = self._transcribir_wake(audio).lower()
                logger.debug("Wake escuchó: %r", texto_wake)

                if not texto_wake or not self._contiene_miku(texto_wake):
                    continue  # no era la palabra; se sigue escuchando

                logger.info("Activación detectada: %r", texto_wake)
                if self.on_wake:
                    try:
                        self.on_wake("activation")
                    except Exception:  # noqa: BLE001
                        logger.exception("Error en on_wake.")

                # Pedimos el comando completo tras despertar.
                if not self._stop.is_set():
                    self._capturar_y_reportar_comando(source)

        finally:
            try:
                mic.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    def _capturar_y_reportar_comando(self, source) -> None:
        """Tras el wake echo, captura la frase del usuario y la reporta."""
        try:
            audio = self._reconocedor.listen(source=source, timeout=6,
                                             phrase_time_limit=12)
        except Exception as e:  # noqa: BLE001
            logger.warning("No llegó comando tras el wake: %s", e)
            if self.on_error:
                self.on_error(e)
            return

        comando = self.transcribir_audio(audio)
        if not comando:
            logger.warning("Comando vacío tras el wake.")
            if self.on_error:
                self.on_error(RuntimeError("Comando vacío."))
            return

        logger.info("Comando transcrito: %s", comando)
        if self.on_comando:
            try:
                self.on_comando(comando)
            except Exception:  # noqa: BLE001
                logger.exception("Error en on_comando.")
