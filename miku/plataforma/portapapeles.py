"""
portapapeles.py - Leer y escribir texto en el portapapeles de Windows.

Lo usan el traductor y las acciones sobre lo copiado ("traducí lo que copié"). El portapapeles
puede estar bloqueado un instante por otro programa, así que se reintenta unas veces. Nunca lanza.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("miku.plataforma.portapapeles")

_REINTENTOS = 6
_PAUSA_S = 0.08


def _win32() -> Any:
    import win32clipboard  # type: ignore  # solo existe en Windows (pywin32)
    return win32clipboard


def leer_texto() -> Optional[str]:
    """El texto copiado, o None si el portapapeles está vacío, tiene otra cosa (imagen, archivos) o falla."""
    try:
        clip = _win32()
    except Exception as e:  # noqa: BLE001
        logger.warning("win32clipboard no disponible: %s", e)
        return None
    for _ in range(_REINTENTOS):
        try:
            clip.OpenClipboard()
            try:
                if clip.IsClipboardFormatAvailable(clip.CF_UNICODETEXT):
                    return str(clip.GetClipboardData(clip.CF_UNICODETEXT))
                return None
            finally:
                clip.CloseClipboard()
        except Exception as e:  # noqa: BLE001
            logger.debug("Portapapeles ocupado (%s); reintento.", e)
            time.sleep(_PAUSA_S)
    return None


def tipo_de_contenido() -> str:
    """``texto``, ``imagen``, ``archivos`` o ``vacio`` (para explicar qué hay copiado cuando no es texto)."""
    try:
        clip = _win32()
        clip.OpenClipboard()
        try:
            if clip.IsClipboardFormatAvailable(clip.CF_UNICODETEXT):
                return "texto"
            if clip.IsClipboardFormatAvailable(clip.CF_HDROP):
                return "archivos"
            if clip.IsClipboardFormatAvailable(clip.CF_DIB):
                return "imagen"
            return "vacio"
        finally:
            clip.CloseClipboard()
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude ver qué hay en el portapapeles: %s", e)
        return "vacio"


def escribir_texto(texto: str) -> bool:
    """Deja ``texto`` en el portapapeles. True si pudo."""
    try:
        clip = _win32()
    except Exception as e:  # noqa: BLE001
        logger.warning("win32clipboard no disponible: %s", e)
        return False
    for _ in range(_REINTENTOS):
        try:
            clip.OpenClipboard()
            try:
                clip.EmptyClipboard()
                clip.SetClipboardData(clip.CF_UNICODETEXT, texto)
            finally:
                clip.CloseClipboard()
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("Portapapeles ocupado (%s); reintento.", e)
            time.sleep(_PAUSA_S)
    logger.error("No pude escribir en el portapapeles.")
    return False
