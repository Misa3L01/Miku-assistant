"""
habitos.py - Plugin de control de hábitos: te rezonga por las páginas de tu lista y pregunta si las cierra.

La lógica de cuándo rezongar está en ``miku/servicios/habitos.py`` (``Vigilante``); este plugin le pasa lo que hay
en primer plano cada pocos segundos, hace la pregunta ("¿La cierro?") por el mismo camino que cualquier
pregunta de Miku (voz, ventana de texto, botones del celular) y, si decís que sí, cierra la pestaña.

Cerrar sin hacer daño: antes de tocar nada se vuelve a leer la ventana y se comprueba que **sigas en esa página**
(si ya cambiaste de pestaña no se cierra nada) y que esté en primer plano (``Ctrl+W`` va a la ventana que tenga el
foco: si fuera otra, cerraría una pestaña que no es). Las ventanas de incógnito se cierran enteras.

Tools: ``resumen_habitos`` ("¿cuántas veces entré a Facebook esta semana?") y ``cerrar_pagina_vigilada``, que es
interna (no está en ``tools``: el LLM no la ve; solo la ejecuta el parser tras tu "sí").
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin
from miku.servicios import habitos
from miku.servicios.habitos import INCOGNITO, Disparo, Vigilante
from miku.servicios.uso import RegistroUso
from miku.voz.frases import catalogo_habitos  # noqa: F401  (registra las frases al importarse)
from miku.voz.frases.banco import frases

logger = logging.getLogger("miku.plugins.habitos")

RUTA_REGISTRO = config_mod.BASE_DIR / "data" / "habitos.json"
#: Cada cuántos segundos se mira la ventana en primer plano.
INTERVALO_SEG = 3.0


class _Win32:
    """Lo que el plugin le pide a Windows. Aparte para poder probar el cierre sin ventanas reales."""

    def titulo(self, hwnd: int) -> Optional[str]:
        """Título de la ventana (None si ya no existe)."""
        import win32gui  # type: ignore
        return win32gui.GetWindowText(hwnd) if win32gui.IsWindow(hwnd) else None

    def es_primer_plano(self, hwnd: int) -> bool:
        import win32gui  # type: ignore
        return win32gui.GetForegroundWindow() == hwnd

    def traer_al_frente(self, hwnd: int) -> None:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        try:
            if win32gui.IsIconic(hwnd):                   # solo si está minimizada: restaurar una maximizada la achica
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude traer la ventana al frente: %s", e)

    def cerrar_pestana(self) -> None:
        """Ctrl+W: cierra la pestaña de la ventana que tenga el foco."""
        import keyboard  # type: ignore
        keyboard.send("ctrl+w")

    def cerrar_ventana(self, hwnd: int) -> None:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def _lista_de_config(clave: str, por_defecto: Any) -> Any:
    return habitos.lista_normalizada(config_mod.config.get(clave), por_defecto)


class Habitos(Plugin):
    """Vigila las páginas de tu lista y te pregunta si las cierra."""

    nombre = "habitos"
    descripcion = "Control de hábitos: te rezonga por las páginas de tu lista y pregunta si las cierra."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "resumen_habitos",
                "description": "Dice cuántas veces te agarré en las páginas que vigilo (control de hábitos): hoy, "
                               "ayer o esta semana. Ej: '¿cuántas veces entré a Facebook?', 'cómo voy con mis "
                               "hábitos', 'resumen de hábitos de la semana'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "periodo": {"type": "string", "enum": ["hoy", "ayer", "semana"],
                                    "description": "Qué período resumir (por defecto hoy)."},
                    },
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._vigilante: Optional[Vigilante] = None
        self._registro: Optional[RegistroUso] = None
        self._win: Any = _Win32()
        self._leer_ventana: Optional[Callable[[], Any]] = None
        self._leer_inactivo: Optional[Callable[[], float]] = None
        self._dormir: Callable[[float], None] = time.sleep
        self._detener = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    # ------------------------------------------------------------ ciclo de vida
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        cfg = config_mod.config
        reglas = habitos.cargar_reglas(cfg.get("habitos_vigilar"))
        incognito = bool(cfg.get("habitos_incognito", False))
        if not reglas and not incognito:
            logger.info("Control de hábitos sin nada que vigilar (HABITOS_VIGILAR vacío): inactivo.")
            return
        try:
            espera = float(cfg.get("habitos_espera_seg", habitos.ESPERA_SEG))
            cooldown = float(cfg.get("habitos_cooldown_min", habitos.COOLDOWN_MIN))
        except (TypeError, ValueError):
            espera, cooldown = habitos.ESPERA_SEG, habitos.COOLDOWN_MIN
        self._vigilante = Vigilante(
            reglas, incognito, _lista_de_config("habitos_navegadores", habitos.NAVEGADORES),
            _lista_de_config("habitos_marcas_incognito", habitos.MARCAS_INCOGNITO), espera, cooldown)
        self._registro = RegistroUso(RUTA_REGISTRO)
        from miku.plataforma import inactividad, procesos
        self._leer_ventana, self._leer_inactivo = procesos.ventana_primer_plano, inactividad.segundos_inactivo
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="miku_habitos")
        self._hilo.start()
        logger.info("Control de hábitos activo (vigilo %d cosa(s)%s).", len(reglas),
                    " y las ventanas de incógnito" if incognito else "")

    def cerrar(self) -> None:
        self._detener.set()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=3.0)
        self._hilo = None
        if self._registro is not None:
            self._registro.guardar()

    def _bucle(self) -> None:
        while not self._detener.is_set():
            try:
                self.revisar()
            except Exception:  # noqa: BLE001
                logger.exception("Error en el control de hábitos; sigo.")
            self._detener.wait(INTERVALO_SEG)

    # ------------------------------------------------------------ vigilancia
    def revisar(self) -> None:
        """Una vuelta: mira la ventana en primer plano y, si toca, rezonga y pregunta."""
        if self._vigilante is None or self._leer_ventana is None or self._leer_inactivo is None:
            return
        try:
            inactivo = float(self._leer_inactivo())
        except Exception:  # noqa: BLE001
            inactivo = 0.0
        disparo = self._vigilante.revisar(self._leer_ventana(), inactivo)
        if disparo is not None:
            self._rezongar(disparo)

    def _rezongar(self, d: Disparo) -> None:
        """Dice la frase (rezongo + "¿la cierro?") y deja la pregunta esperando tu sí / no."""
        preguntar = getattr(self._event_bus, "preguntar", None)
        if not callable(preguntar) or self._registro is None or self._vigilante is None:
            if self._vigilante is not None:
                self._vigilante.olvidar(d.clave)
            return
        veces = int(round(self._registro.totales(1).get(d.nombre, 0.0))) + 1
        if d.clave == INCOGNITO:
            intencion = "habito.incognito"
        else:
            intencion = "habito.otra_vez" if veces >= 2 else "habito.regano"
        texto = frases.elegir(intencion, pagina=d.nombre, veces=veces)
        args = {"hwnd": d.hwnd, "proceso": d.proceso, "regla": d.clave, "nombre": d.nombre,
                "modo": "ventana" if d.incognito and d.clave == INCOGNITO else "pestana"}
        if not preguntar(texto, "cerrar_pagina_vigilada", args, lambda: frases.elegir("habito.perdon")):
            self._vigilante.olvidar(d.clave)               # había otra pregunta esperando: se reintenta enseguida
            return
        self._registro.sumar(d.nombre, 1.0)
        logger.info("Hábitos: rezongué por '%s' (%d hoy).", d.nombre, veces)

    # ------------------------------------------------------------ tools
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "resumen_habitos":
            if self._registro is None:
                return "No tengo nada vigilado: poné tus páginas en HABITOS_VIGILAR."
            return habitos.resumen(self._registro, str((args or {}).get("periodo", "hoy") or "hoy"))
        if nombre_tool == "cerrar_pagina_vigilada":
            return self._cerrar_pagina(args or {})
        return None

    def _cerrar_pagina(self, args: Dict[str, Any]) -> str:
        """Tu "sí": cierra la pestaña (o la ventana de incógnito) si seguís en esa página. Devuelve qué decir."""
        nombre = str(args.get("nombre", "") or "esa página")
        try:
            hwnd = int(args.get("hwnd") or 0)
        except (TypeError, ValueError):
            hwnd = 0
        resultado = self._cerrar(hwnd, str(args.get("regla", "")), str(args.get("proceso", "")),
                                 str(args.get("modo", "pestana")))
        if resultado == "cerrada":
            if self._vigilante is not None:
                self._vigilante.tras_cerrar(str(args.get("regla", "")))
            return frases.elegir("habito.cerrada", pagina=nombre)
        return frases.elegir("habito.ya_no" if resultado == "ya_no" else "habito.no_pude")

    def _sigue_en_esa_pagina(self, regla: str, hwnd: int, proceso: str, titulo: str) -> bool:
        """True si la ventana ``hwnd`` todavía cumple la regla que había saltado."""
        if self._vigilante is None:
            return False
        visto = self._vigilante.coincidencia((hwnd, proceso, titulo))
        return visto is not None and visto.clave == regla

    def _cerrar(self, hwnd: int, regla: str, proceso: str, modo: str) -> str:
        """``"cerrada"``, ``"ya_no"`` (ya no está en esa página) o ``"no_pude"``."""
        w = self._win
        titulo = w.titulo(hwnd) if hwnd else None
        if titulo is None or not self._sigue_en_esa_pagina(regla, hwnd, proceso, titulo):
            return "ya_no"
        if not w.es_primer_plano(hwnd):
            w.traer_al_frente(hwnd)
            self._dormir(0.3)
            if not w.es_primer_plano(hwnd):
                return "no_pude"                               # Ctrl+W iría a otra ventana: mejor no hacer nada
            titulo = w.titulo(hwnd)
            if titulo is None or not self._sigue_en_esa_pagina(regla, hwnd, proceso, titulo):
                return "ya_no"
        try:
            if modo == "ventana":
                w.cerrar_ventana(hwnd)
            else:
                w.cerrar_pestana()
        except Exception:  # noqa: BLE001
            logger.exception("No pude cerrar la página.")
            return "no_pude"
        return "cerrada"
