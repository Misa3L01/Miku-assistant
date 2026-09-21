"""
plugins - Paquete de funcionalidades extensibles del asistente Miku.

Cada plugin es una clase que hereda de ``Plugin`` y se declara en
``miku/plugins/registro.py``, que la importa de forma perezosa (un plugin roto o sin
sus dependencias no impide que arranque el resto).

Contrato de un plugin (todo salvo ``nombre`` es opcional):

    nombre         Identificador único.
    descripcion    Texto corto para logs/ayuda.
    tools          Schemas OpenAI function-calling que ve el LLM.
    peligrosas     Nombres de tools que exigen CONFIRMACIÓN del usuario antes
                   de ejecutarse (apagar la PC, expulsar a alguien...).
    initialize()   Se llama una vez al arrancar (recursos, hilos). Recibe el ``event_bus``: el
                   registro de plugins y el contexto compartido (``voice``, ``parser``...).
    manejar_tool() Ejecuta una tool propia; devuelve None si no es suya.
    cerrar()       Libera recursos al cerrar el asistente.

Los comandos llegan por el LLM (tools) o por el fast-path del parser.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

# Logger genérico para plugins.
logger = logging.getLogger("miku.plugins")


class Plugin:
    """Interfaz base que todos los plugins deben implementar.

    Un plugin nuevo hereda de esta clase, define ``nombre`` y (si expone
    tools) ``tools`` + ``manejar_tool``. Ver el docstring del módulo.
    """

    #: Nombre identificador del plugin (único dentro del sistema).
    nombre: str = "base"

    #: Descripción corta para logs / ayuda.
    descripcion: str = ""

    #: Schemas de "tools" (formato OpenAI function calling) que publica este
    #: plugin para que el cerebro (LLM) las invoque.
    tools: list[dict] = []

    #: Nombres de las tools de este plugin que son PELIGROSAS: el parser pide
    #: un "sí" explícito antes de ejecutarlas.
    peligrosas: frozenset = frozenset()

    def __init__(self) -> None:
        self._event_bus: "Any | None" = None

    def initialize(self, event_bus: "Any | None" = None) -> None:
        """Prepara el plugin (recursos, hilos, etc.).

        Se llama una sola vez al arrancar. ``event_bus`` da acceso a los demás plugins y a lo
        compartido (``voice``, ``parser``); acá también se cargan los recursos pesados.
        """
        self._event_bus = event_bus
        logger.debug("Plugin '%s' inicializado.", self.nombre)

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Ejecuta una tool declarada en `self.tools`.

        Se llama desde el despachador del cerebro cuando el LLM decide
        invocar una de las tools de este plugin.

        Returns:
            Resultado/texto de la tool, o None si no la maneja.
        """
        return None

    def cerrar(self) -> None:
        """Libera los recursos propios del plugin (hilos, sockets, bots...).

        Se llama UNA sola vez, cuando el asistente se cierra: ``miku/app.py``
        recorre los plugins registrados en el bus y llama a ``cerrar()`` a
        todos los que lo tengan (chequeo con ``getattr`` + ``callable``, así
        que los plugins que no lo definan ni se enteran).

        El default no hace nada; los plugins con recursos propios (hilos
        daemon, clientes async, etc.) deben sobreescribirlo. Si un plugin
        falla al cerrar, el asistente sigue cerrando los demás.
        """
        return None


def registrar_plugins(plugins: list[Plugin], event_bus: "Any") -> list[Plugin]:
    """Inicializa cada plugin con el bus y devuelve los que arrancaron bien.

    Un plugin que falla al inicializarse se omite (queda en el log) sin tumbar a los demás.
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

