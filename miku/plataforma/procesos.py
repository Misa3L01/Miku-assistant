"""
procesos.py - Consultas sobre procesos de Windows (ventana en primer plano).

Lo usan el modo gaming automático y el motor de avisos proactivos. Nunca lanza: si falta
win32/psutil o no hay ventana, devuelve None.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger("miku.plataforma.procesos")


def proceso_primer_plano() -> Optional[str]:
    """Nombre en minúsculas (sin ``.exe``) del proceso de la ventana en primer plano."""
    try:
        import psutil  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.debug("Falta win32/psutil: %s", e)
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return None
        nombre = (psutil.Process(pid).name() or "").lower()
        return nombre[:-4] if nombre.endswith(".exe") else nombre
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude obtener el proceso en primer plano: %s", e)
        return None


def ventana_primer_plano() -> Optional[Tuple[int, str, str]]:
    """``(hwnd, proceso, título)`` de la ventana en primer plano; None si no se puede leer.

    El proceso va en minúsculas y sin ``.exe``, como en ``proceso_primer_plano``.
    """
    try:
        import psutil  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.debug("Falta win32/psutil: %s", e)
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return None
        nombre = (psutil.Process(pid).name() or "").lower()
        proceso = nombre[:-4] if nombre.endswith(".exe") else nombre
        return int(hwnd), proceso, win32gui.GetWindowText(hwnd) or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la ventana en primer plano: %s", e)
        return None
