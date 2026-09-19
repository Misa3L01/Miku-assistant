"""
recordatorios.py - Cómo se dice un recordatorio y cómo se avisan los que se pasaron.

Lo comparten el plugin de energía (que los programa) y la app (que los recupera al arrancar).
Un recordatorio persistente se guarda como ``{"tipo": "recordatorio", "mensaje": "sacar la pizza"}``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from miku.voz.frases.banco import frases

logger = logging.getLogger("miku.recordatorios")

TIPO = "recordatorio"

frases.registrar_varias({
    "recordatorio.aviso": [
        "¡Recordatorio! {mensaje}",
        "Ojo, te acordaba esto: {mensaje}",
        "Es hora: {mensaje}",
    ],
    "recordatorio.perdido_uno": [
        "Mientras yo no estaba se te pasó un recordatorio: {mensajes}.",
        "Ojo, un recordatorio venció mientras estaba cerrada: {mensajes}.",
    ],
    "recordatorio.perdido_varios": [
        "Mientras yo no estaba se te pasaron {cantidad} recordatorios: {mensajes}.",
        "Tenías {cantidad} recordatorios que vencieron con Miku cerrada: {mensajes}.",
    ],
})


def datos_de(mensaje: str) -> Dict[str, Any]:
    """Datos persistibles de un recordatorio."""
    return {"tipo": TIPO, "mensaje": mensaje}


def fabricar_callback(obtener_voz: Callable[[], Any], datos: Dict[str, Any]) -> Callable[[], None]:
    """Callback que dice el recordatorio por voz (o lo imprime si no hay voz).

    ``obtener_voz`` se llama al **dispararse**, no al programar: así funciona aunque la voz se haya
    creado después (o el recordatorio venga de una sesión anterior).
    """
    mensaje = str(datos.get("mensaje") or "un recordatorio")

    def _callback() -> None:
        frase = frases.elegir("recordatorio.aviso", mensaje=mensaje)
        voz = obtener_voz()
        if voz is not None:
            try:
                voz.decir(frase)
                return
            except Exception:  # noqa: BLE001
                logger.exception("No pude decir el recordatorio por voz.")
        print(f"[Recordatorio] {mensaje}")

    return _callback


def texto_perdidos(perdidos: List[Dict[str, Any]]) -> str:
    """Frase para avisar los recordatorios que vencieron con Miku cerrada ("" si no hay)."""
    mensajes = [str(d.get("mensaje") or "").strip() for d in perdidos if d.get("tipo") == TIPO]
    mensajes = [m for m in mensajes if m]
    if not mensajes:
        return ""
    clave = "recordatorio.perdido_uno" if len(mensajes) == 1 else "recordatorio.perdido_varios"
    return frases.elegir(clave, cantidad=len(mensajes), mensajes="; ".join(mensajes))
