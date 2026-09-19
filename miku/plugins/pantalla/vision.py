# -*- coding: utf-8 -*-
"""
vision.py - Visión de pantalla con Gemini (Google AI Studio).

Publica las tools ``ver_pantalla`` / ``que_error_me_tira`` /
``que_hay_en_pantalla``. Captura la pantalla, la COMPRIME (redimensiona +
JPEG) y la envía a la API de Gemini con una pregunta en español.

Privacidad: la captura SALE de tu PC y se envía a Google (Gemini). Se avisa en
el mensaje de respuesta. Requiere ``GEMINI_API_KEY`` en config_local.py; sin
clave el plugin queda inactivo (no rompe el arranque). Todo con imports lazy.

Uso de la API: se usa el endpoint REST ``generateContent`` con la imagen en
base64 (sin SDK extra, solo `requests` + `Pillow`).
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any, Dict, List, Optional

import requests

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.vision")

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class Vision(Plugin):
    """Analiza la pantalla con Gemini y responde preguntas sobre lo que se ve."""

    nombre = "vision"
    descripcion = "Mira la pantalla con Gemini (errores, qué hay, leer cosas)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "ver_pantalla",
                "description": "Mira la pantalla y responde una pregunta sobre "
                               "lo que se ve (error, ventana, texto). Ej: "
                               "'qué error me tira', 'qué hay en pantalla'. "
                               "La captura se envía a Google (Gemini).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pregunta": {
                            "type": "string",
                            "description": "Qué querés saber de la pantalla.",
                        },
                    },
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin vision listo (%s).",
                    "con API key" if config_mod.config.gemini_api_key
                    else "sin GEMINI_API_KEY (inactivo)")

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool in ("ver_pantalla", "que_error_me_tira",
                           "que_hay_en_pantalla"):
            pregunta = str(args.get("pregunta", "") or "").strip()
            if not pregunta:
                pregunta = ("Describí qué se ve en la pantalla y, si hay un "
                            "error o mensaje importante, decime cuál es.")
            return self.ver_pantalla(pregunta)
        return None

    # ---------------- Acción principal ---------------- #
    def ver_pantalla(self, pregunta: str) -> str:
        """Captura la pantalla y pregunta a Gemini `pregunta` sobre ella."""
        clave = config_mod.config.gemini_api_key
        if not clave:
            return ("No tengo configurada la clave de Gemini para mirar la "
                    "pantalla. Agregá GEMINI_API_KEY en config_local.py.")

        img_b64 = self._capturar_comprimida()
        if img_b64 is None:
            return "No pude capturar la pantalla."

        modelo = config_mod.config.gemini_modelo
        # La clave va en un HEADER, no en la URL: una excepción de ``requests``
        # incluye la URL completa y la clave terminaba en los logs.
        url = f"{_BASE_URL}/{modelo}:generateContent"
        payload = {
            "contents": [{
                "parts": [
                    {"text": pregunta},
                    {"inline_data": {"mime_type": "image/jpeg",
                                     "data": img_b64}},
                ],
            }],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
        }
        try:
            resp = requests.post(url, json=payload, timeout=30,
                                 headers={"x-goog-api-key": clave})
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Error consultando a Gemini (%s).", type(e).__name__)
            return "No pude consultar la visión ahora mismo."

        texto = self._extraer_texto(data)
        if texto is None:
            logger.error("Respuesta inesperada de Gemini: %s", data)
            return "No pude leer la respuesta de la visión."
        # Avisamos que la imagen salió de la PC (privacidad).
        return f"{texto} (Ojo: la captura se envió a Google para analizarla.)"

    def _capturar_comprimida(self, max_lado: int = 1280,
                             calidad: int = 70) -> Optional[str]:
        """Captura la pantalla, la comprime y devuelve base64 (JPEG).

        Redimensiona para no gastar tokens/ancho de banda y devuelve el JPEG en
        base64 SIN el prefijo data:.
        """
        try:
            from PIL import ImageGrab  # import tardío
        except Exception:  # noqa: BLE001
            logger.error("Pillow no disponible para visión.")
            return None
        try:
            img = ImageGrab.grab(all_screens=True)
        except TypeError:
            try:
                img = ImageGrab.grab()
            except Exception as e:  # noqa: BLE001
                logger.error("Error capturando pantalla: %s", e)
                return None
        except Exception as e:  # noqa: BLE001
            logger.error("Error capturando pantalla: %s", e)
            return None

        try:
            # Redimensionado proporcional si supera `max_lado`.
            ancho, alto = img.size
            if max(ancho, alto) > max_lado:
                factor = max_lado / float(max(ancho, alto))
                img = img.resize((int(ancho * factor), int(alto * factor)))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=calidad)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as e:  # noqa: BLE001
            logger.error("No pude comprimir la captura: %s", e)
            return None

    @staticmethod
    def _extraer_texto(data: Dict[str, Any]) -> Optional[str]:
        """Extrae el texto de la respuesta de Gemini (o None si no hay)."""
        try:
            candidatos = data.get("candidates") or []
            if not candidatos:
                return None
            partes = candidatos[0].get("content", {}).get("parts", [])
            textos = [p.get("text", "") for p in partes if p.get("text")]
            resultado = " ".join(t.strip() for t in textos if t.strip())
            return resultado or None
        except Exception:  # noqa: BLE001
            return None