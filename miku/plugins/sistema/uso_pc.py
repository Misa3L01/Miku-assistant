"""
uso_pc.py - Plugin del registro de uso: "¿en qué gasté el tiempo hoy?" y el aviso de juego seguido.

Arma el vigía (``miku/servicios/uso.py``), lo arranca en segundo plano y publica la tool ``tiempo_de_uso``.
La regla que avisa "llevás 2 horas jugando" vive en el motor proactivo (``HorasDeJuego``) y le pide a este
plugin la sesión de juego en curso (``sesion_juego``).

Privacidad: solo anota el nombre del programa y los minutos (nunca títulos de ventana ni páginas).
Se apaga con ``USO_REGISTRO = False``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin
from miku.servicios import uso

logger = logging.getLogger("miku.plugins.uso_pc")

RUTA_REGISTRO = config_mod.BASE_DIR / "data" / "uso.json"


class UsoPC(Plugin):
    """Anota cuánto tiempo usás cada programa y cuánto llevás jugando seguido."""

    nombre = "uso_pc"
    descripcion = "Tiempo de uso de la PC por programa y horas de juego seguido."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "tiempo_de_uso",
                "description": "Dice cuánto tiempo usaste la PC y en qué programas o juegos lo pasaste "
                               "(registro de uso, horas jugadas, en qué gasté el tiempo). Ej: '¿en qué gasté "
                               "el tiempo hoy?', '¿cuánto jugué ayer?', 'resumen de uso de la semana'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "periodo": {
                            "type": "string",
                            "enum": ["hoy", "ayer", "semana"],
                            "description": "Qué período resumir (por defecto hoy).",
                        },
                    },
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._registro: Optional[uso.RegistroUso] = None
        self._sesion: Optional[uso.SesionJuego] = None
        self._vigia: Optional[uso.Vigia] = None

    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._registro = uso.RegistroUso(RUTA_REGISTRO)
        self._sesion = uso.SesionJuego(self._pausa_seg())
        self._vigia = uso.Vigia(self._registro, self._sesion, juegos=lambda: uso.juegos_de_config(config_mod.config))
        self._vigia.iniciar()
        logger.info("Registro de uso activo (se guarda en %s).", RUTA_REGISTRO.name)

    def cerrar(self) -> None:
        if self._vigia is not None:
            self._vigia.detener()

    @staticmethod
    def _pausa_seg() -> float:
        """Minutos sin jugar que cortan una sesión (``USO_PAUSA_MIN``), en segundos."""
        try:
            return max(1.0, float(config_mod.config.get("uso_pausa_min", 15))) * 60.0
        except (TypeError, ValueError):
            return 900.0

    def sesion_juego(self) -> Optional[Tuple[str, float, int]]:
        """``(juego, segundos jugados, número de sesión)`` si hay una sesión en marcha (la usa el aviso)."""
        if self._sesion is None:
            return None
        self._sesion.pausa_seg = self._pausa_seg()
        return self._sesion.actual()

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        if nombre_tool != "tiempo_de_uso":
            return None
        if self._registro is None:
            return "Todavía no empecé a anotar el uso de la PC."
        return uso.resumen(self._registro, str((args or {}).get("periodo", "hoy") or "hoy"))
