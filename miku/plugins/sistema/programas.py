"""
programas.py - Abrir y cerrar programas y juegos.

Busca en: alias/AppOpener -> biblioteca de Steam -> Epic (JUEGOS_EPIC) -> ejecutable por nombre
con Everything (con desambiguación si hay varios candidatos).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from miku.plataforma.texto import normalizar
from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin
from miku.plugins.sistema import lanzadores
from miku.plugins.utiles import aperturas
from miku.plataforma.everything import Everything
from miku.plugins.sistema.biblioteca_juegos import BibliotecaJuegos
from miku.plugins.utiles import RutasOfrecidas, abrir_resultado
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.programas")


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


class Programas(Plugin):
    """Abre y cierra programas y juegos (AppOpener, Steam, Epic, Everything)."""

    nombre = "programas"
    descripcion = "Abre y cierra programas y juegos (AppOpener, Steam, Epic, Everything)."

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
                "name": "actualizar_biblioteca_juegos",
                "description": "Re-escanea la biblioteca de Steam a demanda "
                               "(para cuando instalás un juego nuevo sin "
                               "reiniciar el asistente). Sin parámetros.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._everything = Everything()
        self._biblioteca = BibliotecaJuegos()
        self._ofrecidas = RutasOfrecidas()

    def initialize(self, event_bus: Any = None) -> None:
        """Deja el plugin listo."""
        super().initialize(event_bus)
        self._event_bus = event_bus
        logger.info("Plugin programas listo.")

    def _avisar_juego(self, nombre: str, estado: str, ok: bool) -> None:
        """Cuenta (por voz y toast) cómo terminó el arranque de un juego con lanzador."""
        from miku.voz.frases.respuesta import responder
        texto = str(responder(f"juego.{estado}", ok, nombre=nombre))
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar("Miku", texto)
        except Exception:  # noqa: BLE001
            logger.debug("Sin toast del juego.", exc_info=True)
        voz = getattr(getattr(self, "_event_bus", None), "voice", None)
        if voz is not None:
            try:
                voz.decir(texto)
            except Exception:  # noqa: BLE001
                pass

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools de programas y juegos."""
        if nombre_tool == "abrir_programa":
            # Si viene "ruta_elegida" (desambiguación ya resuelta) se abre ESO directo,
            # pero solo si es una de las rutas que nosotros ofrecimos.
            ruta_elegida = args.get("ruta_elegida")
            if ruta_elegida:
                if not self._ofrecidas.contiene(str(ruta_elegida)):
                    return "Esa opción no es una de las que te ofrecí."
                return abrir_resultado(str(ruta_elegida), False)
            return self.abrir_programa(str(args.get("nombre", "")))
        if nombre_tool == "cerrar_programa":
            return self.cerrar_programa(str(args.get("nombre", "")))
        if nombre_tool == "actualizar_biblioteca_juegos":
            return self.actualizar_biblioteca_juegos()
        return None

    def actualizar_biblioteca_juegos(self) -> str:
        """Re-escanea la biblioteca de Steam a demanda (tool sin parámetros)."""
        try:
            cantidad = self._biblioteca.actualizar()
        except Exception as e:  # noqa: BLE001
            logger.error("Error re-escaneando la biblioteca de Steam: %s", e)
            return "No pude escanear la biblioteca de Steam."
        if cantidad == 0:
            return ("No encontré juegos en tu biblioteca de Steam (¿está "
                    "instalado Steam?).")
        return f"Encontré {cantidad} juegos en tu biblioteca de Steam."

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

        # 0) Juegos con lanzador (JUEGOS_LANZADOR): abre el lanzador y, cuando aparece, el juego.
        juego = lanzadores.buscar_juego(nombre, config_mod.config.get("juegos_lanzador", {}))
        if juego is not None:
            aperturas.marcar(nombre)
            lanzadores.iniciar_en_hilo(juego, lambda estado, ok, n=nombre: self._avisar_juego(n, estado, ok))
            return exito("juego.lanzador_iniciado", nombre=nombre, con_lanzador=bool(juego.lanzador))

        app = _APPS.get(nombre) or self._buscar_alias(nombre)

        if app is not None:
            try:
                from AppOpener import open as app_open  # lazy
            except Exception:  # noqa: BLE001
                logger.exception("Falta la librería AppOpener.")
                return "No tengo el módulo AppOpener para abrir programas."
            try:
                app_open(app, match_closest=True)
                aperturas.marcar(nombre)
                return exito("programa.abriendo", nombre=nombre)
            except Exception as e:  # noqa: BLE001
                logger.error("Error abriendo %s: %s", app, e)
                return f"No pude abrir {nombre}."

        # 2) Biblioteca de Steam.
        appid = self._biblioteca.buscar_steam(nombre)
        if appid:
            try:
                aperturas.marcar(nombre)
                os.startfile(f"steam://rungameid/{appid}")  # type: ignore[attr-defined]
                logger.info("Lanzando juego de Steam '%s' (appid=%s).",
                            nombre, appid)
                return exito("programa.abriendo", nombre=nombre)
            except Exception as e:  # noqa: BLE001
                logger.error("No pude lanzar el juego de Steam '%s': %s",
                             nombre, e)
                return f"Encontré {nombre} en Steam pero no pude abrirlo."

        # 3) Epic configurado a mano (JUEGOS_EPIC en config_local.py).
        item_epic = self._biblioteca.buscar_epic(nombre)
        if item_epic:
            try:
                os.startfile(  # type: ignore[attr-defined]
                    f"com.epicgames.launcher://apps/{item_epic}?action=launch")
                logger.info("Lanzando juego de Epic '%s' (id=%s).",
                            nombre, item_epic)
                return exito("programa.abriendo", nombre=nombre)
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
        return falla("programa.no_encontrado", nombre=nombre)

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
        carpeta = normalizar(os.path.dirname(ruta)).replace("/", "\\")
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
        es = self._everything.ruta_es()
        if not es:
            logger.info("Sin es.exe no puedo buscar '%s' como ejecutable.",
                        nombre)
            return None
        terminos = [t for t in normalizar(nombre).split() if t]
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
                self._everything.ejecutar(es, consulta, _MAX_RESULTADOS_APPS))
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
            ((self._everything.puntaje(r, terminos) +
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

        mejor = self._everything.elegir_mejor(rutas_ord, puntajes_ord, nombre=nombre)
        if mejor:
            logger.info("Abriendo '%s' (encontrado por Everything).",
                        os.path.basename(mejor))
            return abrir_resultado(mejor, False)

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
        self._ofrecidas.registrar(top)
        logger.info("'%s': %d ejecutables candidatos; pregunto cuál.",
                    nombre, len(rutas_ord))
        return {
            "desambiguar": True,
            "tool_origen": "abrir_programa",
            "args_origen": {"nombre": nombre},
            "opciones": opciones,
        }

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
            return exito("programa.cerrado", nombre=nombre)
        except Exception as e:  # noqa: BLE001
            logger.error("Error cerrando %s: %s", nombre, e)
            return f"No pude cerrar {nombre}."
