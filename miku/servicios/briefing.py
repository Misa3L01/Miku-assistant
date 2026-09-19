# -*- coding: utf-8 -*-
"""
briefing.py - Saludo de arranque con contexto (hora + clima + recordatorios).

Reemplaza el "Ya estoy lista" seco del modo voz por una frase natural y corta
que resume: la hora, el clima (si hay ciudad configurada) y los recordatorios
pendientes del día (del Scheduler), en UNA sola frase.

Es best-effort: cada fuente (clima, scheduler) puede faltar y el briefing se
arma igual con lo que haya. Nunca debe romper el arranque.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Optional

from miku.voz.frases.respuesta import hubo_falla

logger = logging.getLogger("miku.briefing")


def generar(contexto: Optional[dict] = None) -> str:
    """Arma el saludo de arranque con contexto.

    Args:
        contexto: dict de runtime (puede traer "scheduler" para los
            recordatorios pendientes). Opcional.

    Returns:
        Una frase natural y corta (siempre no vacía).
    """
    partes: List[str] = []

    # 1) Saludo según la hora del día.
    partes.append(_saludo_hora())

    # 2) Hora actual (breve).
    hora = datetime.now().strftime("%H:%M")
    partes.append(f"son las {hora}")

    # 3) Clima (si hay ciudad/coords configuradas). Best-effort.
    clima = _clima_resumen()
    if clima:
        partes.append(clima)

    texto = ", ".join(partes).strip() + "."

    # 4) Recordatorios pendientes (del Scheduler).
    recordatorios = _recordatorios(contexto)
    if recordatorios:
        texto += " " + recordatorios

    return texto


def _saludo_hora() -> str:
    """Devuelve un saludo acorde a la franja horaria."""
    h = datetime.now().hour
    if h < 6:
        return "¡Buenas noches! Soy Miku"
    if h < 12:
        return "¡Buenos días! Soy Miku"
    if h < 20:
        return "¡Buenas tardes! Soy Miku"
    return "¡Buenas noches! Soy Miku"


def _clima_resumen() -> str:
    """Devuelve un resumen corto del clima, o "" si no se puede.

    Reutiliza el plugin de clima (Open-Meteo) SIN depender del LLM. Se importa
    de forma perezosa y se protege con try/except para no romper el arranque.
    """
    try:
        from miku.plugins.productividad.clima import Clima  # import tardío
        texto = Clima().consultar_clima()
    except Exception as e:  # noqa: BLE001
        logger.debug("Briefing sin clima: %s", e)
        return ""

    if not texto or hubo_falla(texto):
        return ""
    # Comprimimos a una frase corta: tomamos hasta el primer punto.
    corto = texto.split(".")[0].strip()
    return corto if corto else ""


def _recordatorios(contexto: Optional[dict]) -> str:
    """Arma un aviso con los recordatorios/acciones programadas pendientes."""
    if not contexto:
        return ""
    scheduler = contexto.get("scheduler")
    if scheduler is None:
        return ""
    try:
        pendientes = scheduler.listar_pendientes()
    except Exception as e:  # noqa: BLE001
        logger.debug("Briefing sin recordatorios: %s", e)
        return ""

    if not pendientes:
        return ""

    # Enumeramos hasta 3, con su tiempo restante aproximado.
    items = []
    for t in pendientes[:3]:
        desc = str(t.get("descripcion", "algo pendiente"))
        seg = t.get("segundos_restantes", 0) or 0
        items.append(f"{desc} (en {_formato_tiempo(seg)})")
    extra = "" if len(pendientes) <= 3 else \
        f" y {len(pendientes) - 3} más"
    if len(items) == 1:
        return f"Tenés pendiente: {items[0]}{extra}."
    return "Tenés pendientes: " + "; ".join(items) + f"{extra}."


def _formato_tiempo(segundos: Any) -> str:
    """Formatea segundos como 'X min' o 'X h Y min' (aprox.)."""
    try:
        seg = int(float(segundos))
    except (TypeError, ValueError):
        return "un rato"
    if seg < 60:
        return f"{seg} seg"
    minutos = seg // 60
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos // 60
    resto = minutos % 60
    if resto == 0:
        return f"{horas} h"
    return f"{horas} h {resto} min"