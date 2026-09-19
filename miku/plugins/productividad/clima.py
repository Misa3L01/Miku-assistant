# -*- coding: utf-8 -*-
"""
clima.py - Clima con contexto (Open-Meteo, sin API key).

Publica la tool ``clima`` (alias ``como_esta_el_clima`` / ``va_a_llover``).
Usa **Open-Meteo** (https://open-meteo.com), que es gratis y NO requiere clave.

Flujo:
    1. Resolver la ubicación: coordenadas de config_local (CLIMA_LAT/CLIMA_LON)
       o geocodificar la ciudad (CIUDAD_CLIMA) con la Geocoding API de Open-Meteo.
    2. Pedir el pronóstico actual (+ mín/máx del día y probabilidad de lluvia).
    3. Armar una frase NATURAL con recomendación ("hace frío, llevate campera").

Todo con ``requests`` (ya presente), imports minimalistas y manejo de error
claro (nunca rompe el arranque ni la conversación).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from miku.ajustes import carga as config_mod
from miku.plataforma import openmeteo
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import falla

logger = logging.getLogger("miku.plugins.clima")


class Clima(Plugin):
    """Informa el clima actual y del día con una recomendación natural."""

    nombre = "clima"
    descripcion = "Clima actual y del día (Open-Meteo), con recomendación."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "clima",
                "description": "Informa el clima actual y del día (temperatura, "
                               "si va a llover, etc.) con una recomendación "
                               "natural (llevar campera, paraguas...). Ej: "
                               "'¿cómo está el clima?', 'va a llover hoy?', "
                               "'qué temperatura hace'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ciudad": {
                            "type": "string",
                            "description": "Ciudad a consultar (opcional; si "
                                           "se omite, usa la configurada).",
                        },
                    },
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        cfg = config_mod.config
        ubic = cfg.ciudad_clima or (
            f"{cfg.clima_lat},{cfg.clima_lon}" if cfg.clima_lat and cfg.clima_lon
            else "(sin configurar)")
        logger.info("Plugin clima listo (ubicación: %s).", ubic)

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool in ("clima", "como_esta_el_clima", "va_a_llover"):
            return self.consultar_clima(str(args.get("ciudad", "") or ""))
        return None

    # ---------------- Consulta principal ---------------- #
    def consultar_clima(self, ciudad: str = "") -> str:
        """Devuelve una frase natural con el clima actual y del día."""
        lat, lon, nombre = openmeteo.resolver_ubicacion(config_mod.config, ciudad)
        if lat is None or lon is None:
            return falla("clima.sin_ciudad")

        p = openmeteo.obtener_pronostico(lat, lon, nombre)
        if p is None:
            return falla("clima.sin_datos")

        frase = [f"En {p.nombre} ahora {p.descripcion}"]
        if p.temperatura is not None:
            frase.append(f"con {round(p.temperatura)} grados")
        if p.temp_min is not None and p.temp_max is not None:
            frase.append(f"(hoy de {round(p.temp_min)} a {round(p.temp_max)})")
        if p.prob_lluvia_dia is not None and p.prob_lluvia_dia >= 40:
            frase.append(f"y hay {round(p.prob_lluvia_dia)}% de probabilidad de lluvia")
        if (p.sensacion is not None and p.temperatura is not None
                and abs(p.sensacion - p.temperatura) >= 3):
            frase.append(f"(se siente como {round(p.sensacion)})")

        texto = " ".join(frase).strip()
        if not texto.endswith("."):
            texto += "."
        recomendacion = _recomendacion(p.temperatura, p.sensacion, p.prob_lluvia_dia)
        if recomendacion:
            texto += " " + recomendacion
        return texto


def _recomendacion(temp: Any, sensacion: Any, prob_lluvia: Any) -> str:
    """Frase natural de recomendación según temperatura/lluvia."""
    consejos: List[str] = []
    referencia = sensacion if isinstance(sensacion, (int, float)) else temp

    if isinstance(referencia, (int, float)):
        if referencia <= 5:
            consejos.append("hace frío, abrigate bien")
        elif referencia <= 13:
            consejos.append("hace fresco, llevate algo abrigado")
        elif referencia >= 30:
            consejos.append("hace mucho calor, tomá agua")
        elif referencia >= 25:
            consejos.append("hace calor, andá liviano")

    if isinstance(prob_lluvia, (int, float)) and prob_lluvia >= 40:
        consejos.append("llevate paraguas por las dudas")

    if not consejos:
        return ""
    return "Te diría que " + " y ".join(consejos) + "."
