"""
catalogo_ideas.py - Frases de las ideas guardadas (guardar, recordar, avisar que no se pudo).

Varias formulaciones por situación: el banco rota sin repetir la anterior. ``{titulo}`` es el título que le puso la
visión a la idea; ``{cuando}`` el momento del recordatorio ("mañana a las 18", "el sábado a las 11").
Importar el módulo registra el catálogo (``registrar()`` es idempotente).
"""
from __future__ import annotations

from typing import Dict, List

from miku.voz.frases.banco import Banco, frases

CATALOGO: Dict[str, List[str]] = {
    # ------------------------------------------------ la guardé y te la recuerdo
    "idea.guardada": [
        "Guardada: {titulo}. Te la recuerdo {cuando}.",
        "Listo, guardé la idea: {titulo}. Te aviso {cuando}.",
        "Anotada: {titulo}. Te la recuerdo {cuando}, quedate tranqui.",
        "Ya la guardé: {titulo}. {cuando}, te lo recuerdo.",
        "Hecho. Guardé {titulo} y te la recuerdo {cuando}.",
    ],
    # ------------------------------------------------ la guardé sin recordatorio
    "idea.guardada_sin_aviso": [
        "Guardada: {titulo}. No te la recuerdo, pero queda en tu lista.",
        "Listo, guardé {titulo} sin recordatorio. Pedime tus ideas cuando quieras.",
        "Anotada: {titulo}. Sin aviso, la dejo en tu lista.",
    ],
    # ------------------------------------------------ llegó la hora de recordártela
    "idea.recordatorio": [
        "Che, te acordaba de una idea que guardaste: {titulo}.",
        "¿Te acordás de {titulo}? Me pediste que te la recordara hoy.",
        "Recordatorio de idea: {titulo}. ¿La miramos?",
        "Ey, hoy tocaba revisar esto que guardaste: {titulo}.",
        "No te me olvides: guardaste una idea, {titulo}. Te la dejé en el celular.",
        "Es la hora de tu idea guardada: {titulo}.",
    ],
    # ------------------------------------------------ cambié el recordatorio
    "idea.recordar": [
        "Dale, te recuerdo {titulo} {cuando}.",
        "Listo, {titulo} te la recuerdo {cuando}.",
        "Cambiado. Te aviso de {titulo} {cuando}.",
    ],
    "idea.sin_aviso": [
        "Ok, {titulo} queda sin recordatorio, pero la sigo guardando.",
        "Listo, sin aviso para {titulo}. Sigue en tu lista.",
    ],
    # ------------------------------------------------ cerrar
    "idea.hecha": [
        "Marcada como vista: {titulo}. ¡Una menos!",
        "Perfecto, {titulo} ya está revisada.",
        "Listo, saqué {titulo} de tus pendientes.",
    ],
    "idea.descartada": [
        "Descartada: {titulo}.",
        "Ok, tiré {titulo} a la basura.",
        "Chau {titulo}, ya no está en tu lista.",
    ],
    # ------------------------------------------------ cosas que salieron mal
    "idea.sin_captura": [
        "No encuentro ninguna captura reciente. Sacá una con Win + Shift + S y probá de nuevo.",
        "No veo capturas guardadas ni nada copiado. Sacá una captura y decime de nuevo.",
    ],
    "idea.sin_vision": [
        "Guardé la idea, pero no pude leer la imagen para ponerle título (la visión no está disponible).",
        "La guardé, pero sin título: no pude analizar la imagen ahora.",
    ],
    "idea.no_entendi_cuando": [
        "No entendí cuándo. Decime por ejemplo mañana, el sábado o en una semana.",
        "¿Cuándo te la recuerdo? Probá con mañana, el finde o en una semana.",
    ],
    "idea.no_hay": [
        "No tenés ideas pendientes. Pasame una captura cuando veas algo.",
        "Tu lista de ideas está vacía.",
    ],
    "idea.no_existe": [
        "Esa idea ya no está en tu lista.",
        "No encuentro esa idea.",
    ],
}

TITULOS: Dict[str, str] = {clave: "Ideas" for clave in CATALOGO}


def registrar(banco: Banco = frases) -> None:
    """Carga este catálogo en el banco (se puede llamar varias veces)."""
    banco.registrar_varias(CATALOGO)


registrar()
