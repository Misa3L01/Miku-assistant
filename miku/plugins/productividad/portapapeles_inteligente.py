# -*- coding: utf-8 -*-
"""
portapapeles_inteligente.py - Hacer cosas útiles con lo que acabás de copiar.

    "Traducí lo que copié al inglés"      -> el portapapeles queda con la traducción (pegás con Ctrl+V)
    "Corregí lo que copié"                -> queda el texto corregido
    "Reescribí lo que copié más formal"   -> queda el texto reescrito
    "Resumí lo que copié"                 -> Miku te lo cuenta en voz alta (no toca el portapapeles)
    "Explicame lo que copié"              -> Miku te lo explica en voz alta
    "Leeme lo que copié"                  -> Miku lo lee en voz alta
    "Deshacé" / "volvé al texto anterior" -> vuelve a lo que había antes de la última transformación

Las acciones que **reemplazan** el portapapeles guardan antes el texto original para poder
deshacerlo. Lo copiado se manda al LLM (Groq, o el servidor de ``LLM_BASE_URL``): no copies claves.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.cerebro import complemento
from miku.plataforma import portapapeles
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.portapapeles")

#: Tope de caracteres que se mandan al LLM (más que esto se recorta y se avisa).
MAX_CARACTERES = 12000
#: Tope de lo que Miku dice en voz alta (leer / explicar / resumir).
MAX_HABLADO = 900

_ACCIONES = ("traducir", "resumir", "corregir", "explicar", "reescribir", "leer")
#: Las que dejan el resultado en el portapapeles (reemplazan lo copiado).
_REEMPLAZAN = ("traducir", "corregir", "reescribir")

_INSTRUCCIONES = {
    "traducir": ("Traducí el texto del usuario al idioma {idioma}. Respondé SOLO con la traducción, sin "
                 "comillas, sin explicaciones y conservando saltos de línea y formato."),
    "resumir": ("Resumí el texto del usuario en español en 2 o 3 oraciones cortas, para leerlas en voz alta. "
                "Sin markdown, sin viñetas, sin emojis."),
    "corregir": ("Corregí la ortografía, la puntuación y la gramática del texto del usuario, respetando su idioma, "
                 "su tono y su significado. Respondé SOLO con el texto corregido, sin comentarios."),
    "explicar": ("Explicá con palabras simples, en español y en 3 o 4 oraciones, qué dice el texto (o qué hace el "
                 "código) del usuario. Sin markdown, sin viñetas, sin emojis."),
    "reescribir": ("Reescribí el texto del usuario siguiendo esta instrucción: {instruccion}. Conservá el idioma y "
                   "el significado. Respondé SOLO con el texto resultante, sin comentarios."),
}


class PortapapelesInteligente(Plugin):
    """Traduce, corrige, resume o explica lo que hay en el portapapeles."""

    nombre = "portapapeles_inteligente"
    descripcion = "Traduce, corrige, reescribe, resume, explica o lee lo copiado."

    tools: List[dict] = [
        {"type": "function", "function": {
            "name": "procesar_portapapeles",
            "description": "Hace algo con el texto que el usuario acaba de COPIAR (el portapapeles). Ej: "
                           "'traducí lo que copié al inglés', 'corregí lo que copié', 'resumí lo que copié', "
                           "'explicame lo que copié', 'reescribí lo que copié más formal', 'leeme lo que copié'. "
                           "Traducir, corregir y reescribir dejan el resultado en el portapapeles para pegarlo.",
            "parameters": {"type": "object", "properties": {
                "accion": {"type": "string", "enum": list(_ACCIONES), "description": "Qué hacer con el texto."},
                "idioma": {"type": "string", "description": "Solo para traducir: idioma destino en español."},
                "instruccion": {"type": "string",
                                "description": "Solo para reescribir: cómo (más formal, más corto, más simple...)."},
                "copiar": {"type": "boolean",
                           "description": "Para resumir/explicar: true si además pidió dejar el resultado copiado."}},
                "required": ["accion"]}}},
        {"type": "function", "function": {
            "name": "deshacer_portapapeles",
            "description": "Vuelve el portapapeles al texto que tenía antes de la última traducción/corrección/"
                           "reescritura. Ej: 'deshacé lo del portapapeles', 'volvé al texto anterior'.",
            "parameters": {"type": "object", "properties": {}}}},
    ]

    def __init__(self) -> None:
        super().__init__()
        self._anterior: Optional[str] = None

    # ---------------- Despacho ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "procesar_portapapeles":
            copiar = args.get("copiar")
            return self.procesar(str(args.get("accion", "")), str(args.get("idioma", "") or ""),
                                 str(args.get("instruccion", "") or ""), copiar if isinstance(copiar, bool) else False)
        if nombre_tool == "deshacer_portapapeles":
            return self.deshacer()
        return None

    # ---------------- Lógica ----------------
    def procesar(self, accion: str, idioma: str = "", instruccion: str = "", copiar: bool = False) -> str:
        """Aplica ``accion`` al texto copiado."""
        accion = (accion or "").lower().strip()
        if accion not in _ACCIONES:
            return falla("portapapeles.accion_desconocida")
        texto = portapapeles.leer_texto()
        if texto is None or not texto.strip():
            contenido = portapapeles.tipo_de_contenido()
            return falla("portapapeles.no_es_texto" if contenido in ("imagen", "archivos") else "portapapeles.vacio",
                         contenido=contenido)
        recortado = len(texto) > MAX_CARACTERES
        texto_llm = texto[:MAX_CARACTERES]

        if accion == "leer":
            return exito("portapapeles.dicho", texto=_para_voz(texto))

        cfg = config_mod.config
        if accion == "traducir":
            idioma = idioma.strip() or str(cfg.get("idioma_juego", "") or "").strip()
            if not idioma:
                return falla("portapapeles.sin_idioma")
        if accion == "reescribir" and not instruccion.strip():
            return falla("portapapeles.sin_instruccion")

        pedido = _INSTRUCCIONES[accion].format(idioma=idioma, instruccion=instruccion.strip())
        resultado = complemento.pedir_texto(cfg, pedido, texto_llm, max_tokens=1500 if accion in _REEMPLAZAN else 400)
        if not resultado:
            return falla("portapapeles.sin_respuesta")

        if accion in _REEMPLAZAN:
            self._anterior = texto
            if not portapapeles.escribir_texto(resultado):
                return falla("portapapeles.no_pude_copiar")
            return exito(f"portapapeles.{accion}", idioma=idioma, recortado=" (era muy largo: traduje solo el principio)"
                         if recortado else "")
        # resumir / explicar: se dicen en voz alta; el portapapeles solo cambia si se pidió.
        if copiar:
            self._anterior = texto
            portapapeles.escribir_texto(resultado)
        return exito("portapapeles.dicho_y_copiado" if copiar else "portapapeles.dicho", texto=_para_voz(resultado))

    def deshacer(self) -> str:
        """Devuelve el portapapeles a lo que había antes de la última transformación."""
        if self._anterior is None:
            return falla("portapapeles.nada_que_deshacer")
        if not portapapeles.escribir_texto(self._anterior):
            return falla("portapapeles.no_pude_copiar")
        self._anterior = None
        return exito("portapapeles.deshecho")


def _para_voz(texto: str) -> str:
    """Recorta lo que se dice en voz alta (y compacta saltos de línea)."""
    plano = " ".join(texto.split())
    if len(plano) <= MAX_HABLADO:
        return plano
    return plano[:MAX_HABLADO].rsplit(" ", 1)[0] + "… (sigue, pero corto acá)"
