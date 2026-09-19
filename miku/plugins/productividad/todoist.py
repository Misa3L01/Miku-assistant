# -*- coding: utf-8 -*-
"""
todoist.py - Tareas de facultad con Todoist (API REST v2).

Publica las tools ``tareas_hoy``, ``agregar_tarea`` y ``completar_tarea``.
Requiere ``TODOIST_API_TOKEN`` en config_local.py (Token personal de Todoist;
plan FREE alcanza para esto). Sin token, el plugin queda inactivo (no rompe).

API usada (REST v2, sin SDK):
    - GET    /tasks?filter=...         -> listar
    - POST   /tasks                    -> crear
    - POST   /tasks/{id}/close         -> completar
    - GET    /tasks?filter=...         -> para resolver por nombre

Notas: el token va en el header Authorization: Bearer. Matching tolerante por
contenido para completar. Errores honestos (no inventa).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin
from miku.plataforma.texto import normalizar
from miku.voz.frases.respuesta import falla

logger = logging.getLogger("miku.plugins.todoist")

_BASE = "https://api.todoist.com/rest/v2"


class Todoist(Plugin):
    """Lista, agrega y completa tareas en Todoist."""

    nombre = "todoist"
    descripcion = "Tareas de facultad en Todoist (listar hoy, agregar, completar)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "tareas_hoy",
                "description": "Lista las tareas para HOY en Todoist (vencimiento "
                               "hoy + atrasadas). Ej: 'qué tengo para hoy'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "agregar_tarea",
                "description": "Agrega una tarea en Todoist. Podés dar fecha "
                               "(formato YYYY-MM-DD) o dejar vacío. Ej: "
                               "'agregá estudiar cátedra de física'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contenido": {"type": "string",
                                      "description": "Texto de la tarea."},
                        "fecha": {"type": "string",
                                  "description": "Fecha de vencimiento "
                                                 "YYYY-MM-DD (opcional)."},
                    },
                    "required": ["contenido"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "completar_tarea",
                "description": "Marca como completada la tarea que mejor "
                               "coincida con el texto dado. Ej: 'completá la "
                               "tarea de física'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "descripcion": {"type": "string",
                                        "description": "Parte del nombre de "
                                                       "la tarea a completar."},
                    },
                    "required": ["descripcion"],
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin todoist listo (%s).",
                    "con token" if config_mod.config.todoist_api_token
                    else "sin TODOIST_API_TOKEN (inactivo)")

    def _headers(self) -> Optional[Dict[str, str]]:
        """Headers de auth (o None si no hay token)."""
        token = config_mod.config.todoist_api_token
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    def _sin_token(self) -> str:
        return ("No tengo configurado el token de Todoist. Agregá "
                "TODOIST_API_TOKEN en config_local.py.")

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "tareas_hoy":
            return self.tareas_hoy()
        if nombre_tool == "agregar_tarea":
            return self.agregar_tarea(str(args.get("contenido", "")),
                                      str(args.get("fecha", "") or ""))
        if nombre_tool == "completar_tarea":
            return self.completar_tarea(str(args.get("descripcion", "")))
        return None

    # ---------------- Acciones ---------------- #
    def tareas_hoy(self) -> str:
        """Lista tareas de hoy (vencimiento hoy + atrasadas)."""
        headers = self._headers()
        if headers is None:
            return self._sin_token()
        try:
            resp = requests.get(f"{_BASE}/tasks",
                                headers=headers,
                                params={"filter": "(today | overdue)"},
                                timeout=12)
            if resp.status_code != 200:
                logger.error("Todoist listar %s: %s", resp.status_code,
                             resp.text[:200])
                return "No pude leer tus tareas de Todoist."
            tareas = resp.json() or []
        except Exception as e:  # noqa: BLE001
            logger.error("Error consultando Todoist: %s", e)
            return "No pude conectar con Todoist."

        if not tareas:
            return "No tenés tareas para hoy. ¡Bien ahí!"
        lineas = [f"- {t.get('content', '(sin título)')}" for t in tareas[:15]]
        return "Tus tareas de hoy:\n" + "\n".join(lineas)

    def agregar_tarea(self, contenido: str, fecha: str = "") -> str:
        """Crea una tarea (opcionalmente con fecha de vencimiento)."""
        contenido = (contenido or "").strip()
        if not contenido:
            return "¿Qué tarea querés que agregue?"
        headers = self._headers()
        if headers is None:
            return self._sin_token()

        payload: Dict[str, Any] = {"content": contenido}
        fecha = (fecha or "").strip()
        if fecha:
            payload["due_string"] = fecha

        try:
            resp = requests.post(f"{_BASE}/tasks", headers=headers,
                                 json=payload, timeout=12)
            if resp.status_code in (200, 201):
                return f"Listo, agregué la tarea: {contenido}."
            logger.error("Todoist crear %s: %s", resp.status_code,
                         resp.text[:200])
            return "No pude agregar la tarea en Todoist."
        except Exception as e:  # noqa: BLE001
            logger.error("Error creando tarea: %s", e)
            return "No pude conectar con Todoist."

    def completar_tarea(self, descripcion: str) -> str:
        """Completa la tarea que mejor coincida por contenido."""
        descripcion = (descripcion or "").strip()
        if not descripcion:
            return "¿Qué tarea querés que complete?"
        headers = self._headers()
        if headers is None:
            return self._sin_token()

        try:
            resp = requests.get(f"{_BASE}/tasks", headers=headers,
                                timeout=12)
            if resp.status_code != 200:
                return "No pude leer tus tareas de Todoist."
            tareas = resp.json() or []
        except Exception as e:  # noqa: BLE001
            logger.error("Error listando para completar: %s", e)
            return "No pude conectar con Todoist."

        objetivo = normalizar(descripcion)
        coincidencias = [t for t in tareas
                         if objetivo in normalizar(t.get("content", ""))]
        if not coincidencias:
            return falla("todoist.tarea_no_encontrada", descripcion=descripcion)
        if len(coincidencias) > 1:
            lista = ", ".join(t.get("content", "?")
                              for t in coincidencias[:5])
            return (f"Tengo varias que coinciden: {lista}. "
                    f"Decime cuál con más detalle.")

        tarea = coincidencias[0]
        try:
            rid = tarea.get("id")
            resp = requests.post(f"{_BASE}/tasks/{rid}/close",
                                 headers=headers, timeout=12)
            if resp.status_code in (200, 204):
                return f"Listo, completé: {tarea.get('content', 'la tarea')}."
            return "No pude completar la tarea en Todoist."
        except Exception as e:  # noqa: BLE001
            logger.error("Error completando tarea: %s", e)
            return "No pude conectar con Todoist."