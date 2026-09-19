"""
enrutador.py - Elige qué tools mandarle al LLM en cada consulta.

Mandar las ~45 tools en cada pregunta cuesta unos 5 mil tokens (más latencia, más costo y peor
precisión con modelos chicos). El enrutador compara lo que dijo el usuario con el nombre y la
descripción de cada tool y se queda con las más relacionadas.

Diseñado para **no perder tools por error** (alto recall):
    * compara raíces de palabras (los primeros 4 caracteres), así "silenciá" encuentra "silenciar";
    * el nombre de la tool pesa más que su descripción;
    * hay un diccionario de sinónimos ("lanzá" -> abrir, "googleá" -> buscar, "bloqueá" -> energía);
    * si ninguna tool tiene una relación **clara** con lo dicho (charla, preguntas de conocimiento,
      respuestas cortas como "y en Chrome?" o palabras que no conoce), se mandan **todas**, como antes;
    * se queda con las ``maximo`` mejores (14 por defecto), no con un puñado.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Sequence, Set

from miku.plataforma.texto import normalizar

logger = logging.getLogger("miku.enrutador")

#: Palabras que no dicen de qué va la tool.
_VACIAS = frozenset("""
    de la el los las un una unos unas que para por con sin del al en y o a lo le les se su sus es son
    mi tu me te nos como cual cuales esto esta este eso esa ese muy mas menos todo toda todos todas
    ej ejemplo ejemplos opcional opcionales tool herramienta usa usar usas devuelve devuelva informa
    puede pueden si no ya hay ser sea cuando donde desde hasta sobre entre segun tambien solo
    nombre valor accion parametro texto tipo ahora mismo actual actuales activa activo
    pedi pedis quiero quiere queres hace hacer haz dale vamos poder podes
    estas estan estoy estamos hola chau gracias contame contar cuentame decime dime
""".split())

#: Cuántos caracteres de cada palabra se comparan (raíz aproximada).
_RAIZ = 4

#: Puntaje mínimo del mejor candidato para confiar en el enrutado (3 = coincidió con el NOMBRE de una
#: tool; coincidir solo con su descripción no alcanza y se mandan todas).
_CONFIANZA = 3

#: Sinónimos: raíz de lo que dice el usuario -> raíces que se buscan además en las tools.
_SINONIMOS: Dict[str, Sequence[str]] = {
    "lanz": ("abri",), "ejec": ("abri", "macr"), "inic": ("abri",), "corr": ("abri",), "arra": ("abri",),
    "prend": ("abri", "ener"), "cerr": ("cerr",), "mata": ("cerr",), "fina": ("cerr",), "salg": ("cerr",),
    "bloq": ("ener",), "hibe": ("ener",), "susp": ("ener",), "apag": ("ener", "cerr"), "reini": ("ener",),
    "goog": ("busc", "web"), "busc": ("busc", "web"), "inve": ("busc", "web"), "yout": ("busc", "web"),
    "mercad": ("busc", "web"), "wiki": ("busc", "web"),
    "fuer": ("volu",), "bajal": ("volu",), "subil": ("volu",), "sile": ("mute", "volu"), "mute": ("mute", "volu"),
    "sona": ("soni", "esta"), "canc": ("prog", "musi", "tema"), "tema": ("musi", "sono"),
    "pros": ("sigu",), "prox": ("sigu",), "sigu": ("sigu",), "tarea": ("tare",),
    "temp": ("clim",), "lluv": ("clim",), "pron": ("clim",), "calor": ("clim",), "frio": ("clim",),
    "scre": ("capt",), "foto": ("capt",), "ves": ("ver",), "mira": ("ver", "lee"), "lee": ("lee",),
    "compu": ("estad",), "pc": ("estad", "ener"), "memo": ("estad",), "cpu": ("estad",), "ram": ("estad",),
    "traduc": ("trad",), "idio": ("trad",), "ingle": ("trad",), "japo": ("trad",), "portu": ("trad",),
    "acor": ("recu", "guar"), "anot": ("recu", "guar"), "memor": ("recu",), "olvi": ("olvi",),
    "empe": ("olvi",),
    "monit": ("moni",), "pantalla": ("pant",), "resol": ("reso",),
}


def _raiz(palabra: str) -> str:
    return palabra[:_RAIZ]


def _palabras(texto: str) -> List[str]:
    """Palabras útiles (sin acentos, minúsculas, sin vacías ni cortas)."""
    limpio = normalizar(texto or "")
    return [p for p in re.findall(r"[a-z0-9ñ]+", limpio) if len(p) >= 3 and p not in _VACIAS]


def _senales(tool: dict) -> Dict[str, int]:
    """Raíces de la tool con su peso: 3 si están en el nombre, 1 si solo en la descripción."""
    funcion = tool.get("function", {})
    senales: Dict[str, int] = {}
    for p in _palabras(str(funcion.get("name", "")).replace("_", " ")):
        senales[_raiz(p)] = 3
    for p in _palabras(str(funcion.get("description", ""))):
        senales.setdefault(_raiz(p), 1)
    return senales


def puntuar(texto: str, tools: Sequence[dict]) -> List[int]:
    """Puntaje de cada tool para ``texto`` (mismo orden que ``tools``)."""
    raices: Set[str] = set()
    for p in _palabras(texto):
        raices.add(_raiz(p))
        for prefijo, extra in _SINONIMOS.items():
            if p.startswith(prefijo):
                raices.update(extra)
    puntajes: List[int] = []
    for tool in tools:
        senales = _senales(tool)
        puntajes.append(sum(peso for raiz, peso in senales.items() if raiz in raices))
    return puntajes


def seleccionar(texto: str, tools: List[dict], maximo: int = 14) -> List[dict]:
    """Devuelve las tools más relacionadas con ``texto`` (o todas si no hay relación clara).

    Args:
        texto: Lo que dijo el usuario.
        tools: Todas las tools disponibles.
        maximo: Cuántas mandar como mucho. 0 o menos desactiva el enrutado.
    """
    if maximo <= 0 or len(tools) <= maximo:
        return tools
    puntajes = puntuar(texto, tools)
    if max(puntajes) < _CONFIANZA:
        return tools
    orden = sorted(range(len(tools)), key=lambda i: (-puntajes[i], i))
    elegidas = sorted(i for i in orden[:maximo] if puntajes[i] > 0)
    logger.debug("Tools enviadas (%d de %d): %s", len(elegidas), len(tools),
                 [tools[i]["function"]["name"] for i in elegidas])
    return [tools[i] for i in elegidas]
