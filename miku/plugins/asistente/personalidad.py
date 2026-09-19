# -*- coding: utf-8 -*-
"""
personalidad.py (plugin) - Cambia la personalidad de Miku por voz.

Publica la tool ``cambiar_personalidad`` para que el usuario diga cosas como
"hablá más tsundere", "modo formal" o "volvé a la normal". El estilo se aplica
agregando una instrucción al system prompt del LLM (``miku/servicios/personalidad.py``),
así no hay que duplicar lógica de respuestas.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from miku.plugins.base import Plugin
import miku.servicios.personalidad as pers

logger = logging.getLogger("miku.plugins.personalidad")


class PersonalidadPlugin(Plugin):
    """Permite cambiar el perfil de personalidad de Miku."""

    nombre = "personalidad"
    descripcion = "Cambia el estilo de Miku (neutral, tsundere, formal, entusiasta)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "cambiar_personalidad",
                "description": "Cambia el ESTILO/personalidad de Miku. Perfiles "
                               "válidos: neutral, tsundere, formal, entusiasta. "
                               "Ej: 'hablá más tsundere', 'modo formal', "
                               "'volvé a la normal'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "perfil": {
                            "type": "string",
                            "description": "Perfil de personalidad a activar "
                                           "(neutral, tsundere, formal o "
                                           "entusiasta).",
                        },
                        "persistir": {
                            "type": "boolean",
                            "description": "Si True, guarda el cambio para "
                                           "próximas sesiones. Default False.",
                        },
                    },
                    "required": ["perfil"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "listar_personalidades",
                "description": "Lista las personalidades disponibles y cuál "
                               "está activa. Sin parámetros.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin personalidad listo (activa: %s).", pers.cargar_perfil())

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "cambiar_personalidad":
            return pers.cambiar_perfil(
                str(args.get("perfil", "")),
                bool(args.get("persistir", False)))
        if nombre_tool == "listar_personalidades":
            return self.listar_personalidades()
        return None

    def listar_personalidades(self) -> str:
        """Lista los perfiles y marca el activo."""
        activa = pers.cargar_perfil()
        disp = ", ".join(pers.perfiles_disponibles())
        return f"Puedo ser: {disp}. Ahora estoy en modo {activa}."