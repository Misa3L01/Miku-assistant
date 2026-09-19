# -*- coding: utf-8 -*-
"""
captura.py - Captura de pantalla por voz.

Plugin liviano que publica la tool ``capturar_pantalla``. Toma una captura de
toda la pantalla (o de un monitor puntual), la guarda como PNG/JPG con fecha y
hora en una carpeta fija y confirma el resultado.

Carpeta de destino:
    1. ``CARPETA_CAPTURAS`` de config_local.py (ruta absoluta o relativa).
    2. Si no se define, ``data/capturas/`` (relativa a la raíz del proyecto).

Usa ``PIL.ImageGrab`` (Pillow). Import perezoso: si falta, degrada con un
mensaje claro en vez de tumbar el asistente.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.captura")


class Captura(Plugin):
    """Saca capturas de pantalla y las guarda en disco."""

    nombre = "captura"
    descripcion = "Capturas de pantalla guardadas con fecha y hora."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "capturar_pantalla",
                "description": "Toma una captura de TODA la pantalla y la "
                               "guarda como archivo con fecha y hora. Ej: "
                               "'sacá una captura', 'capturá la pantalla'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "monitor": {
                            "type": "integer",
                            "description": "Opcional: número de monitor "
                                           "(1-based). Si se omite, captura "
                                           "todas las pantallas juntas.",
                        },
                        "formato": {
                            "type": "string",
                            "enum": ["png", "jpg"],
                            "description": "Formato de imagen (default png).",
                        },
                    },
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        """Prepara la carpeta de capturas y verifica Pillow (lazy)."""
        super().initialize(event_bus)
        self._pillow_ok = False
        try:
            from PIL import ImageGrab  # noqa: F401
            self._pillow_ok = True
        except Exception:  # noqa: BLE001
            logger.warning("Pillow (ImageGrab) no disponible; capturas "
                           "deshabilitadas.")
        logger.info("Plugin captura listo (pillow=%s).", self._pillow_ok)

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools publicadas por este plugin."""
        if nombre_tool == "capturar_pantalla":
            return self.capturar_pantalla(args.get("monitor"),
                                          str(args.get("formato", "png") or
                                              "png"))
        return None

    # ---------------- Acción ---------------- #
    def _carpeta(self) -> Path:
        """Devuelve (y crea) la carpeta donde se guardan las capturas."""
        # La propiedad de config resuelve la ruta (con default data/capturas).
        ruta = config_mod.config.carpeta_capturas
        carpeta = Path(ruta)
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta

    def capturar_pantalla(self, monitor: Optional[int] = None,
                          formato: str = "png") -> str:
        """Toma la captura y la guarda. Devuelve una confirmación de texto.

        Args:
            monitor: monitor 1-based opcional (None = todas las pantallas).
            formato: "png" (default) o "jpg".
        """
        try:
            from PIL import ImageGrab  # import tardío (lazy)
        except Exception:  # noqa: BLE001
            return ("No tengo Pillow instalado, así que no puedo sacar "
                    "capturas. Instalá 'Pillow' y reintentá.")

        formato = (formato or "png").lower().strip()
        if formato not in ("png", "jpg", "jpeg"):
            formato = "png"
        ext = "jpg" if formato in ("jpg", "jpeg") else "png"

        # Región de captura: None = todas las pantallas; si hay monitor, la
        # bounding box de ese monitor.
        bbox = None
        if monitor is not None:
            try:
                bbox = self._bbox_monitor(int(monitor))
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude calcular la bbox del monitor: %s", e)
            if bbox is None and monitor is not None:
                return f"No encontré el monitor {monitor}."

        try:
            # ``all_screens=True`` también con bbox: sin eso Pillow (Windows)
            # solo ve el monitor primario y un monitor secundario sale negro.
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            # Versiones viejas de Pillow no aceptan all_screens.
            img = ImageGrab.grab(bbox=bbox)
        except Exception as e:  # noqa: BLE001
            logger.error("Error tomando la captura: %s", e)
            return "No pude tomar la captura de pantalla."

        carpeta = self._carpeta()
        base = f"captura_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        nombre = f"{base}.{ext}"
        n = 1
        while (carpeta / nombre).exists():  # dos capturas en el mismo segundo
            n += 1
            nombre = f"{base}_{n}.{ext}"
        ruta = carpeta / nombre

        try:
            if ext == "jpg":
                img.convert("RGB").save(str(ruta), "JPEG", quality=90)
            else:
                img.save(str(ruta), "PNG")
            logger.info("Captura guardada en %s", ruta)
        except Exception as e:  # noqa: BLE001
            logger.error("No pude guardar la captura: %s", e)
            return "No pude guardar la captura."

        return f"Listo, guardé la captura como {nombre}."

    @staticmethod
    def _bbox_monitor(monitor: int) -> Optional[tuple]:
        """Devuelve la bounding box (x1, y1, x2, y2) del monitor indicado.

        Usa win32api si está disponible (misma técnica que system_control);
        devuelve None si no se puede determinar.
        """
        try:
            import win32api  # type: ignore
            monitores = win32api.EnumDisplayMonitors()
            idx = int(monitor) - 1
            if idx < 0 or idx >= len(monitores):
                return None
            info = win32api.GetMonitorInfo(monitores[idx][0])
            x1, y1, x2, y2 = info["Monitor"]
            return (int(x1), int(y1), int(x2), int(y2))
        except Exception as e:  # noqa: BLE001
            logger.debug("Sin win32api para bbox de monitor: %s", e)
            return None