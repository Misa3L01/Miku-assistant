"""Resolución de pantalla en Windows (ctypes, sin dependencias).

Antes había DOS copias de la estructura ``DEVMODE`` y de la lógica de cambio: una en el
plugin de macros y otra en los snapshots de "salir del modo". Ahora viven acá.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger("miku.plataforma.pantalla")

_ENUM_CURRENT_SETTINGS = -1
_DM_PELSWIDTH = 0x00080000
_DM_PELSHEIGHT = 0x00100000
_CDS_TEST = 0x00000002
_CDS_UPDATEREGISTRY = 0x00000001
_DISP_CHANGE_SUCCESSFUL = 0
_DISP_CHANGE_BADMODE = -2

# Resultados de ``cambiar_resolucion``.
OK = "ok"
REINICIO = "reinicio"              # se aplicó, pero Windows pide reiniciar para dejarlo fijo
NO_SOPORTADA = "no_soportada"      # el driver/monitor no tiene ese modo
ERROR = "error"


class _DEVMODE(ctypes.Structure):  # noqa: N801 - nombre estilo Win32
    """Subconjunto de DEVMODEW (220 bytes): alcanza para leer/cambiar ancho y alto."""

    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


def _user32() -> Any:
    """``user32.dll`` (función aparte para poder simularla en los tests)."""
    return ctypes.windll.user32


def _modo_actual(user32: Any) -> Optional[_DEVMODE]:
    dm = _DEVMODE()
    dm.dmSize = ctypes.sizeof(_DEVMODE)
    if not user32.EnumDisplaySettingsW(None, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return None
    return dm


def resolucion_actual() -> Optional[Tuple[int, int]]:
    """Devuelve ``(ancho, alto)`` del monitor principal, o None si no se puede leer."""
    try:
        dm = _modo_actual(_user32())
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la resolución: %s", e)
        return None
    return (int(dm.dmPelsWidth), int(dm.dmPelsHeight)) if dm is not None else None


def cambiar_resolucion(ancho: int, alto: int, solo_probar: bool = False) -> Tuple[str, int]:
    """Cambia la resolución (o solo la prueba con ``solo_probar=True``, sin tocar la pantalla).

    Parte del modo ACTUAL (frecuencia, color...) y cambia únicamente ancho y alto. Primero
    prueba el modo con ``CDS_TEST``: si no existe, la pantalla no queda a medias.

    Returns:
        ``(estado, código de Windows)``; ``estado`` es ``OK``, ``REINICIO``, ``NO_SOPORTADA``
        o ``ERROR``.
    """
    try:
        user32 = _user32()
        dm = _modo_actual(user32)
    except Exception as e:  # noqa: BLE001
        logger.debug("Sin acceso a user32: %s", e)
        return ERROR, -1
    if dm is None:
        return ERROR, -1

    dm.dmPelsWidth = int(ancho)
    dm.dmPelsHeight = int(alto)
    dm.dmFields = _DM_PELSWIDTH | _DM_PELSHEIGHT

    prueba = user32.ChangeDisplaySettingsW(ctypes.byref(dm), _CDS_TEST)
    if prueba == _DISP_CHANGE_BADMODE:
        return NO_SOPORTADA, prueba
    if prueba != _DISP_CHANGE_SUCCESSFUL:
        return ERROR, prueba
    if solo_probar:
        return OK, prueba

    res = user32.ChangeDisplaySettingsW(ctypes.byref(dm), _CDS_UPDATEREGISTRY)
    if res == _DISP_CHANGE_SUCCESSFUL:
        logger.info("Resolución cambiada a %dx%d.", ancho, alto)
        return OK, res
    if res > 0:  # "restart required": se aplicó, no es un error fatal
        return REINICIO, res
    return ERROR, res
