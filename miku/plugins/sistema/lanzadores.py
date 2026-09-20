"""
lanzadores.py - Juegos que hay que abrir en dos pasos: primero el lanzador, después el juego.

Caso típico: Genshin Impact / Zenless Zone Zero. "Abrí Genshin" abría solo HoYoPlay (el lanzador), que
además corre como administrador y pide confirmación (UAC). Con ``JUEGOS_LANZADOR`` Miku hace lo que
harías vos a mano:

    1. Abre el lanzador (si no está abierto).
    2. Espera a que aparezca su proceso (le das el "Sí" del UAC con calma: espera hasta 2 minutos).
    3. Abre el ejecutable del juego (también como administrador: aparece otro "Sí").

Configuración (``config_local.py``)::

    JUEGOS_LANZADOR = {
        "genshin": {"juego": r"D:\\HoYoPlay\\games\\Genshin Impact game\\GenshinImpact.exe",
                    "lanzador": r"D:\\HoYoPlay\\launcher.exe", "proceso": "HYP"},
    }

La clave es una parte del nombre que decís ("genshin"). ``proceso`` es el nombre del proceso del
lanzador (sin .exe); si se omite, el del ejecutable del lanzador. ``espera`` (segundos, 4 por defecto)
es el respiro entre que aparece el lanzador y se abre el juego.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from miku.plataforma.texto import normalizar

logger = logging.getLogger("miku.plugins.lanzadores")

#: Segundos máximos esperando que el lanzador aparezca (incluye el tiempo de aceptar el UAC).
ESPERA_LANZADOR_S = 120.0


@dataclass
class Juego:
    """Un juego con lanzador."""

    alias: str
    juego: str
    lanzador: str = ""
    proceso: str = ""
    espera: float = 4.0

    @property
    def proceso_lanzador(self) -> str:
        """Nombre (minúsculas, sin .exe) del proceso del lanzador."""
        nombre = self.proceso or os.path.basename(self.lanzador)
        nombre = nombre.lower()
        return nombre[:-4] if nombre.endswith(".exe") else nombre

    @property
    def proceso_juego(self) -> str:
        nombre = os.path.basename(self.juego).lower()
        return nombre[:-4] if nombre.endswith(".exe") else nombre


def buscar_juego(nombre: str, config: Any) -> Optional[Juego]:
    """El juego de ``JUEGOS_LANZADOR`` que corresponde a lo que dijo el usuario (None si ninguno).

    Coincide si la clave está contenida en lo dicho o al revés ("genshin" con "genshin impact").
    """
    if not isinstance(config, dict) or not nombre.strip():
        return None
    pedido = normalizar(nombre).strip()
    for alias, datos in config.items():
        clave = normalizar(str(alias)).strip()
        if not clave or not isinstance(datos, dict) or not datos.get("juego"):
            continue
        if clave in pedido or pedido in clave:
            try:
                espera = float(datos.get("espera", 4))
            except (TypeError, ValueError):
                espera = 4.0
            return Juego(str(alias), str(datos["juego"]), str(datos.get("lanzador", "") or ""),
                         str(datos.get("proceso", "") or ""), espera)
    return None


def proceso_corriendo(nombre: str) -> bool:
    """True si hay un proceso llamado ``nombre`` (sin .exe, sin distinguir mayúsculas)."""
    try:
        import psutil
        objetivo = nombre.lower()
        for p in psutil.process_iter(["name"]):
            actual = (p.info.get("name") or "").lower()
            if actual == objetivo or actual == objetivo + ".exe":
                return True
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude listar procesos: %s", e)
    return False


def abrir_como_administrador(ruta: str) -> bool:
    """Abre ``ruta`` pidiendo permisos de administrador (aparece el "Sí/No" de Windows)."""
    try:
        resultado = ctypes.windll.shell32.ShellExecuteW(None, "runas", ruta, None, os.path.dirname(ruta) or None, 1)
        return int(resultado) > 32
    except Exception as e:  # noqa: BLE001
        logger.error("No pude abrir %s como administrador: %s", ruta, e)
        return False


def iniciar(juego: Juego, avisar: Callable[[str, bool], None],
            corriendo: Callable[[str], bool] = proceso_corriendo,
            abrir_lanzador: Optional[Callable[[str], Any]] = None,
            abrir_juego: Callable[[str], bool] = abrir_como_administrador,
            dormir: Callable[[float], None] = time.sleep,
            reloj: Callable[[], float] = time.monotonic,
            espera_lanzador: float = ESPERA_LANZADOR_S) -> bool:
    """Ejecuta los pasos (bloquea: correr en un hilo). ``avisar(mensaje, ok)`` cuenta cómo salió.

    Returns:
        True si se pidió abrir el juego.
    """
    if abrir_lanzador is None:
        abrir_lanzador = os.startfile  # type: ignore[attr-defined]
    if corriendo(juego.proceso_juego):
        avisar("ya_abierto", True)
        return True
    if juego.lanzador and not corriendo(juego.proceso_lanzador):
        try:
            abrir_lanzador(juego.lanzador)
        except OSError as e:
            logger.error("No pude abrir el lanzador %s: %s", juego.lanzador, e)
            avisar("lanzador_no_abre", False)
            return False
        limite = reloj() + espera_lanzador
        while not corriendo(juego.proceso_lanzador):
            if reloj() > limite:
                avisar("lanzador_no_aparece", False)
                return False
            dormir(1.0)
        dormir(juego.espera)          # que termine de cargar antes de pedirle nada
    if not abrir_juego(juego.juego):
        avisar("juego_no_abre", False)
        return False
    avisar("abriendo_juego", True)
    return True


def iniciar_en_hilo(juego: Juego, avisar: Callable[[str, bool], None]) -> threading.Thread:
    """Lanza ``iniciar`` en un hilo aparte (no bloquea la conversación)."""
    hilo = threading.Thread(target=iniciar, args=(juego, avisar), daemon=True, name="miku_lanzador")
    hilo.start()
    return hilo
