"""
inactividad.py - Cuánto hace que el usuario no toca el teclado ni el mouse (Windows).

Lo usa el motor proactivo para saber cuándo alguien "volvió" tras estar ausente (AFK).
Nunca lanza: si no se puede leer, devuelve 0 (= "está activo": ante la duda no se molesta).
"""
from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger("miku.plataforma.inactividad")


class _LASTINPUTINFO(ctypes.Structure):  # noqa: N801 - nombre estilo Win32
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def segundos_inactivo() -> float:
    """Segundos desde la última pulsación de tecla o movimiento del mouse."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        ahora = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, ((ahora - info.dwTime) & 0xFFFFFFFF) / 1000.0)
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la inactividad: %s", e)
        return 0.0
