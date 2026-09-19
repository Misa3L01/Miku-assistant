# -*- coding: utf-8 -*-
"""
modos.py - Snapshots de estado del sistema y reversión ("salir de modo").

Cuando se activa un "modo de contexto" (p. ej. una macro que cambia resolución
y/o volumen para Fortnite), conviene poder REVERTIR después. Este módulo
captura el estado relevante ANTES del cambio y lo restaura con ``salir_modo``.

Qué captura (todo best-effort, con imports lazy):
    - Volumen GENERAL del sistema (nivel + mute) vía pycaw.
    - Resolución de pantalla actual vía ctypes (DEVMODE, igual que macros.py).
    - Brillo de pantalla (si `screen_brightness_control` está disponible).

El snapshot se guarda en memoria (uno solo "activo"). Persistirlo no hace
falta: si se cierra el asistente, un modo activo tampoco sobrevive.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from miku.plataforma import pantalla
from miku.plataforma.audio import volumen_master

logger = logging.getLogger("miku.modos")

# Snapshot activo (uno solo). None = no hay modo del que salir.
_snapshot: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# Captura
# --------------------------------------------------------------------------- #
def capturar() -> Dict[str, Any]:
    """Captura el estado actual del sistema (volumen, resolución, brillo).

    Devuelve el dict del snapshot y lo deja como activo. Si ya había uno
    activo, NO lo pisa (para no perder el punto de restauración original).
    """
    global _snapshot
    if _snapshot is not None:
        return _snapshot

    snap: Dict[str, Any] = {}
    vol = _capturar_volumen()
    if vol is not None:
        snap["volumen"] = vol
    res = _capturar_resolucion()
    if res is not None:
        snap["resolucion"] = res
    brillo = _capturar_brillo()
    if brillo is not None:
        snap["brillo"] = brillo

    _snapshot = snap
    logger.info("Snapshot de modo capturado: %s", list(snap.keys()))
    return snap


def capturar_si_libre() -> bool:
    """Captura un snapshot SOLO si no había uno activo.

    A diferencia de ``capturar()`` (que devuelve el existente sin avisar),
    indica si ESTA llamada creó el snapshot. Quien lo creó es el dueño y el
    único que debe revertirlo: así el Game Booster no consume (ni borra) el
    snapshot de una macro activa.

    Returns:
        True si se creó un snapshot nuevo; False si ya había uno.
    """
    if _snapshot is not None:
        return False
    capturar()
    return True


def hay_snapshot() -> bool:
    """True si hay un snapshot guardado (hay modo del que salir)."""
    return _snapshot is not None


def limpiar() -> None:
    """Descarta el snapshot (tras revertir o si el usuario lo pide)."""
    global _snapshot
    _snapshot = None


# --------------------------------------------------------------------------- #
# Reversión
# --------------------------------------------------------------------------- #
def salir_modo() -> str:
    """Revierte el estado al del último snapshot. Devuelve un mensaje natural.

    Si no hay snapshot, lo avisa y no rompe nada.
    """
    global _snapshot
    if _snapshot is None:
        return ("No tengo ningún modo activo del que salir. "
                "No toqué nada.")

    aplicados = []
    fallos = []

    if "volumen" in _snapshot:
        if _restaurar_volumen(_snapshot["volumen"]):
            aplicados.append("volumen")
        else:
            fallos.append("volumen")

    if "resolucion" in _snapshot:
        r = _snapshot["resolucion"]
        if _restaurar_resolucion(r.get("ancho"), r.get("alto")):
            aplicados.append("resolución")
        else:
            fallos.append("resolución")

    if "brillo" in _snapshot:
        if _restaurar_brillo(_snapshot["brillo"]):
            aplicados.append("brillo")
        else:
            fallos.append("brillo")

    _snapshot = None  # consumimos el snapshot

    if not aplicados and not fallos:
        return "Salí del modo, pero no había nada para restaurar."
    if fallos:
        return ("Salí del modo. Restauré: " + ", ".join(aplicados or ["nada"]) +
                f". No pude restaurar: {', '.join(fallos)}.")
    return "Listo, salí del modo y dejé todo como estaba (" + \
        ", ".join(aplicados) + ")."


# --------------------------------------------------------------------------- #
# Volumen (pycaw)
# --------------------------------------------------------------------------- #
def _capturar_volumen() -> Optional[Dict[str, Any]]:
    try:
        vol = volumen_master()
        if vol is None:
            return None
        return {
            "nivel": float(vol.GetMasterVolumeLevelScalar()),
            "mute": bool(vol.GetMute()),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude capturar el volumen: %s", e)
        return None


def _restaurar_volumen(datos: Dict[str, Any]) -> bool:
    try:
        vol = volumen_master()
        if vol is None:
            return False
        vol.SetMasterVolumeLevelScalar(float(datos.get("nivel", 0.5)), None)
        vol.SetMute(1 if datos.get("mute") else 0, None)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude restaurar el volumen: %s", e)
        return False


# --------------------------------------------------------------------------- #
# Resolución (miku.plataforma.pantalla)
# --------------------------------------------------------------------------- #
def _capturar_resolucion() -> Optional[Dict[str, int]]:
    actual = pantalla.resolucion_actual()
    return {"ancho": actual[0], "alto": actual[1]} if actual else None


def _restaurar_resolucion(ancho: Any, alto: Any) -> bool:
    try:
        ancho, alto = int(ancho), int(alto)
    except (TypeError, ValueError):
        return False
    estado, _ = pantalla.cambiar_resolucion(ancho, alto)
    return estado in (pantalla.OK, pantalla.REINICIO)


# --------------------------------------------------------------------------- #
# Brillo (screen_brightness_control, opcional)
# --------------------------------------------------------------------------- #
def _capturar_brillo() -> Optional[int]:
    try:
        import screen_brightness_control as sbc  # lazy
        valores = sbc.get_brightness(display=0)
        if valores:
            return int(valores[0])
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude capturar el brillo: %s", e)
    return None


def _restaurar_brillo(nivel: int) -> bool:
    try:
        import screen_brightness_control as sbc  # lazy
        sbc.set_brightness(int(nivel))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude restaurar el brillo: %s", e)
        return False