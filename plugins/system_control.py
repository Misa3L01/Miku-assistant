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

import logging
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
