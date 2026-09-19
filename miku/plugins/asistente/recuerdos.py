# -*- coding: utf-8 -*-
"""
recuerdos.py - Tools para que el usuario maneje lo que Miku recuerda.

Hasta ahora la memoria solo se llenaba con "acordate que X" y se consultaba sola. Este plugin agrega:
    - ``guardar_recuerdo``: "recordá que mi cumple es el 3 de mayo" dicho de cualquier manera.
    - ``olvidar_recuerdo``: "olvidá lo de mi cumple" (borra solo si hay UNA coincidencia clara).
    - ``listar_recuerdos``: "qué recordás de mí" (cuántos hay y los últimos).
    - ``olvidar_conversacion``: borra el hilo de la charla actual (no toca los recuerdos guardados).

La memoria y el historial viven en el parser (``bus.parser``), que este plugin consulta al usarse.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.recuerdos")


class Recuerdos(Plugin):
    """Guardar, listar y olvidar recuerdos."""

    nombre = "recuerdos"
    descripcion = "Guarda, lista y olvida recuerdos; borra el hilo de la charla."

    tools: List[dict] = [
        {"type": "function", "function": {
            "name": "guardar_recuerdo",
            "description": "Guarda algo para recordarlo después (un dato del usuario, una preferencia, "
                           "una fecha). Ej: 'acordate que mi cumple es el 3 de mayo', 'anotá que uso "
                           "auriculares azules'.",
            "parameters": {"type": "object", "properties": {
                "texto": {"type": "string", "description": "Lo que hay que recordar, en una frase."}},
                "required": ["texto"]}}},
        {"type": "function", "function": {
            "name": "olvidar_recuerdo",
            "description": "Borra un recuerdo guardado. Ej: 'olvidá lo de mi cumple', 'borrá que uso "
                           "auriculares azules'.",
            "parameters": {"type": "object", "properties": {
                "descripcion": {"type": "string", "description": "De qué trata el recuerdo a borrar."}},
                "required": ["descripcion"]}}},
        {"type": "function", "function": {
            "name": "listar_recuerdos",
            "description": "Dice qué recuerdos hay guardados (cuántos y los más recientes). Ej: 'qué "
                           "recordás de mí', 'qué tenés anotado'.",
            "parameters": {"type": "object", "properties": {
                "cantidad": {"type": "integer", "description": "Cuántos nombrar (por defecto 5)."}}}}},
        {"type": "function", "function": {
            "name": "olvidar_conversacion",
            "description": "Borra el hilo de la charla actual para empezar de cero (no borra los "
                           "recuerdos guardados). Ej: 'empecemos de nuevo', 'olvidá lo que hablamos'.",
            "parameters": {"type": "object", "properties": {}}}},
    ]

    def __init__(self) -> None:
        super().__init__()
        self._bus: Any = None

    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._bus = event_bus

    # El parser aparece en el bus recién cuando la app termina de armarse: se busca al usarlo.
    def _parser(self) -> Optional[Any]:
        return getattr(self._bus, "parser", None) if self._bus is not None else None

    def _memoria(self) -> Optional[Any]:
        parser = self._parser()
        return getattr(parser, "memoria", None) if parser is not None else None

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "guardar_recuerdo":
            return self.guardar(str(args.get("texto", "")))
        if nombre_tool == "olvidar_recuerdo":
            return self.olvidar(str(args.get("descripcion", "")))
        if nombre_tool == "listar_recuerdos":
            try:
                cantidad = int(args.get("cantidad") or 5)
            except (TypeError, ValueError):
                cantidad = 5
            return self.listar(cantidad)
        if nombre_tool == "olvidar_conversacion":
            return self.olvidar_conversacion()
        return None

    def guardar(self, texto: str) -> str:
        """Guarda ``texto`` como recuerdo."""
        memoria = self._memoria()
        if memoria is None:
            return falla("memoria.inactiva")
        texto = (texto or "").strip()
        if not texto:
            return falla("memoria.guardar_sin_texto")
        return exito("memoria.guardado") if memoria.guardar_recuerdo(texto) else falla("memoria.error_guardar")

    def olvidar(self, descripcion: str) -> str:
        """Borra el recuerdo que coincida con ``descripcion`` (solo si es uno)."""
        memoria = self._memoria()
        if memoria is None:
            return falla("memoria.inactiva")
        return memoria.olvidar_recuerdo(descripcion)

    def listar(self, cantidad: int = 5) -> str:
        """Dice cuántos recuerdos hay y nombra los más recientes."""
        memoria = self._memoria()
        if memoria is None:
            return falla("memoria.inactiva")
        cantidad = max(1, min(int(cantidad), 10))
        total, recientes = memoria.listar_recuerdos(cantidad)
        if total == 0:
            return exito("memoria.vacia")
        listado = "; ".join(recientes)
        return exito("memoria.lista" if total <= len(recientes) else "memoria.lista_parcial",
                     total=total, cantidad=len(recientes), listado=listado)

    def olvidar_conversacion(self) -> str:
        """Borra el historial de la charla en curso."""
        parser = self._parser()
        if parser is None or not hasattr(parser, "olvidar_historial"):
            return falla("memoria.inactiva")
        parser.olvidar_historial()
        return exito("memoria.charla_olvidada")
