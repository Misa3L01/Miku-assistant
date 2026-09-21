"""
eventos.py - Registro central de plugins y contexto compartido del asistente.

``EventBus`` es el objeto que ``miku/app.py`` le pasa a cada plugin en ``initialize(event_bus)``. Aunque
conserva el nombre, no publica ni suscribe eventos: nadie lo usaba. Es el punto donde los plugins
encuentran lo que comparten sin importarse entre sí:

    plugins          los plugins activos (un plugin busca a otro por ``nombre``).
    voice            el motor de voz, para los avisos propios (proactivo, game booster, TIDAL...).
    parser           el cerebro, para que Telegram ejecute comandos remotos con la misma lógica.
    contexto_base    fábrica del ``contexto`` que reciben las tools (``cfg``, ``voice``, ``scheduler``).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("miku.eventos")


class EventBus:
    """Registro de plugins activos + contexto compartido (ver el docstring del módulo)."""

    def __init__(self) -> None:
        #: Plugins activos, en orden de registro (los registra ``miku/app.py``).
        self.plugins: List[Any] = []
        #: Motor de voz (``TextoAVoz``); ``None`` hasta que Miku habla por primera vez.
        self.voice: Optional[Any] = None
        #: Cerebro (``CommandParser``); lo inyecta ``miku/app.py``.
        self.parser: Optional[Any] = None
        #: Fábrica del contexto de las tools (``() -> dict``); la inyecta ``miku/app.py``.
        self.contexto_base: Optional[Callable[[], Dict[str, Any]]] = None

    def registrar_plugin(self, plugin: Any) -> None:
        """Registra un plugin ya inicializado."""
        if plugin not in self.plugins:
            self.plugins.append(plugin)
            logger.info("Plugin registrado: %s", plugin.nombre)

    def listar_plugins(self) -> List[str]:
        """Nombres de los plugins activos (para la ayuda)."""
        return [p.nombre for p in self.plugins]
