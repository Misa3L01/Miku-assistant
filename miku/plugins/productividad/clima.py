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
from typing import Any, Dict, List, Optional, Tuple

import requests

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.clima")

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


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

    # ---------------- Resolución de ubicación ---------------- #
    def _resolver_ubicacion(self, ciudad: str) -> Tuple[Optional[float],
                                                        Optional[float], str]:
        """Devuelve (lat, lon, nombre) de la ubicación a consultar.

        Prioridad: ciudad pasada por parámetro -> coords de config -> ciudad de
        config. Si no hay nada, devuelve (None, None, "").
        """
        cfg = config_mod.config

        # 1) Coordenadas directas de config (si no se pidió otra ciudad).
        if not ciudad and cfg.clima_lat and cfg.clima_lon:
            try:
                return (float(cfg.clima_lat), float(cfg.clima_lon), "tu zona")
            except (TypeError, ValueError):
                pass

        objetivo = ciudad.strip() or cfg.ciudad_clima
        if not objetivo:
            return (None, None, "")

        try:
            resp = requests.get(
                _GEO_URL,
                params={"name": objetivo, "count": 1, "language": "es",
                        "format": "json"},
                timeout=8)
            datos = resp.json()
            resultados = datos.get("results") or []
            if not resultados:
                return (None, None, "")
            r = resultados[0]
            nombre = r.get("name", objetivo)
            pais = r.get("country", "")
            etiqueta = f"{nombre}, {pais}".strip(", ")
            return (float(r["latitude"]), float(r["longitude"]), etiqueta)
        except Exception as e:  # noqa: BLE001
            logger.error("Error geocodificando '%s': %s", objetivo, e)
            return (None, None, "")

    # ---------------- Consulta principal ---------------- #
    def consultar_clima(self, ciudad: str = "") -> str:
        """Devuelve una frase natural con el clima actual y del día."""
        lat, lon, nombre = self._resolver_ubicacion(ciudad)
        if lat is None or lon is None:
            return ("No sé de qué ciudad me hablás. Configurá CIUDAD_CLIMA en "
                    "config_local.py o decime una ciudad.")

        try:
            resp = requests.get(
                _FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,apparent_temperature,"
                               "precipitation,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max,weather_code",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
                timeout=8)
            datos = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Error consultando el clima: %s", e)
            return "No pude consultar el clima ahora mismo."

        actual = datos.get("current") or {}
        diario = datos.get("daily") or {}
        if not actual:
            return "No pude obtener el clima ahora mismo."

        temp = actual.get("temperature_2m")
        sensacion = actual.get("apparent_temperature")
        codigo = actual.get("weather_code")

        temp_max = _primer(diario.get("temperature_2m_max"))
        temp_min = _primer(diario.get("temperature_2m_min"))
        prob_lluvia = _primer(diario.get("precipitation_probability_max"))

        descripcion = _describir_codigo(codigo)
        frase = [f"En {nombre} ahora {descripcion}"]
        if temp is not None:
            frase.append(f"con {round(temp)} grados")
        if temp_min is not None and temp_max is not None:
            frase.append(f"(hoy de {round(temp_min)} a {round(temp_max)})")
        if prob_lluvia is not None and prob_lluvia >= 40:
            frase.append(f"y hay {round(prob_lluvia)}% de probabilidad de lluvia")
        if sensacion is not None and temp is not None and abs(sensacion - temp) >= 3:
            frase.append(f"(se siente como {round(sensacion)})")

        texto = " ".join(frase).strip()
        if not texto.endswith("."):
            texto += "."
        recomendacion = _recomendacion(temp, sensacion, prob_lluvia)
        if recomendacion:
            texto += " " + recomendacion
        return texto


# --------------------------------------------------------------------------- #
# Helpers de formato
# --------------------------------------------------------------------------- #
def _primer(valor: Any) -> Optional[float]:
    """Toma el primer elemento si es lista (Open-Meteo devuelve listas)."""
    if isinstance(valor, list):
        return valor[0] if valor else None
    return valor if isinstance(valor, (int, float)) else None


# Códigos WMO -> descripción corta en español.
_CODIGOS: Dict[int, str] = {
    0: "está despejado", 1: "está mayormente despejado",
    2: "está parcialmente nublado", 3: "está nublado",
    45: "hay niebla", 48: "hay niebla con escarcha",
    51: "llovizna débil", 53: "llovizna", 55: "llovizna intensa",
    61: "llueve un poco", 63: "llueve", 65: "llueve fuerte",
    66: "llueve helado", 67: "llueve helado fuerte",
    71: "nieva levemente", 73: "nieva", 75: "nieva fuerte",
    77: "caen granos de nieve",
    80: "hay chaparrones", 81: "hay chaparrones fuertes",
    82: "hay chaparrones violentos",
    85: "nieva en ráfagas", 86: "nieva fuerte en ráfagas",
    95: "hay tormenta", 96: "hay tormenta con granizo",
    99: "hay tormenta fuerte con granizo",
}


def _describir_codigo(codigo: Any) -> str:
    """Traduce el código WMO a una frase corta en español."""
    try:
        return _CODIGOS.get(int(codigo), "no sé bien cómo está")
    except (TypeError, ValueError):
        return "no sé bien cómo está"


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