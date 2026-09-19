"""Normalización de texto para comparar lo que dice el usuario, sin acentos ni mayúsculas.

Whisper transcribe "química" o "Quimica" según el día: todas las comparaciones de nombres
(apps, ventanas, carpetas, usuarios de Discord, tareas...) pasan por acá para ser tolerantes.
Antes había siete copias casi idénticas repartidas por el proyecto.
"""
from __future__ import annotations

import unicodedata
from typing import Optional


def sin_acentos(texto: Optional[str]) -> str:
    """Minúsculas y sin acentos ni diéresis ("Qué" -> "que", "ñ" -> "n"). No recorta espacios."""
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(texto: Optional[str]) -> str:
    """Como ``sin_acentos`` pero además recorta los espacios de los extremos."""
    return sin_acentos(texto).strip()


def clave_compacta(texto: Optional[str]) -> str:
    """Normaliza y QUITA todo lo que no sea letra o número.

    Sirve para que ``"lista animes"`` coincida con ``"ListaAnimes"`` o ``"lista_animes"``.
    """
    return "".join(c for c in sin_acentos(texto) if c.isalnum())
