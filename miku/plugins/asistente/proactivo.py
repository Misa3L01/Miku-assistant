# -*- coding: utf-8 -*-
"""
asistente_proactivo.py - Avisos automaticos (minimo viable, no invasivo).

Un hilo daemon revisa cada N minutos el estado del sistema y avisa UNA vez por
cooldown cuando algo se pone critico:
    - Bateria baja (si es notebook).
    - Disco del sistema casi lleno.

SIN temperatura (muchos equipos no la exponen de forma fiable). Avisa por toast
(core.notificaciones) y, si hay voz en el contexto del bus, tambien habla una
frase corta. Todo tolerante: si falta psutil, no arranca el hilo (no rompe).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.asistente_proactivo")


class AsistenteProactivo(Plugin):
    """Monitorea bateria y disco y avisa (con cooldown)."""

    nombre = "asistente_proactivo"
    descripcion = "Avisos automaticos: bateria baja y disco casi lleno."

    tools: List[dict] = []

    def __init__(self) -> None:
        super().__init__()
        self._hilo: Optional[threading.Thread] = None
        self._detener = threading.Event()
        self._event_bus = None
        # Anti-spam: ultimo aviso por tipo (monotonic), para respetar cooldown.
        self._ultimo_aviso: Dict[str, float] = {}

    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._event_bus = event_bus
        if not config_mod.config.proactivo_activo:
            logger.info("Asistente proactivo desactivado por config.")
            return
        try:
            import psutil  # noqa: F401  (verificacion lazy)
        except Exception as e:  # noqa: BLE001
            logger.warning("psutil no disponible (%s): proactivo inactivo.", e)
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True,
                                      name="miku_proactivo")
        self._hilo.start()
        logger.info("Asistente proactivo activo (cada %d min).",
                    config_mod.config.proactivo_intervalo_min)

    def cerrar(self) -> None:
        """Detiene el hilo (lo llama main al cerrar)."""
        self._detener.set()
        if self._hilo is not None:
            try:
                self._hilo.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            self._hilo = None

    def _bucle(self) -> None:
        """Revisa periodicamente el estado y dispara avisos con cooldown."""
        while not self._detener.is_set():
            try:
                self._revisar()
            except Exception as e:  # noqa: BLE001
                logger.debug("Proactivo: error revisando estado: %s", e)
            espera = max(1, config_mod.config.proactivo_intervalo_min) * 60
            self._detener.wait(espera)

    def _revisar(self) -> None:
        """Chequea bateria y disco y avisa si corresponde."""
        import psutil
        try:
            bat = psutil.sensors_battery()
            if bat is not None and not bat.power_plugged:
                umbral = config_mod.config.proactivo_bateria_min
                if bat.percent <= umbral:
                    self._avisar(
                        "bateria_baja", "Bateria baja",
                        f"Te queda {round(bat.percent)}% de bateria. "
                        f"Conecta el cargador cuando puedas.")
        except Exception as e:  # noqa: BLE001
            logger.debug("Proactivo: sin bateria: %s", e)

        try:
            disco = self._disco_principal(psutil)
            if disco is not None:
                libre_gb = disco.free / (1024 ** 3)
                umbral_gb = config_mod.config.proactivo_disco_gb
                if libre_gb <= umbral_gb:
                    self._avisar(
                        "disco_lleno", "Poco espacio en disco",
                        f"Te quedan {round(libre_gb)} GB libres. "
                        f"Conviene liberar espacio.")
        except Exception as e:  # noqa: BLE001
            logger.debug("Proactivo: sin disco: %s", e)

    @staticmethod
    def _disco_principal(psutil_mod: Any) -> Optional[Any]:
        """Uso de la particion del sistema (o la primera disponible)."""
        try:
            import os
            objetivo = os.environ.get("SystemDrive", "C:").upper()
        except Exception:  # noqa: BLE001
            objetivo = "C:"
        for p in psutil_mod.disk_partitions(all=False):
            montaje = (p.mountpoint or "").upper()
            if montaje.startswith(objetivo):
                try:
                    return psutil_mod.disk_usage(p.mountpoint)
                except Exception:  # noqa: BLE001
                    continue
        for p in psutil_mod.disk_partitions(all=False):
            try:
                return psutil_mod.disk_usage(p.mountpoint)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _avisar(self, clave: str, titulo: str, mensaje: str) -> None:
        """Avisa (toast + voz si hay) respetando el cooldown por `clave`."""
        cooldown = max(1, config_mod.config.proactivo_cooldown_min) * 60
        ahora = time.monotonic()
        ultimo = self._ultimo_aviso.get(clave, 0.0)
        if (ahora - ultimo) < cooldown:
            return
        self._ultimo_aviso[clave] = ahora
        logger.info("Proactivo [%s]: %s", clave, mensaje)
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar(titulo, mensaje)
        except Exception as e:  # noqa: BLE001
            logger.debug("Proactivo: sin toast: %s", e)
        try:
            voz = getattr(self._event_bus, "voice", None) \
                if self._event_bus else None
            if voz is not None:
                voz.decir(mensaje)
        except Exception:  # noqa: BLE001
            pass