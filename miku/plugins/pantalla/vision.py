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
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import falla

logger = logging.getLogger("miku.plugins.vision")

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
#: Alias de Google que siempre apunta al Flash vigente (los modelos con número se retiran).
MODELO_ALIAS = "gemini-flash-latest"
#: Modelo de Groq que también ve imágenes (respaldo si Gemini falla).
MODELO_GROQ = "qwen/qwen3.8-27b"
#: Segundos que se aparta a Gemini tras un error de clave/cuota/créditos (para no perder tiempo en cada pedido).
_APARTAR_GEMINI_S = 600.0


def _modelo_no_disponible(data: Any) -> bool:
    """True si la respuesta de Gemini dice que el modelo no existe / fue retirado."""
    error = data.get("error") if isinstance(data, dict) else None
    return bool(error) and (error.get("status") == "NOT_FOUND" or error.get("code") == 404)


class Vision(Plugin):
    """Analiza la pantalla con Gemini y responde preguntas sobre lo que se ve."""

    nombre = "vision"
    descripcion = "Mira la pantalla con Gemini (errores, qué hay, leer cosas)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "ver_pantalla",
                "description": "Mira la pantalla (o un monitor puntual) y responde una "
                               "pregunta sobre lo que se ve (error, ventana, texto). Ej: "
                               "'qué error me tira', 'qué hay en pantalla', 'qué estoy viendo en "
                               "el monitor 2'. La captura se envía a Google (Gemini).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pregunta": {
                            "type": "string",
                            "description": "Qué querés saber de la pantalla.",
                        },
                        "monitor": {
                            "type": "integer",
                            "description": "Número de monitor a mirar (1, 2...). Opcional: sin esto "
                                           "se miran todos juntos.",
                        },
                    },
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._gemini_caido_hasta = 0.0

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
            monitor = args.get("monitor")
            try:
                monitor = int(monitor) if monitor not in (None, "") else None
            except (TypeError, ValueError):
                monitor = None
            return self.ver_pantalla(pregunta, monitor)
        return None

    # ---------------- Acción principal ---------------- #
    def ver_pantalla(self, pregunta: str, monitor: Optional[int] = None) -> str:
        """Captura la pantalla (o el ``monitor`` pedido) y pregunta ``pregunta`` sobre ella.

        Proveedor (``VISION_PROVEEDOR``): ``auto`` (por defecto) prueba Gemini y, si falla por clave,
        cuota o créditos, sigue con un modelo de Groq que también ve imágenes; ``gemini`` o ``groq``
        fuerzan uno solo.
        """
        cfg = config_mod.config
        proveedor = str(cfg.get("vision_proveedor", "auto") or "auto").strip().lower()
        usar_gemini = proveedor in ("auto", "gemini") and bool(cfg.gemini_api_key) \
            and time.monotonic() >= self._gemini_caido_hasta
        usar_groq = proveedor in ("auto", "groq") and bool(str(cfg.groq_api_key).strip())
        if not usar_gemini and not usar_groq:
            if proveedor == "gemini" or not cfg.gemini_api_key and proveedor == "auto":
                return ("No tengo con qué mirar la pantalla. Agregá GEMINI_API_KEY (o GROQ_API_KEY) "
                        "en config_local.py.")
            return "Ahora no puedo mirar la pantalla: Gemini no está disponible y no hay clave de Groq."

        bbox = None
        if monitor is not None:
            from miku.plugins.pantalla.captura import Captura
            bbox = Captura._bbox_monitor(monitor)
            if bbox is None:
                return falla("captura.monitor_invalido", monitor=monitor)
        img_b64 = self._capturar_comprimida(bbox=bbox)
        if img_b64 is None:
            return "No pude capturar la pantalla."

        problema = ""
        if usar_gemini:
            texto, problema = self._via_gemini(pregunta, img_b64)
            if texto:
                return f"{texto} (Ojo: la captura se envió a Google para analizarla.)"
        if usar_groq:
            texto = self._via_groq(pregunta, img_b64)
            if texto:
                return f"{texto} (Ojo: la captura se envió a Groq para analizarla.)"
        return problema or "No pude leer la respuesta de la visión."

    # ---------------- Proveedores ---------------- #
    def _via_gemini(self, pregunta: str, img_b64: str) -> Tuple[Optional[str], str]:
        """``(texto, mensaje de problema)``. Si Gemini falla por clave/cuota/créditos se lo aparta 10 min."""
        cfg = config_mod.config
        clave, modelo = cfg.gemini_api_key, cfg.gemini_modelo
        payload = {
            "contents": [{"parts": [{"text": pregunta},
                                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
        }
        data = self._consultar(modelo, payload, clave)
        if data is None:
            return None, "No pude consultar la visión ahora mismo."
        if _modelo_no_disponible(data) and modelo != MODELO_ALIAS:
            # Google retira modelos con el tiempo (gemini-2.0-flash ya no existe): se reintenta una
            # vez con el alias que siempre apunta al Flash vigente.
            logger.warning("El modelo '%s' ya no está disponible; reintento con '%s'. Ponelo en "
                           "GEMINI_MODELO para no perder tiempo.", modelo, MODELO_ALIAS)
            data = self._consultar(MODELO_ALIAS, payload, clave)
            if data is None:
                return None, "No pude consultar la visión ahora mismo."

        texto = self._extraer_texto(data)
        if texto is not None:
            return texto, ""
        logger.error("Respuesta inesperada de Gemini: %s", data)
        error = (data.get("error") or {}) if isinstance(data, dict) else {}
        codigo, estado = error.get("code"), error.get("status")
        if estado in ("PERMISSION_DENIED", "UNAUTHENTICATED") or codigo in (401, 403):
            self._gemini_caido_hasta = time.monotonic() + _APARTAR_GEMINI_S
            return None, "Gemini rechazó mi clave. Revisá GEMINI_API_KEY en config_local.py."
        if codigo in (402, 429) or estado == "RESOURCE_EXHAUSTED":
            self._gemini_caido_hasta = time.monotonic() + _APARTAR_GEMINI_S
            return None, ("Gemini no me deja usarlo ahora (se acabaron los créditos o el límite de uso). "
                          "Revisá tu cuenta de Google AI Studio.")
        if _modelo_no_disponible(data):
            return None, "El modelo de visión configurado ya no existe. Poné GEMINI_MODELO = 'gemini-flash-latest'."
        return None, "No pude leer la respuesta de la visión."

    def _via_groq(self, pregunta: str, img_b64: str) -> Optional[str]:
        """Pregunta a un modelo de Groq que ve imágenes (formato OpenAI). None si falla."""
        cfg = config_mod.config
        modelo = str(cfg.get("vision_modelo_groq", "") or "").strip() or MODELO_GROQ
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {str(cfg.groq_api_key).strip()}"},
                json={"model": modelo, "max_tokens": 500, "temperature": 0.3,
                      "messages": [{"role": "user", "content": [
                          {"type": "text", "text": pregunta + " Respondé en español, breve y sin markdown."},
                          {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]},
                timeout=45)
            data = resp.json()
            texto = (data["choices"][0]["message"].get("content") or "").strip()
            return texto or None
        except Exception as e:  # noqa: BLE001
            logger.error("La visión por Groq falló (%s).", type(e).__name__)
            return None

    @staticmethod
    def _consultar(modelo: str, payload: Dict[str, Any], clave: str) -> Optional[Dict[str, Any]]:
        """Llama a ``generateContent``; None si falla la red.

        La clave va en un HEADER, no en la URL: una excepción de ``requests`` incluye la URL completa
        y la clave terminaba en los logs.
        """
        try:
            resp = requests.post(f"{_BASE_URL}/{modelo}:generateContent", json=payload, timeout=30,
                                 headers={"x-goog-api-key": clave})
            return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Error consultando a Gemini (%s).", type(e).__name__)
            return None

    def _capturar_comprimida(self, max_lado: int = 1280, calidad: int = 70,
                             bbox: Optional[tuple] = None) -> Optional[str]:
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
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            try:
                img = ImageGrab.grab(bbox=bbox)
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