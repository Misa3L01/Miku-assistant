"""
banco.py - Banco de frases con variantes que rotan (respuestas naturales, no repetitivas).

Cada intención ("clima.lluvia", "app.no_encontrada"...) tiene varias formulaciones. ``elegir`` devuelve
una, **distinta de la última que se usó** para esa intención, y rellena los datos ({temp}, {nombre}...).

    from miku.voz.frases.banco import frases
    frases.elegir("clima.lluvia", cuando="a eso de las 17")

Los datos que falten no rompen nada: se reemplazan por texto vacío (y se deja un aviso de depuración).
"""
from __future__ import annotations

import logging
import random
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("miku.frases")


class _Datos(dict):
    """Para ``str.format_map``: un dato que falta se reemplaza por vacío."""

    def __missing__(self, clave: str) -> str:
        logger.debug("Falta el dato '%s' en una frase.", clave)
        return ""


class Banco:
    """Catálogo de frases por intención, con rotación anti-repetición.

    Args:
        azar: Generador de números aleatorios (los tests pasan uno con semilla).
    """

    def __init__(self, azar: Optional[random.Random] = None) -> None:
        self._variantes: Dict[str, List[str]] = {}
        self._ultima: Dict[str, int] = {}
        self._azar = azar or random.Random()
        self._lock = threading.Lock()

    def registrar(self, clave: str, variantes: List[str]) -> None:
        """Agrega (o reemplaza) las variantes de una intención."""
        variantes = [v for v in variantes if v and v.strip()]
        if not variantes:
            raise ValueError(f"La intención '{clave}' necesita al menos una frase.")
        with self._lock:
            self._variantes[clave] = variantes
            self._ultima.pop(clave, None)

    def registrar_varias(self, catalogo: Dict[str, List[str]]) -> None:
        """Registra un diccionario ``{intención: [variantes]}``."""
        for clave, variantes in catalogo.items():
            self.registrar(clave, variantes)

    def existe(self, clave: str) -> bool:
        """True si hay frases para esa intención."""
        return clave in self._variantes

    def claves(self) -> List[str]:
        """Todas las intenciones registradas."""
        return sorted(self._variantes)

    def elegir(self, clave: str, **datos: Any) -> str:
        """Devuelve una variante de ``clave`` (distinta de la anterior) con los datos aplicados.

        Raises:
            KeyError: si la intención no existe (es un error de programación, no de uso).
        """
        with self._lock:
            variantes = self._variantes[clave]
            indices = list(range(len(variantes)))
            ultima = self._ultima.get(clave)
            if len(indices) > 1 and ultima in indices:
                indices.remove(ultima)
            elegido = self._azar.choice(indices)
            self._ultima[clave] = elegido
            plantilla = variantes[elegido]
        try:
            return plantilla.format_map(_Datos(datos)).strip()
        except (ValueError, IndexError):
            logger.warning("Frase mal formada en '%s': %r", clave, plantilla)
            return plantilla


#: Banco compartido por toda la aplicación.
frases = Banco()
