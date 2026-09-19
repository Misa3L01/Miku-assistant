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

Hay un solo snapshot "activo". Se guarda también en ``data/modo_snapshot.json`` para que
sobreviva a un cierre o a un cuelgue: si Miku se cae mientras jugabas en otra resolución, al volver a
abrirla "salí del modo" sigue sabiendo a qué volver. Un snapshot con más de ``VIGENCIA_H`` horas se
descarta (el estado de la PC ya cambió demasiado como para restaurarlo a ciegas).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from miku.ajustes.carga import BASE_DIR
from miku.plataforma import pantalla
from miku.plataforma.audio import volumen_master
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.modos")

#: Dónde se guarda el snapshot activo (los tests lo redirigen a una carpeta temporal).
RUTA_SNAPSHOT: Path = BASE_DIR / "data" / "modo_snapshot.json"
#: Horas que un snapshot guardado sigue siendo confiable.
VIGENCIA_H = 12

# Snapshot activo (uno solo). None = no hay modo del que salir.
_snapshot: Optional[Dict[str, Any]] = None
_cargado = False


def _activo() -> Optional[Dict[str, Any]]:
    """Snapshot activo; la primera vez lo recupera del disco si quedó uno vigente."""
    global _snapshot, _cargado
    if not _cargado:
        _cargado = True
        if _snapshot is None:
            _snapshot = _leer_de_disco()
    return _snapshot


def _leer_de_disco() -> Optional[Dict[str, Any]]:
    try:
        datos = json.loads(RUTA_SNAPSHOT.read_text(encoding="utf-8"))
        if time.time() - float(datos["capturado"]) > VIGENCIA_H * 3600:
            logger.info("Snapshot guardado vencido; lo descarto.")
            _borrar_de_disco()
            return None
        snap = datos["snapshot"]
        logger.info("Recuperé un snapshot de modo del disco: %s", list(snap))
        return snap if isinstance(snap, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.debug("Snapshot guardado ilegible (%s); lo ignoro.", e)
        return None


def _guardar_en_disco(snap: Dict[str, Any]) -> None:
    try:
        RUTA_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        tmp = RUTA_SNAPSHOT.with_suffix(".tmp")
        tmp.write_text(json.dumps({"capturado": time.time(), "snapshot": snap}), encoding="utf-8")
        os.replace(tmp, RUTA_SNAPSHOT)
    except OSError as e:
        logger.debug("No pude guardar el snapshot: %s", e)


def _borrar_de_disco() -> None:
    try:
        RUTA_SNAPSHOT.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug("No pude borrar el snapshot guardado: %s", e)


# --------------------------------------------------------------------------- #
# Captura
# --------------------------------------------------------------------------- #
def capturar() -> Dict[str, Any]:
    """Captura el estado actual del sistema (volumen, resolución, brillo).

    Devuelve el dict del snapshot y lo deja como activo. Si ya había uno
    activo, NO lo pisa (para no perder el punto de restauración original).
    """
    global _snapshot
    if _activo() is not None:
        return _snapshot  # type: ignore[return-value]

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
    _guardar_en_disco(snap)
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
    if _activo() is not None:
        return False
    capturar()
    return True


def hay_snapshot() -> bool:
    """True si hay un snapshot guardado (hay modo del que salir)."""
    return _activo() is not None


def limpiar() -> None:
    """Descarta el snapshot (tras revertir o si el usuario lo pide)."""
    global _snapshot, _cargado
    _snapshot = None
    _cargado = True
    _borrar_de_disco()


# --------------------------------------------------------------------------- #
# Reversión
# --------------------------------------------------------------------------- #
def salir_modo() -> str:
    """Revierte el estado al del último snapshot. Devuelve un mensaje natural.

    Si no hay snapshot, lo avisa y no rompe nada.
    """
    if _activo() is None:
        return falla("modo.sin_modo")

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

    limpiar()  # consumimos el snapshot (también el guardado en disco)

    if not aplicados and not fallos:
        return exito("modo.nada_que_restaurar")
    if fallos:
        return falla("modo.restauracion_parcial", restaurado=", ".join(aplicados or ["nada"]),
                     fallos=", ".join(fallos))
    return exito("modo.restaurado", restaurado=", ".join(aplicados))


def volver_a_resolucion_nativa() -> str:
    """Red de seguridad: lleva la pantalla a su resolución máxima, haya o no un snapshot.

    Sirve cuando un modo dejó la pantalla en una resolución rara y no queda un snapshot del cual
    volver (se perdió, venció o se cambió a mano).
    """
    nativa = pantalla.resolucion_nativa()
    if nativa is None:
        return falla("modo.sin_resolucion_nativa")
    actual = pantalla.resolucion_actual()
    if actual == nativa:
        return exito("modo.ya_nativa", ancho=nativa[0], alto=nativa[1])
    estado, _ = pantalla.cambiar_resolucion(*nativa)
    if estado in (pantalla.OK, pantalla.REINICIO):
        return exito("modo.nativa_aplicada", ancho=nativa[0], alto=nativa[1])
    return falla("modo.nativa_error", ancho=nativa[0], alto=nativa[1])


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