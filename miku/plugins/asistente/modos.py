# -*- coding: utf-8 -*-
"""
modos.py (plugin) - Salir de modos de contexto (revertir).

Publica la tool ``salir_modo``, que restaura el estado capturado por
``miku/servicios/modos.py`` (volumen general, resolución y brillo) al momento de activar
un "modo" (típicamente una macro que cambia resolución y/o volumen, p. ej.
Fortnite). Si no hay snapshot, lo avisa y no rompe nada.

La CAPTURA del snapshot la dispara quien activa el modo (ver
``miku/plugins/productividad/macros.py``), no este plugin.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from miku.plugins.base import Plugin
from miku.servicios import modos

logger = logging.getLogger("miku.plugins.modos")


class Modos(Plugin):
    """Permite revertir el estado del sistema tras entrar en un modo."""

    nombre = "modos"
    descripcion = "Sale de un modo de contexto y revierte resolución/volumen/brillo."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "salir_modo",
                "description": "Revierte el estado del sistema (resolución, "
                               "volumen, brillo) al que había ANTES de activar "
                               "un modo/macro. Ej: 'salí del modo', 'volvé "
                               "todo como estaba'. Si no hay modo activo, lo "
                               "avisa sin romper nada.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "volver_resolucion_nativa",
                "description": "Lleva la pantalla a su resolución nativa (la máxima del monitor). "
                               "Red de seguridad si la resolución quedó rara después de un juego "
                               "o macro. Ej: 'volvé a la resolución normal', 'arreglá la resolución'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin modos listo.")

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "salir_modo":
            return modos.salir_modo()
        if nombre_tool == "volver_resolucion_nativa":
            return modos.volver_a_resolucion_nativa()
        return None