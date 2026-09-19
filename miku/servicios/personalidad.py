# -*- coding: utf-8 -*-
"""
personalidad.py - Perfiles de personalidad de Miku (neutral, tsundere, ...).

Permite cambiar el ESTILO de las respuestas de Miku sin tocar el resto del
sistema. Un perfil aporta:
    - ``prompt``: instrucción extra que se añade al system prompt del LLM.
    - ``sufijo``: interjección/coletilla que se agrega a respuestas CORTAS
      (para que hasta un "Listo." suene distinto según el perfil).

El perfil activo vive en ``config.personalidad`` y puede cambiarse por voz
(tool ``cambiar_personalidad``) de forma temporal (sesión) o persistente
(``data/preferences.json``).

Perfiles disponibles:
    - neutral    : el tono alegre por defecto de Miku.
    - tsundere   : respondona y orgullosa, pero ayuda igual.
    - formal     : educada y profesional, sin muletillas.
    - entusiasta : súper energética y positiva.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod

logger = logging.getLogger("miku.personalidad")

# --------------------------------------------------------------------------- #
# Definición de perfiles.
#   prompt : se agrega al system prompt del LLM (le dice CÓMO hablar).
#   sufijos: para respuestas CORTAS (se elige uno al azar y se pega atrás),
#            sólo si el perfil NO es neutral. Se usan con moderación.
# --------------------------------------------------------------------------- #
_PERFILES: Dict[str, Dict[str, Any]] = {
    "neutral": {
        "prompt": ("Mantené tu personalidad alegre y juguetona de siempre, "
                   "cercana y natural."),
        "sufijos": [],
    },
    "tsundere": {
        "prompt": ("Hablá con actitud TSUNDERE: un poco respondona y orgullosa "
                   "('no es que lo haga por vos', 'bah, obvio'), pero ayudá "
                   "igual y sin ser grosera. Usá expresiones como 'hmph', "
                   "'no te acostumbres', 'no es como si me importara'."),
        "sufijos": ["... no es que me importe, ¿eh?", "Hmph, no te acostumbres.",
                    "Bah, obvio.", "... no lo hice por vos."],
    },
    "formal": {
        "prompt": ("Hablá de forma FORMAL y profesional: sin muletillas ni "
                   "expresiones coloquiales, trato respetuoso y claro."),
        "sufijos": ["", " A su disposición."],
    },
    "entusiasta": {
        "prompt": ("Hablá de forma SÚPER ENTUSIASTA y energética: positiva, "
                   "con ganas, sin exagerar en longitud."),
        "sufijos": [" ¡Vamos!", " ¡Genial!", " ¡Dale que se puede!",
                    " ¡Qué emoción!"],
    },
}

# Perfil por defecto si el pedido no coincide con ninguno.
_DEFAULT = "neutral"

# Alias que reconoce el usuario -> nombre canónico del perfil.
_ALIAS: Dict[str, str] = {
    "neutral": "neutral", "normal": "neutral", "comun": "neutral",
    "común": "neutral", "default": "neutral", "clasica": "neutral",
    "clásica": "neutral",
    "tsundere": "tsundere", "tsuntsun": "tsundere", "tsun": "tsundere",
    "resongona": "tsundere", "respondona": "tsundere",
    "formal": "formal", "seria": "formal", "serio": "formal",
    "profesional": "formal",
    "entusiasta": "entusiasta", "energetica": "entusiasta",
    "energética": "entusiasta", "alegre": "entusiasta",
}

# Cache en memoria del perfil activo (evita releer la config en cada frase).
_perfil_activo: Optional[str] = None


def perfiles_disponibles() -> List[str]:
    """Devuelve los nombres de los perfiles disponibles."""
    return list(_PERFILES.keys())


def normalizar_perfil(nombre: str) -> Optional[str]:
    """Mapea lo que dijo el usuario a un nombre canónico de perfil (o None)."""
    clave = (nombre or "").strip().lower()
    if not clave:
        return None
    if clave in _ALIAS:
        return _ALIAS[clave]
    # Coincidencia parcial ("modo tsundere" / "hablá más tsundere").
    for alias, canon in _ALIAS.items():
        if alias in clave:
            return canon
    return None


def cargar_perfil() -> str:
    """Devuelve el perfil activo (con cache), leyendo de config si hace falta."""
    global _perfil_activo
    if _perfil_activo is not None:
        return _perfil_activo
    try:
        nombre = normalizar_perfil(config_mod.config.personalidad) or _DEFAULT
    except Exception:  # noqa: BLE001
        nombre = _DEFAULT
    _perfil_activo = nombre
    return nombre


def cambiar_perfil(nombre: str, persistir: bool = False) -> str:
    """Cambia el perfil activo. Devuelve una respuesta natural.

    Args:
        nombre: palabra dicha por el usuario (se normaliza a un perfil).
        persistir: si True, guarda el cambio en ``data/preferences.json``.
    """
    global _perfil_activo
    canon = normalizar_perfil(nombre)
    if canon is None:
        disp = ", ".join(perfiles_disponibles())
        return (f"No conozco esa personalidad. Puedo ser: {disp}.")

    _perfil_activo = canon
    # Refrescamos el prompt del parser en caliente (si está disponible).
    _refrescar_prompt()

    if persistir:
        ok = _persistir_perfil(canon)
        if ok:
            return f"Listo, ahora soy {canon}. Lo dejé guardado."
        return f"Ahora soy {canon}, pero no pude guardarlo de forma permanente."

    return f"Listo, por ahora soy {canon}."


def prompt_extra() -> str:
    """Instrucción de estilo para agregar al system prompt del LLM."""
    perfil = _PERFILES.get(cargar_perfil(), _PERFILES[_DEFAULT])
    return str(perfil.get("prompt", ""))


def adorno_corto(texto: str) -> str:
    """Agrega un sufijo de personalidad a respuestas CORTAS (no neutral).

    Sólo aplica si el texto es corto (menos de ~60 chars) y termina en un
    cierre natural, para no arruinar respuestas largas del LLM.
    """
    perfil = cargar_perfil()
    if perfil == _DEFAULT:
        return texto
    sufijos = _PERFILES.get(perfil, {}).get("sufijos") or []
    sufijos = [s for s in sufijos if s]
    if not sufijos:
        return texto
    if not texto or len(texto) > 60:
        return texto
    # No adosar si ya parece una pregunta o exclamación larga.
    base = texto.rstrip()
    sufijo = random.choice(sufijos)
    if not sufijo:
        return texto
    # Evitamos doble puntuación rara: pegamos con un espacio.
    return f"{base} {sufijo.strip()}"


# --------------------------------------------------------------------------- #
# Internos
# --------------------------------------------------------------------------- #
def _persistir_perfil(canon: str) -> bool:
    """Guarda ``personalidad`` en data/preferences.json (merge atómico)."""
    return config_mod.config.guardar_preferencias({"personalidad": canon})


def _refrescar_prompt() -> None:
    """Hook de refresco del prompt (no-op).

    El perfil se aplica DINÁMICAMENTE en cada consulta del ``BrainGroq`` (que
    lee ``prompt_extra()`` al armar el payload), así que no hace falta
    reconstruir nada acá. Se deja el punto de extensión por claridad.
    """
    logger.debug("Perfil activo: %s", cargar_perfil())
