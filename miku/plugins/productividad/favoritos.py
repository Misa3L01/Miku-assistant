# -*- coding: utf-8 -*-
"""
favoritos.py - Carpetas favoritas (accesos rápidos por voz).

Permite abrir carpetas de uso frecuente (animes, descargas, documentos,
facultad...) con un nombre corto, y "enseñar" carpetas nuevas por voz.

Fuentes de datos (se fusionan, gana la más específica):
    1. ``CARPETAS_FAVORITAS`` de config_local.py (mapa nombre -> ruta).
    2. ``carpetas_favoritas`` de data/preferences.json (las "enseñadas" por
       voz; se persisten ahí sin tocar el resto del JSON).

Publica dos tools:
    - ``abrir_carpeta_favorita`` (abre una carpeta por su nombre).
    - ``guardar_carpeta_favorita`` (aprende/persiste una carpeta nueva).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.favoritos")

# Mapa de reemplazo de diacríticos (comparaciones sin acentos).
_DIACRITICOS = str.maketrans(
    "áàäâãéèëêíìïîóòöôõúùüûñç",
    "aaaaaeeeeiiiiooooouuuunc")


def _normalizar(texto: Optional[str]) -> str:
    """Minúsculas + sin acentos, para matching tolerante."""
    if not texto:
        return ""
    return str(texto).lower().translate(_DIACRITICOS).strip()


class Favoritos(Plugin):
    """Abre y aprende carpetas favoritas por nombre."""

    nombre = "favoritos"
    descripcion = "Abre carpetas favoritas por nombre y aprende nuevas."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "abrir_carpeta_favorita",
                "description": "Abre en el Explorador una carpeta favorita del "
                               "usuario por su nombre corto (ej: 'animes', "
                               "'descargas', 'documentos', 'facultad'). Ej: "
                               "'abrí la carpeta de animes'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Nombre/etiqueta de la carpeta "
                                           "favorita (ej: 'animes').",
                        },
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "guardar_carpeta_favorita",
                "description": "Aprende/guarda una carpeta como favorita para "
                               "poder abrirla luego por nombre. Ej: 'enseñá "
                               "esta carpeta como facultad: R:\\facultad'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Etiqueta corta para la carpeta "
                                           "(ej: 'facultad').",
                        },
                        "ruta": {
                            "type": "string",
                            "description": "Ruta completa de la carpeta.",
                        },
                    },
                    "required": ["nombre", "ruta"],
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        """Carga las favoritas configuradas y persistidas."""
        super().initialize(event_bus)
        self._carpetas: Dict[str, str] = {}
        self._recargar()
        logger.info("Plugin favoritos listo. %d carpeta(s).",
                    len(self._carpetas))

    # ---------------- Carga / persistencia ---------------- #
    def _recargar(self) -> None:
        """Fusiona favoritas de config_local y de preferences.json."""
        carpetas: Dict[str, str] = {}

        # 1) config_local.py (vía la propiedad de config).
        try:
            for nombre, ruta in (config_mod.config.carpetas_favoritas or {}).items():
                carpetas[str(nombre).strip().lower()] = str(ruta).strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer carpetas_favoritas de config: %s", e)

        # 2) preferences.json (las enseñadas por voz pisan a las de config).
        try:
            datos = self._leer_preferences()
            persistidas = datos.get("carpetas_favoritas") or {}
            if isinstance(persistidas, dict):
                for nombre, ruta in persistidas.items():
                    if str(nombre).strip() and str(ruta).strip():
                        carpetas[str(nombre).strip().lower()] = str(ruta).strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer favoritas de preferences: %s", e)

        self._carpetas = carpetas

    def _ruta_preferences(self) -> Path:
        """Ruta al preferences.json (data/)."""
        return Path(config_mod.BASE_DIR) / "data" / "preferences.json"

    def _leer_preferences(self) -> Dict[str, Any]:
        """Lee preferences.json como dict (o {} si no existe / error)."""
        ruta = self._ruta_preferences()
        if not ruta.exists():
            return {}
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                datos = json.load(f)
            return datos if isinstance(datos, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.warning("No pude leer %s: %s", ruta, e)
            return {}

    def _persistir_favoritas(self, carpetas: Dict[str, str]) -> bool:
        """Escribe el mapa de favoritas en preferences.json (merge atómico).

        Actualiza SOLO la clave ``carpetas_favoritas`` y conserva el resto del
        JSON. Devuelve True si pudo.
        """
        ok = config_mod.config.guardar_preferencias(
            {"carpetas_favoritas": carpetas})
        if ok:
            logger.info("Favoritas persistidas (%d).", len(carpetas))
        return ok

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools publicadas por este plugin."""
        if nombre_tool == "abrir_carpeta_favorita":
            return self.abrir_carpeta_favorita(str(args.get("nombre", "")))
        if nombre_tool == "guardar_carpeta_favorita":
            return self.guardar_carpeta_favorita(str(args.get("nombre", "")),
                                                 str(args.get("ruta", "")))
        return None

    # ---------------- Resolución del nombre ---------------- #
    def _resolver(self, nombre: str) -> Optional[str]:
        """Devuelve la ruta de la carpeta cuyo nombre coincide (o None).

        Matching: exacto (normalizado), luego substring en cualquier dirección.
        """
        objetivo = _normalizar(nombre)
        if not objetivo:
            return None

        # 1) Exacto normalizado.
        for clave, ruta in self._carpetas.items():
            if _normalizar(clave) == objetivo:
                return ruta
        # 2) Substring (tolerante a "carpeta de animes" -> "animes").
        for clave, ruta in self._carpetas.items():
            ck = _normalizar(clave)
            if ck and (ck in objetivo or objetivo in ck):
                return ruta
        return None

    # ---------------- Acciones ---------------- #
    def abrir_carpeta_favorita(self, nombre: str) -> str:
        """Abre en el Explorador la carpeta favorita `nombre`."""
        nombre = (nombre or "").strip()
        if not nombre:
            return "¿Qué carpeta querés que abra?"

        if not self._carpetas:
            return ("No tengo carpetas favoritas configuradas todavía. "
                    "Podés enseñarme una diciendo, por ejemplo, 'enseñá esta "
                    "carpeta como descargas'.")

        ruta = self._resolver(nombre)
        if not ruta:
            disponibles = ", ".join(sorted(self._carpetas.keys()))
            return (f"No conozco la carpeta '{nombre}'. Tengo: {disponibles}.")

        if not os.path.isdir(ruta):
            return (f"La carpeta '{nombre}' apunta a '{ruta}', pero no existe "
                    f"o no es accesible.")

        try:
            os.startfile(ruta)  # type: ignore[attr-defined]
            logger.info("Carpeta favorita '%s' abierta (%s).", nombre, ruta)
            return f"Abrí la carpeta de {nombre}."
        except Exception as e:  # noqa: BLE001
            logger.error("No pude abrir la carpeta '%s' (%s): %s",
                         nombre, ruta, e)
            return f"No pude abrir la carpeta de {nombre}."

    def guardar_carpeta_favorita(self, nombre: str, ruta: str) -> str:
        """Aprende una carpeta nueva y la persiste en preferences.json.

        Acepta que la ruta no exista todavía (así el usuario puede configurar
        una carpeta que creará luego), pero avisa en ese caso.
        """
        nombre = (nombre or "").strip().lower()
        ruta = (ruta or "").strip()
        if not nombre or not ruta:
            return "Necesito un nombre y una ruta para guardar la carpeta."

        ruta = os.path.expandvars(os.path.expanduser(ruta))
        existe = os.path.isdir(ruta)

        self._carpetas[nombre] = ruta
        # Persistimos SOLO las "enseñadas" por voz: las de config_local viven
        # allá y copiarlas acá las congelaría, pisando futuras ediciones.
        persistidas = self._leer_preferences().get("carpetas_favoritas")
        persistidas = dict(persistidas) if isinstance(persistidas, dict) else {}
        persistidas[nombre] = ruta
        guardado = self._persistir_favoritas(persistidas)

        if not guardado:
            return (f"Apunté '{nombre}' a '{ruta}', pero no pude guardarlo de "
                    f"forma permanente.")
        if not existe:
            return (f"Guardé '{nombre}' apuntando a '{ruta}', aunque esa "
                    f"carpeta todavía no existe. Ojo con eso.")
        return f"Listo, aprendí la carpeta '{nombre}'."