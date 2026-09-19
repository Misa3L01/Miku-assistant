"""
respuesta.py - Resultados de tools que llevan *qué pasó* además del texto.

Antes las tools devolvían texto fijo y otras partes del código dependían de ese texto
(``res.startswith("No encontré")``): cambiar una frase podía romper lógica. Ahora una tool puede
devolver una ``Respuesta``: es un ``str`` (todo lo que consume texto sigue funcionando: el parser, el
LLM, el TTS) que además sabe si salió bien (``ok``), su ``intencion`` y los ``datos``.

    from miku.voz.frases.respuesta import falla, exito, hubo_falla

    return falla("ventana.no_encontrada", app=nombre_app)     # frase variada, ok=False
    ...
    if hubo_falla(resultado): ...                             # sin mirar el texto

La frase sale del banco (``miku/voz/frases/banco.py``), que rota las variantes sin repetir la anterior.
Una tool que siga devolviendo un ``str`` común sigue siendo válida (se la considera exitosa).
"""
from __future__ import annotations

from typing import Any, Dict

from miku.voz.frases import catalogo_respuestas
from miku.voz.frases.banco import frases


class Respuesta(str):
    """Texto de respuesta con metadatos. Se comporta como un ``str`` normal."""

    ok: bool
    intencion: str
    datos: Dict[str, Any]

    def __new__(cls, texto: str, ok: bool = True, intencion: str = "",
                datos: Dict[str, Any] | None = None) -> "Respuesta":
        obj = super().__new__(cls, texto)
        obj.ok = ok
        obj.intencion = intencion
        obj.datos = dict(datos or {})
        return obj


def responder(intencion: str, ok: bool = True, **datos: Any) -> Respuesta:
    """Arma una ``Respuesta`` con una variante de la frase de ``intencion``."""
    catalogo_respuestas.registrar()
    return Respuesta(frases.elegir(intencion, **datos), ok, intencion, datos)


def exito(intencion: str, **datos: Any) -> Respuesta:
    """Respuesta de algo que salió bien."""
    return responder(intencion, True, **datos)


def falla(intencion: str, **datos: Any) -> Respuesta:
    """Respuesta de algo que no salió (``ok=False``)."""
    return responder(intencion, False, **datos)


def hubo_falla(resultado: Any) -> bool:
    """True si ``resultado`` es una ``Respuesta`` fallida. Un ``str`` común cuenta como éxito."""
    return isinstance(resultado, Respuesta) and not resultado.ok
