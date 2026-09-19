"""
openmeteo.py - Clima con Open-Meteo (gratis, sin clave): geocodificación y pronóstico.

Antes vivía dentro del plugin de clima; ahora lo usan también el briefing y el motor proactivo.
Todas las funciones devuelven None ante cualquier fallo de red (nunca lanzan).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("miku.plataforma.openmeteo")

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Horas hacia adelante que se miran para avisar "va a llover".
HORIZONTE_LLUVIA_H = 6

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
# Códigos que significan "está lloviendo/nevando ahora".
_CODIGOS_PRECIPITACION = frozenset(range(51, 100)) - {56, 57}


def describir_codigo(codigo: Any) -> str:
    """Traduce un código WMO a una frase corta en español."""
    try:
        return _CODIGOS.get(int(codigo), "no sé bien cómo está")
    except (TypeError, ValueError):
        return "no sé bien cómo está"


def primer_valor(valor: Any) -> Optional[float]:
    """Primer elemento si es lista (Open-Meteo devuelve listas); None si no hay número."""
    if isinstance(valor, list):
        valor = valor[0] if valor else None
    return valor if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


@dataclass
class Pronostico:
    """Clima actual y del día para un lugar."""

    nombre: str
    temperatura: Optional[float] = None
    sensacion: Optional[float] = None
    codigo: Optional[int] = None
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    prob_lluvia_dia: Optional[float] = None
    #: Mayor probabilidad de lluvia en las próximas horas y en cuántas horas ocurre: (prob, horas).
    proxima_lluvia: Optional[Tuple[float, int]] = None

    @property
    def esta_lloviendo(self) -> bool:
        """True si ahora mismo hay lluvia/nieve según el código WMO."""
        return self.codigo is not None and int(self.codigo) in _CODIGOS_PRECIPITACION

    @property
    def descripcion(self) -> str:
        """Cómo está el cielo, en una frase corta."""
        return describir_codigo(self.codigo)


def geocodificar(nombre: str, timeout: float = 8) -> Optional[Tuple[float, float, str]]:
    """Busca una ciudad. Devuelve ``(lat, lon, "Ciudad, País")`` o None."""
    if not nombre.strip():
        return None
    try:
        datos = requests.get(GEO_URL, params={"name": nombre, "count": 1, "language": "es",
                                              "format": "json"}, timeout=timeout).json()
        resultados = datos.get("results") or []
        if not resultados:
            return None
        r = resultados[0]
        etiqueta = f"{r.get('name', nombre)}, {r.get('country', '')}".strip(", ")
        return float(r["latitude"]), float(r["longitude"]), etiqueta
    except Exception as e:  # noqa: BLE001
        logger.error("Error geocodificando '%s': %s", nombre, e)
        return None


def resolver_ubicacion(cfg: Any, ciudad: str = "") -> Tuple[Optional[float], Optional[float], str]:
    """``(lat, lon, nombre)`` a consultar: ciudad pedida -> coordenadas de config -> ciudad de config.

    Devuelve ``(None, None, "")`` si no hay nada configurado o no se encuentra.
    """
    if not ciudad and cfg.clima_lat and cfg.clima_lon:
        try:
            return float(cfg.clima_lat), float(cfg.clima_lon), "tu zona"
        except (TypeError, ValueError):
            pass
    objetivo = ciudad.strip() or cfg.ciudad_clima
    if not objetivo:
        return None, None, ""
    encontrado = geocodificar(objetivo)
    return encontrado if encontrado else (None, None, "")


def _proxima_lluvia(datos: Dict[str, Any]) -> Optional[Tuple[float, int]]:
    """Mayor probabilidad de lluvia dentro de ``HORIZONTE_LLUVIA_H`` horas, con su distancia en horas."""
    horario = datos.get("hourly") or {}
    horas: List[str] = horario.get("time") or []
    probs: List[Any] = horario.get("precipitation_probability") or []
    ahora = str((datos.get("current") or {}).get("time", ""))[:13]
    if not horas or not probs:
        return None
    try:
        inicio = next(i for i, h in enumerate(horas) if h[:13] >= ahora)
    except StopIteration:
        return None
    mejor: Optional[Tuple[float, int]] = None
    for offset, valor in enumerate(probs[inicio:inicio + HORIZONTE_LLUVIA_H + 1]):
        if isinstance(valor, (int, float)) and (mejor is None or valor > mejor[0]):
            mejor = (float(valor), offset)
    return mejor


def obtener_pronostico(lat: float, lon: float, nombre: str = "tu zona",
                       timeout: float = 8) -> Optional[Pronostico]:
    """Clima actual, resumen del día y probabilidad de lluvia de las próximas horas."""
    try:
        datos = requests.get(FORECAST_URL, params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "hourly": "precipitation_probability",
            "timezone": "auto", "forecast_days": 2}, timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        logger.error("Error consultando el clima: %s", e)
        return None
    actual = datos.get("current") or {}
    if not actual:
        return None
    diario = datos.get("daily") or {}
    codigo = actual.get("weather_code")
    return Pronostico(
        nombre=nombre,
        temperatura=actual.get("temperature_2m"),
        sensacion=actual.get("apparent_temperature"),
        codigo=int(codigo) if isinstance(codigo, (int, float)) else None,
        temp_max=primer_valor(diario.get("temperature_2m_max")),
        temp_min=primer_valor(diario.get("temperature_2m_min")),
        prob_lluvia_dia=primer_valor(diario.get("precipitation_probability_max")),
        proxima_lluvia=_proxima_lluvia(datos),
    )
