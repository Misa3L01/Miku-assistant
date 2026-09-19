"""
traductor.py - Traduce un texto a CUALQUIER idioma y lo deja en el portapapeles.

Nació para juegos (mensajes rápidos en CS2, Genshin…), pero sirve para cualquier cosa: el juego
era solo el contexto. Pedís "traducí 'buena partida' al portugués" o "decí gracias en japonés" y el
resultado queda en el portapapeles para pegarlo con Ctrl+V donde quieras. **No hace nada más**: no
escribe en ninguna ventana ni manda el mensaje.

Config (config_local.py, todas opcionales):
    - IDIOMA_JUEGO: idioma por defecto cuando no decís uno ("inglés", "japonés"…).
    - PERFILES_JUEGO: idioma por juego, {"cs2": "portugués", "genshinimpact": "inglés"}. Si estás
      dentro de uno de esos juegos y no decís idioma, se usa el suyo.
    - MENSAJES_JUEGO: atajos {clave: frase en español}. Decir la clave ("gg") usa la frase asociada.
          MENSAJES_JUEGO = {"gg": "buena partida", "nuevo": "perdón, soy nuevo"}

Tool publicada:
    - traducir_mensaje_juego(texto, idioma?): traduce ``texto`` (o la frase del atajo) y lo copia al
      portapapeles. Idioma: el que digas > el perfil del juego en primer plano > IDIOMA_JUEGO.

Reutiliza ``miku.voz.salida.traduccion.traducir_con_groq`` (el cliente de Groq que usa también el TTS).
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.traductor_juegos")

#: Traducciones recordadas en memoria (frase, idioma) -> texto; tope para no crecer sin límite.
_CACHE_MAX = 200


def _copiar_al_portapapeles(texto: str) -> bool:
    """Copia ``texto`` al portapapeles de Windows (CF_UNICODETEXT). True si pudo."""
    try:
        import win32clipboard  # type: ignore  # import tardío (Windows)
    except Exception as e:  # noqa: BLE001
        logger.warning("win32clipboard no disponible: %s", e)
        return False
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, texto)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("No pude copiar al portapapeles: %s", e)
        return False


def resolver_atajo(texto: str, mensajes: Dict[str, Any]) -> str:
    """Devuelve la frase en español de un atajo, o ``texto`` tal cual si no es un atajo.

    Solo cuenta la coincidencia **exacta** con una clave ("gg") o con una frase ya definida: con
    texto libre una coincidencia parcial traduciría otra cosa de la que pediste.
    """
    buscado = (texto or "").strip().lower()
    for clave, frase in mensajes.items():
        if str(clave).strip().lower() == buscado:
            return str(frase)
    return (texto or "").strip()


class TraductorJuegos(Plugin):
    """Traduce texto a cualquier idioma y lo deja en el portapapeles."""

    nombre = "traductor_juegos"
    descripcion = ("Traduce un texto a cualquier idioma y lo copia al portapapeles "
                   "(config opcional: IDIOMA_JUEGO / MENSAJES_JUEGO).")

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "traducir_mensaje_juego",
                "description": "Traduce un texto (o un atajo como 'gg') a CUALQUIER idioma y lo deja "
                               "SOLO en el portapapeles para pegarlo con Ctrl+V; no lo escribe ni lo "
                               "envía. Ej: 'traducí buena partida al portugués', 'decí gracias en "
                               "japonés', 'mandá gg' (usa el idioma por defecto).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "texto": {
                            "type": "string",
                            "description": "Lo que hay que traducir (una frase cualquiera) o un atajo "
                                           "configurado (ej: 'gg').",
                        },
                        "idioma": {
                            "type": "string",
                            "description": "Idioma destino en español ('inglés', 'portugués', "
                                           "'japonés'…). Opcional: si se omite se usa el idioma por "
                                           "defecto configurado.",
                        },
                    },
                    "required": ["texto"],
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cache: "OrderedDict[str, str]" = OrderedDict()

    # ---------------- Ciclo de vida ----------------
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin traductor_juegos listo.")

    # ---------------- Lectura de config ----------------
    @staticmethod
    def _idioma_del_juego_actual(perfiles: Any) -> str:
        """Idioma del perfil del juego en primer plano ("" si no hay juego o no tiene perfil)."""
        if not isinstance(perfiles, dict) or not perfiles:
            return ""
        try:
            from miku.plataforma import procesos
            juego = procesos.proceso_primer_plano()
        except Exception:  # noqa: BLE001
            return ""
        if not juego:
            return ""
        for proceso, idioma in perfiles.items():
            if str(proceso).lower().removesuffix(".exe") == juego:
                return str(idioma or "").strip()
        return ""

    @classmethod
    def _leer_config(cls) -> Tuple[str, Dict[str, Any], str]:
        """``(idioma por defecto, atajos, clave de Groq)`` de la config viva.

        El "idioma por defecto" ya incluye el perfil del juego en primer plano (si lo hay).
        """
        try:
            from miku.ajustes import carga as config_mod
            cfg = config_mod.config
            mensajes = cfg.get("mensajes_juego", {}) or {}
            if not isinstance(mensajes, dict):
                logger.warning("mensajes_juego no es un dict; lo ignoro.")
                mensajes = {}
            idioma = (cls._idioma_del_juego_actual(cfg.get("perfiles_juego"))
                      or str(cfg.get("idioma_juego", "") or "").strip())
            return idioma, mensajes, str(cfg.groq_api_key or "")
        except Exception as e:  # noqa: BLE001
            logger.error("No pude leer la config del traductor: %s", e)
            return "", {}, ""

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "traducir_mensaje_juego":
            # "clave" era el nombre del parámetro antes de que aceptara texto libre.
            texto = str(args.get("texto") or args.get("clave") or "")
            return self.traducir_mensaje_juego(texto, str(args.get("idioma") or ""))
        return None

    # ---------------- Lógica principal ----------------
    def _traducir(self, frase: str, idioma: str, api_key: str) -> Optional[str]:
        """Traduce con Groq recordando el resultado (LRU acotado)."""
        clave = f"{frase}||{idioma}".lower()
        if clave in self._cache:
            self._cache.move_to_end(clave)
            return self._cache[clave]
        try:
            from miku.voz.salida import traduccion
        except Exception:  # noqa: BLE001
            return None
        resultado = traduccion.traducir_con_groq(frase, idioma, api_key=api_key)
        if resultado:
            self._cache[clave] = resultado
            while len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
        return resultado

    def traducir_mensaje_juego(self, texto: str, idioma: str = "") -> str:
        """Traduce ``texto`` (o su atajo) a ``idioma`` y lo copia al portapapeles.

        Devuelve siempre una respuesta hablable (``Respuesta``); nunca lanza.
        """
        idioma_defecto, mensajes, api_key = self._leer_config()
        idioma = (idioma or "").strip() or idioma_defecto
        if not idioma:
            return falla("traductor.sin_idioma")
        frase = resolver_atajo(texto, mensajes)
        if not frase:
            return falla("traductor.sin_texto")

        traduccion_txt = self._traducir(frase, idioma, api_key)
        if not traduccion_txt:
            return falla("traductor.error")
        if not _copiar_al_portapapeles(traduccion_txt):
            return falla("traductor.sin_portapapeles", traduccion=traduccion_txt)
        return exito("traductor.copiado", idioma=idioma, traduccion=traduccion_txt)
