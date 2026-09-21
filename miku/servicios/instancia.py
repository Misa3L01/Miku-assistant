"""
instancia.py - Una sola Miku a la vez, y forma de "invocarla" desde otra ejecución.

Cómo funciona (solo ``ctypes``, sin dependencias):
    - La primera ejecución toma un *mutex* con nombre de Windows.
    - Si otra ejecución arranca (p. ej. por el atajo de teclado o por ``run.bat``), no puede tomar el mutex:
      en vez de abrir una segunda Miku, dispara un *evento* con nombre y termina.
    - La primera ejecución tiene un hilo esperando ese evento y responde con su callback
      (saludar y escuchar un comando).

Los nombres se pueden cambiar con la variable de entorno ``MIKU_INSTANCIA`` (los tests la usan para no
chocar con una Miku real que esté corriendo).

Este módulo se importa ANTES que el resto de la aplicación: debe seguir siendo liviano.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger("miku.instancia")

_NOMBRE_BASE = "MikuAssistant"
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0


def _nombre(base: Optional[str] = None) -> str:
    return base or os.environ.get("MIKU_INSTANCIA") or _NOMBRE_BASE


class InstanciaUnica:
    """Garantiza una sola Miku y permite invocar a la que ya está corriendo.

    Args:
        nombre: Nombre base de los objetos de Windows (por defecto ``MikuAssistant``).
    """

    def __init__(self, nombre: Optional[str] = None) -> None:
        base = _nombre(nombre)
        self._nombre_mutex = f"Local\\{base}.Mutex"
        self._nombre_evento = f"Local\\{base}.Invocar"
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateMutexW.restype = ctypes.c_void_p
        self._k32.CreateEventW.restype = ctypes.c_void_p
        self._k32.OpenEventW.restype = ctypes.c_void_p
        self._k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._k32.SetEvent.argtypes = [ctypes.c_void_p]
        self._k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._mutex: Optional[int] = None
        self._evento: Optional[int] = None
        self._hilo: Optional[threading.Thread] = None
        self._parar = threading.Event()

    # ------------------------------------------------------------------ primera ejecución
    def adquirir(self) -> bool:
        """Intenta ser LA instancia. True si lo es; False si ya hay otra Miku corriendo."""
        ctypes.set_last_error(0)
        manejador = self._k32.CreateMutexW(None, False, self._nombre_mutex)
        ya_existia = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
        if not manejador:
            logger.warning("No pude crear el mutex de instancia única; sigo sin control.")
            return True
        if ya_existia:
            self._k32.CloseHandle(manejador)
            return False
        self._mutex = manejador
        # Evento de invocación (auto-reset): lo dispara la segunda ejecución.
        self._evento = self._k32.CreateEventW(None, False, False, self._nombre_evento)
        return True

    def escuchar(self, al_invocar: Callable[[], None]) -> None:
        """Arranca un hilo que llama a ``al_invocar`` cada vez que otra ejecución nos invoca."""
        if not self._evento or self._hilo is not None:
            return

        def bucle() -> None:
            while not self._parar.is_set():
                if self._k32.WaitForSingleObject(self._evento, 400) == _WAIT_OBJECT_0:
                    logger.info("Me invocaron desde otra ejecución.")
                    try:
                        al_invocar()
                    except Exception:  # noqa: BLE001
                        logger.exception("Falló el callback de invocación.")

        self._hilo = threading.Thread(target=bucle, name="miku_invocacion", daemon=True)
        self._hilo.start()

    def liberar(self) -> None:
        """Libera el mutex y el evento (al cerrar)."""
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=1.5)
            self._hilo = None
        for nombre in ("_evento", "_mutex"):
            manejador = getattr(self, nombre)
            if manejador:
                self._k32.CloseHandle(manejador)
                setattr(self, nombre, None)

    # ------------------------------------------------------------------ segunda ejecución
    def invocar_existente(self) -> bool:
        """Avisa a la Miku que ya corre. True si pudo (había alguien escuchando)."""
        evento = self._k32.OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, self._nombre_evento)
        if not evento:
            return False
        try:
            return bool(self._k32.SetEvent(evento))
        finally:
            self._k32.CloseHandle(evento)
