# -*- coding: utf-8 -*-
"""
browser.py - Control del navegador (Brave) por CDP (DevTools Protocol).

Publica las tools ``abrir_pestana``, ``cerrar_pestana`` y
``buscar_en_pestana_actual``. Se conecta al puerto de depuración remota de
Brave (``brave_debug_port``, default 9222) usando su **HTTP JSON API** (sin
dependencias extra, con ``requests``).

Si no hay puerto de depuración activo, intenta LANZAR Brave con la ruta
configurada (``brave_ruta_exe``) y el puerto de debug. Si tampoco puede,
responde con un mensaje claro (y honesto) sobre qué falta.

Nota: NO se intenta "arreglar" la restauración de pestañas de Brave; es
comportamiento del navegador. Abrir pestaña funciona igual.

Requisitos para que el control REAL funcione:
    - Brave lanzado con ``--remote-debugging-port=9222`` (lo hacemos nosotros
      si hay ``brave_ruta_exe``). El perfil debe ser DEDICADO para evitar el
      bloqueo de "otra instancia"; si el usuario abre Brave normalmente sin
      ese flag, no habrá CDP y lo avisamos.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.browser")


class Browser(Plugin):
    """Control de Brave por CDP (pestañas)."""

    nombre = "browser"
    descripcion = "Abre/cierra pestañas y busca en Brave por CDP."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "abrir_pestana",
                "description": "Abre una nueva pestaña en Brave con una URL o "
                               "búsqueda. Ej: 'abrí una pestaña de YouTube', "
                               "'abrí mercadolibre.com'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "destino": {
                            "type": "string",
                            "description": "URL, dominio o término a buscar "
                                           "en una pestaña nueva.",
                        },
                    },
                    "required": ["destino"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cerrar_pestana",
                "description": "Cierra la pestaña ACTUAL de Brave. Ej: "
                               "'cerrá esta pestaña'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "buscar_en_pestana_actual",
                "description": "Hace una búsqueda en Google y la abre en una "
                               "pestaña nueva de Brave. Ej: 'buscá en otra "
                               "pestaña recetas de milanesas'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "consulta": {
                            "type": "string",
                            "description": "Término a buscar en Google.",
                        },
                    },
                    "required": ["consulta"],
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        cfg = config_mod.config
        logger.info("Plugin browser listo (puerto debug: %s, ruta exe: %s).",
                    cfg.valores.get("brave_debug_port", 9222),
                    "configurada" if cfg.valores.get("brave_ruta_exe")
                    else "no configurada")

    # ---------------- Helpers de conexión CDP ---------------- #
    def _url_base(self) -> str:
        """URL base del endpoint CDP."""
        try:
            puerto = int(config_mod.config.valores.get("brave_debug_port", 9222))
        except (TypeError, ValueError):
            puerto = 9222
        return f"http://127.0.0.1:{puerto}"

    def _cdp_vivo(self) -> bool:
        """True si hay un Brave con CDP escuchando."""
        try:
            resp = requests.get(f"{self._url_base()}/json/version", timeout=2)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _asegurar_cdp(self) -> bool:
        """Asegura un Brave con CDP. Lanza Brave si hace falta y puede.

        Returns:
            True si hay CDP disponible, False si no se pudo.
        """
        if self._cdp_vivo():
            return True

        # Intentamos lanzar Brave con el puerto de depuración.
        lanzado = self._lanzar_brave_debug()
        if not lanzado:
            return False
        # Esperamos un momento razonable a que levante el endpoint.
        for _ in range(15):  # ~4.5s
            if self._cdp_vivo():
                return True
            time.sleep(0.3)
        return False

    def _lanzar_brave_debug(self) -> bool:
        """Lanza Brave con el puerto de depuración (si hay ruta configurada)."""
        ruta = str(config_mod.config.valores.get("brave_ruta_exe", "") or "").strip()
        if not ruta:
            logger.info("Sin BRAVE_RUTA_EXE: no puedo lanzar Brave con CDP.")
            return False
        try:
            puerto = int(config_mod.config.valores.get("brave_debug_port", 9222))
        except (TypeError, ValueError):
            puerto = 9222
        perfil = str(config_mod.config.valores.get("brave_perfil_dir", "") or "").strip()

        args = [ruta, f"--remote-debugging-port={puerto}"]
        if perfil:
            args.append(f"--user-data-dir={perfil}")
        try:
            subprocess.Popen(args, shell=False,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            logger.info("Lancé Brave con CDP (puerto %s).", puerto)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("No pude lanzar Brave: %s", e)
            return False

    def _nueva_pestana(self, url: str) -> Optional[str]:
        """Pide a CDP abrir una pestaña nueva. Devuelve el id o None."""
        try:
            resp = requests.put(f"{self._url_base()}/json/new?{url}", timeout=5)
            if resp.status_code == 200:
                try:
                    return resp.json().get("id")
                except Exception:  # noqa: BLE001
                    return "ok"
        except Exception as e:  # noqa: BLE001
            logger.error("Error abriendo pestaña por CDP: %s", e)
        return None

    def _pestanas(self) -> List[Dict[str, Any]]:
        """Lista las pestañas actuales (objetos con 'type' == 'page')."""
        try:
            resp = requests.get(f"{self._url_base()}/json", timeout=4)
            datos = resp.json()
            return [p for p in datos if p.get("type") == "page"]
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude listar pestañas: %s", e)
            return []

    # ---------------- Resolución de destino -> URL ---------------- #
    @staticmethod
    def _a_url(destino: str) -> str:
        """Convierte un destino en URL (o búsqueda de Google)."""
        destino = (destino or "").strip()
        if not destino:
            return "https://www.google.com"
        bajo = destino.lower()
        if bajo.startswith(("http://", "https://")):
            return destino
        # Si parece dominio (tiene punto y no espacios), lo tratamos como URL.
        if "." in destino and " " not in destino:
            return "https://" + destino
        # Si no, búsqueda en Google.
        return "https://www.google.com/search?q=" + quote_plus(destino)

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "abrir_pestana":
            return self.abrir_pestana(str(args.get("destino", "")))
        if nombre_tool == "cerrar_pestana":
            return self.cerrar_pestana()
        if nombre_tool == "buscar_en_pestana_actual":
            return self.buscar_en_pestana_actual(str(args.get("consulta", "")))
        return None

    # ---------------- Acciones ---------------- #
    def abrir_pestana(self, destino: str) -> str:
        """Abre una pestaña nueva con `destino` (URL o búsqueda)."""
        destino = (destino or "").strip()
        if not destino:
            return "¿Qué querés que abra?"

        if not self._asegurar_cdp():
            return ("No puedo controlar Brave ahora: no tengo el puerto de "
                    "depuración (CDP). Configurá BRAVE_RUTA_EXE para que lo "
                    "lance yo, o abrí Brave con --remote-debugging-port.")

        url = self._a_url(destino)
        if self._nueva_pestana(url):
            logger.info("Pestaña abierta: %s", url)
            return f"Listo, abrí una pestaña con {destino}."
        return "No pude abrir la pestaña en Brave."

    def cerrar_pestana(self) -> str:
        """Cierra la pestaña actual (la primera de la lista de CDP).

        ``/json`` lista los targets con la pestaña más recientemente activa
        primero; la última sería la más ANTIGUA, no la actual.
        """
        if not self._asegurar_cdp():
            return ("No puedo controlar Brave ahora (falta el puerto de "
                    "depuración/CDP).")

        pestanas = [p for p in self._pestanas() if p.get("type", "page") == "page"]
        if not pestanas:
            return "No encontré pestañas para cerrar."
        actual = pestanas[0]
        pid = actual.get("id")
        if not pid:
            return "No pude identificar la pestaña actual."
        try:
            requests.get(f"{self._url_base()}/json/close/{pid}", timeout=4)
            return "Listo, cerré la pestaña."
        except Exception as e:  # noqa: BLE001
            logger.error("No pude cerrar la pestaña: %s", e)
            return "No pude cerrar la pestaña."

    def buscar_en_pestana_actual(self, consulta: str) -> str:
        """Abre una búsqueda de Google en una pestaña nueva."""
        consulta = (consulta or "").strip()
        if not consulta:
            return "¿Qué querés que busque?"
        # Reutilizamos abrir_pestana con la consulta (la convierte a búsqueda).
        return self.abrir_pestana(consulta)