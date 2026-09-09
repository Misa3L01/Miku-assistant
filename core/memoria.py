"""
memoria.py - Memoria persistente (DESACTIVADA por ahora).

Originalmente esta módulo usaba ``chromadb`` para guardar recuerdos del
usuario y consultarlos por similitud vectorial. En la refactorización actual
se decidió **desactivarlo** para quitar la dependencia pesada de chromadb y
acelerar el arranque en modo texto.

Este archivo queda como "stub deshabilitado" (no importa chromadb). Se puede
rehabilitar más adelante reconectando el parser/main con esta clase.

Cualquier código que lo use recibirá una clase ``Memoria`` "inoperante" que
todas sus llamadas responden con valores neutros (sin importar nada pesado).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("miku.memoria")


class Memoria:
    """Capa de memoria DESACTIVADA temporalmente.

    Mantiene la misma interfaz que antes (guardar/buscar/olvidar/cantidad)
    pero sin backend: los métodos devuelven un valor neutro o hacen no-op.
    """

    _DESACTIVADA = True  # flag para inspección externa

    def __init__(self, *args, **kwargs) -> None:
        logger.info("Memoria desactivada (no se inicializa ningún backend).")

    # ---------------- API conservada ----------------
    def guardar_recuerdo(self, texto_recuerdo: str,
                         categoria: str = "general") -> Optional[str]:
        """No op: devuelve None porque está desactivada."""
        logger.debug("Memoria inactiva: se ignora 'guardar'.")
        return None

    def buscar_recuerdos(self, consulta: str, cantidad: int = 3) -> str:
        """No op: devuelve vacío porque está desactivada."""
        logger.debug("Memoria inactiva: se ignora 'buscar'.")
        return ""

    def olvidar_recuerdo(self, texto_aproximado: str) -> str:
        """No op: mensaje informativo."""
        return "La memoria no está activa en esta versión."

    def cantidad(self) -> int:
        """Devuelve 0 porque no hay backend."""
        return 0
