"""
catalogo_proactivo.py - Frases de los avisos proactivos (clima y estado de la PC).

Varias formulaciones por intención: el banco rota sin repetir la anterior, así Miku no suena a
disco rayado. Los ``{datos}`` los completa cada regla (ver ``miku/servicios/reglas_proactivas.py``).
Importar el módulo registra el catálogo (``registrar()`` es idempotente).
"""
from __future__ import annotations

from typing import Dict, List

from miku.voz.frases.banco import Banco, frases

CATALOGO: Dict[str, List[str]] = {
    # ------------------------------------------------------------------- clima
    "clima.lluvia": [
        "Ojo que {cuando} se viene la lluvia, con un {prob}% de probabilidad. Llevate el paraguas si salís.",
        "Te aviso que {cuando} podría llover ({prob}%). Por si salís, agarrá un paraguas.",
        "Parece que {cuando} va a llover, hay un {prob}% de chances. No te quedes sin paraguas.",
        "Che, {cuando} pinta lluvia: {prob}% de probabilidad. Mejor salir con paraguas.",
    ],
    "clima.lloviendo": [
        "Está lloviendo ahora mismo. Si tenés que salir, llevate paraguas.",
        "Afuera está lloviendo. Paraguas si vas a salir.",
        "Ahora mismo llueve afuera, así que abrigate y no te olvides del paraguas.",
    ],
    "clima.frio": [
        "Hoy hace frío, se siente como {sensacion} grados. Abrigate bien.",
        "Ojo que afuera hace bastante frío, sensación de {sensacion} grados. Llevate campera.",
        "Te aviso que hoy está fresquito: {sensacion} grados de sensación. Salí abrigado.",
        "Hace frío, {sensacion} grados de sensación térmica. No salgas de remera.",
    ],
    "clima.calor": [
        "Hoy hace mucho calor, se siente como {sensacion} grados. Tomá agua y evitá el sol fuerte.",
        "Ojo con el calor: sensación de {sensacion} grados. Hidratate.",
        "Te aviso que afuera hay calor fuerte, {sensacion} grados de sensación. Tomá bastante agua.",
        "Está pesado el día, {sensacion} grados de sensación. Andá liviano y tomá agua.",
    ],

    # --------------------------------------------------------------- estado PC
    "pc.juego_tranquilo": [
        "Arrancaste {juego}. Todo tranquilo: {detalle}.",
        "Listo con {juego}. La PC anda bien, {detalle}.",
        "{juego} en marcha. Estado de la PC: {detalle}. Que te diviertas.",
        "Ya estás en {juego}. Por ahora todo en orden, {detalle}.",
    ],
    "pc.juego_exigente": [
        "Empezó {juego} y la PC va exigida: {detalle}. Conviene cerrar lo que no uses.",
        "Ojo que con {juego} la PC anda pesada, {detalle}. Cerrá lo que no necesites.",
        "{juego} está pidiendo bastante: {detalle}. Fijate si podés cerrar algo.",
    ],
    "pc.carga": [
        "La {recurso} está muy cargada hace un rato ({valor}%). Puede que algo esté trabado.",
        "Hace varios minutos que la {recurso} anda en {valor}%. Revisá qué la está usando.",
        "Ojo, la {recurso} sigue en {valor}%. Capaz conviene cerrar alguna aplicación.",
    ],
    "pc.gpu_caliente": [
        "La placa de video está en {temp} grados. Ojo con el calor, revisá la ventilación.",
        "Cuidado: la GPU llegó a {temp} grados. Conviene bajar los gráficos o ventilar mejor.",
        "La GPU está muy caliente, {temp} grados. Fijate que el aire circule bien.",
    ],
    "pc.bateria": [
        "Te queda {porcentaje}% de batería. Conectá el cargador cuando puedas.",
        "Batería baja: {porcentaje}%. Enchufá el cargador.",
        "Ojo que la batería está en {porcentaje}%. Cargala pronto.",
    ],
    "briefing.texto": ["{texto}"],
    "comedor.es_hora": [
        "Ya son más de las {hora}: si querés, decime 'comedor' y te inscribo para mañana.",
        "Ya podés inscribirte al comedor de mañana. Decime 'comedor' y me encargo.",
        "Recordatorio del comedor: falta inscribirse para mañana. Decime 'comedor'.",
    ],
    "comedor.auto": [
        "Son las {hora}: voy a inscribirte al comedor de mañana. Se abre una ventana, no la toques.",
        "Hora del comedor: te inscribo para mañana. Se abre una ventana aparte.",
        "Me pongo con el comedor de mañana. Dejá la ventana que se abre trabajar.",
    ],
    "briefing.vuelta": [
        "Bienvenido de vuelta",
        "Ya volviste",
        "Qué bueno verte de nuevo",
        "Volviste",
    ],
    "pc.disco": [
        "Te quedan {libre} GB libres en el disco. Conviene liberar espacio.",
        "Poco espacio en disco: {libre} GB libres. Hacé un poco de limpieza.",
        "El disco se está llenando, quedan {libre} GB. Fijate qué podés borrar.",
    ],
}

#: Título del cartel (toast) por prefijo de intención.
TITULOS: Dict[str, str] = {
    "clima.lluvia": "Va a llover",
    "clima.lloviendo": "Está lloviendo",
    "clima.frio": "Hace frío",
    "clima.calor": "Hace calor",
    "pc.juego_tranquilo": "Estado de la PC",
    "pc.juego_exigente": "La PC va exigida",
    "pc.carga": "PC muy cargada",
    "pc.gpu_caliente": "GPU muy caliente",
    "pc.bateria": "Batería baja",
    "pc.disco": "Poco espacio en disco",
    "briefing.texto": "Miku",
    "comedor.es_hora": "Comedor",
    "comedor.auto": "Comedor",
    "briefing.vuelta": "Miku",
}


def registrar(banco: Banco = frases) -> None:
    """Carga este catálogo en el banco (se puede llamar varias veces)."""
    banco.registrar_varias(CATALOGO)


registrar()
