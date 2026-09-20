"""
ventanas.py - Ventanas de Windows: listar, minimizar, mover entre monitores y acomodar.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from miku.plataforma.texto import normalizar
from miku.plugins.base import Plugin
from miku.plugins.utiles import a_entero, aperturas
from miku.voz.frases.respuesta import exito, falla, hubo_falla

logger = logging.getLogger("miku.plugins.ventanas")


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


def _cloaked(hwnd: Any) -> bool:
    """True si Windows tiene la ventana "cloaked" (oculta al usuario: escritorio virtual ajeno, UWP en segundo plano)."""
    try:
        import ctypes
        valor = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(int(hwnd), 14, ctypes.byref(valor), ctypes.sizeof(valor))
        return valor.value != 0
    except Exception:  # noqa: BLE001
        return False


def _nombre_corto(titulo: str) -> str:
    """Título de ventana -> nombre hablable: "archivo - Visual Studio Code" -> "Visual Studio Code (archivo)".

    Se recorta a 60 caracteres y se dejan las apps conocidas por su nombre (lo que va tras el último " - ").
    """
    partes = [p.strip() for p in titulo.replace(" \u2014 ", " - ").split(" - ") if p.strip()]
    if len(partes) >= 2:
        app, resto = partes[-1], partes[0]
        return f"{app} ({resto[:40]})" if resto.lower() != app.lower() else app
    return titulo[:60]


class Ventanas(Plugin):
    """Lista, minimiza, mueve y acomoda ventanas (Snap 50/50)."""

    nombre = "ventanas"
    descripcion = "Lista, minimiza, mueve y acomoda ventanas (Snap 50/50)."

    tools: List[dict] = [
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
    ]

    def __init__(self) -> None:
        super().__init__()
        self._w: Optional[Dict[str, Any]] = None  # módulos win32, importados una vez

    def initialize(self, event_bus: Any = None) -> None:
        """Deja el plugin listo."""
        super().initialize(event_bus)
        self._importar_windows()
        logger.info("Plugin ventanas listo.")

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools de ventanas."""
        if nombre_tool == "listar_ventanas":
            return self.listar_ventanas_abiertas()
        if nombre_tool == "mover_ventana":
            return self.mover_ventana(str(args.get("nombre", "")),
                                      args.get("monitor", 1))
        if nombre_tool == "posicionar_ventana":
            return self.posicionar_ventana(str(args.get("nombre", "")),
                                           str(args.get("posicion", "")),
                                           args.get("monitor", 1))
        if nombre_tool == "organizar_ventanas":
            return self.organizar_ventanas(
                str(args.get("ventana_izquierda", "") or ""),
                str(args.get("ventana_derecha", "") or ""),
                str(args.get("posicion", "") or "izquierda"),
                args.get("monitor", 1))
        if nombre_tool == "minimizar_ventana":
            return self.minimizar_ventana(str(args.get("nombre", "")))
        return None

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

    # ---------------- Ventanas (Windows) ---------------- #
    def _enumerar_ventanas(self) -> List[Any]:
        """Lista de (hwnd, título) de ventanas visibles."""
        self._importar_windows()
        if not self._w:
            return []
        gui = self._w["gui"]
        ventanas: List[Any] = []

        def _cb(hwnd: Any, _extra: Any) -> None:
            if gui.IsWindowVisible(hwnd) and gui.GetWindowText(hwnd) and self._es_ventana_de_usuario(hwnd):
                ventanas.append((hwnd, gui.GetWindowText(hwnd)))

        gui.EnumWindows(_cb, None)
        return ventanas

    #: Segundos que se espera la ventana de una app que Miku acaba de abrir.
    ESPERA_APERTURA_S = 12.0

    #: Títulos de ventanas del sistema que no son "una app abierta".
    _TITULOS_DEL_SISTEMA = frozenset({"program manager", "microsoft text input application",
                                      "windows input experience"})

    def _es_ventana_de_usuario(self, hwnd: Any) -> bool:
        """True si ``hwnd`` es una ventana que el usuario reconocería como una app abierta.

        ``EnumWindows`` devuelve también cosas invisibles para el usuario aunque tengan la marca de
        "visible": ventanas UWP ocultas ("cloaked", p. ej. la Experiencia de entrada de Windows), overlays
        transparentes (NVIDIA GeForce Overlay), ventanas de herramientas y ventanas hijas de otras.
        Sin este filtro Miku las nombraba como si estuvieran abiertas.
        """
        gui = self._w["gui"]
        try:
            if gui.GetWindowText(hwnd).strip().lower() in self._TITULOS_DEL_SISTEMA:
                return False
            estilo = gui.GetWindowLong(hwnd, -20)                    # GWL_EXSTYLE
            es_app = bool(estilo & 0x00040000)                       # WS_EX_APPWINDOW
            if estilo & 0x00000080 and not es_app:                   # WS_EX_TOOLWINDOW
                return False
            if estilo & 0x08000000 or estilo & 0x00000020:           # NOACTIVATE / TRANSPARENT (overlays)
                return False
            if gui.GetWindow(hwnd, 4) and not es_app:                # GW_OWNER: pertenece a otra ventana
                return False
            if _cloaked(hwnd):
                return False
            izq, arr, der, aba = gui.GetWindowRect(hwnd)
            minimizada = bool(gui.IsIconic(hwnd))
            if not minimizada and (der - izq < 40 or aba - arr < 40):
                return False
        except Exception:  # noqa: BLE001
            return True          # ante la duda, no ocultar una ventana real
        return True

    def listar_ventanas_abiertas(self) -> str:
        """Dice qué apps tiene abiertas el usuario (una por ventana real, sin las de fondo del sistema)."""
        ventanas = self._enumerar_ventanas()
        if not ventanas:
            return falla("ventana.ninguna_abierta")
        nombres = [_nombre_corto(titulo) for _, titulo in ventanas[:8]]
        extra = len(ventanas) - len(nombres)
        return exito("ventana.lista", cantidad=len(ventanas), nombres="; ".join(nombres),
                     mas=f" y {extra} más" if extra > 0 else "")

    def _hwnd_de(self, nombre_app: str) -> Optional[Any]:
        """Busca el hwnd de la primera ventana cuyo título coincide.

        El título de la ventana raramente es el nombre corto ("VSCode" -> la
        ventana se llama "archivo - Visual Studio Code"). Por eso, además del
        substring directo, probamos alias de título conocidos (ver
        ``_ALIAS_TITULO``). La comparación es tolerante a acentos.
        """
        objetivo = normalizar(nombre_app).strip()
        if not objetivo:
            return None

        # Conjunto de fragmentos a buscar en el título.
        fragmentos = [objetivo]
        for clave, titulos in _ALIAS_TITULO.items():
            if objetivo == normalizar(clave) or clave in objetivo:
                fragmentos.extend(normalizar(t) for t in titulos)

        # Si Miku acaba de abrir esa app (orden en cadena), su ventana puede tardar unos segundos en
        # aparecer: se la espera en vez de decir "no la veo".
        limite = time.monotonic() + (self.ESPERA_APERTURA_S if aperturas.reciente(nombre_app) else 0.0)
        while True:
            for hwnd, titulo in self._enumerar_ventanas():
                titulo_n = normalizar(titulo)
                if any(f and f in titulo_n for f in fragmentos):
                    return hwnd
            if time.monotonic() >= limite:
                return None
            time.sleep(0.5)

    def minimizar_ventana(self, nombre_app: str) -> str:
        """Minimiza la ventana del programa indicado."""
        hwnd = self._hwnd_de(nombre_app)
        if not hwnd:
            return falla("ventana.no_encontrada", app=nombre_app)
        try:
            self._w["gui"].ShowWindow(hwnd, self._w["con"].SW_MINIMIZE)
            return exito("ventana.minimizada", app=nombre_app)
        except Exception:  # noqa: BLE001
            return falla("ventana.error", app=nombre_app, accion="minimizar")

    def mover_ventana(self, nombre_app: str, monitor: int = 1) -> str:
        """Mueve la ventana de `nombre_app` al `monitor` indicado."""
        hwnd = self._hwnd_de(nombre_app)
        if not hwnd:
            return falla("ventana.no_encontrada", app=nombre_app)

        rect = self._rect_monitor(monitor)
        if rect is None:
            return falla("ventana.monitor_invalido")
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

        idx = a_entero(monitor, 1) - 1
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
            return falla("ventana.monitor_invalido")
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
            return falla("ventana.ninguna_de_dos", a=ventana_a, b=ventana_b)

        # Si falta una, acomodamos la que SÍ está y avisamos cuál faltó.
        if hwnd_a is None:
            self._mover_a_rect(hwnd_b, *rect_b, maximizar=False)
            self._traer_al_frente(hwnd_b)
            return falla("ventana.falta_una", falta=ventana_a, puesta=ventana_b)
        if hwnd_b is None:
            self._mover_a_rect(hwnd_a, *rect_a, maximizar=False)
            self._traer_al_frente(hwnd_a)
            return falla("ventana.falta_una", falta=ventana_b, puesta=ventana_a)

        self._acoplar_par(hwnd_a, rect_a, hwnd_b, rect_b)
        return exito("ventana.acoplada", a=ventana_a, b=ventana_b, orientacion=orientacion)

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
            return falla("ventana.no_encontrada", app=nombre_app)

        rect = self._rect_monitor(monitor)
        if rect is None:
            return falla("ventana.monitor_invalido")
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
            return falla("ventana.posicion_invalida")

        if self._mover_a_rect(hwnd, rx1, ry1, rx2, ry2, maximizar=maximizar):
            lugar = ("a pantalla completa" if maximizar else f"a la {pos}")
            return exito("ventana.posicionada", app=nombre_app, lugar=lugar)
        return falla("ventana.error", app=nombre_app, accion="posicionar")

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

        izq_ok = not hubo_falla(res_izq)
        der_ok = not hubo_falla(res_der)

        if izq_ok and der_ok:
            return exito("ventana.organizada", izq=app_izquierda, der=app_derecha)
        if izq_ok:
            return falla("ventana.organizada_parcial", puesta=app_izquierda,
                         lado="a la izquierda", falta=app_derecha)
        if der_ok:
            return falla("ventana.organizada_parcial", puesta=app_derecha,
                         lado="a la derecha", falta=app_izquierda)
        return falla("ventana.ninguna_de_dos", a=app_izquierda, b=app_derecha)

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
                misma = normalizar(izq) == normalizar(der)
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
