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

Recordatorios persistentes:
    Una tarea programada con ``persistente={...}`` (solo datos simples: un recordatorio) se guarda
    también en un JSON (``ruta_persistencia``) con su hora de vencimiento absoluta. Sobrevive a
    cerrar Miku: al arrancar, ``recuperar`` reprograma los que faltan y devuelve los que se pasaron
    mientras estaba cerrada (hasta ``MAX_ATRASO_H`` horas) para avisarlos. Las acciones peligrosas
    (apagar, suspender) NUNCA se persisten: un apagado programado no debe dispararse en otra sesión.

Hilos:
    Cada ``threading.Timer`` es un hilo propio de la stdlib. Mantenemos un
    lock para que programar/cancelar/listar sean seguros desde varios hilos
    (el parser puede programar desde el hilo del TTS/consola).
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("miku.scheduler")

#: Un recordatorio vencido mientras Miku estaba cerrada se avisa si pasaron menos de estas horas.
MAX_ATRASO_H = 24


class Scheduler:
    """Programa callbacks para ejecutarse luego de N segundos.

    Es seguro para usar desde varios hilos. Las tareas ya disparadas (o
    canceladas) se eliminan de la lista de pendientes automáticamente.
    """

    def __init__(self, ruta_persistencia: Optional[Path] = None) -> None:
        self._ruta = Path(ruta_persistencia) if ruta_persistencia else None
        self._lock = threading.RLock()
        # id -> {"timer": Timer, "descripcion": str, "cuando": float,
        #         "creado": float, "callback": callable}
        self._tareas: Dict[int, Dict[str, Any]] = {}
        self._ids = itertools.count(1)

    # ---------------- API principal ----------------
    def programar(self, cuando_segundos: float, callback: Callable[[], Any],
                  descripcion: str = "", persistente: Optional[Dict[str, Any]] = None) -> int:
        """Programa `callback` para dentro de `cuando_segundos` segundos.

        Args:
            cuando_segundos: demora en segundos (>= 0; si es <= 0 se ejecuta
                casi de inmediato).
            callback: función sin argumentos a llamar al dispararse.
            descripcion: texto legible para listar ("Apagar la PC").
            persistente: datos simples (JSON) para guardar la tarea en disco y recuperarla al
                reiniciar (un recordatorio). None = solo vive en memoria.

        Returns:
            El id de la tarea (para poder cancelarla después).
        """
        try:
            demora = max(0.0, float(cuando_segundos))
        except (TypeError, ValueError):
            demora = 0.0
        # ``inf``/valores enormes hacen fallar al Timer y dejarían la tarea
        # como zombi en la lista de pendientes.
        demora = min(demora, float(threading.TIMEOUT_MAX))

        with self._lock:
            tarea_id = next(self._ids)

        def _disparar() -> None:
            # Quitamos la tarea de pendientes ANTES de correr el callback, así
            # listar_pendientes() no la muestra como si siguiera esperando.
            with self._lock:
                self._tareas.pop(tarea_id, None)
            self._quitar_de_disco(tarea_id)
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
                "persistente": persistente,
            }
        if persistente is not None:
            self._guardar_en_disco(tarea_id, demora, descripcion, persistente)
        timer.start()
        logger.info("[Scheduler] Tarea %s programada en %.1f s (%s).",
                    tarea_id, demora, descripcion or "-")
        return tarea_id

    def cancelar(self, tarea_id: Optional[int] = None, olvidar: bool = True) -> bool:
        """Cancela una tarea por id. Si `tarea_id` es None, cancela la última.

        Args:
            olvidar: Si es False el timer se detiene pero la tarea sigue guardada en disco (se usa
                al cerrar Miku: los recordatorios deben volver en la próxima sesión).

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
        if olvidar:
            self._quitar_de_disco(tarea_id)
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

    def cancelar_todos(self, olvidar: bool = True) -> int:
        """Cancela TODAS las tareas pendientes. Devuelve cuántas canceló.

        Con ``olvidar=False`` (cierre de Miku) los recordatorios guardados en disco se conservan.
        """
        with self._lock:
            ids = list(self._tareas.keys())
        canceladas = 0
        for tarea_id in ids:
            if self.cancelar(tarea_id, olvidar=olvidar):
                canceladas += 1
        return canceladas

    # ---------------- Persistencia ----------------
    def _leer_disco(self) -> List[Dict[str, Any]]:
        if self._ruta is None:
            return []
        try:
            datos = json.loads(self._ruta.read_text(encoding="utf-8"))
            return [t for t in datos if isinstance(t, dict)]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError) as e:
            logger.warning("[Scheduler] Recordatorios guardados ilegibles (%s); los ignoro.", e)
            return []

    def _escribir_disco(self, tareas: List[Dict[str, Any]]) -> None:
        if self._ruta is None:
            return
        try:
            self._ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps(tareas, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._ruta)
        except OSError as e:
            logger.warning("[Scheduler] No pude guardar los recordatorios: %s", e)

    def _guardar_en_disco(self, tarea_id: int, demora: float, descripcion: str,
                          datos: Dict[str, Any]) -> None:
        with self._lock:
            tareas = [t for t in self._leer_disco() if t.get("id") != tarea_id]
            tareas.append({"id": tarea_id, "vence": time.time() + demora,
                           "descripcion": descripcion, "datos": datos})
            self._escribir_disco(tareas)

    def _quitar_de_disco(self, tarea_id: int) -> None:
        if self._ruta is None:
            return
        with self._lock:
            tareas = self._leer_disco()
            restantes = [t for t in tareas if t.get("id") != tarea_id]
            if len(restantes) != len(tareas):
                self._escribir_disco(restantes)

    def recuperar(self, fabricar_callback: Callable[[Dict[str, Any]], Callable[[], Any]]
                  ) -> List[Dict[str, Any]]:
        """Reprograma los recordatorios guardados. Devuelve los que se pasaron mientras estaba cerrada.

        Args:
            fabricar_callback: ``f(datos) -> callback`` que arma la acción de un recordatorio a
                partir de sus datos guardados.

        Returns:
            Lista de ``datos`` de los recordatorios vencidos (recientes) para avisarlos ahora.
        """
        guardadas = self._leer_disco()
        if not guardadas:
            return []
        self._escribir_disco([])          # se vuelven a guardar al reprogramarlas
        ahora = time.time()
        perdidos: List[Dict[str, Any]] = []
        for t in guardadas:
            try:
                falta = float(t["vence"]) - ahora
                datos = dict(t["datos"])
                descripcion = str(t.get("descripcion") or "")
            except (KeyError, TypeError, ValueError):
                continue
            if falta > 0:
                self.programar(falta, fabricar_callback(datos), descripcion, persistente=datos)
            elif -falta <= MAX_ATRASO_H * 3600:
                perdidos.append(datos)
            else:
                logger.info("[Scheduler] Recordatorio muy viejo descartado: %s", descripcion)
        if guardadas:
            logger.info("[Scheduler] Recuperados %d recordatorio(s); %d se pasaron.",
                        len(guardadas) - len(perdidos), len(perdidos))
        return perdidos
