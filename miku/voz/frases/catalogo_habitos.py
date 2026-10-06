"""
catalogo_habitos.py - La ruleta de frases del control de hábitos.

Cuando Miku te ve en una página de tu lista te rezonga con una de estas frases y pregunta si la cierra. Hay muchas
variantes por situación y el banco no repite la anterior, así no suena a disco rayado. Todas las preguntas
terminan en "¿la cierro?" (o parecido) para que alcance con contestar "sí" o "no".

``{pagina}`` es la palabra de tu lista tal como la escribiste ("facebook"); ``{veces}`` cuántas veces te agarró hoy.
Importar el módulo registra el catálogo (``registrar()`` es idempotente).
"""
from __future__ import annotations

from typing import Dict, List

from miku.voz.frases.banco import Banco, frases

CATALOGO: Dict[str, List[str]] = {
    # --------------------------------------------- la primera vez (la pregunta)
    "habito.regano": [
        "¿Qué hacés viendo {pagina}? ¿La cierro?",
        "¿Por qué estás viendo {pagina}? Dale, ¿la cierro?",
        "Eh, ¿{pagina}? ¿Qué hacés ahí? ¿La cierro?",
        "Te agarré mirando {pagina}. ¿La cierro?",
        "¿Y eso de {pagina}? ¿Seguro que no tenías algo más importante? ¿La cierro?",
        "No me gusta que mires {pagina}. ¿La cierro?",
        "¿Eso es {pagina}? Lo voy a cerrar, ¿sí?",
        "Mmm, {pagina}. Yo creo que tenés cosas mejores para hacer. ¿La cierro?",
        "Pará, pará, ¿qué es eso de {pagina}? ¿La cierro?",
        "¿Querés estar en {pagina} ahora, en serio? ¿La cierro?",
        "A ver, a ver... ¿{pagina}? ¿Qué estamos haciendo? ¿La cierro?",
        "Ey, vi que entraste a {pagina}. ¿La cierro y seguimos con lo tuyo?",
        "¿{pagina}? ¿Ahora? Mirá que me preocupo. ¿La cierro?",
        "Che, ¿qué hacés en {pagina}? Decime que sí y la cierro.",
    ],
    # --------------------------------------------- la reincidencia (ya van varias veces hoy)
    "habito.otra_vez": [
        "¡Otra vez {pagina}! Ya van {veces} veces hoy. ¿La cierro?",
        "{pagina} de nuevo... ya van {veces} hoy. ¿La cierro?",
        "Che, {veces} veces hoy en {pagina}. ¿Y si la cierro?",
        "¿Otra vez con {pagina}? Dale, ¿la cierro?",
        "Ya van {veces} veces con {pagina} hoy. ¿La cierro de una vez?",
        "No aprendés más, ¿eh? {pagina} otra vez. ¿La cierro?",
        "Vuelta a {pagina}. Van {veces} hoy, ¿la cierro o qué?",
        "Te lo vengo diciendo: {pagina} no. Van {veces} hoy. ¿La cierro?",
    ],
    # --------------------------------------------- ventana de incógnito (sin importar la página)
    "habito.incognito": [
        "Una ventana de incógnito... ¿qué estás escondiendo? ¿La cierro?",
        "¿Incógnito? ¿Qué hacés ahí? ¿La cierro?",
        "Te vi en incógnito. Eso no me da buena espina. ¿La cierro?",
        "¿Por qué estás en incógnito? ¿La cierro?",
        "Ventana de incógnito detectada. ¿La cierro?",
        "Mmm, incógnito... ¿me contás? ¿Mientras tanto la cierro?",
        "¿Navegando a escondidas? ¿La cierro?",
    ],
    # --------------------------------------------- dijiste que sí y se cerró
    "habito.cerrada": [
        "Listo, cerrada. Mejor así.",
        "Cerrada. De nada.",
        "Chau {pagina}. A lo tuyo.",
        "Ya está, la cerré. Seguí con lo importante.",
        "Cerrada. ¡A seguir con lo tuyo!",
        "Hecho. Era lo mejor, confiá en mí.",
        "Ya la cerré. Orgullosa de vos.",
        "Fuera {pagina}. Ahora sí, a laburar.",
    ],
    # --------------------------------------------- dijiste que no
    "habito.perdon": [
        "Bueno, esta vez te dejo. Pero te estoy mirando.",
        "Ok, vos sabrás. Después no te quejes.",
        "Está bien... pero lo voy a recordar.",
        "Como quieras. Yo avisé.",
        "Dale, no la cierro. Igual no me convence.",
        "Ah, bueno. Disfrutá, supongo.",
        "Está bien, pero no tardes mucho, ¿eh?",
        "Ok, te dejo. Pero a la próxima no tengo piedad.",
    ],
    # --------------------------------------------- no se pudo cerrar
    "habito.no_pude": [
        "No pude cerrarla. Cerrala vos, dale.",
        "Se me escapó la ventana, no logré cerrarla. Hacelo vos.",
        "Uy, no pude cerrarla. Te toca a vos.",
        "Intenté cerrarla y no me dejó. Cerrala vos, por favor.",
    ],
    # --------------------------------------------- ya la habías cerrado o cambiado
    "habito.ya_no": [
        "Ya no estás en esa página, listo.",
        "Ya la cambiaste, bien ahí.",
        "Parece que ya saliste de esa página. Bien.",
        "Ya no la veo, así que está todo bien.",
    ],
}

#: Título del cartel (toast) de cada situación, por si se muestra como notificación.
TITULOS: Dict[str, str] = {
    "habito.regano": "Hábitos",
    "habito.otra_vez": "Hábitos",
    "habito.incognito": "Hábitos",
    "habito.cerrada": "Hábitos",
    "habito.perdon": "Hábitos",
    "habito.no_pude": "Hábitos",
    "habito.ya_no": "Hábitos",
}


def registrar(banco: Banco = frases) -> None:
    """Carga este catálogo en el banco (se puede llamar varias veces)."""
    banco.registrar_varias(CATALOGO)


registrar()
