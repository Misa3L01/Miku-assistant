"""
macros.py - Sistema de macros configurables (data/macros_config.json).

Las macros son atajos con nombre que el usuario define en un JSON. Ejemplo:

    {
      "modo_fortnite": {"descripcion": "Cambiar resolución a 1440x1080",
                        "comando": "resolucion 1440x1080"},
      "comedor":       {"descripcion": "Abrir página y autocompletar",
                        "comando": "abrir_comedor"}
    }

Este plugin:
    - Lee el JSON al inicializar (con manejo de error CLARO: si no existe o
      tiene JSON inválido, NO tumba el arranque; se degrada a "sin macros").
    - Publica las tools `ejecutar_macro` y `listar_macros`.
    - Interpreta el campo "comando" de cada macro con un REGISTRO de
      manejadores conocidos. Lo que no tiene manejador real se responde
      HONESTAMENTE como "pendiente de implementar" (nunca inventa qué hace).

Manejadores implementados por ahora:
    - "resolucion WxH"  -> cambia la resolución de pantalla (ctypes, sin deps).
    - (el resto)        -> "pendiente de implementar" (honesto).

Alcance: es un plugin de BAJA prioridad. La idea es que sirva de esqueleto
extensible: sumar un manejador nuevo = agregar una entrada en
``self._manejadores``.
"""
from __future__ import annotations

import ctypes
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from plugins import Plugin

logger = logging.getLogger("miku.plugins.macros")

# Ruta del JSON de macros (relativa a la raíz del proyecto).
_RUTA_MACROS = Path(__file__).resolve().parent.parent / "data" / "macros_config.json"

# Patrón de comando "resolucion WxH" (o "resolución").
_RE_RESOLUCION = re.compile(r"^\s*resoluci[oó]n\s+(\d{2,5})\s*[xX]\s*(\d{2,5})\s*$")


