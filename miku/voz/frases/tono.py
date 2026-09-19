"""
tono.py - Variantes de tono para las respuestas CORTAS de Miku.

Problema que resuelve:
    Cuando Miku confirma algo rápido ("Listo", "Ya está", "Dale"), hoy siempre
    dice EXACTAMENTE la misma frase. Al ser frases cortas y frecuentes suenan
    robóticas y repetitivas.

Solución:
    Un catálogo de ``frase canónica -> lista de variantes equivalentes``.
    ``variar(texto)`` busca el texto (normalizado) en el catálogo y devuelve
    una variante AL AZAR, evitando repetir la última que se usó para esa clave
    (así dos "Listo" seguidos no salen idénticos).

Notas de diseño:
    - Sólo afecta respuestas CORTAS que coincidan EXACTAMENTE con una clave
      del catálogo. Las respuestas largas del LLM pasan intactas.
    - Las variantes están elegidas para que el sentido y el tono (alegre,
      juguetón de Miku) se mantengan y para que el subtítulo en español quede
      natural.
    - Es independiente del TTS: devuelve un string; quien lo use decide si lo
      habla o lo imprime.
"""
from __future__ import annotations

import logging
import random
import re
from typing import Dict, List

logger = logging.getLogger("miku.tono")

# --------------------------------------------------------------------------- #
# Catálogo de variantes.
#   clave: forma canónica NORMALIZADA (minúsculas, sin puntuación al final).
#   valor: lista de variantes (al menos 2). Se elige una al azar.
# --------------------------------------------------------------------------- #
_VARIANTES: Dict[str, List[str]] = {
    # Confirmaciones genéricas de "acción hecha".
    "listo": [
        "Listo.",
        "Ya está.",
        "Hecho.",
        "Listo, ya fue.",
        "Ahí está.",
        "Marchando.",
    ],
    "ya está": [
        "Ya está.",
        "Listo.",
        "Hecho.",
        "Todo listo.",
    ],
    "dale": [
        "Dale.",
        "Vamos.",
        "Ahí va.",
        "Marchando.",
    ],
    # Confirmación tras resolver algo.
    "cancelado, no hice nada": [
        "Cancelado, no hice nada.",
        "Listo, lo dejé pasar.",
        "Ok, no hice nada.",
    ],
    "listo, lo dejo": [
        "Listo, lo dejo.",
        "Ok, lo dejo.",
        "Bueno, lo dejo.",
    ],
    # Saludos / respuestas rápidas frecuentes del fast-path.
    "¡hola! ¿en qué te ayudo?": [
        "¡Hola! ¿En qué te ayudo?",
        "¡Hola! ¿Qué necesitás?",
        "¡Hey! ¿En qué te doy una mano?",
        "¡Holi! ¿Qué hacemos?",
    ],
    "sí? no escuché nada": [
        "Sí? No escuché nada.",
        "¿Mm? No te escuché.",
        "No llegué a escucharte, repetime.",
    ],
    "anotado, no me olvido": [
        "Anotado, no me olvido.",
        "Anotado, lo recordaré.",
        "Perfecto, lo tendré en cuenta.",
        "Listo, lo tengo presente.",
        "Ya lo guardé en mi memoria.",
        "Dale, me lo anoté.",
        "Anotado, quedate tranqui.",
        "Hecho, me voy a acordar.",
    ],
    # Memoria inactiva (cuando memoria_activa = false o falló el backend).
    "no tengo memoria activa en este modo": [
        "No tengo memoria activa en este modo.",
        "Uy, ahora mismo no tengo memoria encendida.",
        "Mi memoria está apagada en este modo.",
        "No puedo acordarme de eso: la memoria no está activa.",
    ],
    "la memoria no está activa": [
        "La memoria no está activa.",
        "Mi memoria está apagada ahora.",
        "No tengo memoria encendida en este modo.",
    ],
    # Wake word ("¿Sí? Decime."), lo setea miku/app.py.
    "¿sí? decime": [
        "¿Sí? Decime.",
        "¿Qué necesitás?",
        "Te escucho.",
        "¿Sí, decime?",
    ],
    # Saludo de arranque del modo voz ("Ya estoy lista").
    "ya estoy lista": [
        "Ya estoy lista.",
        "¡Ya estoy lista!",
        "Acá estoy.",
        "Estoy lista para escucharte.",
    ],
}

# Anti-repetición: última variante usada por clave.
_ultima_por_clave: Dict[str, str] = {}

