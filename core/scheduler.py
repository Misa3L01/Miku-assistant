"""
scheduler.py - Programador de acciones diferidas (timers) del asistente.

Permite decir cosas como "suspendé la PC en 5 minutos" o "recordame algo en
10 minutos" y poder CANCELAR lo programado. Es genérico: no sabe nada de
energía ni de recordatorios; solo ejecuta un `callback` cuando pasa el tiempo.

Diseño:
    - ``Scheduler`` maneja un conjunto de ``threading.Timer`` (uno por tarea).
    - Cada tarea tiene un ``id`` incremental, una ``descripcion`` legible y un
      ``callback`` que se llama al dispararse.
    - ``programar`` devuelve el id; ``cancelar`` cancela por id;
      ``listar_pendientes`` devuelve el estado actual.
    - ``cancelar_todos`` se usa al cerrar el asistente (evita timers vivos).

Hilos:
    Cada ``threading.Timer`` es un hilo propio de la stdlib. Mantenemos un
    lock para que programar/cancelar/listar sean seguros desde varios hilos
    (el parser puede programar desde el hilo del TTS/consola).
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("miku.scheduler")


class Scheduler:
    """Programa callbacks para ejecutarse luego de N segundos.

    Es seguro para usar desde varios hilos. Las tareas ya disparadas (o
    canceladas) se eliminan de la lista de pendientes automáticamente.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # id -> {"timer": Timer, "descripcion": str, "cuando": float,
        #         "creado": float, "callback": callable}
        self._tareas: Dict[int, Dict[str, Any]] = {}
        self._ids = itertools.count(1)

    # ---------------- API principal ----------------
    def programar(self, cuando_segundos: float, callback: Callable[[], Any],
                  descripcion: str = "") -> int:
        """Programa `callback` para dentro de `cuando_segundos` segundos.

        Args:
            cuando_segundos: demora en segundos (>= 0; si es <= 0 se ejecuta
                casi de inmediato).
            callback: función sin argumentos a llamar al dispararse.
            descripcion: texto legible para listar ("Apagar la PC").

        Returns:
            El id de la tarea (para poder cancelarla después).
        """
        try:
            demora = max(0.0, float(cuando_segundos))
        except (TypeError, ValueError):
            demora = 0.0

        with self._lock:
            tarea_id = next(self._ids)

        def _disparar() -> None:
            # Quitamos la tarea de pendientes ANTES de correr el callback, así
            # listar_pendientes() no la muestra como si siguiera esperando.
            with self._lock:
                self._tareas.pop(tarea_id, None)
            try:
                logger.info("[Scheduler] Disparando tarea %s (%s).",
                            tarea_id, descripcion or "sin descripción")
                callback()
            except Exception:  # noqa: BLE001
                logger.exception("[Scheduler] Error en la tarea %s.", tarea_id)

        timer = threading.Timer(demora, _disparar)
        timer.daemon = True  # no debe bloquear el cierre de la app

        with self._lock:
            self._tareas[tarea_id] = {
                "timer": timer,
                "descripcion": descripcion or "tarea programada",
                "cuando": demora,
                "creado": time.time(),
                "callback": callback,
            }
        timer.start()
        logger.info("[Scheduler] Tarea %s programada en %.1f s (%s).",
                    tarea_id, demora, descripcion or "-")
        return tarea_id

    def cancelar(self, tarea_id: Optional[int] = None) -> bool:
        """Cancela una tarea por id. Si `tarea_id` es None, cancela la última.

        Returns:
            True si se canceló algo, False si no había nada para cancelar.
        """
        with self._lock:
            if not self._tareas:
                return False
            if tarea_id is None:
                # La última programada = el id más alto.
                tarea_id = max(self._tareas.keys())
            tarea = self._tareas.pop(tarea_id, None)
        if tarea is None:
            return False
        try:
            tarea["timer"].cancel()
        except Exception:  # noqa: BLE001
            logger.debug("[Scheduler] No pude cancelar el Timer %s.", tarea_id)
        logger.info("[Scheduler] Tarea %s cancelada (%s).",
                    tarea_id, tarea.get("descripcion", "-"))
        return True

    def listar_pendientes(self) -> List[Dict[str, Any]]:
        """Devuelve una lista de las tareas aún pendientes (sin el Timer)."""
        with self._lock:
            ahora = time.time()
            pendientes = []
            for tarea_id, tarea in self._tareas.items():
                transcurrido = ahora - tarea["creado"]
                restante = max(0.0, tarea["cuando"] - transcurrido)
                pendientes.append({
                    "id": tarea_id,
                    "descripcion": tarea["descripcion"],
                    "segundos_restantes": round(restante, 1),
                })
        return sorted(pendientes, key=lambda t: t["id"])

    def hay_pendientes(self) -> bool:
        """True si queda alguna tarea programada."""
        with self._lock:
            return bool(self._tareas)

    def cancelar_todos(self) -> int:
        """Cancela TODAS las tareas pendientes. Devuelve cuántas canceló."""
        with self._lock:
            ids = list(self._tareas.keys())
        canceladas = 0
        for tarea_id in ids:
            if self.cancelar(tarea_id):
                canceladas += 1
        return canceladas
