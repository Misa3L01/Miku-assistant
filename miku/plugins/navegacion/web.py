"""
web.py - Búsqueda y apertura de sitios web.

Publica la tool `buscar_en_web`, que arma la URL correcta según el sitio
pedido y la abre en el navegador por defecto con `webbrowser` (stdlib).

IMPORTANTE (alcance): esta tool SOLO ABRE la búsqueda en el navegador. NO lee,
resume ni procesa los resultados. La respuesta hablada lo aclara siempre para
no dar a entender que Miku "sabe" la respuesta.

Sitios soportados (alias -> armado de URL):
    - mercadolibre -> https://listado.mercadolibre.com.ar/<termino-con-guiones>
    - youtube      -> https://www.youtube.com/results?search_query=<urlencoded>
    - google       -> https://www.google.com/search?q=<urlencoded>   (default)
    - wikipedia    -> https://es.wikipedia.org/w/index.php?search=<urlencoded>
    - github       -> https://github.com/search?q=<urlencoded>
    - otro con "." -> se trata como dominio/URL directa (best-effort)
"""
from __future__ import annotations

import logging
import re
import webbrowser
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.web_search")


class WebSearch(Plugin):
    """Abre búsquedas web y URLs directas en el navegador por defecto."""

    nombre = "web_search"
    descripcion = "Búsqueda/apertura de sitios web en el navegador."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "buscar_en_web",
                "description": "Abre el navegador con una búsqueda web o un "
                               "sitio. Solo ABRE la búsqueda, no lee los "
                               "resultados. Ej: 'buscá zapatillas en "
                               "mercadolibre', 'buscá lofi en youtube', "
                               "'buscá en wikipedia sobre agujeros negros'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "termino": {
                            "type": "string",
                            "description": "Qué buscar (o qué pegarle al "
                                           "dominio si es un sitio directo).",
                        },
                        "sitio": {
                            "type": "string",
                            "description": "Opcional: mercadolibre, youtube, "
                                           "google, wikipedia, github, o un "
                                           "dominio/URL (ej: 'infobae.com'). "
                                           "Si se omite, usa Google.",
                        },
                    },
                    "required": ["termino"],
                },
            },
        },
    ]

    # ---- Sitios conocidos: alias -> cómo armar la URL ----
    # Cada entrada es una función (termino -> url) para dejar lugar a reglas
    # particulares (p. ej. MercadoLibre usa el término con guiones).
    @staticmethod
    def _url_mercadolibre(termino_url: str, termino_encoded: str) -> str:
        return f"https://listado.mercadolibre.com.ar/{termino_url}"

    @staticmethod
    def _url_youtube(termino_url: str, termino_encoded: str) -> str:
        return f"https://www.youtube.com/results?search_query={termino_encoded}"

    @staticmethod
    def _url_google(termino_url: str, termino_encoded: str) -> str:
        return f"https://www.google.com/search?q={termino_encoded}"

    @staticmethod
    def _url_wikipedia(termino_url: str, termino_encoded: str) -> str:
        return ("https://es.wikipedia.org/w/index.php?search="
                f"{termino_encoded}")

    @staticmethod
    def _url_github(termino_url: str, termino_encoded: str) -> str:
        return f"https://github.com/search?q={termino_encoded}"

    def __init__(self) -> None:
        super().__init__()
        # Mapa de alias -> constructor de URL.
        self._constructores = {
            "mercadolibre": self._url_mercadolibre,
            "mercadolibre.com.ar": self._url_mercadolibre,
            "youtube": self._url_youtube,
            "google": self._url_google,
            "wikipedia": self._url_wikipedia,
            "github": self._url_github,
        }

    # ---------------- Ciclo de vida ----------------
    def initialize(self, event_bus: Any = None) -> None:
        """No requiere recursos: solo hereda la inicialización base."""
        super().initialize(event_bus)
        logger.info("Plugin web_search listo.")

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve la tool `buscar_en_web`."""
        if nombre_tool == "buscar_en_web":
            return self.buscar_en_web(
                str(args.get("termino", "") or ""),
                args.get("sitio"))
        return None

    # ---------------- Lógica principal ----------------
    @staticmethod
    def _termino_a_slug(termino: str) -> str:
        """Convierte el término a slug con guiones para MercadoLibre.

        Ej: "zapatillas nike" -> "zapatillas-nike".
        """
        t = (termino or "").strip().lower()
        # Quitamos todo lo que no sea alfanumérico o espacio.
        t = re.sub(r"[^\w\s]", "", t, flags=re.UNICODE)
        # Espacios (uno o más) -> un guion.
        return re.sub(r"\s+", "-", t).strip("-")

    def _armar_url(self, termino: str, sitio: Optional[str]) -> Optional[str]:
        """Arma la URL final según el sitio (o dominio directo).

        Devuelve None si por algún motivo no se puede armar.
        """
        termino = (termino or "").strip()
        sitio = (sitio or "").strip().lower()

        termino_encoded = quote_plus(termino)
        termino_url = self._termino_a_slug(termino)

        # 1) Sitio conocido por alias.
        constructor = self._constructores.get(sitio)
        if constructor is not None:
            return constructor(termino_url, termino_encoded)

        # 2) Sin sitio (o vacío) -> Google por default.
        if not sitio:
            return self._url_google(termino_url, termino_encoded)

        # 3) "Parece" un dominio/URL directa (best-effort): si tiene un punto,
        #    lo tratamos como dominio. Si además ya trae esquema, se respeta.
        #    NOTA: NO le pegamos "?q=" a dominios arbitrarios porque no todos
        #    los sitios soportan ese parámetro; abrimos el dominio tal cual.
        if "." in sitio:
            base = sitio
            if not base.startswith(("http://", "https://")):
                base = "https://" + base
            return base

        # 4) Sitio desconocido y sin punto -> Google con "termino sitio".
        return self._url_google(self._termino_a_slug(f"{termino} {sitio}"),
                                quote_plus(f"{termino} {sitio}"))

    def buscar_en_web(self, termino: str, sitio: Optional[str] = None) -> str:
        """Arma la URL y la abre en el navegador por defecto.

        Args:
            termino: Qué buscar (obligatorio).
            sitio: Alias de sitio o dominio/URL (opcional; default Google).

        Returns:
            Respuesta hablada que aclara QUÉ se abrió y QUE solo es la
            búsqueda (no se leen los resultados).
        """
        termino = (termino or "").strip()
        if not termino:
            return "¿Qué querés que busque?"

        url = self._armar_url(termino, sitio)
        if not url:
            return "No pude armar la búsqueda."

        try:
            abierto = webbrowser.open(url)
        except Exception:  # noqa: BLE001
            logger.exception("Error abriendo el navegador para '%s'.", url)
            abierto = False

        # Nombre legible del sitio para la confirmación hablada.
        nombre_sitio = self._nombre_sitio(sitio)

        if not abierto:
            # Algunos entornos devuelven False aunque el navegador abre; igual
            # intentamos ser claros y le pasamos la URL por si hay que abrirla.
            logger.warning("webbrowser.open devolvió False para '%s'.", url)
            return (f"No estoy segura de si se abrió, pero te dejé la "
                    f"búsqueda de '{termino}' en {nombre_sitio}: {url}")

        if sitio and self._es_busqueda(nombre_sitio):
            return (f"Te abrí la búsqueda de '{termino}' en {nombre_sitio}. "
                    f"Eso sí: solo abro la búsqueda, no leo los resultados.")
        if sitio:
            return (f"Te abrí {nombre_sitio}. Solo abro el sitio, no leo el "
                    f"contenido.")
        return (f"Te abrí la búsqueda de '{termino}' en Google. Solo abro la "
                f"búsqueda, no leo los resultados.")

    @staticmethod
    def _es_busqueda(nombre_sitio: str) -> bool:
        """True si el nombre de sitio corresponde a un buscador (no dominio)."""
        return nombre_sitio.lower() in (
            "mercadolibre", "youtube", "google", "wikipedia", "github")

    def _nombre_sitio(self, sitio: Optional[str]) -> str:
        """Nombre legible del sitio para la respuesta hablada."""
        sitio = (sitio or "").strip()
        if not sitio:
            return "Google"
        alias = {
            "mercadolibre": "MercadoLibre",
            "mercadolibre.com.ar": "MercadoLibre",
            "youtube": "YouTube",
            "google": "Google",
            "wikipedia": "Wikipedia",
            "github": "GitHub",
        }
        return alias.get(sitio.lower(), sitio)
