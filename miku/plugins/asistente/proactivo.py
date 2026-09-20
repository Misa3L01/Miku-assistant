# -*- coding: utf-8 -*-
"""
proactivo.py - Plugin de avisos automáticos (envoltorio del motor proactivo).

La lógica vive en ``miku/servicios/proactivo.py`` (motor y política) y
``miku/servicios/reglas_proactivas.py`` (qué se vigila: clima, juego, carga, GPU, batería, disco).
Este plugin solo arma el motor, lo arranca y lo detiene, y conecta la salida: notificación (toast)
y, si hay voz en el bus, la dice en voz alta.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from miku.ajustes import carga as config_mod
from miku.ajustes.carga import BASE_DIR
from miku.plugins.base import Plugin
from miku.servicios.proactivo import MotorProactivo
from miku.servicios.reglas_proactivas import reglas_por_defecto

logger = logging.getLogger("miku.plugins.asistente_proactivo")


class AsistenteProactivo(Plugin):
    """Avisos automáticos: clima, estado de la PC al jugar, carga alta, batería y disco."""

    nombre = "asistente_proactivo"
    descripcion = "Avisos automáticos (clima, estado de la PC, batería, disco) con frases variadas."

    tools: List[dict] = []

    def __init__(self) -> None:
        super().__init__()
        self._motor: Optional[MotorProactivo] = None
        self._event_bus: Any = None

    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._event_bus = event_bus
        cfg = config_mod.config
        if not cfg.proactivo_activo:
            logger.info("Asistente proactivo desactivado por config.")
            return
        try:
            import psutil  # noqa: F401  (verificación lazy)
        except Exception as e:  # noqa: BLE001
            logger.warning("psutil no disponible (%s): proactivo inactivo.", e)
            return
        self._motor = MotorProactivo(cfg, reglas_por_defecto(cfg, self._contexto, self._plugin), self._emitir,
                                     ruta_estado=BASE_DIR / "data" / "proactivo_estado.json")
        self._motor.iniciar()
        logger.info("Asistente proactivo activo (%d reglas).", len(self._motor.reglas))

    def _contexto(self) -> Optional[dict]:
        """Contexto de runtime de la app (scheduler...), si ya está armado."""
        fabrica = getattr(self._event_bus, "contexto_base", None)
        return fabrica() if callable(fabrica) else None

    def _plugin(self, nombre: str) -> Any:
        """Otro plugin del bus por su nombre (None si no está cargado)."""
        for p in getattr(self._event_bus, "plugins", []) or []:
            if getattr(p, "nombre", "") == nombre:
                return p
        return None

    def cerrar(self) -> None:
        """Detiene el motor (lo llama la app al cerrar)."""
        if self._motor is not None:
            self._motor.detener()
            self._motor = None

    def _emitir(self, titulo: str, mensaje: str) -> None:
        """Muestra el aviso: toast y, si hay voz disponible, lo dice."""
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar(titulo, mensaje)
        except Exception as e:  # noqa: BLE001
            logger.debug("Proactivo: sin toast: %s", e)
        try:
            voz = getattr(self._event_bus, "voice", None) if self._event_bus else None
            if voz is not None:
                voz.decir(mensaje)
        except Exception:  # noqa: BLE001
            pass