class Macros(Plugin):
    """Ejecuta macros definidas por el usuario en data/macros_config.json."""

    nombre = "macros"
    descripcion = "Ejecuta macros/atajos configurables definidos por el usuario."

    # ---- Tools publicadas ----
    # NOTA: la descripción de `ejecutar_macro` se completa en runtime con las
    # claves disponibles (ver `_tools_con_macros`), para que el LLM sepa qué
    # macros existen. Acá va un texto base por si se recopilan antes de init.
    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "ejecutar_macro",
                "description": "Ejecuta una macro/atajo definido por el "
                               "usuario por su nombre. Ej: 'modo fortnite', "
                               "'comedor'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Nombre de la macro a ejecutar.",
                        },
                    },
                    "required": ["nombre"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "listar_macros",
                "description": "Lista las macros disponibles con su "
                               "descripción.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._macros: Dict[str, Dict[str, str]] = {}
        # Registro de manejadores: nombre lógico -> función(comando, macro)->str
        self._manejadores: Dict[str, Callable[[str, Dict[str, str]], str]] = {
            "resolucion": self._manejar_resolucion,
        }

    # ---------------- Ciclo de vida ----------------
    def initialize(self, event_bus: Any = None) -> None:
        """Carga el JSON de macros (tolerante a fallos)."""
        super().initialize(event_bus)
        self._macros = self._cargar_macros()
        # Reconstruimos la tool `ejecutar_macro` con las claves reales en su
        # descripción, para que el LLM sepa qué macros existen.
        self.tools = self._tools_con_macros()
        if self._macros:
            logger.info("Plugin macros listo. %d macro(s): %s",
                        len(self._macros), ", ".join(self._macros.keys()))
        else:
            logger.info("Plugin macros listo, pero sin macros cargadas.")

    # ---------------- Carga del JSON ----------------
    def _cargar_macros(self) -> Dict[str, Dict[str, str]]:
        """Lee data/macros_config.json y devuelve {nombre: {descripcion, comando}}.

        NUNCA lanza: ante archivo faltante o JSON inválido, loguea el motivo y
        devuelve {} (el asistente sigue funcionando, solo que sin macros).
        """
        if not _RUTA_MACROS.exists():
            logger.warning(
                "No encontré el archivo de macros (%s). Se arranca sin macros.",
                _RUTA_MACROS)
            return {}

        try:
            with open(_RUTA_MACROS, "r", encoding="utf-8") as f:
                datos = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(
                "El archivo de macros tiene JSON inválido (línea %s): %s. "
                "Se arranca sin macros.", e.lineno, e.msg)
            return {}
        except Exception as e:  # noqa: BLE001
            logger.error("No pude leer el archivo de macros (%s): %s. "
                         "Se arranca sin macros.", _RUTA_MACROS, e)
            return {}

        if not isinstance(datos, dict):
            logger.error("El archivo de macros no es un objeto JSON. "
                         "Se arranca sin macros.")
            return {}

        # Normalizamos: solo entradas con dict y campo "comando" no vacío.
        macros: Dict[str, Dict[str, str]] = {}
        for nombre, valor in datos.items():
            if not isinstance(valor, dict):
                logger.warning("Macro '%s' ignorada: no es un objeto.", nombre)
                continue
            comando = str(valor.get("comando", "") or "").strip()
            if not comando:
                logger.warning("Macro '%s' ignorada: sin campo 'comando'.",
                               nombre)
                continue
            macros[str(nombre)] = {
                "descripcion": str(valor.get("descripcion", "") or "").strip(),
                "comando": comando,
            }
        return macros

    def _tools_con_macros(self) -> List[dict]:
        """Devuelve las tools con las macros reales en la descripción."""
        nombres = list(self._macros.keys())
        if nombres:
            lista = ", ".join(f"'{n}'" for n in nombres)
            desc = ("Ejecuta una macro/atajo definido por el usuario por su "
                    f"nombre. Macros disponibles: {lista}.")
        else:
            desc = ("Ejecuta una macro/atajo definido por el usuario por su "
                    "nombre. (No hay macros configuradas todavía.)")

        return [
            {
                "type": "function",
                "function": {
                    "name": "ejecutar_macro",
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "nombre": {
                                "type": "string",
                                "description": "Nombre de la macro a ejecutar.",
                            },
                        },
                        "required": ["nombre"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "listar_macros",
                    "description": "Lista las macros disponibles con su "
                                   "descripción.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "ejecutar_macro":
            return self.ejecutar_macro(str(args.get("nombre", "") or ""))
        if nombre_tool == "listar_macros":
            return self.listar_macros()
        return None

    # ---------------- API de macros ----------------
    def listar_macros(self) -> str:
        """Devuelve las macros disponibles (nombre + descripción)."""
        if not self._macros:
            return "No hay macros configuradas todavía."
        lineas = []
        for nombre, macro in self._macros.items():
            desc = macro.get("descripcion") or "(sin descripción)"
            lineas.append(f"- {nombre}: {desc}")
        return "Macros disponibles:\n" + "\n".join(lineas)

    def ejecutar_macro(self, nombre: str) -> str:
        """Ejecuta la macro `nombre` interpretando su campo 'comando'."""
        nombre = (nombre or "").strip().lower()
        if not nombre:
            return "¿Qué macro querés que ejecute?"

        if not self._macros:
            return "No tengo macros configuradas."

        # Resolución tolerante del nombre (espacios -> guiones bajos).
        macro = self._buscar_macro(nombre)
        if macro is None:
            disponibles = ", ".join(self._macros.keys())
            return f"No conozco la macro '{nombre}'. Tengo: {disponibles}."

        comando = macro.get("comando", "").strip()
        logger.info("Ejecutando macro '%s' -> comando '%s'.", nombre, comando)

        # Buscamos un manejador según el patrón del comando.
        manejador = self._resolver_manejador(comando)
        if manejador is not None:
            try:
                return manejador(comando, macro)
            except Exception as e:  # noqa: BLE001
                logger.error("Error ejecutando macro '%s' (%s): %s",
                             nombre, comando, e)
                return f"No pude ejecutar la macro '{nombre}'."

        # Sin manejador: NO inventamos. Respondemos honestamente.
        return (f"La macro '{nombre}' está pendiente de implementar "
                f"(su comando es '{comando}').")

    def _buscar_macro(self, nombre: str) -> Optional[Dict[str, str]]:
        """Busca la macro tolerando espacios vs guiones bajos."""
        if nombre in self._macros:
            return self._macros[nombre]
        compacto = nombre.replace(" ", "_")
        if compacto in self._macros:
            return self._macros[compacto]
        compacto2 = nombre.replace("_", " ")
        if compacto2 in self._macros:
            return self._macros[compacto2]
        return None

    def _resolver_manejador(
            self, comando: str) -> Optional[Callable[[str, Dict[str, str]], str]]:
        """Devuelve el manejador aplicable al `comando` (o None).

        Por ahora sólo reconoce el patrón "resolucion WxH". Sumar soporte para
        otro tipo de comando = agregar acá su detección + un método.
        """
        if _RE_RESOLUCION.match(comando):
            return self._manejadores["resolucion"]
        return None

    # ---------------- Manejadores concretos ----------------
    def _manejar_resolucion(self, comando: str,
                            macro: Dict[str, str]) -> str:
        """Cambia la resolución de pantalla usando ctypes (sin dependencias).

        Usa las APIs de Windows:
          - ``EnumDisplaySettingsW`` para verificar que el modo exista.
          - ``ChangeDisplaySettingsW`` para aplicarlo.
        """
        m = _RE_RESOLUCION.match(comando)
        if not m:
            return "No entendí la resolución pedida."
        ancho, alto = int(m.group(1)), int(m.group(2))
        return cambiar_resolucion(ancho, alto)


# ===================================================================== #
#                      Cambio de resolución (ctypes)                    #
# ===================================================================== #
# Estructuras/constantes de Windows para ChangeDisplaySettings.
# Se declaran a nivel de módulo pero SOLO se usan dentro de la función (y esa
# función se ejecuta solo en Windows con ctypes disponible).
_ENUM_CURRENT_SETTINGS = -1
_DM_PELSWIDTH = 0x00080000
_DM_PELSHEIGHT = 0x00100000
_CDS_TEST = 0x00000002
_CDS_UPDATEREGISTRY = 0x00000001
_DISP_CHANGE_SUCCESSFUL = 0
_DISP_CHANGE_BADMODE = -2


class _DEVMODE(ctypes.Structure):  # noqa: N801 - nombre estilo Win32
    """Subconjunto de la estructura DEVMODEW de Windows (suficiente para cambiar
    resolución por ancho/alto)."""

    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


def cambiar_resolucion(ancho: int, alto: int) -> str:
    """Cambia la resolución de pantalla a `ancho`x`alto` (Windows, ctypes).

    Estrategia:
      1. Arma un DEVMODE con ancho/alto pedidos y el resto en "actual".
      2. Prueba el modo con ``ChangeDisplaySettingsW(..., CDS_TEST)``.
      3. Si es válido, lo aplica con ``CDS_UPDATEREGISTRY``.

    Devuelve un mensaje natural. Si el modo no existe o falla, lo dice claro
    (no deja la pantalla a medias).
    """
    try:
        user32 = ctypes.windll.user32
    except Exception:  # noqa: BLE001
        return "El cambio de resolución solo funciona en Windows."

    dm = _DEVMODE()
    dm.dmSize = ctypes.sizeof(_DEVMODE)
    # Partimos del modo ACTUAL y solo cambiamos ancho/alto.
    if not user32.EnumDisplaySettingsW(None, _ENUM_CURRENT_SETTINGS,
                                       ctypes.byref(dm)):
        return "No pude leer la configuración de pantalla actual."

    dm.dmPelsWidth = ancho
    dm.dmPelsHeight = alto
    dm.dmFields = _DM_PELSWIDTH | _DM_PELSHEIGHT

    # 1) Prueba (no cambia nada todavía).
    prueba = user32.ChangeDisplaySettingsW(ctypes.byref(dm), _CDS_TEST)
    if prueba == _DISP_CHANGE_BADMODE:
        return f"Tu pantalla no soporta la resolución {ancho}x{alto}."
    if prueba != _DISP_CHANGE_SUCCESSFUL:
        return (f"No pude aplicar la resolución {ancho}x{alto} "
                f"(código {prueba}).")

    # 2) Aplicación real.
    res = user32.ChangeDisplaySettingsW(ctypes.byref(dm), _CDS_UPDATEREGISTRY)
    if res == _DISP_CHANGE_SUCCESSFUL:
        logger.info("Resolución cambiada a %dx%d.", ancho, alto)
        return f"Listo, cambié la resolución a {ancho}x{alto}."

    # OJO: "restart required" (códigos > 0) NO es error fatal; avisamos igual.
    if res > 0:
        return (f"Cambié la resolución a {ancho}x{alto}, "
                f"pero puede pedir reiniciar para quedar fija.")
    return f"No pude cambiar la resolución (código {res})."
