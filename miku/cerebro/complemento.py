"""
complemento.py - Pedirle una respuesta de texto al LLM (sin herramientas ni historial).

Para tareas sueltas: traducir, resumir o corregir un texto. Usa el mismo servidor que el cerebro
(Groq por defecto, o el de ``LLM_BASE_URL``), así que respeta la configuración de proveedores.
Devuelve ``None`` ante cualquier fallo; quien lo llama decide qué decirle al usuario.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from miku.cerebro.parser import endpoint_llm
from miku.plataforma import red

logger = logging.getLogger("miku.cerebro.complemento")

_RE_PENSAMIENTO = re.compile(r"<think>.*?</think>", re.S | re.I)


def pedir_texto(cfg: Any, instruccion: str, texto: str, max_tokens: int = 700,
                temperatura: float = 0.2, timeout: float = 40.0) -> Optional[str]:
    """Aplica ``instruccion`` a ``texto`` con el LLM y devuelve la respuesta (None si falla).

    Args:
        instruccion: Qué hacer ("Traducí al inglés...", "Resumí en 2 oraciones...").
        texto: El texto sobre el que se trabaja.
    """
    destino = endpoint_llm(cfg)
    if destino["requiere_key"] and not destino["key"]:
        logger.error("Falta GROQ_API_KEY para esta tarea.")
        return None
    cabeceras = {"Content-Type": "application/json"}
    if destino["key"]:
        cabeceras["Authorization"] = f"Bearer {destino['key']}"
    try:
        resp = red.post(
            destino["url"], headers=cabeceras, timeout=timeout,
            json={"model": destino["modelo"], "temperature": temperatura, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": instruccion},
                               {"role": "user", "content": texto}]})
        datos = resp.json()
        contenido = str(datos["choices"][0]["message"].get("content") or "")
    except Exception as e:  # noqa: BLE001
        logger.error("El LLM no respondió (%s).", type(e).__name__)
        return None
    limpio = _RE_PENSAMIENTO.sub("", contenido).strip()
    return limpio or None
