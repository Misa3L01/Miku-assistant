"""
traductor_juegos.py - Traduce mensajes predefinidos y los copia al portapapeles.

Idea de uso: en juegos (CS:GO, Genshin, etc.) muchos mensajes son fijos
("gg", "buena partida", "perdón, soy nuevo"). Este plugin tiene un DICCIONARIO
de frases configurables; cuando el usuario pide una ("mandá 'buena partida'"),
la traduce al idioma del juego y la **copia al portapapeles** para pegarla con
Ctrl+V en el chat del juego.

Config (config_local.py):
    - IDIOMA_JUEGO: idioma destino (ej. "inglés", "japonés", "portugués").
    - MENSAJES_JUEGO: dict {clave: frase_en_español} con los mensajes.
      Ejemplo:
          MENSAJES_JUEGO = {
              "gg": "buena partida",
              "gracias": "gracias por jugar",
              "nuevo": "perdón, soy nuevo",
          }

Tool publicada:
    - traducir_mensaje_juego(clave): traduce la frase asociada a `clave` y la
      copia al portapapeles.

Reutiliza ``core.traduccion.traducir_con_groq`` (el mismo cliente de Groq que
usa el TTS para ES->JA), con una cache en memoria por (frase, idioma).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.traductor_juegos")


def _copiar_al_portapapeles(texto: str) -> bool:
    """Copia `texto` al portapapeles de Windows (win32clipboard).

    Devuelve True si pudo. Importa ``win32clipboard`` de forma perezosa (solo
    existe en Windows). Maneja el formato CF_UNICODETEXT.
    """
    try:
        import win32clipboard  # type: ignore  # import tardío (Windows)
    except Exception as e:  # noqa: BLE001
        logger.warning("win32clipboard no disponible: %s", e)
        return False
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT,
                                            texto)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("No pude copiar al portapapeles: %s", e)
        return False


class TraductorJuegos(Plugin):
    """Traduce mensajes de juego preconfigurados y los copia al portapapeles."""

    nombre = "traductor_juegos"
    descripcion = ("Traduce mensajes de juego predefinidos y los copia al "
                   "portapapeles (config: IDIOMA_JUEGO / MENSAJES_JUEGO).")

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "traducir_mensaje_juego",
                "description": "Traduce un mensaje de juego predefinido al "
                               "idioma configurado y lo deja en el "
                               "portapapeles para pegar (Ctrl+V) en el chat "
                               "del juego. Ej: 'mandá buena partida', 'decí "
                               "gracias en el chat'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clave": {
                            "type": "string",
                            "description": "Clave o frase del mensaje a "
                                           "enviar (ej: 'buena partida').",
                        },
                    },
                    "required": ["clave"],
                },
            },
        },
    ]

    # Cache de traducciones por (frase, idioma) para no repetir llamadas.
    _cache: Dict[str, str] = {}

    # ---------------- Ciclo de vida ----------------
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin traductor_juegos listo.")

    # ---------------- Lectura de config ----------------
    def _leer_config(self) -> Dict[str, Any]:
        """Devuelve {'idioma': str, 'mensajes': dict} desde config (runtime)."""
        try:
            from miku.ajustes import carga as config_mod  # ruta segura
            config_mod.cargar()
            cfg = config_mod.config
            idioma = str(cfg.get("idioma_juego", "") or "").strip()
            mensajes = cfg.get("mensajes_juego", {}) or {}
            if not isinstance(mensajes, dict):
                logger.warning("mensajes_juego no es un dict; lo ignoro.")
                mensajes = {}
            return {"idioma": idioma, "mensajes": mensajes}
        except Exception as e:  # noqa: BLE001
            logger.error("No pude leer la config del traductor de juegos: %s", e)
            return {"idioma": "", "mensajes": {}}

    @staticmethod
    def _resolver_clave(clave: str, mensajes: Dict[str, Any]) -> Optional[str]:
        """Resuelve la frase en español a partir de la clave o la propia frase.

        Acepta:
          - una CLAVE del diccionario ("gg");
          - parte de una clave ("buen" -> "gg"/"buena partida");
          - directamente la frase en español ("buena partida").
        Devuelve la frase en español o None si no la encuentra.
        """
        clave = (clave or "").strip().lower()
        if not clave:
            return None
        # 1) Clave exacta.
        for k, v in mensajes.items():
            if str(k).lower() == clave:
                return str(v)
        # 2) Coincidencia por substring en la clave o en el valor.
        for k, v in mensajes.items():
            if clave in str(k).lower() or clave in str(v).lower():
                return str(v)
        return None

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "traducir_mensaje_juego":
            return self.traducir_mensaje_juego(str(args.get("clave", "") or ""))
        return None

    # ---------------- Lógica principal ----------------
    def traducir_mensaje_juego(self, clave: str) -> str:
        """Traduce el mensaje asociado a `clave` y lo copia al portapapeles.

        Flujo:
          1. Lee config (idioma + diccionario de mensajes).
          2. Resuelve la frase en español a partir de la clave.
          3. Traduce con Groq (``core.traduccion``) usando la cuenta principal
             (``groq_api_key``) — no la de STT.
          4. Copia el resultado al portapapeles (win32clipboard).

        Devuelve un mensaje natural en todos los casos (sin romper).
        """
        cfg = self._leer_config()
        idioma = cfg["idioma"]
        mensajes = cfg["mensajes"]

        if not idioma:
            return ("No tengo configurado el idioma del juego. Definí "
                    "IDIOMA_JUEGO en config_local.py (ej. 'inglés').")
        if not mensajes:
            return ("No tengo mensajes de juego configurados. Definí "
                    "MENSAJES_JUEGO en config_local.py.")

        frase_es = self._resolver_clave(clave, mensajes)
        if frase_es is None:
            disponibles = ", ".join(list(mensajes.keys())[:8])
            return (f"No tengo un mensaje para '{clave}'. Tengo estos: "
                    f"{disponibles}.")

        # Traducir (cache por frase+idioma). Usamos la cuenta PRINCIPAL (cerebro).
        cache_key = f"{frase_es}||{idioma}".lower()
        traduccion_txt = self._cache.get(cache_key)
        if traduccion_txt is None:
            try:
                from miku.voz.salida import traduccion  # import tardío
            except Exception:  # noqa: BLE001
                return "No tengo disponible el módulo de traducción."
            traduccion_txt = traduccion.traducir_con_groq(
                frase_es, idioma,
                api_key=str(self._api_key_principal() or ""),
                cache=self._cache_core())
            if not traduccion_txt:
                return ("No pude traducir el mensaje ahora mismo (revisá la "
                        "API key o la conexión).")
            self._cache[cache_key] = traduccion_txt

        # Copiar al portapapeles.
        if not _copiar_al_portapapeles(traduccion_txt):
            return (f"Traduje '{frase_es}' -> '{traduccion_txt}', pero no pude "
                    f"copiarlo al portapapeles (falta pywin32?).")

        return (f"Listo, copié al portapapeles el mensaje en {idioma}: "
                f"'{traduccion_txt}'. Pegalo con Ctrl+V.")

    def _api_key_principal(self) -> str:
        """Devuelve la groq_api_key (la del cerebro, NO la de STT)."""
        try:
            from miku.ajustes import carga as config_mod
            config_mod.cargar()
            return str(config_mod.config.groq_api_key or "")
        except Exception:  # noqa: BLE001
            return ""

    def _cache_core(self) -> Dict[str, str]:
        """Devuelve la cache interna del módulo de traducción (por texto).

        La usamos además para aprovechar el cacheo por texto normalizado que ya
        hace ``traducir_con_groq``. Es un dict vivo del módulo.
        """
        try:
            from miku.voz.salida import traduccion
            return traduccion.__dict__.setdefault("_CACHE_JUEGOS", {})
        except Exception:  # noqa: BLE001
            return {}
