"""
transcriptores.py - Quién convierte tu voz en texto (nube o local).

``SpeechToText`` (escucha.py) solo captura audio; la transcripción la hace un *transcriptor*
intercambiable, elegido con ``STT_PROVEEDOR``:

    * ``groq``  (por defecto): Whisper en la nube de Groq. Rápido, necesita clave e internet.
    * ``local``: faster-whisper en tu PC (``pip install faster-whisper``). Sin nube ni clave; el
      primer uso descarga el modelo (``STT_MODELO_LOCAL``, "small" por defecto) y es más lento sin GPU.

Contrato: ``transcribir(wav_bytes, modelo, timeout) -> str`` devuelve el texto o ``""`` si falló
(nunca lanza). El filtro de "alucinaciones" de Whisper lo aplica quien llama.
"""
from __future__ import annotations

import io
import logging
import threading
from typing import Any, Optional, Protocol

from miku.plataforma import red

logger = logging.getLogger("miku.stt.transcriptores")

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class Transcriptor(Protocol):
    """Interfaz de un transcriptor de voz."""

    nombre: str

    def transcribir(self, wav_bytes: bytes, modelo: str, timeout: float) -> str:
        """Texto del audio WAV, o "" si no se pudo."""
        ...


class WhisperGroq:
    """Whisper en la API de Groq."""

    nombre = "groq"

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def transcribir(self, wav_bytes: bytes, modelo: str, timeout: float) -> str:
        clave = str(self.cfg.groq_api_key_stt).strip()
        if not clave:
            logger.error("Falta la API key para STT. Configurala en GROQ_API_KEY_STT "
                         "(entorno) o en config_local.GROQ_API_KEY_STT.")
            return ""
        try:
            resp = red.post(
                GROQ_URL, headers={"Authorization": f"Bearer {clave}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model": modelo, "language": "es", "response_format": "json", "temperature": 0},
                timeout=timeout)
            data = resp.json()
            if "text" not in data:
                logger.error("Error transcribiendo con Groq: %s", data)
                return ""
            return (data["text"] or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.error("Error llamando a la API de transcripción de Groq: %s", e)
            return ""


class WhisperLocal:
    """faster-whisper en la PC (sin nube)."""

    nombre = "local"

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._modelo: Optional[Any] = None
        self._fallo = False
        self._lock = threading.Lock()

    def _cargar(self) -> Optional[Any]:
        """Carga el modelo la primera vez (puede tardar y descargar); None si no se puede."""
        with self._lock:
            if self._modelo is not None or self._fallo:
                return self._modelo
            try:
                from faster_whisper import WhisperModel  # type: ignore
                tamano = str(self.cfg.get("stt_modelo_local", "small") or "small").strip()
                logger.info("Cargando faster-whisper '%s' (la primera vez descarga el modelo)...", tamano)
                self._modelo = WhisperModel(tamano, device="auto", compute_type="int8")
            except Exception as e:  # noqa: BLE001
                self._fallo = True      # no se reintenta en cada frase
                logger.error("No pude cargar faster-whisper (%s). Instalalo con "
                             "'pip install faster-whisper' o usá STT_PROVEEDOR = 'groq'.", e)
            return self._modelo

    def transcribir(self, wav_bytes: bytes, modelo: str, timeout: float) -> str:
        motor = self._cargar()
        if motor is None:
            return ""
        try:
            segmentos, _ = motor.transcribe(io.BytesIO(wav_bytes), language="es", beam_size=1,
                                            vad_filter=True, temperature=0)
            return " ".join(s.text.strip() for s in segmentos).strip()
        except Exception as e:  # noqa: BLE001
            logger.error("Error transcribiendo con faster-whisper: %s", e)
            return ""


def crear_transcriptor(cfg: Any) -> Transcriptor:
    """El transcriptor que pide la config (``groq`` si el valor no se reconoce)."""
    proveedor = str(cfg.get("stt_proveedor", "groq") or "groq").strip().lower()
    if proveedor == "local":
        return WhisperLocal(cfg)
    if proveedor != "groq":
        logger.warning("STT_PROVEEDOR '%s' desconocido; uso groq.", proveedor)
    return WhisperGroq(cfg)
