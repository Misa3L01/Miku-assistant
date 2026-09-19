"""
audio.py - Acceso a pycaw (volumen del sistema y de cada app) y a las teclas multimedia.

Lo usan el plugin de audio y los snapshots de "salir del modo" (antes cada uno tenía su copia).
Todo se importa de forma perezosa y degrada a None/[]/False si pycaw o las teclas no están.
"""
from __future__ import annotations

import ctypes
import logging
import time
from typing import Any, List, Optional

from miku.plataforma.texto import clave_compacta, normalizar

logger = logging.getLogger("miku.plataforma.audio")


# ---------------- Multimedia (teclas virtuales Windows) ---------------- #
# Códigos de VK (virtual key) para reproducción multimedia.
# NOTA: el MUTE real NO se maneja por tecla "toggle" (0xAD): se hace con
# pycaw/SetMute en `ajustar_volumen()` para que sea una acción 100% predecible
# (silenciar SIEMPRE silencia, desmutear SIEMPRE devuelve el sonido).
VK = {
    "play_pausa": 0xB3, "siguiente": 0xB0, "anterior": 0xB1,
    "subir_volumen": 0xAF, "bajar_volumen": 0xAE,
}


NOMBRE_KEYBOARD = {
    "play_pausa": "play/pause media",
    "siguiente": "next track", "anterior": "previous track",
    "subir_volumen": "volume up", "bajar_volumen": "volume down",
}


def volumen_master() -> Optional[Any]:
    """``IAudioEndpointVolume`` de la salida por defecto, o None si pycaw no está."""
    try:
        from pycaw.pycaw import AudioUtilities
        dispositivo = AudioUtilities.GetSpeakers()
        return getattr(dispositivo, "EndpointVolume", None)
    except Exception as e:  # noqa: BLE001
        logger.debug("pycaw no disponible (%s).", e)
        return None



# ---------------- Volumen POR APP (sesiones de audio) ---------------- #
def sesiones_de_app(nombre_app: str) -> List[Any]:
    """Devuelve las SESIONES de audio de la app cuyo nombre coincide.

    Compara por nombre de proceso (DisplayName/ProcessName), case-insensitive
    y tolerante a acentos. Devuelve TODAS las sesiones que matcheen (un
    navegador puede tener varias pestañas/pestañas de audio abiertas): así
    "bajá el volumen de Brave" afecta a todas por igual.
    """
    try:
        from pycaw.pycaw import AudioUtilities  # import tardío
    except Exception as e:  # noqa: BLE001
        logger.debug("pycaw no disponible para volumen por app: %s", e)
        return []

    objetivo = normalizar(nombre_app)
    objetivo_compacto = clave_compacta(nombre_app)
    if not objetivo or not objetivo_compacto:
        # Un nombre vacío ("" / "-") matchearía TODAS las sesiones.
        return []
    coincidentes: List[Any] = []
    try:
        sesiones = AudioUtilities.GetAllSessions()
    except Exception as e:  # noqa: BLE001
        logger.error("No pude listar sesiones de audio: %s", e)
        return coincidentes

    for sesion in sesiones:
        try:
            proc = getattr(sesion, "Process", None)
            nombre_proc = ""
            if proc is not None:
                try:
                    nombre_proc = proc.name() or ""
                except Exception:  # noqa: BLE001
                    nombre_proc = ""
            # A veces Process es None pero hay DisplayName.
            display = ""
            try:
                display = sesion.DisplayName or ""
            except Exception:  # noqa: BLE001
                display = ""

            candidatos = [normalizar(nombre_proc),
                          normalizar(display)]
            for cand in candidatos:
                if not cand:
                    continue
                if (objetivo in cand
                        or objetivo_compacto in clave_compacta(cand)):
                    # Ojo: el nombre del proceso suele terminar en ".exe".
                    coincidentes.append(sesion)
                    break
        except Exception:  # noqa: BLE001
            continue
    return coincidentes


def enviar_tecla(clave: str) -> bool:
    """Envía una tecla multimedia/volumen (``play_pausa``, ``siguiente``...) por hardware.

    Intenta primero con ``keyboard`` y, si no, con ``keybd_event`` de ctypes. No se usa para
    silenciar (eso se hace con pycaw, que es determinístico).
    """
    try:
        import keyboard
        nombre = NOMBRE_KEYBOARD.get(clave)
        if nombre:
            keyboard.send(nombre)
            return True
    except Exception:  # noqa: BLE001
        pass  # seguimos con el fallback por ctypes

    vk = VK.get(clave)
    if vk is None:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)   # tecla abajo
        time.sleep(0.02)
        user32.keybd_event(vk, 0, 2, 0)   # KEYEVENTF_KEYUP = 2
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("No se pudo enviar la tecla virtual %s: %s", clave, e)
        return False
