"""
utiles.py - Piezas chicas que comparten varios plugins.

    a_entero            Convierte argumentos que vienen del LLM sin lanzar excepciones.
    RutasOfrecidas      Recuerda las opciones ofrecidas en una desambiguación.
    aperturas           Recuerda qué apps abrió Miku hace poco (para esperar su ventana en órdenes en cadena).
    abrir_resultado     Abre un archivo (o su carpeta) y arma el mensaje de confirmación.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger("miku.plugins.utiles")


def a_entero(valor: Any, defecto: int) -> int:
    """Convierte ``valor`` a int; si no se puede (None, "diez"...), ``defecto``.

    Los argumentos de las tools vienen del LLM y pueden ser cualquier cosa: un
    ``int()`` sin proteger tiraba una excepción que el despachador tragaba y el
    usuario oía "¡Listo!" sin que se hubiera hecho nada.
    """
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return defecto


class _AperturasRecientes:
    """Apps que Miku acaba de abrir.

    En una orden en cadena ("abrí Discord y llevalo al monitor 2") la ventana todavía no existe cuando
    llega el segundo paso. Los plugins de ventanas consultan esto para **esperar** la ventana de algo
    recién abierto en vez de contestar "no la veo".
    """

    def __init__(self) -> None:
        self._marcas: Dict[str, float] = {}
        self._lock = threading.Lock()

    def marcar(self, nombre: str) -> None:
        """Anota que se abrió ``nombre`` ahora."""
        from miku.plataforma.texto import normalizar
        with self._lock:
            self._marcas[normalizar(nombre).strip()] = time.monotonic()

    def reciente(self, nombre: str, segundos: float = 45.0) -> bool:
        """True si se abrió ``nombre`` (o algo parecido) hace menos de ``segundos``."""
        from miku.plataforma.texto import normalizar
        buscado = normalizar(nombre).strip()
        if not buscado:
            return False
        ahora = time.monotonic()
        with self._lock:
            return any(ahora - t <= segundos and (buscado in n or n in buscado)
                       for n, t in self._marcas.items() if n)


#: Registro compartido por los plugins.
aperturas = _AperturasRecientes()


class RutasOfrecidas:
    """Rutas ofrecidas al usuario en la ÚLTIMA desambiguación.

    ``ruta_elegida`` no está en el schema de las tools (el LLM no debería inventarla): un plugin
    solo acepta una ruta que él mismo ofreció.
    """

    def __init__(self) -> None:
        self._rutas: set = set()

    def registrar(self, rutas: List[str]) -> None:
        """Reemplaza las rutas ofrecidas por ``rutas``."""
        self._rutas = {os.path.normcase(str(r)) for r in rutas}

    def contiene(self, ruta: str) -> bool:
        """True si ``ruta`` fue una de las ofrecidas (sin distinguir mayúsculas, como Windows)."""
        return os.path.normcase(ruta) in self._rutas



def abrir_resultado(ruta: str, abrir_carpeta: bool) -> str:
    """Abre `ruta` (o su carpeta con el archivo seleccionado).

    Devuelve una confirmación por NOMBRE (nunca la ruta completa).
    """
    nombre_arch = os.path.basename(ruta)
    try:
        if abrir_carpeta:
            # OJO: son DOS elementos; "/select," va PEGADO a la ruta en el
            # mismo string (sin espacio), o Windows lo interpreta mal.
            subprocess.Popen(["explorer", f"/select,{ruta}"])
            return f"Te la abrí en la carpeta: {nombre_arch}"
        os.startfile(ruta)  # type: ignore[attr-defined]
        return f"¡Abrí {nombre_arch}!"
    except Exception as e:  # noqa: BLE001
        logger.error("No pude abrir '%s': %s", ruta, e)
        return f"No pude abrir {nombre_arch}."
