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
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.system_control")

# Mapa nombre -> identificador de apertura (alias del sistema original).
#
# IMPORTANTE — qué se puede y qué NO se puede agregar acá:
#   * El VALOR es un identificador que resuelve AppOpener contra el menú Inicio
#     (ej. "code", "brave", "discord"); NO es una ruta a un .exe. Poner una
#     ruta completa acá NO funciona (AppOpener no abre rutas).
#   * Entonces, para un programa/juego que NO aparece ni en AppOpener, ni en la
#     biblioteca de Steam (STEAM_RUTA / rutas típicas), ni en JUEGOS_EPIC, ni en
#     el índice de Everything, la única forma de abrirlo por voz hoy es:
#       - agregarlo acá SOLO si AppOpener lo reconoce por nombre, o
#       - agregarlo a JUEGOS_EPIC (config_local.py) si es de Epic, o
#       - instalarlo dentro de una biblioteca de Steam detectada.
#   * Ejemplo de entrada (sin datos reales):
#       # "mi_juego": "mi_juego",   # alias del menú Inicio que entiende AppOpener
#
# PROPUESTA (no implementada todavía): una tool `agregar_programa_favorito`
# análoga a `guardar_carpeta_favorita` de miku/plugins/productividad/favoritos.py, o aceptar rutas
# absolutas acá y abrirlas con os.startfile() en abrir_programa(), para poder
# registrar juegos instalados "a mano" sin depender de AppOpener.
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

# Máximo de resultados que le pedimos a es.exe al buscar ejecutables (fallback
# de abrir_programa): más que en una búsqueda de archivo, porque los .exe
# suelen venir con mucho ruido de carpetas de sistema.
_MAX_RESULTADOS_APPS = 30


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
}

# Procesos que NUNCA se cierran por voz, aunque estén en ``app_whitelist``:
# matar el shell de Windows deja el escritorio sin barra ni iconos.
_NUNCA_CERRAR = frozenset({"explorer", "explorador", "explorer.exe"})


def _a_entero(valor: Any, defecto: int) -> int:
    """Convierte ``valor`` a int; si no se puede (None, "diez"...), ``defecto``.

    Los argumentos de las tools vienen del LLM y pueden ser cualquier cosa: un
    ``int()`` sin proteger tiraba una excepción que el despachador tragaba y el
    usuario oía "¡Listo!" sin que se hubiera hecho nada.
    """
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return defecto


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


# Alias de TÍTULO de ventana: el usuario dice el nombre corto ("VSCode") pero
# el título real es distinto ("... - Visual Studio Code"). Mapeamos el nombre
# normalizado del alias -> fragmentos (normalizados) que SÍ aparecen en el título.
_ALIAS_TITULO: Dict[str, tuple] = {
    "vscode": ("visual studio code",),
    "code": ("visual studio code",),
    "visual studio": ("visual studio code",),
    "brave": ("brave",),
    # "el navegador" (sin aclarar cuál): probamos los navegadores típicos.
    "navegador": ("brave", "google chrome", "microsoft edge", "firefox"),
    "chrome": ("google chrome",),
    "edge": ("microsoft edge",),
    "explorador": ("explorador de archivos", "file explorer"),
    "explorer": ("explorador de archivos", "file explorer"),
    "tidal": ("tidal",),
    "spotify": ("spotify",),
    "discord": ("discord",),
    "steam": ("steam",),
    "notepad": ("bloc de notas", "notepad"),
    "bloc de notas": ("bloc de notas", "notepad"),
    "calculadora": ("calculadora", "calculator"),
}