# Puntuación final que ignoramos al normalizar (para que "Listo" y "Listo."
# sean la misma clave).
_PUNTUACION_FINAL = re.compile(r"[.!?…\s]+$")


def _normalizar_clave(texto: str) -> str:
    """Normaliza para buscar en el catálogo.

    Recorta, pasa a minúsculas y quita puntuación final. NO quita signos de
    apertura (¡¿) porque forman parte del tono elegido.
    """
    t = (texto or "").strip().lower()
    t = _PUNTUACION_FINAL.sub("", t)
    return t


# Las claves del catálogo se normalizan igual que las entradas: si no, las que
# terminan en "?" (p. ej. "¡hola! ¿en qué te ayudo?") nunca coincidían, porque
# ``_normalizar_clave`` le quita el "?" final al texto que llega.
_VARIANTES = {_normalizar_clave(k): v for k, v in _VARIANTES.items()}


def variar(texto: str) -> str:
    """Devuelve una variante de tono para `texto` si es una frase conocida.

    Reglas:
      - Si `texto` (normalizado) NO está en el catálogo, se devuelve tal cual.
      - Si está, se elige una variante al azar DISTINTA de la última usada para
        esa clave (cuando hay más de una opción).

    Esto mantiene el significado pero evita que Miku suene repetitiva.
    """
    if not texto:
        return texto

    clave = _normalizar_clave(texto)
    opciones = _VARIANTES.get(clave)
    if not opciones:
        return texto  # no es una frase corta conocida: no la tocamos.

    if len(opciones) == 1:
        return opciones[0]

    ultima = _ultima_por_clave.get(clave)
    # Candidatas: todas menos la última usada (para no repetir).
    candidatas = [o for o in opciones if o != ultima] or opciones
    elegida = random.choice(candidatas)
    _ultima_por_clave[clave] = elegida
    logger.debug("[Tono] '%s' -> variante: %r", clave, elegida)
    return elegida


def registrar_variantes(clave: str, variantes: List[str]) -> None:
    """Permite ampliar el catálogo en runtime (útil para tests/plugins).

    `clave` se normaliza igual que en ``variar`` para no duplicar entradas.
    """
    if not variantes:
        return
    _VARIANTES[_normalizar_clave(clave)] = list(variantes)


# --------------------------------------------------------------------------- #
# Variantes registradas de respuestas ANTES hardcodeadas (plugins).
#
# Estas frases las produce `miku/plugins/sistema/` y quedaban siempre
# EXACTAMENTE iguales (el bug reportado: "canción anterior" -> "Volví a la
# anterior" repetitivo). Se registran acá, con el mismo tono informal de
# "listo"/"dale", para que `variar()` las intercepte. Se llama DESPUÉS de
# definir `registrar_variantes` (requisito de orden de ejecución en Python).
# --------------------------------------------------------------------------- #
# Multimedia (control_multimedia).
registrar_variantes("alterné play/pausa.", [
    "Alterné play/pausa.",
    "Dale, le di al play/pausa.",
    "Listo, play o pausa.",
    "Ahí va el play/pausa.",
])
registrar_variantes("pasé a la siguiente.", [
    "Pasé a la siguiente.",
    "Siguiente tema.",
    "Dale, salté a la siguiente.",
    "Ahí va la que sigue.",
])
registrar_variantes("volví a la anterior.", [
    "Volví a la anterior.",
    "Tema anterior.",
    "Dale, volví a la de antes.",
    "Ahí va la anterior.",
])
# Volumen (ajustar_volumen).
registrar_variantes("subí el volumen.", [
    "Subí el volumen.",
    "Dale, más ruido.",
    "Listo, subí el volumen.",
    "Ahí subí el volumen.",
])
registrar_variantes("bajé el volumen.", [
    "Bajé el volumen.",
    "Listo, más bajito.",
    "Dale, bajé el volumen.",
    "Ahí lo bajé.",
])
registrar_variantes("silencié la salida de audio.", [
    "Silencié la salida de audio.",
    "Listo, silencio total.",
    "Dale, sin sonido.",
    "Ahí quedó en mute.",
])
registrar_variantes("reactivé el sonido.", [
    "Reactivé el sonido.",
    "Listo, sonido de vuelta.",
    "Dale, ya se escucha.",
    "Ahí volvió el sonido.",
])
# Energía (control_energia) — respuestas fijas que también quedaban sueltas.
registrar_variantes("voy a suspender la pc.", [
    "Voy a suspender la PC.",
    "Listo, suspendo la PC.",
    "Dale, a dormir la PC.",
    "Ahí suspendo la PC.",
])
