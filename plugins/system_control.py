# -*- coding: utf-8 -*-
"""
system_control.py - Plugin de control básico del sistema.

Este plugin muestra el patrón de los plugins modulares:

    - Implementa la base `Plugin` (initialize / execute).
    - Publica `tools` (schemas OpenAI function-calling) para que el cerebro
      (parser/LLM) las invoque mediante `manejar_tool()`.

Funciones básicas incluidas en esta primera versión:
    - Abrir programas por nombre.
    - Cerrar programas (matar proceso).
    - Controlar brillo de pantalla.
    - Listar, minimizar y mover ventanas entre monitores.

Las dependencias pesadas (AppOpener, win32, sbc) se importan de forma
perezosa dentro de cada método para acelerar el arranque.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from plugins import Plugin

logger = logging.getLogger("miku.plugins.system_control")

# Mapa nombre -> identificador de apertura (alias del sistema original).
_APPS = {
    "brave": "brave", "navegador": "brave",
    "discord": "discord",
    "tidal": "tidal", "spotify": "spotify",
    "vscode": "code", "visual studio": "code", "code": "code",
    "chrome": "chrome", "edge": "msedge",
    "explorador": "explorer",
    "calculadora": "calculator",
    "notepad": "notepad", "bloc de notas": "notepad", "bloc": "notepad",
}

# Mapa nombre -> proceso para cierre por taskkill.
_PROCESOS = {
    "discord": "Discord.exe",
    "brave": "brave.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "vscode": "Code.exe", "code": "Code.exe",
    "tidal": "TIDAL.exe",
    "spotify": "Spotify.exe",
    "steam": "steam.exe",
    "obs": "obs64.exe",
    "notepad": "notepad.exe",
    "calculadora": "CalculatorApp.exe",
    "explorador": "explorer.exe",
}


# Extensiones que usan el operador nativo `ext:` de Everything (es.exe).
# Son extensiones reales del sistema (no de contenido web binario), así que
# Everything las indexa y el filtro nativo es fiable.
_EXT_NATIVAS_ES = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md",
    "csv", "json", "xml", "yaml", "yml", "log", "ini", "cfg",
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "svg",
    "mp3", "wav", "flac", "ogg", "m4a", "aac",
    "mp4", "mkv", "avi", "mov", "wmv", "webm", "flv",
    "py", "js", "ts", "java", "c", "cpp", "h", "cs", "php", "rb", "go",
    "html", "css", "sql", "sh", "bat", "ps1",
    "zip", "rar", "7z", "tar", "gz",
    "exe", "dll", "iso",
}


def _normalizar_extension(extension: Optional[str]) -> Optional[str]:
    """Normaliza una extensión escrita por el usuario.

    Acepta ``"pdf"``, ``".pdf"``, ``"*.pdf"``, ``" PDF "`` -> ``"pdf"``.
    Devuelve None si queda vacía.
    """
    if not extension:
        return None
    ext = str(extension).strip().lower()
    ext = ext.lstrip("*").lstrip(".").strip()
    return ext or None


# Mapa de reemplazo de diacríticos para comparar sin acentos (voz transcribe
# sin acentos: "química" vs "quimica").
_DIACRITICOS = str.maketrans(
    "áàäâãéèëêíìïîóòöôõúùüûñç",
    "aaaaaeeeeiiiiooooouuuunc")


def _normalizar_texto(texto: Optional[str]) -> str:
    """Minúsculas + sin acentos, para comparaciones tolerantes.

    Hace el matching case-insensitive y acento-insensitive de punta a punta.
    """
    if not texto:
        return ""
    return str(texto).lower().translate(_DIACRITICOS)


def _clave_compacta(texto: Optional[str]) -> str:
    """Normaliza y QUITA separadores (espacios, guiones, guiones bajos).

    Sirve para que ``"lista animes"`` matchee con ``"ListaAnimes"``.
    """
    return "".join(ch for ch in _normalizar_texto(texto)
                   if ch.isalnum())


class SystemControl(Plugin):
    """Control básico del sistema (abrir/cerrar, brillo, ventanas)."""

    nombre = "system_control"
    descripcion = "Control básico del sistema (programas, brillo, ventanas)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "abrir_programa",
                "description": "Abre un programa instalado (Discord, Brave, "
                               "VSCode, Tidal, calculadora, etc.). No usar "
                               "para sitios web.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string",
                                   "description": "Nombre o alias del programa."},
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cerrar_programa",
                "description": "Cierra completamente un programa (mata el "
                               "proceso). Ej: 'cerrá Discord'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"}
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "controlar_brillo",
                "description": "Sube, baja o fija el brillo de la pantalla.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {"type": "string",
                                   "enum": ["subir", "bajar", "fijar"]},
                        "valor": {"type": "integer",
                                  "description": "Nivel 0-100 para 'fijar', "
                                                 "o pasos para subir/bajar."},
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "listar_ventanas",
                "description": "Lista las ventanas actualmente abiertas.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mover_ventana",
                "description": "Mueve una ventana abierta a otro monitor.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "monitor": {"type": "integer"},
                    },
                    "required": ["nombre", "monitor"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "minimizar_ventana",
                "description": "Minimiza la ventana de un programa.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"}
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "control_energia",
                "description": "Apaga, reinicia o suspende la PC. ACCIÓN "
                               "PELIGROSA: el sistema SIEMPRE pedirá "
                               "confirmación antes de ejecutarla.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["apagar", "reiniciar", "suspender"],
                            "description": "Qué hacer con la energía de la PC.",
                        },
                    },
                    "required": ["accion"],
                },
            },
        },
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
                "description": "Controla el volumen del sistema. "
                               "Usá accion='subir'/'bajar' para cambios "
                               "RELATIVOS (ej: 'subí el volumen', 'bajá un "
                               "poco'); usá accion='fijar' (con 'valor' 0-100) "
                               "para poner un nivel EXACTO (ej: 'poné el "
                               "volumen en 10', 'dejalo al 50%'); usá "
                               "'silenciar' o 'desmutear' para el mute real.",
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
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "buscar_archivo",
                "description": "Busca un archivo/carpeta en la PC con el "
                               "indexador Everything. Permite filtrar por "
                               "tipo de archivo y opcionalmente ABRIR el "
                               "resultado. Ej: 'buscá un pdf de física', "
                               "'buscá el informe y abrilo', 'buscá capturas "
                               "png'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Palabra o nombre a buscar "
                                           "(sin la extensión).",
                        },
                        "extension": {
                            "type": "string",
                            "description": "Opcional: filtra por extensión "
                                           "(ej: 'pdf', 'docx', 'mp4').",
                        },
                        "abrir_carpeta": {
                            "type": "boolean",
                            "description": "Si True, abre el Explorador con "
                                           "el archivo seleccionado; si False "
                                           "(default), abre el archivo "
                                           "directo.",
                        },
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "programar_accion",
                "description": "Programa una acción DIFERIDA en el tiempo: "
                               "apagar/reiniciar/suspender la PC o un "
                               "recordatorio hablado, dentro de N minutos. "
                               "ACCIÓN IMPORTANTE: el sistema pedirá "
                               "confirmación. Ej: 'suspendé la pc en 5 "
                               "minutos', 'recordame sacar la basura en 10 "
                               "minutos'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["apagar", "reiniciar", "suspender",
                                     "recordatorio"],
                            "description": "Qué programar.",
                        },
                        "en_minutos": {
                            "type": "integer",
                            "description": "Dentro de cuántos minutos "
                                           "disparar la acción.",
                        },
                        "mensaje": {
                            "type": "string",
                            "description": "Texto del recordatorio (solo "
                                           "para accion='recordatorio').",
                        },
                    },
                    "required": ["accion", "en_minutos"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancelar_accion_programada",
                "description": "Cancela la última acción programada (apagado/"
                               "suspensión/recordatorio diferido). Ej: "
                               "'cancelá el apagado'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "Opcional: id de la tarea a "
                                           "cancelar. Si se omite, cancela "
                                           "la última programada.",
                        },
                    },
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        """Prepara recursos opcionales de Windows de forma diferida."""
        super().initialize(event_bus)
        self._w = None  # cache de módulos win32
        self._importar_windows()
        logger.info("Plugin system_control listo.")

    def _importar_windows(self) -> None:
        """Importa los módulos de Windows (win32gui/con/api) una vez."""
        if self._w is not None:
            return
        try:
            import win32gui  # type: ignore
            import win32con  # type: ignore
            import win32api  # type: ignore
            self._w = {"gui": win32gui, "con": win32con, "api": win32api}
        except Exception:  # noqa: BLE001
            logger.warning("Módulos win32 no disponibles.")
            self._w = {}

    # ---------------- Manejo por parte del parser ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve una tool publicada por este plugin."""
        if nombre_tool == "abrir_programa":
            return self.abrir_programa(str(args.get("nombre", "")))
        if nombre_tool == "cerrar_programa":
            return self.cerrar_programa(str(args.get("nombre", "")))
        if nombre_tool == "controlar_brillo":
            return self.controlar_brillo(str(args.get("accion", "")),
                                         args.get("valor"))
        if nombre_tool == "listar_ventanas":
            return self.listar_ventanas_abiertas()
        if nombre_tool == "mover_ventana":
            return self.mover_ventana(str(args.get("nombre", "")),
                                      args.get("monitor", 1))
        if nombre_tool == "minimizar_ventana":
            return self.minimizar_ventana(str(args.get("nombre", "")))
        if nombre_tool == "control_energia":
            return self.control_energia(str(args.get("accion", "")))
        if nombre_tool == "programar_accion":
            return self.programar_accion(
                str(args.get("accion", "")),
                args.get("en_minutos"),
                str(args.get("mensaje", "") or ""),
                contexto)
        if nombre_tool == "cancelar_accion_programada":
            return self.cancelar_accion_programada(args.get("id"), contexto)
        if nombre_tool == "control_multimedia":
            return self.control_multimedia(str(args.get("accion", "")))
        if nombre_tool == "ajustar_volumen":
            return self.ajustar_volumen(str(args.get("accion", "")),
                                        args.get("paso"),
                                        args.get("valor"))
        if nombre_tool == "buscar_archivo":
            # Caso especial: si viene una "ruta_elegida" (de una desambiguación
            # ya resuelta), abrimos ESO directo sin volver a buscar.
            ruta_elegida = args.get("ruta_elegida")
            if ruta_elegida:
                return self._abrir_resultado(
                    str(ruta_elegida), bool(args.get("abrir_carpeta", False)))
            return self.buscar_archivo(
                str(args.get("nombre", "")),
                args.get("extension"),
                bool(args.get("abrir_carpeta", False)))
        return None

    # ---------------- Acciones: programas ---------------- #
    def abrir_programa(self, nombre: str) -> str:
        """Abre un programa por nombre/alias (usa AppOpener diferido)."""
        nombre = nombre.lower().strip()
        app = _APPS.get(nombre) or self._buscar_alias(nombre)
        if app is None:
            return f"No sé qué programa es '{nombre}'."

        try:
            from AppOpener import open as app_open  # lazy
        except Exception:  # noqa: BLE001
            logger.exception("Falta la librería AppOpener.")
            return "No tengo el módulo AppOpener para abrir programas."

        try:
            app_open(app, match_closest=True)
            return f"Abriendo {nombre}."
        except Exception as e:  # noqa: BLE001
            logger.error("Error abriendo %s: %s", app, e)
            return f"No pude abrir {nombre}."

    def _buscar_alias(self, nombre: str) -> Optional[str]:
        """Coincidencia parcial del nombre dentro del mapa de apps."""
        for clave, valor in _APPS.items():
            if clave in nombre or nombre in clave:
                return valor
        return None

    def cerrar_programa(self, nombre: str) -> str:
        """Cierra (mata) el proceso asociado a un programa (seguro).

        Sólo permite cerrar procesos de la lista blanca (_PROCESOS o la
        config `app_whitelist`). No usa ``shell=True`` para evitar
        inyección de comandos.
        """
        cfg = None
        try:
            import config as config_mod  # noqa: ruta segura
            config_mod.cargar()  # asegura que el singleton esté cargado
            cfg = config_mod.config
        except Exception:  # noqa: BLE001
            cfg = None

        blanca = set(_PROCESOS.keys())
        if cfg is not None:
            blanca |= set(getattr(cfg, "app_whitelist", []) or [])

        alias = nombre.lower().strip()
        proceso = _PROCESOS.get(alias)
        if not proceso:
            # No está en el mapa fijo. Solo se permite si el alias coincide
            # EXACTAMENTE con algún elemento de la lista blanca (no substring).
            if alias not in blanca:
                return f"No está permitido cerrar '{nombre}'."
            proceso = alias if alias.endswith(".exe") else alias + ".exe"

        try:
            subprocess.run(
                ["taskkill", "/IM", proceso, "/T"], shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.4)
            subprocess.run(
                ["taskkill", "/F", "/IM", proceso, "/T"], shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Cerré {nombre}."
        except Exception as e:  # noqa: BLE001
            logger.error("Error cerrando %s: %s", nombre, e)
            return f"No pude cerrar {nombre}."

    # ---------------- Energía de la PC (apagar/reiniciar/suspender) ---------------- #
    def control_energia(self, accion: str) -> str:
        """Apaga, reinicia o suspende la PC.

        SOLO se ejecuta tras confirmación explícita (ese gating lo hace el
        CommandParser antes de llegar acá). `accion` ∈ {apagar, reiniciar,
        suspender}.
        """
        accion = (accion or "").lower().strip()
        try:
            if accion == "apagar":
                subprocess.run(["shutdown", "/s", "/t", "5"], shell=False,
                               check=True)
                return "Voy a apagar la PC en unos segundos."
            if accion == "reiniciar":
                subprocess.run(["shutdown", "/r", "/t", "5"], shell=False,
                               check=True)
                return "Voy a reiniciar la PC en unos segundos."
            if accion == "suspender":
                subprocess.run(["rundll32", "powrprof.dll,SetSuspendState",
                                "0,1,0"], shell=False, check=True)
                return "Voy a suspender la PC."
            return ("No entendí la acción. Puedo apagar, reiniciar o "
                    "suspender la PC.")
        except Exception as e:  # noqa: BLE001
            logger.error("Error en control_energia(%s): %s", accion, e)
            return "No pude ejecutar la acción de energía."

    # ---------------- Acciones DIFERIDAS (scheduler) ---------------- #
    def programar_accion(self, accion: str, en_minutos: Any, mensaje: str,
                         contexto: Dict[str, Any]) -> str:
        """Programa una acción diferida usando el Scheduler del contexto.

        SOLO se llega acá tras la confirmación del usuario (el gating lo hace
        el CommandParser). `accion` ∈ {apagar, reiniciar, suspender,
        recordatorio}.

        El scheduler vive en ``contexto["scheduler"]`` (lo inyecta main.py). Si
        no está disponible, devolvemos un mensaje claro en vez de romper.
        """
        accion = (accion or "").lower().strip()
        scheduler = (contexto or {}).get("scheduler")
        if scheduler is None:
            logger.error("No hay scheduler en el contexto; no puedo programar.")
            return "No tengo el programador de tareas disponible ahora."

        # Convertimos minutos a segundos de forma robusta.
        try:
            minutos = float(en_minutos)
        except (TypeError, ValueError):
            return "Decime en cuántos minutos lo programo (por ejemplo, 5)."
        if minutos < 0:
            minutos = 0
        segundos = minutos * 60.0
        minutos_txt = int(minutos) if minutos == int(minutos) else minutos

        if accion == "recordatorio":
            texto = mensaje or "un recordatorio"
            descripcion = f"Recordatorio: {texto}"
            callback = self._crear_callback_recordatorio(contexto, texto)
            scheduler.programar(segundos, callback, descripcion)
            return f"Dale, en {minutos_txt} minutos te aviso: {texto}."

        if accion not in ("apagar", "reiniciar", "suspender"):
            return ("No entendí qué programar. Puedo apagar, reiniciar, "
                    "suspender o poner un recordatorio.")

        mapa = {"apagar": "apago la PC", "reiniciar": "reinicio la PC",
                "suspender": "suspendo la PC"}
        descripcion = f"{accion.capitalize()} diferido"

        def _callback() -> None:
            # Al disparar, ejecutamos la MISMA acción de energía.
            logger.info("[Scheduler] Ejecutando '%s' programado.", accion)
            self.control_energia(accion)

        scheduler.programar(segundos, _callback, descripcion)
        return f"Dale, en {minutos_txt} minutos {mapa[accion]}."

    def _crear_callback_recordatorio(self, contexto: Dict[str, Any],
                                     mensaje: str):
        """Devuelve un callback que hace decir el recordatorio por voz.

        El `voice` viene en ``contexto["voice"]`` (lo inyecta main.py). Si no
        hay voz, el recordatorio se imprime por consola igual.
        """
        def _callback() -> None:
            frase = f"¡Recordatorio! {mensaje}"
            voice = (contexto or {}).get("voice")
            if voice is not None:
                try:
                    voice.decir(frase)
                    return
                except Exception:  # noqa: BLE001
                    logger.exception("No pude decir el recordatorio por voz.")
            # Fallback: consola.
            print(f"[Recordatorio] {mensaje}")
        return _callback

    def cancelar_accion_programada(self, tarea_id: Any,
                                   contexto: Dict[str, Any]) -> str:
        """Cancela una acción diferida (por id, o la última si no se da id)."""
        scheduler = (contexto or {}).get("scheduler")
        if scheduler is None:
            return "No tengo el programador de tareas disponible ahora."

        pendientes = scheduler.listar_pendientes()
        if not pendientes:
            return "No hay ninguna acción programada para cancelar."

        tarea_id = None if tarea_id in (None, "", 0) else tarea_id
        try:
            tarea_id = int(tarea_id) if tarea_id is not None else None
        except (TypeError, ValueError):
            tarea_id = None

        if scheduler.cancelar(tarea_id):
            return "Listo, cancelé la acción programada."
        return "No encontré esa acción programada."

    # ---------------- Multimedia (teclas virtuales Windows) ---------------- #
    # Códigos de VK (virtual key) para reproducción multimedia.
    # NOTA: el MUTE real NO se maneja por tecla "toggle" (0xAD): se hace con
    # pycaw/SetMute en `ajustar_volumen()` para que sea una acción 100% predecible
    # (silenciar SIEMPRE silencia, desmutear SIEMPRE devuelve el sonido).
    _VK = {
        "play_pausa": 0xB3, "siguiente": 0xB0, "anterior": 0xB1,
        "subir_volumen": 0xAF, "bajar_volumen": 0xAE,
    }
    _NOMBRE_KEYBOARD = {
        "play_pausa": "play/pause media",
        "siguiente": "next track", "anterior": "previous track",
        "subir_volumen": "volume up", "bajar_volumen": "volume down",
    }

    def _enviar_tecla_virtual(self, clave: str) -> bool:
        """Envía una tecla multimedia/volumen por hardware (VK).

        No se usa para silenciar (eso se resuelve con pycaw). Intenta
        primero con `keyboard` y, si no, con ctypes ``keybd_event``.
        """
        try:
            import keyboard  # import tardío
            nombre = self._NOMBRE_KEYBOARD.get(clave)
            if nombre:
                keyboard.send(nombre)
                return True
        except Exception:  # noqa: BLE001
            pass  # seguimos con el fallback por ctypes

        vk = self._VK.get(clave)
        if vk is None:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, 0, 0)   # KEY down
            time.sleep(0.02)
            user32.keybd_event(vk, 0, 2, 0)   # KEYEVENTF_KEYUP = 2
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("No se pudo enviar la tecla virtual %s: %s", clave, e)
            return False

    # ---------------- Volumen con pycaw (determinístico) ---------------- #
    def _volumen_pycaw(self):
        """Devuelve el objeto de volumen del endpoint (IAudioEndpointVolume)
        o None si pycaw no está disponible."""
        try:
            from pycaw.pycaw import AudioUtilities
            dev = AudioUtilities.GetSpeakers()
            return getattr(dev, "EndpointVolume", None)
        except Exception as e:  # noqa: BLE001
            logger.debug("pycaw no disponible (%s).", e)
            return None

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
                        valor: Optional[int] = None) -> str:
        """Controla el volumen del sistema.

        Acciones:
        - ``subir``/``bajar``: cambio RELATIVO en pasos (default 5) sobre el
          nivel actual.
        - ``fijar``: pone un nivel EXACTO (``valor`` 0-100); si el nuevo valor
          es > 0, reactiva el audio en caso de estar silenciado.
        - ``silenciar``/``desmutear``: mute determinístico vía pycaw
          (no es toggle; no se invierte por llamada accidental).
        """
        accion = (accion or "").lower().strip()

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

        paso = max(1, min(100, int(paso or 5)))

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
                return f"Volumen en {pct}%."
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
                return f"Volumen en {nivel}%."
            # nivel == 0: lo dejamos en 0 (silencio por nivel, no mute).
            return "Volumen en 0%."
        except Exception as e:  # noqa: BLE001
            logger.error("Error fijando volumen con pycaw: %s", e)
            return "No pude fijar el volumen."

    # ---------------- Búsqueda con Everything (es.exe) ---------------- #
    def buscar_archivo(self, nombre: str, extension: Optional[str] = None,
                       abrir_carpeta: bool = False,
                       max_resultados: int = 20) -> str:
        """Busca un archivo con Everything y opcionalmente lo abre.

        Args:
            nombre: término a buscar (sin la extensión).
            extension: opcional, filtra por tipo (ej. "pdf").
            abrir_carpeta: si True, abre el Explorador con el archivo
                seleccionado; si False, abre el archivo directo.
            max_resultados: máximo a pedir/mostrar (interno).

        Comportamiento:
            - Filtra por extensión (primero con ``ext:`` nativo de es.exe y,
              además, en Python para asegurar el resultado).
            - Ordena priorizando coincidencias en el NOMBRE del archivo (no en
              la ruta de carpetas).
            - Si hay varios resultados razonables y ninguno destaca, NO abre el
              primero: lista los nombres y pregunta cuál abrir.
            - Si hay un ganador claro, lo abre y confirma por NOMBRE.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            return "¿Qué archivo querés que busque?"

        ext = _normalizar_extension(extension)

        es = self._ruta_es_ejecutable()
        if not es:
            return ("No tengo el indexador Everything (es.exe) configurado. "
                    "Copiá es.exe a la carpeta bin/ o definí su ruta y reintentá.")

        # Construcción de la query de es.exe: la extensión va DENTRO del string
        # de búsqueda (operador `ext:`), no como flag separado.
        consulta = f"ext:{ext} {nombre}" if ext else nombre

        rutas = self._ejecutar_es(es, consulta, max_resultados)
        print(f"[buscar_archivo] término='{nombre}' ext={ext} "
              f"-> {len(rutas)} resultado(s) crudos.")

        # IMPORTANTE: comprobamos que combinar `ext:` con el término funciona
        # en ESTA versión de es.exe (v1.1.0.37 no siempre matchea bien). Si no
        # trajo nada y hay extensión, reintentamos solo con el término y
        # filtramos por extensión en Python (fallback robusto).
        if ext and not rutas:
            logger.info("es.exe no dio resultados con 'ext:'; reintento solo "
                        "con el término y filtro en Python.")
            rutas = self._ejecutar_es(es, nombre, max_resultados)
            print(f"[buscar_archivo] fallback sin 'ext:' -> "
                  f"{len(rutas)} resultado(s) crudos.")

        # Fuzzy/tolerancia: si el término tal cual no trajo NADA, probamos
        # variantes. Lo más útil: quitar espacios ("lista animes" -> "listaanimes")
        # y buscar tokenizado. Esto cubre transcripciones de voz con espacios
        # o separadores que no coinciden con el nombre real del archivo.
        if not rutas and " " in nombre:
            for variante in self._variantes_busqueda(nombre):
                rutas = self._ejecutar_es(es, variante, max_resultados)
                if rutas:
                    print(f"[buscar_archivo] sin coincidencia directa; "
                          f"variante '{variante}' dio {len(rutas)} resultado(s).")
                    break

        # Refuerzo en Python del filtro por extensión (siempre).
        if ext:
            filtradas = [r for r in rutas
                         if os.path.splitext(r)[1].lower().lstrip(".") == ext]
            if filtradas:
                rutas = filtradas
            elif rutas:
                logger.info("Nada con extensión .%s; descarto los %d "
                            "resultados de otras extensiones.", ext, len(rutas))
                rutas = []

        if not rutas:
            etiqueta = f" .{ext}" if ext else ""
            print(f"[buscar_archivo] SIN resultados para '{nombre}'{etiqueta}.")
            return f"No encontré archivos{etiqueta} para '{nombre}'."

        # Ordenamos por coincidencia en el NOMBRE del archivo (no en carpetas).
        terminos = [t for t in _normalizar_texto(nombre).split() if t]
        # Preservamos el orden original como desempate estable.
        rankeadas = sorted(
            ((self._puntaje_resultado(r, terminos), i, r)
             for i, r in enumerate(rutas)),
            key=lambda par: (-par[0], par[1]))
        rutas_ord = [r for _, _, r in rankeadas]
        puntajes_ord = [p for p, _, _ in rankeadas]

        # ¿Hay ganador claro o hay que preguntar?
        mejor = self._elegir_mejor(rutas_ord, puntajes_ord, nombre=nombre)
        if mejor is None:
            # Varios razonables y ninguno destaca: NO abrimos. Devolvemos un
            # dict de DESAMBIGUACIÓN para que el parser guarde el estado y le
            # pregunte al usuario cuál quiere (así el siguiente "el tercero"
            # tiene contexto). El parser lo convierte en pregunta + opciones.
            print("[buscar_archivo] Varios resultados, preguntando al usuario.")
            top = rutas_ord[:5]
            opciones = [
                {
                    "indice": i + 1,
                    "etiqueta": os.path.basename(r),
                    # El "valor" es lo que completa la tool al elegir.
                    "valor": {"nombre": nombre, "extension": ext,
                              "abrir_carpeta": abrir_carpeta,
                              "ruta_elegida": r},
                }
                for i, r in enumerate(top)
            ]
            return {
                "desambiguar": True,
                "tool_origen": "buscar_archivo",
                "args_origen": {"nombre": nombre, "extension": ext,
                                "abrir_carpeta": abrir_carpeta},
                "opciones": opciones,
            }

        print(f"[buscar_archivo] ganador claro: {os.path.basename(mejor)} "
              f"(puntaje={puntajes_ord[0]}) -> abriendo.")
        return self._abrir_resultado(mejor, abrir_carpeta)

    @staticmethod
    def _variantes_busqueda(nombre: str) -> List[str]:
        """Variantes de búsqueda para tolerar espacios/separadores de más.

        Ej: ``"lista animes"`` -> ``["listaanimes", "lista", "animes"]``.
        Se probarán en orden hasta que una dé resultados.
        """
        variantes: List[str] = []
        compacto = _clave_compacta(nombre)
        if compacto and compacto != _normalizar_texto(nombre):
            variantes.append(compacto)
        # Último recurso: buscar por tokens sueltos (el más largo primero).
        tokens = sorted((t for t in _normalizar_texto(nombre).split() if len(t) >= 3),
                        key=len, reverse=True)
        variantes.extend(tokens)
        return variantes

    def _ejecutar_es(self, es: str, consulta: str,
                     max_resultados: int) -> List[str]:
        """Ejecuta es.exe con `consulta` y devuelve la lista de rutas.

        Devuelve [] ante error/timeout (el caller decide el mensaje). No usa
        ``shell=True`` para evitar inyección de comandos.
        """
        try:
            resp = subprocess.run(
                [str(es), "-s", "-n", str(max_resultados), consulta],
                capture_output=True, text=True, timeout=15, shell=False)
        except subprocess.TimeoutExpired:
            logger.warning("es.exe agotó el timeout para '%s'.", consulta)
            return []
        except Exception as e:  # noqa: BLE001
            logger.error("Error buscando con Everything: %s", e)
            return []
        return [l.strip().strip('"') for l in (resp.stdout or "").splitlines()
                if l.strip()]

    # Extensiones de "accesos directos" que son ruido: casi nunca es lo que
    # el usuario quiere abrir cuando busca un archivo real.
    _EXT_RUIDO = {"lnk", "url", "tmp", "crdownload", "part"}

    def _puntaje_resultado(self, ruta: str, terminos: List[str]) -> int:
        """Puntúa un resultado priorizando coincidencias en el NOMBRE.

        Más puntos = mejor. Comparaciones case-insensitive Y sin acentos.
        Además:
          - Penaliza accesos directos (.lnk) y archivos temporales, que suelen
            ensuciar la lista (p. ej. los .LNK en AppData\\...\\Recent).
          - Tolera variantes compactas: "lista animes" puntúa alto contra
            "ListaAnimes" (sin separadores).
        """
        nombre_arch = os.path.basename(ruta).lower()
        nombre_sin_ext = os.path.splitext(nombre_arch)[0]
        carpeta = os.path.dirname(ruta).lower()
        ext = os.path.splitext(nombre_arch)[1].lstrip(".")

        # Versiones normalizadas (sin acentos).
        nombre_arch_n = _normalizar_texto(nombre_arch)
        nombre_sin_ext_n = _normalizar_texto(nombre_sin_ext)
        carpeta_n = _normalizar_texto(carpeta)
        nombre_compacto = _clave_compacta(nombre_sin_ext)
        consulta_compacta = _clave_compacta(" ".join(terminos))

        puntos = 0
        for t in terminos:
            tn = _normalizar_texto(t)
            if not tn:
                continue
            if tn in nombre_arch_n:
                puntos += 10
            if tn in nombre_sin_ext_n:
                puntos += 5   # coincidencia en el nombre sin extensión
            elif tn in carpeta_n:
                puntos += 1   # solo aparece en carpetas padre: casi nada

        # Bonus fuerte por coincidencia COMPACTA del nombre completo.
        # "lista animes" -> "listaanimes" == "ListaAnimes" -> match casi exacto.
        if consulta_compacta and consulta_compacta == nombre_compacto:
            puntos += 50
        elif consulta_compacta and consulta_compacta in nombre_compacto:
            puntos += 20

        # Penalizar accesos directos y temporales (ruido). Se usa un valor alto
        # para que un .lnk NUNCA gane por sobre un archivo real aunque el
        # nombre del archivo real solo contenga el término embebido.
        if ext in self._EXT_RUIDO:
            puntos -= 60

        return puntos

    def _coincidencia_nombre_exacta(self, ruta: str, nombre: str) -> bool:
        """¿El nombre del archivo coincide (compacto) con lo buscado?

        Se usa para decidir un ganador CLARO: si el nombre sin extensión,
        compactado, es igual al término buscado compactado, no hace falta
        preguntar aunque haya otros resultados (p. ej. los .lnk).
        """
        nombre_arch = os.path.basename(ruta)
        sin_ext = os.path.splitext(nombre_arch)[0]
        buscado = _clave_compacta(nombre)
        return bool(buscado) and _clave_compacta(sin_ext) == buscado

    def _elegir_mejor(self, rutas: List[str],
                      puntajes: Optional[List[int]] = None,
                      nombre: str = "") -> Optional[str]:
        """Devuelve la mejor ruta si hay ganador CLARO; si no, None.

        Criterios (en orden):
          1. Si el mejor resultado tiene coincidencia EXACTA de nombre (compacto)
             mientras los demás no, se elige aunque el margen sea chico. Esto
             resuelve el caso típico "ListaAnimes.xlsm" vs dos .LNK de acceso.
          2. Si no, se exige un margen >= 5 puntos (una coincidencia de nombre)
             sobre el segundo. Si empatan, devolvemos None para que el usuario
             elija y no abramos al azar.
        """
        if not rutas:
            return None
        if len(rutas) == 1:
            return rutas[0]
        if not puntajes:
            # Sin puntajes no podemos juzgar claridad: pedimos que elija.
            return None

        # 1) Coincidencia exacta de nombre en el TOP (y no en el segundo).
        if nombre:
            if (self._coincidencia_nombre_exacta(rutas[0], nombre)
                    and not self._coincidencia_nombre_exacta(rutas[1], nombre)):
                return rutas[0]

        # 2) Margen de puntaje clásico.
        margen = puntajes[0] - puntajes[1]
        return rutas[0] if margen >= 5 else None

    def _abrir_resultado(self, ruta: str, abrir_carpeta: bool) -> str:
        """Abre `ruta` (o su carpeta con el archivo seleccionado).

        Devuelve una confirmación por NOMBRE (nunca la ruta completa).
        """
        nombre_arch = os.path.basename(ruta)
        try:
            if abrir_carpeta:
                # OJO: son DOS elementos; "/select," va PEGADO a la ruta en el
                # mismo string (sin espacio), o Windows lo interpreta mal.
                subprocess.Popen(["explorer", f"/select,{ruta}"])
                return f"Te la abrí en la carpeta: {nombre_arch}"
            os.startfile(ruta)  # type: ignore[attr-defined]
            return f"¡Abrí {nombre_arch}!"
        except Exception as e:  # noqa: BLE001
            logger.error("No pude abrir '%s': %s", ruta, e)
            return f"No pude abrir {nombre_arch}."

    def _ruta_es_ejecutable(self) -> Optional[str]:
        """Devuelve la ruta al es.exe si existe (bin/ o la de config)."""
        import config as config_mod  # ruta segura, sin deps pesadas
        base = config_mod.BASE_DIR

        try:
            ruta_conf = str(config_mod.config.get("ruta_everything_es", "") or "").strip()
        except Exception:  # noqa: BLE001
            ruta_conf = ""

        candidatos: List[str] = []
        if ruta_conf:
            candidatos.append(ruta_conf)
        # Fallback por convención: <raíz>/bin/es.exe
        candidatos.append(str(base / "bin" / "es.exe"))

        from pathlib import Path
        for c in candidatos:
            p = Path(c)
            if not p.is_absolute():
                p = base / p
            if p.exists():
                return str(p)
        return None

    # ---------------- Brillo ---------------- #
    def controlar_brillo(self, accion: str, valor: Optional[int] = None) -> str:
        """Sube, baja o fija el brillo de la pantalla principal."""
        try:
            import screen_brightness_control as sbc  # lazy
        except Exception:  # noqa: BLE001
            return "No tengo control de brillo disponible."

        try:
            actual = sbc.get_brightness(display=0)[0]
        except Exception:  # noqa: BLE001
            return "No pude leer el brillo."

        try:
            if accion == "fijar" and valor is not None:
                nuevo = max(0, min(100, int(valor)))
            elif accion == "subir":
                paso = int(valor or 15)
                nuevo = max(0, min(100, actual + paso))
            elif accion == "bajar":
                paso = int(valor or 15)
                nuevo = max(0, min(100, actual - paso))
            else:
                return "No entendí qué querés hacer con el brillo."
            sbc.set_brightness(nuevo)
            return f"Brillo en {nuevo}%."
        except Exception:  # noqa: BLE001
            return "No pude cambiar el brillo."

    # ---------------- Ventanas (Windows) ---------------- #
    def _enumerar_ventanas(self) -> List[Any]:
        """Lista de (hwnd, título) de ventanas visibles."""
        self._importar_windows()
        if not self._w:
            return []
        gui = self._w["gui"]
        ventanas: List[Any] = []

        def _cb(hwnd: Any, _extra: Any) -> None:
            if gui.IsWindowVisible(hwnd) and gui.GetWindowText(hwnd):
                ventanas.append((hwnd, gui.GetWindowText(hwnd)))

        gui.EnumWindows(_cb, None)
        return ventanas

    def listar_ventanas_abiertas(self) -> str:
        """Devuelve un texto con las ventanas abiertas actualmente."""
        ventanas = self._enumerar_ventanas()
        if not ventanas:
            return "No hay ventanas visibles."
        nombres = [titulo for _, titulo in ventanas[:15]]
        return "Ventanas abiertas:\n- " + "\n- ".join(nombres)

    def _hwnd_de(self, nombre_app: str) -> Optional[Any]:
        """Busca el hwnd de la primera ventana cuyo título coincide."""
        nombre_l = nombre_app.lower().strip()
        for hwnd, titulo in self._enumerar_ventanas():
            if nombre_l in titulo.lower():
                return hwnd
        return None

    def minimizar_ventana(self, nombre_app: str) -> str:
        """Minimiza la ventana del programa indicado."""
        hwnd = self._hwnd_de(nombre_app)
        if not hwnd:
            return f"No encontré ninguna ventana de {nombre_app}."
        try:
            self._w["gui"].ShowWindow(hwnd, self._w["con"].SW_MINIMIZE)
            return f"Minimicé {nombre_app}."
        except Exception:  # noqa: BLE001
            return f"No pude minimizar {nombre_app}."

    def mover_ventana(self, nombre_app: str, monitor: int = 1) -> str:
        """Mueve la ventana de `nombre_app` al `monitor` indicado."""
        hwnd = self._hwnd_de(nombre_app)
        if not hwnd:
            return f"No encontré ninguna ventana de {nombre_app}."

        try:
            monitores = self._w["api"].EnumDisplayMonitors()
        except Exception:  # noqa: BLE001
            monitores = []

        if not monitores:
            return "No encontré otro monitor."

        idx = int(monitor) - 1
        if idx < 0 or idx >= len(monitores):
            idx = 0

        info = self._w["api"].GetMonitorInfo(monitores[idx][0])
        x1, y1, x2, y2 = info["Monitor"]

        try:
            gui, con = self._w["gui"], self._w["con"]
            gui.ShowWindow(hwnd, con.SW_RESTORE)
            gui.MoveWindow(hwnd, x1, y1, x2 - x1, y2 - y1, True)
            gui.ShowWindow(hwnd, con.SW_MAXIMIZE)
            return f"Llevé {nombre_app} al monitor {monitor}."
        except Exception:  # noqa: BLE001
            return f"No pude mover {nombre_app}."
