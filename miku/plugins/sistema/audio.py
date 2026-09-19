"""
audio.py - Volumen (general y por aplicación), mute por app y control multimedia.

El Game Booster usa ``volumenes_de_app`` / ``restaurar_volumenes_app`` de este plugin.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from miku.plataforma import audio as audio_plat
from miku.plugins.base import Plugin
from miku.plugins.utiles import a_entero
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.audio")


class Audio(Plugin):
    """Volumen general y por app, mute por app y teclas multimedia."""

    nombre = "audio"
    descripcion = "Volumen general y por app, mute por app y teclas multimedia."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "control_multimedia",
                "description": "Controla la reproducción multimedia global: "
                               "pausar/reproducir, siguiente o anterior "
                               "canción (usa teclas multimedia virtuales). "
                               "Ej: 'pausá la música', 'siguiente tema'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["play", "pausa", "play_pausa",
                                     "siguiente", "anterior"],
                            "description": "Acción multimedia a ejecutar.",
                        },
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ajustar_volumen",
                "description": "Controla el volumen del sistema o de una APP "
                               "específica. Usá accion='subir'/'bajar' para "
                               "cambios RELATIVOS (ej: 'subí el volumen'); "
                               "usá accion='fijar' (con 'valor' 0-100) para un "
                               "nivel EXACTO (ej: 'poné el volumen en 10'); "
                               "usá 'silenciar'/'desmutear' para el mute real. "
                               "Para el volumen de UNA app, pasá 'app' (ej: "
                               "'bajá el volumen de Brave' -> app='brave').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["subir", "bajar", "fijar",
                                     "silenciar", "desmutear"],
                            "description": "Qué hacer con el audio.",
                        },
                        "valor": {
                            "type": "integer",
                            "description": "Nivel EXACTO 0-100. Solo se usa "
                                           "cuando accion='fijar'.",
                        },
                        "paso": {
                            "type": "integer",
                            "description": "Opcional: pasos/porcentaje para "
                                           "subir o bajar (default 5).",
                        },
                        "app": {
                            "type": "string",
                            "description": "Opcional: nombre de la app cuyo "
                                           "volumen ajustar (ej: 'brave', "
                                           "'discord', 'tidal'). Si se omite, "
                                           "se ajusta el volumen GENERAL del "
                                           "sistema.",
                        },
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mutear_app",
                "description": "Silencia o reactiva UNA app puntual (Discord, "
                               "Brave, Spotify...) sin tocar el volumen "
                               "general del sistema. Usá accion='mutear' "
                               "(default) para silenciar la app y 'activar' "
                               "para devolverle el sonido. Ej: 'silenciá "
                               "Discord', 'mutear Brave', 'devolvele el audio "
                               "a Discord'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Nombre de la app (ej: 'discord', "
                                           "'brave', 'spotify').",
                        },
                        "accion": {
                            "type": "string",
                            "enum": ["mutear", "activar"],
                            "description": "mutear = silenciar; activar = "
                                           "devolver el sonido.",
                        },
                    },
                    "required": ["nombre"],
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()

    def initialize(self, event_bus: Any = None) -> None:
        """Deja el plugin listo."""
        super().initialize(event_bus)
        logger.info("Plugin audio listo.")

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools de audio y multimedia."""
        if nombre_tool == "control_multimedia":
            return self.control_multimedia(str(args.get("accion", "")))
        if nombre_tool == "ajustar_volumen":
            return self.ajustar_volumen(str(args.get("accion", "")),
                                        args.get("paso"),
                                        args.get("valor"),
                                        args.get("app"))
        if nombre_tool in ("mutear_app", "silenciar_app"):
            return self.mutear_app(str(args.get("nombre", "")),
                                   str(args.get("accion", "mutear") or
                                       "mutear"))
        return None
    def _enviar_tecla_virtual(self, clave: str) -> bool:
        """Envía una tecla multimedia/volumen (ver ``plataforma.audio.enviar_tecla``)."""
        return audio_plat.enviar_tecla(clave)
    def _volumen_pycaw(self):
        """``IAudioEndpointVolume`` del sistema, o None si pycaw no está disponible."""
        return audio_plat.volumen_master()

    def _aplicar_mute_real(self, silenciar: bool) -> str:
        """Silencia (True) o reactiva el audio (False) de forma determinística.

        No depende de una tecla toggle: usa la API de Windows vía pycaw
        (`SetMute`), por lo que llamarlo dos veces no invierte el estado por
        accidente.
        """
        vol = self._volumen_pycaw()
        if vol is None:
            return ("No pude silenciar de forma segura (falta pycaw). "
                    "No uso la tecla de mute para evitar toggles ambiguos.")
        try:
            vol.SetMute(1 if silenciar else 0, None)
            if silenciar:
                return "Silencié la salida de audio."
            return "Reactivé el sonido."
        except Exception as e:  # noqa: BLE001
            logger.error("Error aplicando mute real: %s", e)
            return "No pude cambiar el estado de silencio."

    def control_multimedia(self, accion: str) -> str:
        """Pausa/reproduce, avanza o retrocede el audio/video global."""
        accion = (accion or "").lower().strip()
        alias = {
            "play": "play_pausa", "pausa": "play_pausa",
            "reproducir": "play_pausa", "play_pausa": "play_pausa",
            "siguiente": "siguiente", "adelante": "siguiente",
            "next": "siguiente",
            "anterior": "anterior", "atras": "anterior",
            "prev": "anterior",
        }.get(accion, accion)

        if alias not in self._VK:
            return "No entendí la acción multimedia. Usá play, pausa, siguiente o anterior."

        mensaje = {
            "play_pausa": "Alterné play/pausa.",
            "siguiente": "Pasé a la siguiente.",
            "anterior": "Volví a la anterior.",
        }.get(alias, "Listo.")

        if self._enviar_tecla_virtual(alias):
            return mensaje
        return "No pude enviar el comando multimedia."

    def ajustar_volumen(self, accion: str, paso: Optional[int] = None,
                        valor: Optional[int] = None,
                        app: Optional[str] = None) -> str:
        """Controla el volumen del sistema (o de una APP puntual).

        Acciones:
        - ``subir``/``bajar``: cambio RELATIVO en pasos (default 5).
        - ``fijar``: nivel EXACTO (``valor`` 0-100).
        - ``silenciar``/``desmutear``: mute determinístico vía pycaw.

        Si se pasa ``app`` (ej. "brave"), se ajusta el volumen de ESA app
        (todas sus sesiones de audio). Si no, se ajusta el volumen GENERAL.
        """
        accion = (accion or "").lower().strip()

        # Derivamos a la lógica POR APP si el usuario nombró una app.
        app = (app or "").strip()
        if app and app.lower() not in ("sistema", "general", "pc",
                                       "computadora", "todo"):
            return self._ajustar_volumen_app(app, accion, paso, valor)

        # -------- Volumen GENERAL (comportamiento original) --------
        # Mute real (no toggle): sin ambigüedad.
        if accion in ("silenciar", "mutear", "mute", "sin sonido"):
            return self._aplicar_mute_real(True)
        if accion in ("desmutear", "sonido", "reactivar",
                      "activar sonido", "activar_sonido"):
            return self._aplicar_mute_real(False)

        # Nivel ABSOLUTO 0-100: "poné el volumen en 10".
        if accion in ("fijar", "establecer", "poner", "setear", "set"):
            if valor is None:
                return ("Decime en qué porcentaje lo pongo (0 a 100), "
                        "por ejemplo 'poné el volumen en 30'.")
            return self._fijar_volumen(valor)

        # Determinar subir/bajar (RELATIVO).
        if accion in ("subir", "mas", "arriba"):
            direccion = 1
        elif accion in ("bajar", "menos", "abajo"):
            direccion = -1
        else:
            return ("No entendí. Usá subir, bajar, fijar (con valor), "
                    "silenciar o desmutear.")

        paso = max(1, min(100, a_entero(paso, 5) or 5))

        # pycaw: leemos nivel real, ajustamos y reactivamos si estaba mute.
        vol = self._volumen_pycaw()
        if vol is not None:
            try:
                actual = max(0.0, min(1.0, float(vol.GetMasterVolumeLevelScalar())))
                if actual == 0.0 and direccion > 0:
                    actual = 5.0 / 100.0  # punto de partida para "subir"
                nuevo = max(0.0, min(1.0, actual + direccion * (paso / 100.0)))
                vol.SetMasterVolumeLevelScalar(nuevo, None)
                # Si estaba silenciado y el usuario pide subir/bajar, reactivamos.
                try:
                    if vol.GetMute() and direccion > 0:
                        vol.SetMute(0, None)
                except Exception:  # noqa: BLE001
                    pass
                pct = int(round(nuevo * 100))
                return exito("audio.volumen", pct=pct)
            except Exception as e:  # noqa: BLE001
                logger.error("Error ajustando volumen con pycaw. Usando VK: %s", e)
                # caemos al fallback por teclas
                vol = None

        # Fallback por teclas multimedia (sin poder leer el nivel).
        clave = "subir_volumen" if direccion > 0 else "bajar_volumen"
        pasos = max(1, paso // 2)
        for _ in range(min(pasos, 25)):
            if not self._enviar_tecla_virtual(clave):
                return "No pude ajustar el volumen."
            time.sleep(0.02)
        return ("Subí el volumen." if direccion > 0 else "Bajé el volumen.")

    def _fijar_volumen(self, valor: int) -> str:
        """Fija el volumen a un nivel EXACTO (0-100) usando pycaw.

        A diferencia de subir/bajar (relativo), acá se establece el valor
        absoluto con ``SetMasterVolumeLevelScalar``. Si el valor es > 0,
        reactiva el audio por si estaba silenciado.
        """
        vol = self._volumen_pycaw()
        if vol is None:
            return "No tengo pycaw disponible para fijar el volumen exacto."
        try:
            nivel = max(0, min(100, int(valor)))
            vol.SetMasterVolumeLevelScalar(nivel / 100.0, None)
            # Reactivamos el audio si el nuevo nivel es audible.
            if nivel > 0:
                try:
                    if vol.GetMute():
                        vol.SetMute(0, None)
                except Exception:  # noqa: BLE001
                    pass
                return exito("audio.volumen", pct=nivel)
            # nivel == 0: lo dejamos en 0 (silencio por nivel, no mute).
            return exito("audio.volumen", pct=0)
        except Exception as e:  # noqa: BLE001
            logger.error("Error fijando volumen con pycaw: %s", e)
            return "No pude fijar el volumen."
    def _sesiones_de_app(self, nombre_app: str) -> List[Any]:
        """Sesiones de audio de la app (ver ``plataforma.audio.sesiones_de_app``)."""
        return audio_plat.sesiones_de_app(nombre_app)

    def volumenes_de_app(self, nombre_app: str) -> Dict[int, float]:
        """Devuelve ``{pid: nivel (0-1)}`` de las sesiones de audio de una app.

        Sirve para guardar el volumen ANTES de bajarlo (p. ej. el Game
        Booster) y poder restaurarlo con ``restaurar_volumenes_app``.
        """
        niveles: Dict[int, float] = {}
        for sesion in self._sesiones_de_app(nombre_app):
            vol = self._volumen_sesion(sesion)
            if vol is None:
                continue
            try:
                niveles[int(sesion.ProcessId)] = float(vol.GetMasterVolume())
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude leer el volumen de una sesión: %s", e)
        return niveles

    def restaurar_volumenes_app(self, nombre_app: str,
                                niveles: Dict[int, float]) -> int:
        """Restaura los niveles guardados por ``volumenes_de_app``.

        Solo toca las sesiones cuyo PID sigue existiendo. Devuelve cuántas
        restauró.
        """
        restauradas = 0
        for sesion in self._sesiones_de_app(nombre_app):
            vol = self._volumen_sesion(sesion)
            if vol is None:
                continue
            try:
                nivel = niveles.get(int(sesion.ProcessId))
                if nivel is None:
                    continue
                vol.SetMasterVolume(max(0.0, min(1.0, float(nivel))), None)
                restauradas += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude restaurar el volumen de una sesión: %s", e)
        return restauradas

    def _volumen_sesion(self, sesion: Any):
        """Devuelve el ISimpleAudioVolume de una sesión (o None)."""
        try:
            return sesion.SimpleAudioVolume
        except Exception as e:  # noqa: BLE001
            logger.debug("Sesión sin SimpleAudioVolume: %s", e)
            return None

    def _ajustar_volumen_app(self, nombre_app: str, accion: str,
                             paso: Optional[int], valor: Optional[int]) -> str:
        """Ajusta el volumen de TODAS las sesiones de audio de una app.

        Acciones soportadas: subir/bajar (relativo), fijar (absoluto),
        silenciar/desmutear. Lee/devuelve nivel en porcentaje.
        """
        accion = (accion or "").lower().strip()
        sesiones = self._sesiones_de_app(nombre_app)
        if not sesiones:
            return falla("audio.app_no_suena", app=nombre_app)

        app_txt = nombre_app
        afectadas = 0
        ultimo_pct = None
        for sesion in sesiones:
            vol = self._volumen_sesion(sesion)
            if vol is None:
                continue
            try:
                if accion in ("silenciar", "mutear", "mute", "sin sonido"):
                    vol.SetMute(1, None)
                    afectadas += 1
                    continue
                if accion in ("desmutear", "sonido", "reactivar",
                              "activar sonido", "activar_sonido"):
                    vol.SetMute(0, None)
                    afectadas += 1
                    continue
                actual = max(0.0, min(1.0, float(vol.GetMasterVolume())))
                if accion in ("fijar", "establecer", "poner", "setear", "set"):
                    if valor is None:
                        return ("Decime en qué porcentaje lo pongo (0 a 100), "
                                "por ejemplo 'poné el volumen de brave en 30'.")
                    nuevo = max(0.0, min(1.0, int(valor) / 100.0))
                elif accion in ("subir", "mas", "arriba"):
                    delta = max(1, min(100, a_entero(paso, 5) or 5)) / 100.0
                    nuevo = max(0.0, min(1.0, actual + delta))
                elif accion in ("bajar", "menos", "abajo"):
                    delta = max(1, min(100, a_entero(paso, 5) or 5)) / 100.0
                    nuevo = max(0.0, min(1.0, actual - delta))
                else:
                    return ("No entendí. Usá subir, bajar, fijar (con valor), "
                            "silenciar o desmutear.")
                vol.SetMasterVolume(nuevo, None)
                # Si subimos y estaba silenciada, destrabamos el mute.
                if nuevo > 0:
                    try:
                        if vol.GetMute():
                            vol.SetMute(0, None)
                    except Exception:  # noqa: BLE001
                        pass
                afectadas += 1
                ultimo_pct = int(round(nuevo * 100))
            except Exception as e:  # noqa: BLE001
                logger.error("Error ajustando sesión de '%s': %s", app_txt, e)
                continue

        if afectadas == 0:
            return f"No pude ajustar el volumen de '{app_txt}'."

        if accion in ("silenciar", "mutear", "mute", "sin sonido"):
            return f"Silencié {app_txt}."
        if accion in ("desmutear", "sonido", "reactivar",
                      "activar sonido", "activar_sonido"):
            return f"Reactivé el sonido de {app_txt}."
        if ultimo_pct is not None:
            return f"Volumen de {app_txt} en {ultimo_pct}%."
        return f"Ajusté el volumen de {app_txt}."

    # ---------------- Mute POR APP (silencio puntual) ---------------- #
    def mutear_app(self, nombre_app: str, accion: str = "mutear") -> str:
        """Silencia (o reactiva) SOLO la app indicada, sin tocar el master.

        Usa las sesiones de audio (``ISimpleAudioVolume.SetMute``) de la app
        puntual. Si la app no tiene ninguna sesión de audio activa, devuelve un
        mensaje claro en vez de tocar el volumen general.

        Args:
            nombre_app: Nombre/alias de la app (ej. "discord", "brave").
            accion: "mutear"/"silenciar" (default) o "activar"/"desmutear".
        """
        nombre_app = (nombre_app or "").strip()
        if not nombre_app:
            return "¿Qué app querés que silencie?"

        accion = (accion or "mutear").lower().strip()
        activar = accion in ("activar", "desmutear", "desilenciar",
                             "reactivar", "sonido", "unmute")

        sesiones = self._sesiones_de_app(nombre_app)
        if not sesiones:
            return falla("audio.app_no_suena", app=nombre_app)

        afectadas = 0
        for sesion in sesiones:
            vol = self._volumen_sesion(sesion)
            if vol is None:
                continue
            try:
                vol.SetMute(0 if activar else 1, None)
                afectadas += 1
            except Exception as e:  # noqa: BLE001
                logger.error("Error muteando sesión de '%s': %s",
                             nombre_app, e)
                continue

        if afectadas == 0:
            return f"No pude cambiar el silencio de '{nombre_app}'."
        if activar:
            return f"Le devolví el sonido a {nombre_app}."
        return f"Silencié {nombre_app}."
