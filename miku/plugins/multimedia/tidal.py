# -*- coding: utf-8 -*-
"""
tidal.py - Control fino de TIDAL (play/pausa/siguiente/anterior + "qué suena").

TIDAL (app de escritorio) NO expone una API pública de reproducción, así que:
    - Los controles (play/pausa/siguiente/anterior) se hacen con las **teclas
      multimedia del sistema** (las mismas que usa el reproductor activo). En
      la práctica, si TIDAL tiene el foco/es el reproductor de media, responden.
    - "Qué está sonando" se obtiene con **Windows SMTC** (System Media Transport
      Controls) vía PowerShell + WinRT (Windows.Media.Control), que devuelve el
      título/artista del reproductor de media ACTUAL. Es best-effort: si no se
      puede, lo decimos claro (límite real de Windows, no nuestro).

Ver también: control multimedia genérico en ``miku/plugins/sistema/audio.py``
(``control_multimedia``); este plugin lo especializa para TIDAL.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plataforma.subprocesos import PREAMBULO_WINRT, correr_powershell
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.tidal")


class Tidal(Plugin):
    """Control de reproducción de TIDAL (teclas multimedia + "qué suena")."""

    nombre = "tidal"
    descripcion = "Controla TIDAL (play/pausa/siguiente/anterior) y qué suena."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "controlar_tidal",
                "description": "Controla la reproducción de TIDAL (o el "
                               "reproductor de media activo): reproducir/pausar, "
                               "siguiente o anterior. Ej: 'pausá TIDAL', "
                               "'siguiente en TIDAL'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["play_pausa", "siguiente", "anterior",
                                     "play", "pausa"],
                            "description": "Acción de reproducción.",
                        },
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "que_esta_sonando",
                "description": "Devuelve qué canción está sonando ahora mismo "
                               "(título y artista) en el reproductor de media "
                               "activo, típicamente TIDAL. Ej: 'qué está "
                               "sonando', 'qué tema es este'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        ruta = config_mod.config.valores.get("tidal_ruta_exe", "")
        logger.info("Plugin tidal listo (ruta exe: %s).",
                    "configurada" if ruta else "no configurada")

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "controlar_tidal":
            return self.controlar_tidal(str(args.get("accion", "")))
        if nombre_tool == "que_esta_sonando":
            return self.que_esta_sonando()
        return None

    # ---------------- Controles (teclas multimedia) ---------------- #
    def controlar_tidal(self, accion: str) -> str:
        """Play/pausa/siguiente/anterior por teclas multimedia del sistema."""
        accion = (accion or "").lower().strip()
        mapa = {
            "play_pausa": "play_pausa", "pausa": "play_pausa",
            "reproducir": "play_pausa", "play": "play_pausa",
            "siguiente": "siguiente", "adelante": "siguiente",
            "anterior": "anterior", "atras": "anterior",
        }
        clave = mapa.get(accion)
        if clave is None:
            return ("No entendí. Puedo pausar/reproducir, pasar a la siguiente "
                    "o volver a la anterior.")

        if self._enviar_tecla(clave):
            return {
                "play_pausa": "Listo, alterné play/pausa en TIDAL.",
                "siguiente": "Dale, pasé a la siguiente en TIDAL.",
                "anterior": "Listo, volví a la anterior en TIDAL.",
            }[clave]
        return "No pude enviar el control a TIDAL."

    def _enviar_tecla(self, clave: str) -> bool:
        """Envía una tecla multimedia del sistema (por ctypes VK)."""
        vk = {"play_pausa": 0xB3, "siguiente": 0xB0, "anterior": 0xB1}.get(clave)
        if vk is None:
            return False
        try:
            import ctypes
            import time
            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(vk, 0, 2, 0)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("No pude enviar la tecla multimedia %s: %s", clave, e)
            return False

    # ---------------- Qué está sonando (Windows SMTC) ---------------- #
    def que_esta_sonando(self) -> str:
        """Devuelve título/artista de la sesión de media activa (SMTC).

        Usa PowerShell + WinRT (Windows.Media.Control). Best-effort: si no se
        puede, se explica el límite en vez de inventar.
        """
        info = self._leer_smtc()
        if info is None:
            return ("No pude leer qué está sonando (Windows no me da la info de "
                    "media en este momento). Ojo: TIDAL no expone una API "
                    "propia, depende del sistema.")
        titulo = (info.get("title") or "").strip()
        artista = (info.get("artist") or "").strip()
        if not titulo:
            return "No hay ninguna canción reproduciéndose ahora mismo."
        if artista:
            return f"Está sonando: {titulo}, de {artista}."
        return f"Está sonando: {titulo}."

    def _leer_smtc(self) -> Optional[Dict[str, str]]:
        """Lee título/artista del media actual vía SMTC (PowerShell/WinRT)."""
        # PowerShell 5.1 no puede llamar ``GetAwaiter()`` sobre las operaciones
        # async de WinRT (son COM); se usa el patrón AsTask/Wait (igual que ocr.py).
        ps = PREAMBULO_WINRT + (
            "$T = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]; "
            "$mgr = Await ($T::RequestAsync()) ($T); "
            "$ses = $mgr.GetCurrentSession(); "
            "if ($ses -eq $null) { Write-Output ''; exit 0 }; "
            "$P = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]; "
            "$props = Await ($ses.TryGetMediaPropertiesAsync()) ($P); "
            "Write-Output ($props.Title + [char]9 + $props.Artist);"
        )
        try:
            proc = correr_powershell(ps, timeout=12)
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer SMTC: %s", e)
            return None

        salida = (proc.stdout or "").strip()
        # Un error de PowerShell NO es "no suena nada": devolvemos None para
        # que se avise que no se pudo leer (en vez de una respuesta falsa).
        if proc.returncode != 0:
            logger.debug("SMTC devolvió código %s: %s", proc.returncode,
                         (proc.stderr or "")[:200])
            return None
        if not salida:
            return {"title": "", "artist": ""}
        partes = salida.split("\t")
        return {"title": partes[0].strip(),
                "artist": partes[1].strip() if len(partes) > 1 else ""}

