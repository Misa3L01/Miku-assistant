"""
traduccion.py - traducción por API de Groq, compartida entre features.

Antes, la traducción ES->JA para VOICEVOX vivía en ``core/text_to_speech.py``.
Con la llegada del **traductor para juegos** (mensajes predefinidos CS:GO /
Genshin), la lógica de "pegarle a Groq para traducir" se centraliza acá para
no duplicar:

    - ``traducir_con_groq(texto, idioma_destino, api_key, cache=None)``:
      hace UNA llamada a chat/completions de Groq con un prompt que pide solo
      la traducción (sin comillas ni comentarios). Devuelve el texto traducido
      o None si no se pudo (sin API key, error de red/HTTP, respuesta vacía).

    - ``normalizar_para_traducir(texto)``: limpia signos que el traductor suele
      malinterpretar (¿ ¡ … comillas tipográficas) sin tocar el texto original.

Diseño:
    - ``requests`` se importa a nivel de módulo (ya es dependencia dura del
      proyecto).
    - La función NO guarda cache persistente: si el caller quiere cache, le
      pasa un ``dict`` en ``cache`` y se usa con la clave = texto normalizado.
    - Toda excepción se captura y se devuelve None (el caller decide el fallback).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional

import requests

logger = logging.getLogger("miku.traduccion")

# URL y modelo por defecto (Groq chat/completions).
_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODELO_TRADUCCION = "openai/gpt-oss-20b"


def normalizar_para_traducir(texto: Optional[str]) -> str:
    """Prepara el texto para el traductor (NO afecta lo que se muestra).

    El traductor (Groq) a veces se confunde con signos de apertura y otros
    símbolos que no existen en japonés/otros idiomas. Los reemplazamos por
    equivalentes neutros SOLO en la copia que se manda a traducir:
      - "¿" -> "" y "?" se mantiene (el "?" de cierre ya cierra la pregunta).
      - "¡" -> "" (el "!" de cierre ya está).
      - "…" -> "..." (puntos suspensivos ASCII).
      - comillas tipográficas -> comillas rectas.
      - espacios múltiples colapsados.
    """
    if not texto:
        return ""
    t = texto
    t = t.replace("¿", "").replace("¡", "")
    t = t.replace("…", "...")
    t = (t.replace("\u201c", "\"").replace("\u201d", "\"")
         .replace("\u2018", "'").replace("\u2019", "'"))
    t = re.sub(r"\s+", " ", t).strip()
    return t


def traducir_con_groq(texto: str, idioma_destino: str, api_key: str,
                      cache: Optional[Dict[str, str]] = None,
                      timeout: int = 20) -> Optional[str]:
    """Traduce `texto` a `idioma_destino` usando la API de Groq.

    Args:
        texto: texto a traducir (en español, típicamente).
        idioma_destino: nombre del idioma destino (ej. "japonés", "inglés",
            "portugués"). Se usa tal cual dentro del prompt.
        api_key: clave de Groq. Si está vacía, devuelve None sin llamar.
        cache: dict opcional {texto_normalizado: traduccion}. Si se pasa, se lee
            antes de llamar y se escribe luego (evita repetir llamadas).
        timeout: segundos de timeout HTTP.

    Returns:
        La traducción (str) o None si falló (sin key, red, HTTP != 200, vacío).
    """
    texto_limpio = normalizar_para_traducir(texto)
    if not texto_limpio:
        return None

    # Cache (si la hay): clave = texto normalizado exacto.
    if cache is not None:
        cacheado = cache.get(texto_limpio)
        if cacheado:
            logger.debug("[Traducción] Cache hit: %r", texto_limpio[:40])
            return cacheado

    key = str(api_key or "").strip()
    if not key:
        logger.info("[Traducción] Sin API key para traducir; se omite.")
        return None

    prompt = (f"Traducí el siguiente texto al {idioma_destino}. Devolvé SOLO "
              f"la traducción, sin comillas, sin comentarios, sin texto "
              f"adicional: " + texto_limpio)
    try:
        resp = requests.post(
            _GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "model": _MODELO_TRADUCCION,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.info("[Traducción] Groq falló: HTTP %s. Cuerpo: %s",
                        resp.status_code, (resp.text or "")[:200])
            return None
        data = resp.json()
        trad = (data.get("choices", [{}])[0]
                .get("message", {}).get("content") or "").strip()
        if not trad:
            logger.info("[Traducción] Groq devolvió vacío para %r",
                        texto_limpio[:40])
            return None
        if cache is not None:
            cache[texto_limpio] = trad
        logger.debug("[Traducción] Groq OK: %r -> %r",
                     texto_limpio[:40], trad[:40])
        return trad
    except Exception as e:  # noqa: BLE001
        logger.info("[Traducción] Error de red traduciendo con Groq: %s",
                    str(e)[:160])
        return None
