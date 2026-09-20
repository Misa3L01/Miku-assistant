"""
complemento.py - Pedirle una respuesta de texto al LLM (sin herramientas ni historial).

Para tareas sueltas: traducir, resumir o corregir un texto. Usa el mismo servidor que el cerebro
(Groq por defecto, o el de ``LLM_BASE_URL``), así que respeta la configuración de proveedores.
Devuelve ``None`` ante cualquier fallo; quien lo llama decide qué decirle al usuario.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("miku.cerebro.complemento")

_RE_PENSAMIENTO = re.compile(r"<think>.*?</think>", re.S | re.I)


def _destino(cfg: Any) -> Dict[str, Any]:
    """URL, clave y modelo del LLM según la config (misma lógica que ``BrainGroq.endpoint``)."""
    base = str(cfg.get("llm_base_url", "") or "").strip().rstrip("/")
    modelo = str(cfg.get("llm_modelo", "") or "").strip() or cfg.modelo_api_externa
    if not base:
        return {"url": "https://api.groq.com/openai/v1/chat/completions",
                "key": str(cfg.groq_api_key).strip(), "modelo": modelo, "requiere_key": True}
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    return {"url": url, "key": str(cfg.get("llm_api_key", "") or "").strip(), "modelo": modelo,
            "requiere_key": False}


def pedir_texto(cfg: Any, instruccion: str, texto: str, max_tokens: int = 700,
                temperatura: float = 0.2, timeout: float = 40.0) -> Optional[str]:
    """Aplica ``instruccion`` a ``texto`` con el LLM y devuelve la respuesta (None si falla).

    Args:
        instruccion: Qué hacer ("Traducí al inglés...", "Resumí en 2 oraciones...").
        texto: El texto sobre el que se trabaja.
    """
    destino = _destino(cfg)
    if destino["requiere_key"] and not destino["key"]:
        logger.error("Falta GROQ_API_KEY para esta tarea.")
        return None
    cabeceras = {"Content-Type": "application/json"}
    if destino["key"]:
        cabeceras["Authorization"] = f"Bearer {destino['key']}"
    try:
        resp = requests.post(
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