class SystemControl(Plugin):
    """Control básico del sistema (abrir/cerrar, brillo, ventanas)."""

    nombre = "system_control"
    descripcion = "Control básico del sistema (programas, brillo, ventanas)."

    #: Apagar/reiniciar/suspender (ahora o programado) exigen confirmación.
    peligrosas = frozenset({"control_energia", "programar_accion"})

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "abrir_programa",
                "description": "Abre un programa instalado o un JUEGO de esta "
                               "PC (Discord, Brave, VSCode, Tidal, "
                               "calculadora, o juegos como 'PEAK'). Si no "
                               "está entre las apps conocidas ni en el menú "
                               "Inicio, lo busca por nombre en el disco "
                               "(Everything) y, si hay varios, pregunta cuál. "
                               "No usar para sitios web.",
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
                "description": "Mueve una ventana abierta a otro monitor "
                               "(maximizada). Para ocupar solo una mitad del "
                               "monitor, usá posicionar_ventana.",
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
                "name": "posicionar_ventana",
                "description": "Coloca una ventana en una posición del "
                               "monitor: mitad izquierda/derecha/arriba/"
                               "abajo, o completa (maximizada). Ej: 'ponéme "
                               "Brave a la mitad izquierda', 'poné Discord a "
                               "la derecha'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string",
                                   "description": "Nombre de la app/ventana."},
                        "posicion": {
                            "type": "string",
                            "enum": ["izquierda", "derecha", "arriba",
                                     "abajo", "completa"],
                            "description": "Dónde ubicarla dentro del monitor.",
                        },
                        "monitor": {"type": "integer",
                                    "description": "Monitor (1-based, default 1)."},
                    },
                    "required": ["nombre", "posicion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "organizar_ventanas",
                "description": "Organiza ventanas como el Snap de Windows 11 "
                               "(mitad y mitad). Con DOS ventanas: una en la "
                               "mitad izquierda y otra en la derecha, ambas "
                               "restauradas y al frente. Con UNA sola: la "
                               "manda a la mitad indicada en 'posicion'. "
                               "Ej: 'poné Brave a la izquierda y VSCode a la "
                               "derecha', 'splitea la pantalla con Discord y "
                               "el navegador', 'poné Discord a la mitad "
                               "derecha'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ventana_izquierda": {
                            "type": "string",
                            "description": "App/ventana de la mitad izquierda "
                                           "(o la única ventana a mover si no "
                                           "se pasa ventana_derecha).",
                        },
                        "ventana_derecha": {
                            "type": "string",
                            "description": "Opcional: app/ventana de la mitad "
                                           "derecha. Si se omite, solo se "
                                           "mueve ventana_izquierda.",
                        },
                        "posicion": {
                            "type": "string",
                            "enum": ["izquierda", "derecha"],
                            "description": "Solo si no hay ventana_derecha: a "
                                           "qué mitad mandar la ventana. "
                                           "Default: izquierda.",
                        },
                        "monitor": {"type": "integer",
                                    "description": "Monitor (1-based, default 1)."},
                    },
                    "required": ["ventana_izquierda"],
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
                               "recordatorio hablado. Se indica el momento con "
                               "'en_minutos' (dentro de N minutos) O con 'hora' "
                               "(HH:MM, a hora exacta; si esa hora ya pasó hoy, "
                               "se asume mañana). ACCIÓN IMPORTANTE: el sistema "
                               "pedirá confirmación. Ej: 'suspendé la pc en 5 "
                               "minutos', 'recordame sacar la basura en 10 "
                               "minutos', 'recordame a las 18:30 que salgo'.",
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
                                           "disparar la acción (alternativo a "
                                           "'hora').",
                        },
                        "hora": {
                            "type": "string",
                            "description": "Hora exacta HH:MM (24h) para "
                                           "disparar la acción. Si ya pasó hoy, "
                                           "se entiende mañana.",
                        },
                        "mensaje": {
                            "type": "string",
                            "description": "Texto del recordatorio (solo "
                                           "para accion='recordatorio').",
                        },
                    },
                    "required": ["accion"],
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
        {
            "type": "function",
            "function": {
                "name": "actualizar_biblioteca_juegos",
                "description": "Re-escanea la biblioteca de Steam a demanda "
                               "(para cuando instalás un juego nuevo sin "
                               "reiniciar el asistente). Sin parámetros.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        """Prepara recursos opcionales de Windows de forma diferida."""
        super().initialize(event_bus)
        self._w = None  # cache de módulos win32
        self._importar_windows()
        # Cache de la biblioteca de Steam {nombre_normalizado: appid}. Se
        # rellena al arrancar (lazy, no bloquea si Steam no está) y se puede
        # refrescar con la tool actualizar_biblioteca_juegos.
        self._juegos_steam: Optional[Dict[str, str]] = None
        # Rutas que ofrecimos en la ÚLTIMA desambiguación. ``ruta_elegida`` no
        # está en el schema de las tools (el LLM no debería inventarla): solo
        # se acepta una ruta que nosotros mismos ofrecimos.
        self._rutas_ofrecidas: set = set()
        logger.info("Plugin system_control listo.")

    def _registrar_ofrecidas(self, rutas: List[str]) -> None:
        """Recuerda las rutas de la desambiguación en curso."""
        self._rutas_ofrecidas = {os.path.normcase(str(r)) for r in rutas}

    def _fue_ofrecida(self, ruta: str) -> bool:
        """True si ``ruta`` fue una de las opciones ofrecidas al usuario."""
        return os.path.normcase(ruta) in self._rutas_ofrecidas

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
            # Caso especial: si viene "ruta_elegida" (desambiguación ya
            # resuelta), abrimos ESO directo sin volver a buscar.
            ruta_elegida = args.get("ruta_elegida")
            if ruta_elegida:
                if not self._fue_ofrecida(str(ruta_elegida)):
                    return "Esa opción no es una de las que te ofrecí."
                return self._abrir_resultado(str(ruta_elegida), False)
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
        if nombre_tool == "posicionar_ventana":
            return self.posicionar_ventana(str(args.get("nombre", "")),
                                           str(args.get("posicion", "")),
                                           args.get("monitor", 1))
        # ``dividir_pantalla``/``acoplar_ventanas`` ya no son tools: son
        # funciones internas que usa ``organizar_ventanas``.
        if nombre_tool == "organizar_ventanas":
            return self.organizar_ventanas(
                str(args.get("ventana_izquierda", "") or ""),
                str(args.get("ventana_derecha", "") or ""),
                str(args.get("posicion", "") or "izquierda"),
                args.get("monitor", 1))
        if nombre_tool == "actualizar_biblioteca_juegos":
            return self.actualizar_biblioteca_juegos()
        if nombre_tool == "minimizar_ventana":
            return self.minimizar_ventana(str(args.get("nombre", "")))
        if nombre_tool == "control_energia":
            return self.control_energia(str(args.get("accion", "")))
        if nombre_tool == "programar_accion":
            return self.programar_accion(
                str(args.get("accion", "")),
                args.get("en_minutos"),
                str(args.get("mensaje", "") or ""),
                contexto,
                str(args.get("hora", "") or ""))
        if nombre_tool == "cancelar_accion_programada":
            return self.cancelar_accion_programada(args.get("id"), contexto)
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
        if nombre_tool == "buscar_archivo":
            # Caso especial: si viene una "ruta_elegida" (de una desambiguación
            # ya resuelta), abrimos ESO directo sin volver a buscar.
            ruta_elegida = args.get("ruta_elegida")
            if ruta_elegida:
                if not self._fue_ofrecida(str(ruta_elegida)):
                    return "Esa opción no es una de las que te ofrecí."
                return self._abrir_archivo_buscado(
                    str(ruta_elegida), bool(args.get("abrir_carpeta", False)))
            return self.buscar_archivo(
                str(args.get("nombre", "")),
                args.get("extension"),
                bool(args.get("abrir_carpeta", False)))
        return None

    # ---------------- Acciones: programas ---------------- #
    def abrir_programa(self, nombre: str) -> str:
        """Abre un programa por nombre/alias.

        Orden de búsqueda:
          1. ``_APPS`` + AppOpener (programas instalados / menú Inicio).
          2. Biblioteca de Steam detectada (``steam://rungameid/<appid>``).
          3. Juegos de Epic configurados a mano (``JUEGOS_EPIC`` en config).
        Si no lo encuentra en ninguno, lo dice con una pista útil.
        """
        nombre = nombre.lower().strip()
        app = _APPS.get(nombre) or self._buscar_alias(nombre)

        if app is not None:
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

        # 2) Biblioteca de Steam.
        appid = self._buscar_en_biblioteca_steam(nombre)
        if appid:
            try:
                os.startfile(f"steam://rungameid/{appid}")  # type: ignore[attr-defined]
                logger.info("Lanzando juego de Steam '%s' (appid=%s).",
                            nombre, appid)
                return f"Dale, abriendo {nombre}."
            except Exception as e:  # noqa: BLE001
                logger.error("No pude lanzar el juego de Steam '%s': %s",
                             nombre, e)
                return f"Encontré {nombre} en Steam pero no pude abrirlo."

        # 3) Epic configurado a mano (JUEGOS_EPIC en config_local.py).
        item_epic = self._buscar_en_juegos_epic(nombre)
        if item_epic:
            try:
                os.startfile(  # type: ignore[attr-defined]
                    f"com.epicgames.launcher://apps/{item_epic}?action=launch")
                logger.info("Lanzando juego de Epic '%s' (id=%s).",
                            nombre, item_epic)
                return f"Dale, abriendo {nombre}."
            except Exception as e:  # noqa: BLE001
                logger.error("No pude lanzar el juego de Epic '%s': %s",
                             nombre, e)
                return f"Encontré {nombre} en Epic pero no pude abrirlo."

        # 4) FALLBACK: buscar un ejecutable/acceso directo por NOMBRE con
        #    Everything (mismo motor y scoring que buscar_archivo). Cubre
        #    programas y JUEGOS que no están en _APPS, ni en Steam/Epic, ni en
        #    el menú Inicio (ej: "abrí PEAK").
        resultado = self._buscar_ejecutable_con_everything(nombre)
        if resultado is not None:
            return resultado

        # 5) Nada: mensaje con pista ACCIONABLE (para que la próxima vez sea
        #    instantánea, sin pasar por la búsqueda).
        return (f"No encontré '{nombre}' ni como programa instalado, ni en "
                f"Steam, ni en Epic, ni como ejecutable en el disco. Si lo "
                f"usás seguido, agregalo a _APPS en miku/plugins/sistema/system_control.py "
                f"(o configurá JUEGOS_EPIC / STEAM_RUTA) y la próxima lo abro "
                f"al instante.")

    def _buscar_alias(self, nombre: str) -> Optional[str]:
        """Busca ``nombre`` en el mapa de apps por PALABRAS, no por substring.

        Un substring hacía que ``""`` abriera Brave y que un juego como "Edge
        of Eternity" abriera Edge. Ahora coincide si:
          - ``nombre`` es una palabra de una clave ("code" -> "vs code"), o
          - una clave aparece como palabra en un ``nombre`` corto (hasta 2
            palabras: "brave browser").
        """
        nombre = (nombre or "").strip()
        if len(nombre) < 2:
            return None
        palabras = nombre.split()
        for clave, valor in _APPS.items():
            if nombre in clave.split():
                return valor
            if len(palabras) <= 2 and re.search(
                    rf"\b{re.escape(clave)}\b", nombre):
                return valor
        return None

    # Carpetas típicas donde viven juegos/programas. Si el ejecutable aparece
    # en una de ellas, sumamos un bonus al puntaje (mismo criterio que usa
    # ``buscar_archivo``: coincidencia de nombre + ubicación razonable).
    _CARPETAS_JUEGOS = (
        "steamapps\\common", "steamapps/common",
        "epic games", "gog games", "xboxgames", "riot games", "battle.net",
        "ubisoft", "program files", "program files (x86)",
    )

    def _bonus_carpeta_juego(self, ruta: str) -> int:
        """Bonus de puntaje si la ruta está en una carpeta típica de juegos."""
        carpeta = _normalizar_texto(os.path.dirname(ruta)).replace("/", "\\")
        for patron in self._CARPETAS_JUEGOS:
            if patron in carpeta:
                return 15
        return 0

    def _buscar_ejecutable_con_everything(self, nombre: str) -> Optional[Any]:
        """Busca un ``.exe``/``.lnk`` por nombre con Everything (fallback).

        Se usa cuando ``_APPS`` + AppOpener, Steam y Epic no resolvieron el
        pedido (caso típico: un juego instalado aparte, ej. "abrí PEAK").
        Reutiliza el mismo motor (``_ejecutar_es``), el mismo scoring
        (``_puntaje_resultado``) y el mismo mecanismo de desambiguación que
        ``buscar_archivo``.

        Returns:
            - str: mensaje final (lo abrí / no pude abrirlo).
            - dict: pedido de DESAMBIGUACIÓN (varios candidatos razonables).
            - None: no encontró ningún ejecutable (el caller arma el error).
        """
        es = self._ruta_es_ejecutable()
        if not es:
            logger.info("Sin es.exe no puedo buscar '%s' como ejecutable.",
                        nombre)
            return None
        terminos = [t for t in _normalizar_texto(nombre).split() if t]
        if not terminos:
            return None

        ext_ok = (".exe", ".lnk")

        def _solo_ejecutables(rutas: List[str]) -> List[str]:
            return [r for r in rutas
                    if os.path.splitext(r)[1].lower() in ext_ok]

        # Estrategias de query, EN ORDEN (verificado contra es.exe v1.1.0.37 de
        # este equipo): el operador combinado `ext:exe <término>` devuelve
        # vacío en esa versión, pero buscar el nombre CON su extensión sí
        # funciona ("brave.exe" -> brave.exe). El último intento es el nombre
        # pelado + filtro por extensión en Python.
        consultas = [f"{nombre}.exe", f"{nombre}.lnk", nombre]
        candidatos: List[str] = []
        for consulta in consultas:
            candidatos = _solo_ejecutables(
                self._ejecutar_es(es, consulta, _MAX_RESULTADOS_APPS))
            if candidatos:
                logger.debug("Fallback de ejecutables: query '%s' -> %d "
                             "candidato(s).", consulta, len(candidatos))
                break
        if not candidatos:
            logger.info("No encontré ejecutables para '%s' con Everything.",
                        nombre)
            return None

        # Ordenamos por el MISMO scoring de buscar_archivo + bonus de carpeta
        # de juegos (desempate estable por orden original).
        rankeadas = sorted(
            ((self._puntaje_resultado(r, terminos) +
              self._bonus_carpeta_juego(r), i, r)
             for i, r in enumerate(candidatos)),
            key=lambda par: (-par[0], par[1]))
        rutas_ord = [r for _, _, r in rankeadas]
        puntajes_ord = [p for p, _, _ in rankeadas]

        # Umbral Anti-RUIDO: si lo mejor que hay tiene puntaje <= 0, no es una
        # coincidencia real (caso típico: un .lnk de AppData\...\Recent que no
        # tiene nada que ver con lo pedido). Mejor no abrir nada que abrir algo
        # equivocado; el usuario recibe el mensaje con la pista de _APPS.
        if not puntajes_ord or puntajes_ord[0] <= 0:
            logger.info("Solo encontré coincidencias flojas para '%s' "
                        "(mejor puntaje: %s); no abro nada.", nombre,
                        puntajes_ord[0] if puntajes_ord else "n/a")
            return None

        mejor = self._elegir_mejor(rutas_ord, puntajes_ord, nombre=nombre)
        if mejor:
            logger.info("Abriendo '%s' (encontrado por Everything).",
                        os.path.basename(mejor))
            return self._abrir_resultado(mejor, False)

        # Varios razonables y ninguno destaca: NO adivinamos; devolvemos el
        # mismo dict genérico de desambiguación que buscar_archivo.
        top = rutas_ord[:5]
        # Etiquetas: si hay basenames repetidos (típico de apps con varias
        # versiones instaladas, ej. ...\TIDAL\app-2.0\TIDAL.exe), agregamos la
        # carpeta padre para que el usuario pueda distinguirlas.
        bases = [os.path.basename(r) for r in top]
        repetidos = {b for b in bases if bases.count(b) > 1}
        opciones = []
        for i, r in enumerate(top):
            etiqueta = bases[i]
            if etiqueta in repetidos:
                etiqueta = (f"{etiqueta}   "
                            f"({os.path.basename(os.path.dirname(r))})")
            opciones.append({
                "indice": i + 1,
                "etiqueta": etiqueta,
                "valor": {"nombre": nombre, "ruta_elegida": r},
            })
        self._registrar_ofrecidas(top)
        logger.info("'%s': %d ejecutables candidatos; pregunto cuál.",
                    nombre, len(rutas_ord))
        return {
            "desambiguar": True,
            "tool_origen": "abrir_programa",
            "args_origen": {"nombre": nombre},
            "opciones": opciones,
        }

    # ---------------- Biblioteca de juegos (Steam / Epic) ---------------- #
    def _rutas_steam_candidatas(self) -> List[str]:
        """Devuelve las rutas candidatas a la carpeta de instalación de Steam.

        Prioriza ``STEAM_RUTA`` de ``config_local.py`` si está definida; si
        no, prueba las ubicaciones típicas de Windows. NO hardcodea rutas de
        usuario: solo las convencionales de Steam.
        """
        candidatas: List[str] = []
        # 1) Rutas de config (config_local.py pisa con update directo).
        try:
            from miku.ajustes import carga as config_mod  # ruta segura
            config_mod.cargar()
            for clave in ("steam_ruta", "ruta_steam", "steam_install"):
                val = str(config_mod.config.get(clave, "") or "").strip()
                if val:
                    candidatas.append(val)
        except Exception:  # noqa: BLE001
            pass

        # 2) Ubicaciones típicas (no dependen del usuario).
        candidatas += [
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
            "C:\\Steam",
        ]
        return candidatas

    def _carpeta_steam(self) -> Optional[str]:
        """Primera carpeta de Steam existente entre las candidatas."""
        from pathlib import Path
        for c in self._rutas_steam_candidatas():
            try:
                p = Path(c)
                if (p / "steamapps").is_dir() or (p / "steam.exe").exists():
                    return str(p)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _escanear_biblioteca_steam(self) -> Dict[str, str]:
        """Escanea las bibliotecas de Steam y arma {nombre_norm: appid}.

        Pasos:
          1. Encontrar la instalación de Steam (config o típicas).
          2. Leer ``steamapps/libraryfolders.vdf`` para TODAS las bibliotecas
             (parseo por regex simple; el usuario puede tener juegos en varios
             discos).
          3. Recorrer ``appmanifest_*.acf`` de cada biblioteca y extraer
             ``appid`` + ``name`` (texto plano).

        Devuelve {} si Steam no está instalado (loguea y sigue, no rompe).
        """
        import re
        from pathlib import Path

        steam = self._carpeta_steam()
        if not steam:
            logger.info("No encontré instalación de Steam; sin biblioteca de "
                        "juegos.")
            return {}

        bibliotecas: List[Path] = []
        base = Path(steam)
        # La instalación principal siempre tiene su propio steamapps.
        bibliotecas.append(base / "steamapps")

        # libraryfolders.vdf lista las bibliotecas adicionales (otros discos).
        vdf = base / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                texto = vdf.read_text(encoding="utf-8", errors="ignore")
                # Nos interesan las claves "path" del VDF; tolera escapes \\.
                for ruta in re.findall(r'"path"\s+"([^"]+)"', texto):
                    ruta = ruta.replace("\\\\", "\\")
                    bibliotecas.append(Path(ruta) / "steamapps")
            except Exception as e:  # noqa: BLE001
                logger.warning("No pude leer libraryfolders.vdf: %s", e)

        juegos: Dict[str, str] = {}
        vistas = set()  # evita recorrer la misma carpeta dos veces
        for lib in bibliotecas:
            try:
                if not lib.is_dir():
                    continue
                real = str(lib.resolve()).lower()
                if real in vistas:
                    continue
                vistas.add(real)
            except Exception:  # noqa: BLE001
                continue

            try:
                acfs = list(lib.glob("appmanifest_*.acf"))
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude listar appmanifest en %s: %s", lib, e)
                continue

            for acf in acfs:
                try:
                    datos = acf.read_text(encoding="utf-8", errors="ignore")
                    m_appid = re.search(r'"appid"\s+"(\d+)"', datos)
                    m_name = re.search(r'"name"\s+"([^"]+)"', datos)
                    if not m_appid or not m_name:
                        continue
                    appid = m_appid.group(1)
                    nombre = m_name.group(1)
                    # Normalizamos igual que el resto del archivo.
                    clave = _normalizar_texto(nombre)
                    if clave:
                        juegos[clave] = appid
                except Exception as e:  # noqa: BLE001
                    logger.debug("No pude leer %s: %s", acf, e)

        logger.info("Biblioteca de Steam escaneada: %d juego(s).", len(juegos))
        return juegos

    def _biblioteca_steam(self) -> Dict[str, str]:
        """Devuelve la biblioteca de Steam cacheada (la escanea si hace falta)."""
        if self._juegos_steam is None:
            try:
                self._juegos_steam = self._escanear_biblioteca_steam()
            except Exception as e:  # noqa: BLE001
                logger.error("Fallo escaneando la biblioteca de Steam: %s", e)
                self._juegos_steam = {}
        return self._juegos_steam

    def _buscar_en_biblioteca_steam(self, nombre: str) -> Optional[str]:
        """Busca un juego por nombre (tolerante) en la biblioteca de Steam.

        Matching: exacto (normalizado), luego compacto (sin separadores), y por
        último substring en cualquier dirección. Devuelve el appid o None.
        """
        biblioteca = self._biblioteca_steam()
        if not biblioteca:
            return None
        objetivo = _normalizar_texto(nombre)
        objetivo_compacto = _clave_compacta(nombre)

        # 1) Exacto / compacto.
        if objetivo in biblioteca:
            return biblioteca[objetivo]
        for clave, appid in biblioteca.items():
            if _clave_compacta(clave) == objetivo_compacto:
                return appid
        # 2) Substring (tolerante a "el peak" -> "peak").
        for clave, appid in biblioteca.items():
            if objetivo and (objetivo in clave or clave in objetivo):
                return appid
        for clave, appid in biblioteca.items():
            ck = _clave_compacta(clave)
            if objetivo_compacto and (objetivo_compacto in ck
                                      or ck in objetivo_compacto):
                return appid
        return None

    def _juegos_epic_config(self) -> Dict[str, str]:
        """Lee ``JUEGOS_EPIC`` (dict opcional) desde config_local.py."""
        try:
            from miku.ajustes import carga as config_mod  # ruta segura
            config_mod.cargar()
            valor = config_mod.config.get("juegos_epic", {}) or {}
            if isinstance(valor, dict):
                return {str(k): str(v) for k, v in valor.items()}
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer JUEGOS_EPIC de config: %s", e)
        return {}

    def _buscar_en_juegos_epic(self, nombre: str) -> Optional[str]:
        """Busca un juego en el dict manual de Epic (config_local)."""
        juegos = self._juegos_epic_config()
        if not juegos:
            return None
        objetivo = _normalizar_texto(nombre)
        objetivo_compacto = _clave_compacta(nombre)
        for clave, item_id in juegos.items():
            ck = _normalizar_texto(clave)
            if ck == objetivo or _clave_compacta(clave) == objetivo_compacto:
                return item_id
        for clave, item_id in juegos.items():
            ck = _normalizar_texto(clave)
            if objetivo and (objetivo in ck or ck in objetivo):
                return item_id
        return None

    def actualizar_biblioteca_juegos(self) -> str:
        """Re-escanea la biblioteca de Steam a demanda (tool sin parámetros)."""
        try:
            self._juegos_steam = self._escanear_biblioteca_steam()
        except Exception as e:  # noqa: BLE001
            logger.error("Error re-escaneando la biblioteca de Steam: %s", e)
            return "No pude escanear la biblioteca de Steam."
        cantidad = len(self._juegos_steam or {})
        if cantidad == 0:
            return ("No encontré juegos en tu biblioteca de Steam (¿está "
                    "instalado Steam?).")
        return f"Encontré {cantidad} juegos en tu biblioteca de Steam."

    def cerrar_programa(self, nombre: str) -> str:
        """Cierra (mata) el proceso asociado a un programa (seguro).

        Solo permite cerrar apps de la lista blanca efectiva: ``app_whitelist``
        de la config (si está vacía, las conocidas de ``_PROCESOS``). Quitar una
        app de esa lista SÍ impide cerrarla. ``_PROCESOS`` solo traduce el alias
        al ``.exe``. No usa ``shell=True`` para evitar inyección de comandos.
        """
        alias = (nombre or "").lower().strip()
        if not alias or alias in _NUNCA_CERRAR:
            return f"No está permitido cerrar '{nombre}'."

        try:
            from miku.ajustes import carga as config_mod
            configuradas = {str(a).lower().strip()
                            for a in (config_mod.config.app_whitelist or [])}
        except Exception:  # noqa: BLE001
            configuradas = set()
        blanca = configuradas or set(_PROCESOS.keys())

        proceso = _PROCESOS.get(alias)
        if proceso:
            # El alias ("navegador") o el nombre del exe ("brave") deben estar
            # en la lista blanca.
            if alias not in blanca and os.path.splitext(proceso)[0].lower() not in blanca:
                return f"No está permitido cerrar '{nombre}'."
        else:
            # No está en el mapa fijo: solo coincidencia EXACTA con la lista.
            if alias not in blanca:
                return f"No está permitido cerrar '{nombre}'."
            proceso = alias if alias.endswith(".exe") else alias + ".exe"

        if os.path.splitext(proceso)[0].lower() in _NUNCA_CERRAR:
            return f"No está permitido cerrar '{nombre}'."

        try:
            # 1) Cierre "amable": el programa puede guardar y salir solo.
            suave = subprocess.run(
                ["taskkill", "/IM", proceso, "/T"], shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if suave.returncode == 128:  # 128 = no hay ningún proceso así
                return f"{nombre} no está abierto."
            time.sleep(0.6)
            # 2) Si quedó algo vivo, lo forzamos (128 acá = ya se había cerrado).
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
                         contexto: Dict[str, Any], hora: str = "") -> str:
        """Programa una acción diferida usando el Scheduler del contexto.

        SOLO se llega acá tras la confirmación del usuario (el gating lo hace
        el CommandParser). `accion` ∈ {apagar, reiniciar, suspender,
        recordatorio}.

        El momento se indica con ``hora`` (HH:MM, a hora exacta) O con
        ``en_minutos`` (dentro de N minutos). Para ``hora``, si la hora ya pasó
        hoy, se asume mañana.

        El scheduler vive en ``contexto["scheduler"]`` (lo inyecta miku/app.py). Si
        no está disponible, devolvemos un mensaje claro en vez de romper.
        """
        accion = (accion or "").lower().strip()
        scheduler = (contexto or {}).get("scheduler")
        if scheduler is None:
            logger.error("No hay scheduler en el contexto; no puedo programar.")
            return "No tengo el programador de tareas disponible ahora."

        # Calculamos el momento: hora exacta O dentro de N minutos.
        etiqueta_momento = ""
        if (hora or "").strip():
            delta = self._segundos_hasta_hora(str(hora))
            if delta is None:
                return (f"No entendí la hora '{hora}'. Usá el formato HH:MM "
                        f"(por ejemplo 18:30).")
            segundos = delta
            etiqueta_momento = f"a las {hora.strip()}"
            minutos_txt = None
        else:
            # Convertimos minutos a segundos de forma robusta.
            try:
                minutos = float(en_minutos)
            except (TypeError, ValueError):
                return ("Decime en cuántos minutos lo programo (por ejemplo, 5) "
                        "o la hora exacta (por ejemplo 18:30).")
            if minutos < 0:
                minutos = 0
            segundos = minutos * 60.0
            minutos_txt = int(minutos) if minutos == int(minutos) else minutos
            etiqueta_momento = f"en {minutos_txt} minutos"

        if accion == "recordatorio":
            texto = mensaje or "un recordatorio"
            descripcion = f"Recordatorio: {texto}"
            callback = self._crear_callback_recordatorio(contexto, texto)
            scheduler.programar(segundos, callback, descripcion)
            return f"Dale, {etiqueta_momento} te aviso: {texto}."

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
        return f"Dale, {etiqueta_momento} {mapa[accion]}."

    def _segundos_hasta_hora(self, hora: str) -> Optional[float]:
        """Convierte una hora HH:MM en segundos desde AHORA (o None si inválida).

        Si la hora indicada ya pasó hoy, se asume MAÑANA (así "recordame a las
        8:00" dicho a las 22:00 suena mañana temprano, que es lo esperable).
        """
        import re as _re
        from datetime import datetime, timedelta

        texto = (hora or "").strip()
        # Formato HH:MM (o HH.MM).
        m = _re.match(r"^(\d{1,2})\s*[:.]\s*(\d{2})$", texto)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
        else:
            # "18h", "18hs" o simplemente "18" (hora en punto).
            m2 = _re.match(r"^(\d{1,2})\s*h(?:s)?$", texto, _re.IGNORECASE)
            if m2:
                hh, mm = int(m2.group(1)), 0
            elif _re.match(r"^\d{1,2}$", texto):
                hh, mm = int(texto), 0
            else:
                return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None

        ahora = datetime.now()
        objetivo = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if objetivo <= ahora:
            objetivo = objetivo + timedelta(days=1)
        return (objetivo - ahora).total_seconds()

    def _crear_callback_recordatorio(self, contexto: Dict[str, Any],
                                     mensaje: str):
        """Devuelve un callback que hace decir el recordatorio por voz.

        El `voice` viene en ``contexto["voice"]`` (lo inyecta miku/app.py). Si no
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

        paso = max(1, min(100, _a_entero(paso, 5) or 5))

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

    # ---------------- Volumen POR APP (sesiones de audio) ---------------- #
    def _sesiones_de_app(self, nombre_app: str) -> List[Any]:
        """Devuelve las SESIONES de audio de la app cuyo nombre coincide.

        Compara por nombre de proceso (DisplayName/ProcessName), case-insensitive
        y tolerante a acentos. Devuelve TODAS las sesiones que matcheen (un
        navegador puede tener varias pestañas/pestañas de audio abiertas): así
        "bajá el volumen de Brave" afecta a todas por igual.
        """
        try:
            from pycaw.pycaw import AudioUtilities  # import tardío
        except Exception as e:  # noqa: BLE001
            logger.debug("pycaw no disponible para volumen por app: %s", e)
            return []

        objetivo = _normalizar_texto(nombre_app)
        objetivo_compacto = _clave_compacta(nombre_app)
        if not objetivo or not objetivo_compacto:
            # Un nombre vacío ("" / "-") matchearía TODAS las sesiones.
            return []
        coincidentes: List[Any] = []
        try:
            sesiones = AudioUtilities.GetAllSessions()
        except Exception as e:  # noqa: BLE001
            logger.error("No pude listar sesiones de audio: %s", e)
            return coincidentes

        for sesion in sesiones:
            try:
                proc = getattr(sesion, "Process", None)
                nombre_proc = ""
                if proc is not None:
                    try:
                        nombre_proc = proc.name() or ""
                    except Exception:  # noqa: BLE001
                        nombre_proc = ""
                # A veces Process es None pero hay DisplayName.
                display = ""
                try:
                    display = sesion.DisplayName or ""
                except Exception:  # noqa: BLE001
                    display = ""

                candidatos = [_normalizar_texto(nombre_proc),
                              _normalizar_texto(display)]
                for cand in candidatos:
                    if not cand:
                        continue
                    if (objetivo in cand
                            or objetivo_compacto in _clave_compacta(cand)):
                        # Ojo: el nombre del proceso suele terminar en ".exe".
                        coincidentes.append(sesion)
                        break
            except Exception:  # noqa: BLE001
                continue
        return coincidentes

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
            return (f"No encontré ninguna app sonando que se llame "
                    f"'{nombre_app}'. ¿Está abierta y reproduciendo audio?")

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
                    delta = max(1, min(100, _a_entero(paso, 5) or 5)) / 100.0
                    nuevo = max(0.0, min(1.0, actual + delta))
                elif accion in ("bajar", "menos", "abajo"):
                    delta = max(1, min(100, _a_entero(paso, 5) or 5)) / 100.0
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
            return (f"No encontré ninguna app sonando que se llame "
                    f"'{nombre_app}'. ¿Está abierta y reproduciendo audio?")

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

        # Z2 — Re-rank SEMÁNTICO (capa EXTRA, opcional): "PDF de termodinámica"
        # encuentra el archivo correcto aunque el nombre no coincida palabra a
        # palabra. NO reemplaza Everything: suma un bonus por similitud de
        # SIGNIFICADO entre la consulta y el nombre+ruta, y reordena. Si no hay
        # motor de embeddings, devuelve todo igual (sin cambios).
        rutas_ord, puntajes_ord = self._rerank_semantico(
            nombre, rutas_ord, puntajes_ord)

        # ¿Hay ganador claro o hay que preguntar?
        mejor = self._elegir_mejor(rutas_ord, puntajes_ord, nombre=nombre)
        if mejor is None:
            # Varios razonables y ninguno destaca: NO abrimos. Devolvemos un
            # dict de DESAMBIGUACIÓN para que el parser guarde el estado y le
            # pregunte al usuario cuál quiere (así el siguiente "el tercero"
            # tiene contexto). El parser lo convierte en pregunta + opciones.
            print("[buscar_archivo] Varios resultados, preguntando al usuario.")
            top = rutas_ord[:5]
            self._registrar_ofrecidas(top)
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
        return self._abrir_archivo_buscado(mejor, abrir_carpeta)

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

        Sin ``-s``: esa opción ordena por RUTA COMPLETA y ``-n`` recorta esa
        lista, así que un ``.exe`` bueno en ``D:/Steam`` quedaba fuera detrás
        del ruido alfabético de ``C:/``. El orden por defecto (por nombre)
        deja arriba los nombres que coinciden.
        """
        try:
            resp = subprocess.run(
                [str(es), "-n", str(max_resultados), consulta],
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

    def _rerank_semantico(self, nombre: str, rutas: List[str],
                          puntajes: List[int], max_candidatos: int = 12
                          ) -> tuple:
        """Re-rankea los resultados por similitud semántica (opcional).

        Toma hasta ``max_candidatos`` resultados y les suma un bonus de
        similitud coseno (consulta vs "nombre + carpeta"). Es best-effort: si
        el módulo de embeddings no está disponible, devuelve los mismos
        valores SIN cambios.

        El bonus se escala a la misma magnitud que ``_puntaje_resultado`` (que
        usa decenas de puntos) para no pisar coincidencias exactas de nombre.
        """
        try:
            from miku.cerebro.memoria import embeddings  # import tardío
            if embeddings is None or not embeddings.disponible():
                return rutas, puntajes
            vec_consulta = embeddings.embeber(nombre)
            if vec_consulta is None:
                return rutas, puntajes
        except Exception:  # noqa: BLE001
            return rutas, puntajes

        limite = min(len(rutas), int(max_candidatos))
        # Re-puntuamos los primeros `limite` (el resto queda con su puntaje).
        nuevos: List[tuple] = []
        for idx, ruta in enumerate(rutas):
            base = puntajes[idx] if idx < len(puntajes) else 0
            if idx < limite:
                try:
                    etiqueta = f"{os.path.basename(ruta)} " \
                               f"{os.path.dirname(ruta)}"
                    vec = embeddings.embeber(etiqueta)
                    sim = embeddings.similitud(vec_consulta, vec)
                    # El bonus va de 0 a ~20 puntos (no supera una coincidencia
                    # exacta de nombre, que vale 50): es un desempate, no un piso.
                    base = base + int(round(max(0.0, sim) * 20))
                except Exception:  # noqa: BLE001
                    pass
            nuevos.append((base, idx, ruta))

        nuevos.sort(key=lambda par: (-par[0], par[1]))
        return ([r for _, _, r in nuevos], [p for p, _, _ in nuevos])

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

    # Extensiones que EJECUTAN código: ``buscar_archivo`` nunca las lanza
    # directo (buscar "el instalador" no debe correrlo), solo muestra la carpeta.
    _EXT_EJECUTABLES = frozenset({
        ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse",
        ".wsf", ".scr", ".reg", ".com", ".hta", ".cpl",
    })

    def _abrir_archivo_buscado(self, ruta: str, abrir_carpeta: bool) -> str:
        """Abre el resultado de ``buscar_archivo``; los ejecutables solo se muestran."""
        if (not abrir_carpeta
                and os.path.splitext(ruta)[1].lower() in self._EXT_EJECUTABLES):
            logger.info("'%s' es ejecutable: lo muestro en su carpeta.", ruta)
            abrir_carpeta = True
        return self._abrir_resultado(ruta, abrir_carpeta)

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
        from miku.ajustes import carga as config_mod  # ruta segura, sin deps pesadas
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
        """Busca el hwnd de la primera ventana cuyo título coincide.

        El título de la ventana raramente es el nombre corto ("VSCode" -> la
        ventana se llama "archivo - Visual Studio Code"). Por eso, además del
        substring directo, probamos alias de título conocidos (ver
        ``_ALIAS_TITULO``). La comparación es tolerante a acentos.
        """
        objetivo = _normalizar_texto(nombre_app).strip()
        if not objetivo:
            return None

        # Conjunto de fragmentos a buscar en el título.
        fragmentos = [objetivo]
        for clave, titulos in _ALIAS_TITULO.items():
            if objetivo == _normalizar_texto(clave) or clave in objetivo:
                fragmentos.extend(_normalizar_texto(t) for t in titulos)

        for hwnd, titulo in self._enumerar_ventanas():
            titulo_n = _normalizar_texto(titulo)
            if any(f and f in titulo_n for f in fragmentos):
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

        rect = self._rect_monitor(monitor)
        if rect is None:
            return "No encontré ese monitor."
        x1, y1, x2, y2 = rect

        # Al mover a otro monitor, la dejamos MAXIMIZADA ocupando todo el
        # rectángulo (comportamiento original).
        if self._mover_a_rect(hwnd, x1, y1, x2, y2, maximizar=True):
            return f"Llevé {nombre_app} al monitor {monitor}."
        return f"No pude mover {nombre_app}."

    # ---------------- Split de pantalla (Snap estilo Windows 11) ----------------
    def _rect_monitor(self, monitor: int = 1) -> Optional[tuple]:
        """Devuelve (x1, y1, x2, y2) del monitor indicado (1-based).

        Reusado por `mover_ventana` y por las funciones de split. Devuelve
        None si no hay monitores detectables o win32 no está disponible.
        """
        self._importar_windows()
        if not self._w:
            return None
        try:
            monitores = self._w["api"].EnumDisplayMonitors()
        except Exception:  # noqa: BLE001
            monitores = []
        if not monitores:
            return None

        idx = _a_entero(monitor, 1) - 1
        if idx < 0 or idx >= len(monitores):
            idx = 0
        info = self._w["api"].GetMonitorInfo(monitores[idx][0])
        x1, y1, x2, y2 = info["Monitor"]
        return (x1, y1, x2, y2)

    def _mover_a_rect(self, hwnd: Any, x1: int, y1: int, x2: int, y2: int,
                      maximizar: bool = False) -> bool:
        """Coloca `hwnd` en el rectángulo (x1,y1)-(x2,y2).

        Restaura la ventana ANTES de moverla (si estaba maximizada, Windows a
        veces ignora el MoveWindow directo). Si `maximizar` es True, la
        maximiza DENTRO del rect (mover a monitor); si es False, queda con el
        tamaño EXACTO del rect (split de pantalla).

        Devuelve True si pudo, False ante error.
        """
        self._importar_windows()
        if not self._w:
            return False
        ancho, alto = int(x2 - x1), int(y2 - y1)
        try:
            gui, con = self._w["gui"], self._w["con"]
            gui.ShowWindow(hwnd, con.SW_RESTORE)
            gui.MoveWindow(hwnd, int(x1), int(y1), ancho, alto, True)

            # Compensación del "borde invisible" (DWM): al pedir un rect crudo,
            # Windows puede agregar ~7px de sombra/marco que dejan un gap entre
            # las dos mitades. Corregimos re-moviendo según la diferencia entre
            # lo pedido y el marco VISUAL real (DWMWA_EXTENDED_FRAME_BOUNDS).
            # Solo aplica al split (no al maximizar); si DWM no responde,
            # seguimos sin compensar (mejor eso que romper por otra resolución).
            if not maximizar:
                self._compensar_marco_dwm(hwnd, int(x1), int(y1), ancho, alto)

            if maximizar:
                gui.ShowWindow(hwnd, con.SW_MAXIMIZE)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("No pude mover la ventana al rect: %s", e)
            return False

    def _traer_al_frente(self, hwnd: Any) -> bool:
        """Restaura y trae una ventana al frente (le da el foco).

        Windows bloquea ``SetForegroundWindow`` cuando la llamada no viene de
        una app en primer plano; el truco habitual es simular una pulsación de
        la tecla Alt y reintentar. Es best-effort: si falla, devolvemos False
        pero la ventana ya quedó movida/restaurada.
        """
        self._importar_windows()
        if not self._w or not hwnd:
            return False
        gui, con = self._w["gui"], self._w["con"]
        try:
            gui.ShowWindow(hwnd, con.SW_RESTORE)
            # HWND_TOP = 0: sube la ventana al tope del z-order sin cambiar
            # tamaño ni posición.
            gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                             con.SWP_NOMOVE | con.SWP_NOSIZE |
                             con.SWP_SHOWWINDOW)
            gui.SetForegroundWindow(hwnd)
            return True
        except Exception:  # noqa: BLE001
            # Fallback: "apretar Alt" destraba el bloqueo de foco de Windows.
            try:
                import keyboard  # import tardío
                keyboard.press("alt")
                keyboard.release("alt")
                gui.SetForegroundWindow(hwnd)
                return True
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude traer la ventana al frente: %s", e)
                return False

    def _acoplar_par(self, hwnd_a: Any, rect_a: tuple,
                     hwnd_b: Any, rect_b: tuple) -> None:
        """Coloca dos ventanas en sus rectángulos y trae AMBAS al frente.

        La clave del fix del "split": no basta con mover, hay que RESTAURAR las
        dos (si una estaba maximizada/minimizada Windows ignora el MoveWindow)
        y subirlas al frente para que ninguna quede escondida detrás.
        """
        self._mover_a_rect(hwnd_a, *rect_a, maximizar=False)
        self._mover_a_rect(hwnd_b, *rect_b, maximizar=False)
        # Restauramos/levantamos las dos (primero una, después la otra).
        self._traer_al_frente(hwnd_a)
        self._traer_al_frente(hwnd_b)

    def acoplar_ventanas(self, ventana_a: str, ventana_b: str,
                         layout: str = "izquierda-derecha",
                         monitor: int = 1) -> str:
        """Acopla DOS ventanas visibles y usables (restauradas, lado a lado).

        A diferencia de ``dividir_pantalla`` (que posiciona de a una), acá se
        restauren AMBAS y se suben al frente para evitar el bug de "la otra
        queda atrás/escondida". Layout: 'izquierda-derecha' (lado a lado) o
        'arriba-abajo' (una encima de la otra).
        """
        rect = self._rect_monitor(monitor)
        if rect is None:
            return "No encontré ese monitor."
        x1, y1, x2, y2 = rect

        lay = (layout or "izquierda-derecha").lower().strip()
        lado_a_lado = lay in (
            "izquierda-derecha", "izquierda derecha", "horizontal",
            "lado a lado", "columnas", "verticales", "columnas")
        # 'arriba-abajo' y sinónimos -> apiladas.
        if not lado_a_lado:
            lado_a_lado = False

        if lado_a_lado:
            dx = (x2 - x1) // 2
            rect_a = (x1, y1, x1 + dx, y2)
            rect_b = (x1 + dx, y1, x2, y2)
            orientacion = "lado a lado"
        else:
            dy = (y2 - y1) // 2
            rect_a = (x1, y1, x2, y1 + dy)
            rect_b = (x1, y1 + dy, x2, y2)
            orientacion = "uno arriba del otro"

        hwnd_a = self._hwnd_de(ventana_a)
        hwnd_b = self._hwnd_de(ventana_b)

        if hwnd_a is None and hwnd_b is None:
            return (f"No encontré ninguna ventana ni de {ventana_a} "
                    f"ni de {ventana_b}.")

        # Si falta una, acomodamos la que SÍ está y avisamos cuál faltó.
        if hwnd_a is None:
            self._mover_a_rect(hwnd_b, *rect_b, maximizar=False)
            self._traer_al_frente(hwnd_b)
            return (f"No encontré ninguna ventana de {ventana_a}, pero "
                    f"acomodé {ventana_b}.")
        if hwnd_b is None:
            self._mover_a_rect(hwnd_a, *rect_a, maximizar=False)
            self._traer_al_frente(hwnd_a)
            return (f"No encontré ninguna ventana de {ventana_b}, pero "
                    f"acomodé {ventana_a}.")

        self._acoplar_par(hwnd_a, rect_a, hwnd_b, rect_b)
        return (f"Listo, acoplé {ventana_a} y {ventana_b} {orientacion}.")

    def _compensar_marco_dwm(self, hwnd: Any, x: int, y: int,
                             ancho: int, alto: int) -> None:
        """Ajusta el rect para que el marco VISUAL caiga donde lo pedimos.

        Windows dibuja una sombra/borde que NO forma parte del rect de la
        ventana; el `MoveWindow` con valores crudos deja un gap de unos px
        entre las dos mitades de un split. Acá medimos el rect visual real con
        ``DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`` y re-movemos la
        ventana compensando ese delta. Es best-effort: si DWM falla, no hace
        nada (no rompe el movimiento ya aplicado).
        """
        try:
            import ctypes
            from ctypes import wintypes

            class _RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            rect = _RECT()
            dwm = ctypes.windll.dwmapi
            hr = dwm.DwmGetWindowAttribute(
                ctypes.c_void_p(int(hwnd)), DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect), ctypes.sizeof(rect))
            if hr != 0:  # 0 = S_OK; el resto => sin info fiable, no compensamos
                return
            # El marco visual difiere del rect pedido: corregimos la posición y
            # el tamaño por ese delta (una sola pasada; suele bastar).
            dx = x - rect.left
            dy = y - rect.top
            dancho = (rect.right - rect.left) - ancho
            dalto = (rect.bottom - rect.top) - alto
            if dx == 0 and dy == 0 and dancho == 0 and dalto == 0:
                return
            gui = self._w["gui"]
            gui.MoveWindow(int(hwnd), int(x - dx), int(y - dy),
                           int(ancho - dancho), int(alto - dalto), True)
        except Exception as e:  # noqa: BLE001
            # Cosmético: si falla, dejamos el rect tal cual (sin compensar).
            logger.debug("Sin compensación de marco DWM: %s", e)

    def posicionar_ventana(self, nombre_app: str, posicion: str,
                           monitor: int = 1) -> str:
        """Coloca una ventana en una mitad/posición del monitor.

        `posicion` ∈ {izquierda, derecha, arriba, abajo, completa}. Para
        "completa" se comporta como `mover_ventana` (maximizada en todo el
        monitor); para el resto, ocupa EXACTAMENTE la mitad (sin maximizar
        dentro de ella).
        """
        hwnd = self._hwnd_de(nombre_app)
        if not hwnd:
            return f"No encontré ninguna ventana de {nombre_app}."

        rect = self._rect_monitor(monitor)
        if rect is None:
            return "No encontré ese monitor."
        x1, y1, x2, y2 = rect

        pos = (posicion or "").lower().strip()
        # Sinónimos tolerantes.
        if pos in ("izquierda", "izq", "left", "mitad izquierda"):
            dx = (x2 - x1) // 2
            rx1, ry1, rx2, ry2 = x1, y1, x1 + dx, y2
            maximizar = False
        elif pos in ("derecha", "der", "right", "mitad derecha"):
            dx = (x2 - x1) // 2
            rx1, ry1, rx2, ry2 = x1 + dx, y1, x2, y2
            maximizar = False
        elif pos in ("arriba", "top", "mitad superior"):
            dy = (y2 - y1) // 2
            rx1, ry1, rx2, ry2 = x1, y1, x2, y1 + dy
            maximizar = False
        elif pos in ("abajo", "bottom", "mitad inferior"):
            dy = (y2 - y1) // 2
            rx1, ry1, rx2, ry2 = x1, y1 + dy, x2, y2
            maximizar = False
        elif pos in ("completa", "completo", "entera", "entero", "full",
                     "pantalla completa"):
            rx1, ry1, rx2, ry2 = x1, y1, x2, y2
            maximizar = True  # igual que mover_ventana
        else:
            return ("No entendí la posición. Usá izquierda, derecha, "
                    "arriba, abajo o completa.")

        if self._mover_a_rect(hwnd, rx1, ry1, rx2, ry2, maximizar=maximizar):
            if pos in ("completa", "completo", "entera", "entero", "full",
                       "pantalla completa"):
                return f"Puse {nombre_app} a pantalla completa."
            return f"Puse {nombre_app} a la {pos}."
        return f"No pude posicionar {nombre_app}."

    def dividir_pantalla(self, app_izquierda: str, app_derecha: str,
                         monitor: int = 1) -> str:
        """Divide el monitor: `app_izquierda` a la mitad izquierda y
        `app_derecha` a la derecha.

        Si una de las dos no se encuentra, posiciona la que SÍ encontró y
        avisa específicamente CUÁL faltó (no un mensaje genérico).
        """
        # Usamos el acoplado nuevo (restaura y trae AMBAS al frente), que es el
        # fix del bug en el que la segunda ventana quedaba atrás/escondida.
        try:
            hwnd_izq = self._hwnd_de(app_izquierda)
            hwnd_der = self._hwnd_de(app_derecha)
        except Exception:  # noqa: BLE001
            hwnd_izq = hwnd_der = None
        if hwnd_izq and hwnd_der:
            return self.acoplar_ventanas(app_izquierda, app_derecha,
                                         "izquierda-derecha", monitor)

        res_izq = self.posicionar_ventana(app_izquierda, "izquierda", monitor)
        res_der = self.posicionar_ventana(app_derecha, "derecha", monitor)

        izq_ok = not res_izq.startswith("No encontré") and \
            not res_izq.startswith("No pude")
        der_ok = not res_der.startswith("No encontré") and \
            not res_der.startswith("No pude")

        if izq_ok and der_ok:
            return (f"Listo, {app_izquierda} a la izquierda y "
                    f"{app_derecha} a la derecha.")
        if izq_ok and not der_ok:
            return (f"Puse {app_izquierda} a la izquierda, pero no encontré "
                    f"ninguna ventana de {app_derecha}.")
        if der_ok and not izq_ok:
            return (f"Puse {app_derecha} a la derecha, pero no encontré "
                    f"ninguna ventana de {app_izquierda}.")
        return (f"No encontré ninguna ventana ni de {app_izquierda} "
                f"ni de {app_derecha}.")

    def organizar_ventanas(self, ventana_izquierda: str,
                           ventana_derecha: str = "",
                           posicion: str = "izquierda",
                           monitor: int = 1) -> str:
        """Organiza ventanas al estilo Snap de Windows 11 (50/50).

        Casos:
          - Con ``ventana_derecha``: reparte la pantalla entre las DOS (una a
            cada mitad del ANCHO del monitor, alto completo).
          - Sin ``ventana_derecha``: manda ``ventana_izquierda`` a la mitad
            indicada por ``posicion`` (izquierda/derecha).

        No duplica geometría ni lógica de ventanas: delega en
        ``dividir_pantalla``/``acoplar_ventanas``/``posicionar_ventana``, que ya
        usan ``_enumerar_ventanas``/``_hwnd_de``/``_rect_monitor``/
        ``_mover_a_rect`` (restaura con SW_RESTORE antes de mover y compensa el
        marco invisible de DWM) y avisan claramente si alguna ventana no se
        encontró.
        """
        izq = (ventana_izquierda or "").strip()
        der = (ventana_derecha or "").strip()
        if not izq:
            return "¿Qué ventana querés organizar?"

        if der:
            # ¿Es la MISMA ventana de los dos lados? La resolvemos con la misma
            # lógica que usa el resto del archivo (_hwnd_de, que aplica
            # _ALIAS_TITULO y compara sin acentos). Si ninguna de las dos
            # existe todavía, comparamos el texto normalizado con la misma
            # normalización del módulo. En ese caso NO movemos nada.
            try:
                hwnd_izq = self._hwnd_de(izq)
                hwnd_der = self._hwnd_de(der)
            except Exception:  # noqa: BLE001
                hwnd_izq = hwnd_der = None
            misma = bool(hwnd_izq is not None and hwnd_izq == hwnd_der)
            if not misma and hwnd_izq is None and hwnd_der is None:
                misma = _normalizar_texto(izq) == _normalizar_texto(der)
            if misma:
                logger.info("organizar_ventanas: '%s' y '%s' son la misma "
                            "ventana; no muevo nada.", izq, der)
                return ("No puedo poner la misma ventana a los dos lados. "
                        "Decime dos ventanas distintas, o usá posicionar_ventana "
                        "si querés ponerla en una sola mitad.")
            logger.info("organizar_ventanas: '%s' (izq) | '%s' (der) | "
                        "monitor %s", izq, der, monitor)
            # Reusa el split existente: si falta una ventana, ya devuelve un
            # mensaje diciendo CUÁL no encontró (y mueve la que sí está).
            return self.dividir_pantalla(izq, der, monitor)

        pos = (posicion or "izquierda").lower().strip()
        if pos not in ("izquierda", "izq", "left", "derecha", "der", "right"):
            pos = "izquierda"
        logger.info("organizar_ventanas: '%s' -> %s | monitor %s",
                    izq, pos, monitor)
        return self.posicionar_ventana(izq, pos, monitor)
