"""
event_bus.py - Sistema de eventos (event bus) del asistente Miku.

Permite que el núcleo y los distintos plugins se comuniquen entre sí sin
conocerse directamente (bajo acoplamiento). Componentes publican eventos y
otros componentes se suscriben a ellos. Además sirve de registro central de
los plugins activos para despachar comandos.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("miku.event_bus")

# Firmas de las funciones suscriptoras.
# Un listener recibe (nombre_evento: str, datos: dict).
_LlaveListener = str
_Listener = Callable[[str, Dict[str, Any]], None]


@dataclass
class Evento:
    """Representa un mensaje que viaja por el bus."""

    nombre: str
    datos: Dict[str, Any] = field(default_factory=dict)
    #: Fuente (nombre del plugin/módulo que lo emitió), útil en logs.
    origen: str = "core"


class EventBus:
    """Bus de eventos seguro para hilos.

    Principio de uso:
        bus.emit("nota.guardada", {"texto": "..."})   -> publica
        bus.on("nota.guardada", self._handler)        -> se suscribe
        bus.off("nota.guardada", self._handler)       -> se desuscribe
    """

    def __init__(self) -> None:
        self._suscriptores: Dict[str, List[tuple[_Listener, int]]] = {}
        self._lock = threading.RLock()

        # Cola asíncrona opcional si se quiere procesar eventos afuera del
        # hilo principal sin bloquear. Por defecto los events se despachan
        # en el mismo hilo que los emite (modo síncrono), lo cual es más
        # simple y predecible.
        self._cola: "queue.Queue[Evento]" = queue.Queue()
        self._hilo_worker: Optional[threading.Thread] = None
        self._detener: bool = False

        # Plugins activos registrados (se registran desde miku/app.py).
        self.plugins: List[Any] = []

    # ------------------ Suscripción ------------------
    def on(self, nombre_evento: str, listener: _Listener, prioridad: int = 0) -> None:
        """Registra un listener para un tipo de evento."""
        with self._lock:
            self._suscriptores.setdefault(nombre_evento, [])
            self._suscriptores[nombre_evento].append((listener, prioridad))
            # Ejecutamos los de mayor prioridad primero.
            self._suscriptores[nombre_evento].sort(key=lambda x: x[1], reverse=True)
        logger.debug("Listener registrado para '%s'.", nombre_evento)

    def off(self, nombre_evento: str, listener: _Listener) -> None:
        """Quita un listener previamente registrado."""
        with self._lock:
            lista = self._suscriptores.get(nombre_evento)
            if not lista:
                return
            self._suscriptores[nombre_evento] = [
                (l, p) for (l, p) in lista if l != listener
            ]
        logger.debug("Listener removido de '%s'.", nombre_evento)

    def once(self, nombre_evento: str, listener: _Listener) -> None:
        """Registra un listener que se ejecuta una única vez."""

        def _temporal(evento: str, datos: Dict[str, Any]) -> None:
            self.off(nombre_evento, _temporal)
            listener(evento, datos)

        self.on(nombre_evento, _temporal)

    # ------------------ Publicación ------------------
    def emit(self, nombre_evento: str, datos: Dict[str, Any] | None = None,
             origen: str = "core") -> None:
        """Publica un evento; es llamado de forma síncrona por el emisor."""
        evento = Evento(nombre=nombre_evento, datos=datos or {}, origen=origen)
        logger.debug("Evento emitido: %s de %s", nombre_evento, origen)

        # Copia de la lista para no iterar una lista que pueda mutarse.
        with self._lock:
            suscriptores = list(self._suscriptores.get(nombre_evento, ()))

        for listener, _prioridad in suscriptores:
            try:
                listener(nombre_evento, evento.datos)
            except Exception:  # noqa: BLE001
                # Un listener fallido no debe cortar la cadena de eventos.
                logger.exception("Error en listener de '%s'.", nombre_evento)

    def emit_asincrono(self, nombre_evento: str,
                       datos: Dict[str, Any] | None = None,
                       origen: str = "core") -> None:
        """Publica un evento en una cola procesada por un hilo worker.

        Útil para tareas que no requieren bloqueo del loop principal
        (p. ej. actualizaciones de estado de bajo impacto).
        """
        evento = Evento(nombre=nombre_evento, datos=datos or {}, origen=origen)
        self._cola.put(evento)
        self._asegurar_worker()

    def _asegurar_worker(self) -> None:
        """Lanza el hilo worker si aún no corre."""
        if self._hilo_worker and self._hilo_worker.is_alive():
            return
        self._detener = False
        self._hilo_worker = threading.Thread(
            target=self._bucle_worker, daemon=True, name="event_bus_worker"
        )
        self._hilo_worker.start()

    def _bucle_worker(self) -> None:
        """Consume eventos de la cola y los despacha (fire & forget)."""
        while not self._detener:
            try:
                evento = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            self.emit(evento.nombre, evento.datos, evento.origen)

    def detener(self) -> None:
        """Ordena al worker asíncrono que termine."""
        self._detener = True
        if self._hilo_worker:
            self._hilo_worker.join(timeout=1.0)
            self._hilo_worker = None

    # ------------------ Despacho de comandos a plugins ------------------
    def despachar_comando(self, comando: str, contexto: Dict[str, Any]) -> str | None:
        """Pregunta a cada plugin activo si puede atender `comando`.

        El primer plugin que devuelva una respuesta (no None) gana.

        Args:
            comando: Texto crudo del usuario.
            contexto: Datos compartidos del runtime.

        Returns:
            La respuesta del plugin que lo atendió, o None si nadie pudo.
        """
        for plugin in self.plugins:
            try:
                respuesta = plugin.execute(comando, contexto)
            except Exception:  # noqa: BLE001
                logger.exception("Plugin '%s' falló al ejecutar.", plugin.nombre)
                continue
            if respuesta:
                logger.debug("Comando resuelto por plugin '%s'.", plugin.nombre)
                return respuesta
        return None

    def registrar_plugin(self, plugin: Any) -> None:
        """Registra un plugin ya inicializado en el bus."""
        if plugin not in self.plugins:
            self.plugins.append(plugin)
            logger.info("Plugin registrado en el bus: %s", plugin.nombre)

    def listar_plugins(self) -> List[str]:
        """Devuelve la lista de nombres de plugins activos (para ayuda)."""
        return [p.nombre for p in self.plugins]
