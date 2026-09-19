"""
registro.py - Catálogo de plugins con carga perezosa.

Cada entrada dice en qué módulo (dentro de ``miku.plugins``) vive el plugin, qué clase es y (opcional) una
condición de configuración para instanciarlo. Los módulos se importan con
``importlib`` DENTRO de un ``try/except``:

    - Un plugin con un error de sintaxis o con una dependencia faltante NO
      tumba el arranque: se registra el error y se sigue con los demás.
    - Un plugin que sin credenciales no hace nada (Telegram, Game Booster,
      asistente proactivo) directamente no se importa.

El orden de la lista es el orden en que se consultan las tools.

Para agregar un plugin nuevo: crear ``miku/plugins/<tema>/<modulo>.py`` con una clase que
herede de ``plugins.Plugin`` y sumar una línea a ``_CATALOGO``.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, List, NamedTuple, Optional

from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.registro")


class _Entrada(NamedTuple):
    """Un plugin del catálogo."""

    modulo: str
    clase: str
    #: Condición sobre la config para cargarlo (None = siempre).
    condicion: Optional[Callable[[Any], bool]] = None


_CATALOGO: List[_Entrada] = [
    _Entrada("sistema.programas", "Programas"),
    _Entrada("sistema.archivos", "Archivos"),
    _Entrada("sistema.audio", "Audio"),
    _Entrada("sistema.energia", "Energia"),
    _Entrada("sistema.ventanas", "Ventanas"),
    _Entrada("navegacion.web", "WebSearch"),
    _Entrada("productividad.macros", "Macros"),
    _Entrada("productividad.video", "VideoInterpolador"),
    _Entrada("gaming.traductor", "TraductorJuegos"),
    _Entrada("social.discord_bot", "DiscordControl"),
    _Entrada("sistema.estado_pc", "SystemStatus"),
    _Entrada("productividad.favoritos", "Favoritos"),
    _Entrada("pantalla.captura", "Captura"),
    _Entrada("asistente.personalidad", "PersonalidadPlugin"),
    _Entrada("asistente.modos", "Modos"),
    _Entrada("productividad.clima", "Clima"),
    _Entrada("navegacion.brave", "Browser"),
    _Entrada("multimedia.tidal", "Tidal"),
    _Entrada("pantalla.ocr", "Ocr"),
    # Sin tools propias y sin efecto si no están configurados: ni se importan.
    _Entrada("gaming.game_booster", "GameBooster",
             lambda cfg: bool(cfg.juegos_booster)),
    _Entrada("pantalla.vision", "Vision"),
    _Entrada("productividad.todoist", "Todoist"),
    _Entrada("social.telegram_bot", "TelegramControl",
             lambda cfg: bool(cfg.telegram_bot_token)),
    _Entrada("asistente.proactivo", "AsistenteProactivo",
             lambda cfg: bool(cfg.proactivo_activo)),
]


def instanciar_plugins(cfg: Any) -> List[Plugin]:
    """Importa e instancia los plugins del catálogo que correspondan.

    Args:
        cfg: Config del asistente (para evaluar las condiciones de carga).

    Returns:
        Lista de plugins instanciados (todavía sin ``initialize``), en orden.
        Los que fallan al importarse/instanciarse se omiten con un log de error.
    """
    plugins: List[Plugin] = []
    for entrada in _CATALOGO:
        try:
            if entrada.condicion is not None and not entrada.condicion(cfg):
                logger.info("Plugin '%s' omitido (sin configuración).",
                            entrada.modulo)
                continue
            modulo = importlib.import_module(f"miku.plugins.{entrada.modulo}")
            plugins.append(getattr(modulo, entrada.clase)())
        except Exception:  # noqa: BLE001
            logger.exception("No pude cargar el plugin '%s'; sigo sin él.",
                             entrada.modulo)
    return plugins
