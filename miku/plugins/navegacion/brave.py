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

import json
import logging
import subprocess
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

from miku.ajustes import carga as config_mod
from miku.plataforma.texto import normalizar
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla, hubo_falla

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
                "name": "listar_pestanas",
                "description": "Dice qué PESTAÑAS tiene abiertas el navegador Brave (títulos). Ej: "
                               "'qué pestañas tengo abiertas', 'cuántas pestañas hay'. No es para "
                               "ventanas de otros programas.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cerrar_pestana",
                "description": "Cierra una pestaña de Brave: la ACTUAL, o la que se llame "
                               "como se diga. Ej: 'cerrá esta pestaña', 'cerrá la pestaña de "
                               "YouTube'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "titulo": {"type": "string",
                                   "description": "Parte del título o del sitio de la pestaña a "
                                                  "cerrar. Opcional: sin esto se cierra la actual."},
                    },
                },
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
        if nombre_tool == "listar_pestanas":
            return self.listar_pestanas()
        if nombre_tool == "cerrar_pestana":
            return self.cerrar_pestana(str(args.get("titulo", "") or ""))
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

    def listar_pestanas(self) -> str:
        """Dice cuántas pestañas hay en Brave y los títulos de las primeras."""
        if not self._asegurar_cdp():
            return falla("brave.sin_cdp")
        pestanas = [p for p in self._pestanas() if p.get("type", "page") == "page"
                    and not str(p.get("url", "")).startswith(("devtools://", "chrome-extension://"))]
        if not pestanas:
            return falla("brave.sin_pestanas")
        titulos = [str(p.get("title") or p.get("url") or "sin título").strip()[:50] for p in pestanas]
        mostrados = titulos[:8]
        return exito("brave.pestanas", cantidad=len(pestanas), titulos="; ".join(mostrados),
                     mas=f" y {len(pestanas) - len(mostrados)} más" if len(pestanas) > len(mostrados) else "")

    @staticmethod
    def _coincide(pestana: Dict[str, Any], palabras: List[str]) -> bool:
        """True si TODAS las palabras aparecen en el título o en la URL de la pestaña."""
        texto = normalizar(f"{pestana.get('title', '')} {pestana.get('url', '')}")
        return all(p in texto for p in palabras)

    def _cerrar_por_id(self, pid: str) -> bool:
        try:
            requests.get(f"{self._url_base()}/json/close/{pid}", timeout=4)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("No pude cerrar la pestaña: %s", e)
            return False

    def cerrar_pestana(self, titulo: str = "") -> str:
        """Cierra la pestaña ACTUAL o, si se dice un título, la que coincida (si es una sola).

        ``/json`` lista los targets con la pestaña más recientemente activa primero; la última sería
        la más ANTIGUA, no la actual. Si el título coincide con varias pestañas no se cierra ninguna:
        se dicen cuáles son para que el usuario precise.
        """
        if not self._asegurar_cdp():
            return falla("brave.sin_cdp")

        pestanas = [p for p in self._pestanas() if p.get("type", "page") == "page"]
        if not pestanas:
            return falla("brave.sin_pestanas")

        palabras = [p for p in normalizar(titulo).split() if p]
        if palabras:
            candidatas = [p for p in pestanas if self._coincide(p, palabras)]
            if not candidatas:
                return falla("brave.pestana_no_encontrada", titulo=titulo)
            if len(candidatas) > 1:
                nombres = "; ".join(str(p.get("title") or p.get("url") or "?")[:40]
                                    for p in candidatas[:3])
                return falla("brave.varias_pestanas", titulo=titulo, cantidad=len(candidatas),
                             nombres=nombres)
            objetivo = candidatas[0]
        else:
            objetivo = pestanas[0]

        pid = objetivo.get("id")
        if not pid:
            return falla("brave.pestana_sin_id")
        if not self._cerrar_por_id(pid):
            return falla("brave.no_pude_cerrar")
        return exito("brave.pestana_cerrada", titulo=str(objetivo.get("title") or "")[:40],
                     con_titulo=bool(palabras))

    def _navegar(self, pestana: Dict[str, Any], url: str) -> bool:
        """Lleva ``pestana`` a ``url`` por el websocket de CDP (necesita ``websocket-client``)."""
        ws_url = pestana.get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        try:
            import websocket  # type: ignore  # opcional
        except Exception:  # noqa: BLE001
            return False
        try:
            # Sin Origin: Chromium/Brave rechazan los websockets de CDP que traen esa cabecera.
            ws = websocket.create_connection(ws_url, timeout=4, suppress_origin=True)
            try:
                ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
                respuesta = json.loads(ws.recv())
            finally:
                ws.close()
            return "error" not in respuesta
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude navegar la pestaña por CDP: %s", e)
            return False

    def buscar_en_pestana_actual(self, consulta: str) -> str:
        """Busca en Google **en la pestaña activa**; si no se puede navegarla, abre una nueva."""
        consulta = (consulta or "").strip()
        if not consulta:
            return falla("brave.buscar_sin_consulta")
        if not self._asegurar_cdp():
            return falla("brave.sin_cdp")
        pestanas = [p for p in self._pestanas() if p.get("type", "page") == "page"]
        if pestanas and self._navegar(pestanas[0], self._a_url(consulta)):
            return exito("brave.buscado_en_actual", consulta=consulta)
        # Sin websocket-client (o sin pestañas) se abre una pestaña nueva; se dice que fue eso.
        resultado = self.abrir_pestana(consulta)
        return resultado if hubo_falla(resultado) else exito("brave.buscado_en_nueva", consulta=consulta)
