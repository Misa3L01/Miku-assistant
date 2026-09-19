"""
utiles.py - Piezas chicas que comparten varios plugins.

    a_entero            Convierte argumentos que vienen del LLM sin lanzar excepciones.
    RutasOfrecidas      Recuerda las opciones ofrecidas en una desambiguación.
    abrir_resultado     Abre un archivo (o su carpeta) y arma el mensaje de confirmación.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, List

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
