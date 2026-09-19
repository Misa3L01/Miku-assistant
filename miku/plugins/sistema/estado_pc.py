# -*- coding: utf-8 -*-
"""
system_status.py - Plugin de estado del sistema (CPU, RAM, disco, batería).

Plugin liviano y modular: publica la tool `estado_pc` (alias amigable
`como_esta_la_pc`) para que el usuario pregunte "¿cómo está la PC?" y reciba
una respuesta CORTA y natural con lo esencial.

Usa ``psutil`` (import perezoso: si no está, degrada con un mensaje claro en
vez de tumbar el asistente). NO informa temperaturas (muchos equipos no las
exponen de forma fiable).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from miku.plataforma import hardware
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.system_status")


class SystemStatus(Plugin):
    """Informa el estado del sistema (CPU, RAM, disco y batería)."""

    nombre = "system_status"
    descripcion = "Estado del sistema: CPU, RAM, disco libre y batería."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "estado_pc",
                "description": "Informa el estado de la PC: uso de CPU, "
                               "memoria RAM usada/total, disco libre y "
                               "batería (si es una notebook). Respuesta "
                               "corta. Ej: '¿cómo está la PC?', 'cómo anda "
                               "la compu'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        """Verifica (lazy) disponibilidad de psutil; no bloquea el arranque."""
        super().initialize(event_bus)
        try:
            import psutil  # noqa: F401  (solo probamos que exista)
            self._psutil_ok = True
        except Exception:  # noqa: BLE001
            self._psutil_ok = False
            logger.warning("psutil no disponible; estado_pc quedará limitada.")
        logger.info("Plugin system_status listo (psutil=%s).", self._psutil_ok)

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools publicadas por este plugin."""
        if nombre_tool in ("estado_pc", "como_esta_la_pc"):
            return self.estado_pc()
        return None

    # ---------------- Estado del sistema ---------------- #
    def estado_pc(self) -> str:
        """Arma una respuesta corta y natural con el estado de la PC.

        Incluye CPU %, RAM usada/total, disco libre del disco principal y
        batería (solo si el equipo tiene). SIN temperaturas.
        """
        try:
            import psutil  # import tardío (lazy)
        except Exception:  # noqa: BLE001
            return ("No tengo psutil instalado, así que no puedo leer el "
                    "estado del sistema.")

        partes: List[str] = []

        # --- CPU ---
        try:
            cpu = psutil.cpu_percent(interval=0.4)
            partes.append(f"la CPU está en {cpu:.0f}%")
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer la CPU: %s", e)

        # --- RAM ---
        try:
            mem = psutil.virtual_memory()
            usada_gb = (mem.total - mem.available) / (1024 ** 3)
            total_gb = mem.total / (1024 ** 3)
            partes.append(
                f"la RAM en {usada_gb:.1f} de {total_gb:.1f} GB "
                f"({mem.percent:.0f}%)")
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer la RAM: %s", e)

        # --- Disco principal (donde está Windows) ---
        try:
            disco = hardware.disco_principal(psutil)
            if disco is not None:
                libre_gb = disco.free / (1024 ** 3)
                partes.append(f"te quedan {libre_gb:.0f} GB libres en disco")
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer el disco: %s", e)

        # --- Batería (solo si existe) ---
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                estado_carga = " (cargando)" if bat.power_plugged else ""
                partes.append(f"la batería en {bat.percent:.0f}%{estado_carga}")
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer la batería: %s", e)

        if not partes:
            return "No pude leer el estado del sistema ahora mismo."

        return "Ahora mismo, " + ", ".join(partes) + "."
