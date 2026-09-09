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
                "description": "Sube o baja el volumen del sistema, o lo "
                               "silencia. Ej: 'subí el volumen', 'mutear'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["subir", "bajar", "silenciar"],
                            "description": "Qué hacer con el volumen.",
                        },
                        "paso": {
                            "type": "integer",
                            "description": "Opcional: cantidad de pasos "
                                           "para subir/bajar (default 5).",
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
                "description": "Busca un archivo/carpeta en tu PC usando el "
                               "indexador Everything (es.exe). Requiere que "
                               "es.exe esté presente en bin/ o en la ruta "
                               "configurada.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Palabra o nombre a buscar.",
                        },
                    },
                    "required": ["nombre"],
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
        if nombre_tool == "control_multimedia":
            return self.control_multimedia(str(args.get("accion", "")))
        if nombre_tool == "ajustar_volumen":
            return self.ajustar_volumen(str(args.get("accion", "")),
                                        args.get("paso"))
        if nombre_tool == "buscar_archivo":
            return self.buscar_archivo(str(args.get("nombre", "")))
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

    # ---------------- Multimedia (teclas virtuales Windows) ---------------- #
    # Códigos de VK (virtual key) para medios / volumen (Windows).
    _VK = {
        "play_pausa": 0xB3, "siguiente": 0xB0, "anterior": 0xB1,
        "subir_volumen": 0xAF, "bajar_volumen": 0xAE, "silenciar": 0xAD,
    }
    # Nombres equivalentes que acepta el módulo `keyboard` (fallback).
    _NOMBRE_KEYBOARD = {
        "play_pausa": "play/pause media",
        "siguiente": "next track", "anterior": "previous track",
        "subir_volumen": "volume up", "bajar_volumen": "volume down",
        "silenciar": "volume mute",
    }

    def _enviar_tecla_virtual(self, clave: str) -> bool:
        """Envía una tecla multimedia/volumen por hardware.

        Intenta primero con `keyboard` (si está instalado) y, si no, con
        ctypes ``keybd_event``. Devuelve True si pudo enviarse.
        """
        try:
            import keyboard  # import tardío
            nombre = self._NOMBRE_KEYBOARD.get(clave)
            if nombre:
                keyboard.send(nombre)
                return True
        except Exception:  # noqa: BLE001
            pass  # seguimos con el fallback por ctypes

        # Fallback: keybd_event de user32.
        vk = self._VK.get(clave)
        if vk is None:
            return False
        try:
            user32 = ctypes.windll.user32
            # Down + up para simular la pulsación.
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP = 2
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("No se pudo enviar la tecla virtual %s: %s", clave, e)
            return False

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

    def ajustar_volumen(self, accion: str, paso: Optional[int] = None) -> str:
        """Sube/baja o silencia el volumen del sistema (teclas multimedia)."""
        accion = (accion or "").lower().strip()
        if accion == "silenciar":
            clave = "silenciar"
        elif accion in ("subir", "mas", "arriba"):
            clave = "subir_volumen"
        elif accion in ("bajar", "menos", "abajo"):
            clave = "bajar_volumen"
        else:
            return "No entendí. Usá subir, bajar o silenciar."

        veces = max(1, int(paso or 5)) if clave != "silenciar" else 1
        for _ in range(min(veces, 50)):
            if not self._enviar_tecla_virtual(clave):
                return "No pude ajustar el volumen."
            time.sleep(0.02)
        msj = {"subir_volumen": "Subí el volumen.", "bajar_volumen": "Bajé el volumen.",
               "silenciar": "Silencié el audio."}.get(clave, "Listo.")
        return msj

    # ---------------- Búsqueda con Everything (es.exe) ---------------- #
    def buscar_archivo(self, nombre: str, max_resultados: int = 8) -> str:
        """Busca `nombre` mediante el indexador Everything (es.exe).

        Requiere que el binario `es.exe` exista en `bin/` (o en la ruta
        configurada `ruta_everything_es`). Si no está, devuelve un mensaje
        claro en vez de fallar.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            return "¿Qué archivo querés que busque?"

        es = self._ruta_es_ejecutable()
        if not es:
            return ("No tengo el indexador Everything (es.exe) configurado. "
                    "Copiá es.exe a la carpeta bin/ o definí su ruta y reintentá.")

        try:
            # -s = búsqueda sin interfaz; imprime resultados por consola.
            resp = subprocess.run(
                [str(es), "-s", "-n", str(max_resultados), nombre],
                capture_output=True, text=True, timeout=15, shell=False)
        except subprocess.TimeoutExpired:
            return "La búsqueda tardó demasiado."
        except Exception as e:  # noqa: BLE001
            logger.error("Error buscando con Everything: %s", e)
            return "No pude ejecutar la búsqueda con Everything."

        lineas = [l.strip() for l in (resp.stdout or "").splitlines() if l.strip()]
        if not lineas:
            return f"No encontré resultados para '{nombre}'."
        return "Resultados:\n- " + "\n- ".join(lineas[:max_resultados])

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
