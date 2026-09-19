# -*- coding: utf-8 -*-
"""
notificaciones.py - Notificaciones (toasts) de Windows, con degradación segura.

Se usa para AVISAR visualmente al terminar acciones largas o importantes
(interpolación, recordatorio, errores, activación del Game Booster), sin
depender de que la consola esté a la vista.

Estrategia (sin dependencias obligatorias, todo LAZY y best-effort):
    1. ``win10toast`` si está instalado (toast nativo simple).
    2. PowerShell + Windows Runtime (toast nativo en Win10/11). Es la vía más
       estable disponible "de fábrica" en Windows.
    3. Si todo falla, sólo registra en el log (nunca rompe al asistente).

``notificar(titulo, mensaje)`` es no bloqueante: dispara el toast en un hilo
aparte para no demorar la respuesta del asistente.
"""
from __future__ import annotations

import logging
import subprocess
import threading

logger = logging.getLogger("miku.notificaciones")

# Evita acumular hilos si se piden muchas notificaciones seguidas.
_lock = threading.Lock()


def notificar(titulo: str, mensaje: str,
              duracion_seg: int = 6) -> None:
    """Muestra una notificación (toast) de Windows en segundo plano.

    Args:
        titulo: Encabezado del toast (corto).
        mensaje: Cuerpo del toast.
        duracion_seg: Duración aproximada (segundos) si el backend lo soporta.
    """
    titulo = (titulo or "Miku").strip() or "Miku"
    mensaje = (mensaje or "").strip()
    if not mensaje:
        return

    hilo = threading.Thread(
        target=_notificar_sync, args=(titulo, mensaje, duracion_seg),
        daemon=True, name="miku_toast")
    hilo.start()


def _notificar_sync(titulo: str, mensaje: str, duracion_seg: int) -> None:
    """Intenta los backends en orden; el primero que funcione gana."""
    with _lock:
        if _toast_win10toast(titulo, mensaje, duracion_seg):
            return
        if _toast_powershell(titulo, mensaje, duracion_seg):
            return
    logger.info("[Toast no mostrado] %s: %s", titulo, mensaje)


# --------------------------------------------------------------------------- #
# Backend 1: win10toast (opcional)
# --------------------------------------------------------------------------- #
def _toast_win10toast(titulo: str, mensaje: str, duracion_seg: int) -> bool:
    try:
        from win10toast import ToastNotifier  # import tardío (opcional)
    except Exception:  # noqa: BLE001
        return False
    try:
        ToastNotifier().show_toast(
            titulo, mensaje, duration=duracion_seg, threaded=False)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("win10toast falló: %s", e)
        return False


# --------------------------------------------------------------------------- #
# Backend 2: PowerShell + Windows Runtime (nativo, sin deps)
# --------------------------------------------------------------------------- #
def _toast_powershell(titulo: str, mensaje: str, duracion_seg: int) -> bool:
    """Muestra un toast con PowerShell + WinRT (Win10/11).

    OJO: el texto se inyecta en un XML; hay que ESCAPAR los caracteres
    especiales para no romper el script ni permitir inyección.
    """
    titulo_esc = _escapar_xml(titulo)
    mensaje_esc = _escapar_xml(mensaje)
    # El script arma un toast básico con título y cuerpo. Se ejecuta oculto.
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null; "
        f"$t='{titulo_esc}'; $m='{mensaje_esc}'; "
        "$xml='<toast><visual><binding template=\"ToastGeneric\"><text>'+$t+'</text><text>'+$m+'</text></binding></visual></toast>'; "
        "$doc=New-Object Windows.Data.Xml.Dom.XmlDocument; $doc.LoadXml($xml); "
        "$toast=New-Object Windows.UI.Notifications.ToastNotification $doc; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Miku Assistant').Show($toast);"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle",
             "Hidden", "-Command", ps],
            shell=False, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_sin_ventana())
        # Un código != 0 (WinRT no disponible, XML inválido...) NO es un toast
        # mostrado: devolvemos False para que se registre en el log.
        return proc.returncode == 0
    except Exception as e:  # noqa: BLE001
        logger.debug("Toast por PowerShell falló: %s", e)
        return False


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _escapar_xml(texto: str) -> str:
    """Escapa caracteres especiales de XML para inyectar en el toast.

    Se construyen las entidades con ``chr(38)`` (el '&') para no escribir el
    literal '&' seguido de texto y evitar que se interprete por accidente.
    """
    amp = chr(38)  # '&'
    reemplazos = {
        amp: amp + "amp;",
        "<": amp + "lt;",
        ">": amp + "gt;",
        '"': amp + "quot;",
        "'": amp + "#39;",
    }
    salida = []
    for ch in str(texto or ""):
        salida.append(reemplazos.get(ch, ch))
    return "".join(salida)


def _sin_ventana() -> int:
    """Flags de creación para no abrir ventana de consola (Windows)."""
    try:
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return 0x08000000  # CREATE_NO_WINDOW