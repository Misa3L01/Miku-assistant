"""
plugins - Paquete de funcionalidades extensibles del asistente Miku.

Cada plugin es una clase que implementa la interfaz base `Plugin` y se
registra automáticamente en main.py. Los plugins complejos (Discord, Game
Booster, Traductor, Macros) se añadirán en pasos posteriores.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

# Logger genérico para plugins.
logger = logging.getLogger("miku.plugins")


class Plugin:
    """Interfaz base que todos los plugins deben implementar.

    Un plugin nuevo solo necesita heredar y sobreescribir `initialize` y
    `execute`. Los atributos `nombre` y `comandos` definen qué comandos
    atiende.
    """

    #: Nombre identificador del plugin (lon único dentro del sistema).
    nombre: str = "base"

    #: Descripción corta para logs / ayuda.
    descripcion: str = ""

    # Comandos (frases/patrones) que este plugin acepta en `execute`.
    # Puede quedar vacío si el plugin se engancha a eventos del bus.
    comandos: list[str] = []

    #: Schemas de "tools" (formato OpenAI function calling) que publica este
    #: plugin para que el cerebro (LLM) las invoque.
    tools: list[dict] = []

    def __init__(self) -> None:
        self._inicializado: bool = False
        self._event_bus: "Any | None" = None

    def initialize(self, event_bus: "Any | None" = None) -> None:
        """Prepara el plugin (recursos, listeners, etc.).

        Se llama una sola vez al arrancar. Aquí se pueden suscribir a
        eventos del bus o cargar recursos pesados.
        """
        self._event_bus = event_bus
        self._inicializado = True
        logger.debug("Plugin '%s' inicializado.", self.nombre)

    def execute(self, comando: str, contexto: Dict[str, Any]) -> Any:
        """Ejecuta una orden que fue asignada a este plugin.

        Args:
            comando: El texto de la instrucción a procesar.
            contexto: Datos compartidos (rvc, kokoro, prefs, etc.).

        Returns:
            Una respuesta (str) o None si no puede atenderlo.
        """
        return None

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Ejecuta una tool declarada en `self.tools`.

        Se llama desde el despachador del cerebro cuando el LLM decide
        invocar una de las tools de este plugin.

        Returns:
            Resultado/texto de la tool, o None si no la maneja.
        """
        return None

    @property
    def inicializado(self) -> bool:
        """True si el plugin fue inicializado satisfactoriamente."""
        return self._inicializado


def registrar_plugins(plugins: list[Plugin], event_bus: "Any") -> list[Plugin]:
    """Registra e inicializa una lista de plugins sobre un bus de eventos.

    Cada plugin que pueda inicializarse correctamente se agrega al bus.
    Devuelve la lista de plugins que quedaron activos.
    """
    activos: list[Plugin] = []
    for p in plugins:
        try:
            p.initialize(event_bus)
            activos.append(p)
            logger.info("Plugin activo: %s", p.nombre)
        except Exception:  # noqa: BLE001
            # Un solo plugin fallido no debe tumbar al asistente.
            logger.exception("No se pudo inicializar el plugin '%s'.", p.nombre)
    return activos

