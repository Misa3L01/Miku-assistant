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

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from miku.ajustes.carga import BASE_DIR
from miku.plataforma import pantalla
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.macros")

# Ruta del JSON de macros (relativa a la raíz del proyecto).
_RUTA_MACROS = BASE_DIR / "data" / "macros_config.json"

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
        self._bus = event_bus
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
    #: Cuántas macros se nombran en voz alta (la lista completa va al log).
    _MAX_HABLADAS = 5

    def _nombres_hablados(self) -> str:
        """Los primeros nombres ("a, b y c") para decirlos en voz alta."""
        nombres = [n.replace("_", " ") for n in list(self._macros)[:self._MAX_HABLADAS]]
        return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]

    def listar_macros(self) -> str:
        """Dice cuántas macros hay y nombra solo las primeras (la lista completa queda en el log)."""
        if not self._macros:
            return falla("macros.sin_macros")
        logger.info("Macros disponibles:\n%s", "\n".join(
            f"- {n}: {m.get('descripcion') or '(sin descripción)'}" for n, m in self._macros.items()))
        cantidad = len(self._macros)
        if cantidad <= self._MAX_HABLADAS:
            return exito("macros.lista", cantidad=cantidad, nombres=self._nombres_hablados())
        return exito("macros.lista_larga", cantidad=cantidad, nombres=self._nombres_hablados())

    def ejecutar_macro(self, nombre: str) -> str:
        """Ejecuta la macro `nombre` interpretando su campo 'comando'."""
        nombre = (nombre or "").strip().lower()
        if not nombre:
            return falla("macros.sin_nombre")

        if not self._macros:
            return falla("macros.sin_macros")

        # Resolución tolerante del nombre (espacios -> guiones bajos).
        macro = self._buscar_macro(nombre)
        if macro is None:
            return falla("macros.desconocida", nombre=nombre, nombres=self._nombres_hablados())

        comando = macro.get("comando", "").strip()
        logger.info("Ejecutando macro '%s' -> comando '%s'.", nombre, comando)

        # Buscamos un manejador según el patrón del comando.
        manejador = self._resolver_manejador(comando)
        if manejador is not None:
            # Antes de aplicar un "modo" (que cambia resolución/volumen/etc.),
            # capturamos un SNAPSHOT para poder revertir con `salir_modo`.
            # (Best-effort: si falla la captura, la macro igual corre.)
            try:
                from miku.servicios import modos as modos_core  # import tardío
                modos_core.capturar()
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude capturar snapshot para la macro: %s", e)
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
        """Busca la macro sin distinguir mayúsculas ni espacios/guiones bajos.

        ``ejecutar_macro`` recibe el nombre en minúsculas, pero en el JSON puede
        estar como ``Modo_Fortnite``: se compara contra las claves normalizadas.
        """
        def _clave(texto: str) -> str:
            return texto.strip().lower().replace("_", " ")

        buscada = _clave(nombre)
        for clave, macro in self._macros.items():
            if _clave(clave) == buscada:
                return macro
        return None

    def _resolver_manejador(
            self, comando: str) -> Optional[Callable[[str, Dict[str, str]], str]]:
        """Devuelve el manejador aplicable al `comando` (o None).

        Por ahora sólo reconoce el patrón "resolucion WxH". Sumar soporte para
        otro tipo de comando = agregar acá su detección + un método.
        """
        if _RE_RESOLUCION.match(comando):
            return self._manejadores["resolucion"]
        if comando.strip().lower() == "abrir_comedor":
            return self._manejar_comedor
        return None

    def _manejar_comedor(self, comando: str, macro: Dict[str, str]) -> str:
        """La macro "comedor" delega en el plugin de inscripción (si está configurado)."""
        for plugin in getattr(self._bus, "plugins", []) or []:
            if getattr(plugin, "nombre", "") == "comedor":
                return plugin.inscribir_en_segundo_plano()
        return falla("comedor.sin_usuario")

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
#                      Cambio de resolución                             #
# ===================================================================== #
def cambiar_resolucion(ancho: int, alto: int) -> str:
    """Cambia la resolución de pantalla a `ancho`x`alto` y devuelve un mensaje natural.

    La lógica (probar el modo con ``CDS_TEST`` y recién después aplicarlo) vive en
    ``miku.plataforma.pantalla``, compartida con los snapshots de "salir del modo".
    """
    estado, codigo = pantalla.cambiar_resolucion(ancho, alto)
    if estado == pantalla.OK:
        return f"Listo, cambié la resolución a {ancho}x{alto}."
    if estado == pantalla.REINICIO:
        return (f"Cambié la resolución a {ancho}x{alto}, "
                f"pero puede pedir reiniciar para quedar fija.")
    if estado == pantalla.NO_SOPORTADA:
        return f"Tu pantalla no soporta la resolución {ancho}x{alto}."
    return f"No pude cambiar la resolución a {ancho}x{alto} (código {codigo})."
