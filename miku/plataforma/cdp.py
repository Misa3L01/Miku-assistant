"""
cdp.py - Un navegador (Brave/Chrome) propio de Miku, controlado por el protocolo de depuración (CDP).

Sirve para automatizar páginas web (inscribirte al comedor, por ejemplo) **sin tocar tu Brave de
todos los días**: se abre una instancia aparte, con su propio perfil en ``data/navegador_miku/`` y su
propio puerto de depuración, se usa y se cierra. Es una ventana normal (la ves trabajar) o, si lo
pedís, oculta (``headless``).

    nav = Navegador(ruta_exe, perfil, puerto=9224, visible=True)
    nav.abrir()
    pagina = nav.pagina()
    pagina.ir("https://ejemplo.com")
    pagina.esperar("!!document.querySelector('#usuario')", 15)
    pagina.evaluar("document.title")
    nav.cerrar()

Este navegador **no restaura pestañas**: antes de abrirlo se borran las sesiones guardadas del perfil y,
al pedir la pestaña de trabajo, se cierran todas las demás. Si no, Brave reabre las de la vez anterior
y sitios como WhatsApp Web (que solo admite una ventana) dejan de cargar.

Todo lo que puede fallar (navegador que no arranca, página que no carga, JavaScript con error)
devuelve ``None``/``False`` en vez de lanzar: quien usa esto decide qué decirle al usuario.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger("miku.plataforma.cdp")


def _ws_por_defecto(url: str) -> Any:
    import websocket  # type: ignore  # websocket-client
    # Sin cabecera Origin: Chromium rechaza los websockets de CDP que traen una.
    return websocket.create_connection(url, timeout=15, suppress_origin=True)


class Pagina:
    """Una pestaña del navegador."""

    def __init__(self, base: str, datos: Dict[str, Any],
                 abrir_ws: Callable[[str], Any] = _ws_por_defecto) -> None:
        self._base = base
        self.id = str(datos.get("id", ""))
        self._url_ws = str(datos.get("webSocketDebuggerUrl", ""))
        self._abrir_ws = abrir_ws
        self._ws: Any = None
        self._n = 0

    # ------------------------------------------------------------------ comandos
    def _conexion(self) -> Any:
        if self._ws is None:
            self._ws = self._abrir_ws(self._url_ws)
        return self._ws

    def llamar(self, metodo: str, params: Optional[dict] = None) -> Dict[str, Any]:
        """Un comando CDP. ``{}`` si falló la conexión."""
        try:
            ws = self._conexion()
            self._n += 1
            ws.send(json.dumps({"id": self._n, "method": metodo, "params": params or {}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == self._n:
                    return r
        except Exception as e:  # noqa: BLE001
            logger.debug("Falló el comando CDP %s: %s", metodo, e)
            self._ws = None
            return {}

    def evaluar(self, js: str) -> Any:
        """Ejecuta JavaScript en la página y devuelve el valor (None si falla)."""
        r = self.llamar("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
        resultado = (r.get("result") or {})
        if resultado.get("exceptionDetails"):
            logger.debug("Error de JavaScript: %s", resultado["exceptionDetails"].get("text"))
            return None
        return (resultado.get("result") or {}).get("value")

    def esperar(self, js: str, segundos: float = 15.0) -> bool:
        """Espera (sondeando) a que ``js`` dé algo verdadero. False si vence el tiempo."""
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            if self.evaluar(js):
                return True
            time.sleep(0.4)
        return False

    def ir(self, url: str, espera: float = 20.0) -> bool:
        """Navega a ``url`` y espera a que el documento termine de cargar."""
        self.llamar("Page.navigate", {"url": url})
        time.sleep(0.5)
        return self.esperar("document.readyState === 'complete'", espera)

    def url(self) -> str:
        return str(self.evaluar("location.href") or "")

    def texto(self, limite: int = 6000) -> str:
        """Texto visible de la página (recortado)."""
        return str(self.evaluar(f"document.body ? document.body.innerText.slice(0, {int(limite)}) : ''") or "")

    def escribir(self, selector: str, valor: str) -> bool:
        """Pone ``valor`` en un campo y avisa a la página (eventos input/change)."""
        js = ("(() => { const e = document.querySelector(%s); if (!e) return false; e.focus(); "
              "e.value = %s; e.dispatchEvent(new Event('input', {bubbles: true})); "
              "e.dispatchEvent(new Event('change', {bubbles: true})); return true; })()"
              % (json.dumps(selector), json.dumps(valor)))
        return bool(self.evaluar(js))

    def clic(self, selector: str) -> bool:
        """Aprieta el primer elemento que coincida con ``selector``."""
        js = ("(() => { const e = document.querySelector(%s); if (!e) return false; e.click(); return true; })()"
              % json.dumps(selector))
        return bool(self.evaluar(js))

    def captura(self, ruta: Path) -> bool:
        """Guarda una captura PNG de la página."""
        r = self.llamar("Page.captureScreenshot", {"format": "png"})
        datos = (r.get("result") or {}).get("data")
        if not datos:
            return False
        try:
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
            Path(ruta).write_bytes(base64.b64decode(datos))
            return True
        except OSError as e:
            logger.debug("No pude guardar la captura: %s", e)
            return False

    def cerrar(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001
            pass
        self._ws = None


#: Dónde guarda Chromium/Brave las pestañas para restaurarlas (dentro del perfil).
_SESIONES = ("Sessions", "Current Session", "Current Tabs", "Last Session", "Last Tabs")


def limpiar_sesiones(perfil: Path) -> None:
    """Borra las pestañas guardadas del perfil (nada más: cookies, sesiones de sitios y demás no se tocan).

    Solo se llama con el navegador cerrado. También marca el cierre anterior como normal, para que
    Brave no ofrezca "restaurar páginas".
    """
    base = Path(perfil) / "Default"
    for nombre in _SESIONES:
        destino = base / nombre
        try:
            if destino.is_dir():
                shutil.rmtree(destino, ignore_errors=True)
            elif destino.exists():
                destino.unlink()
        except OSError as e:
            logger.debug("No pude borrar %s: %s", destino, e)
    preferencias = base / "Preferences"
    try:
        datos = json.loads(preferencias.read_text(encoding="utf-8"))
        perfil_pref = datos.setdefault("profile", {})
        if perfil_pref.get("exit_type") != "Normal" or perfil_pref.get("exited_cleanly") is False:
            perfil_pref["exit_type"] = "Normal"
            perfil_pref["exited_cleanly"] = True
            preferencias.write_text(json.dumps(datos, separators=(",", ":")), encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        pass


class Navegador:
    """Una instancia aparte de Brave/Chrome con puerto de depuración.

    Args:
        ruta_exe: ``brave.exe`` (o ``chrome.exe``).
        perfil: Carpeta del perfil propio (se crea).
        puerto: Puerto de depuración (distinto del de tu Brave normal).
        visible: False = sin ventana (headless).
        minimizada: con ``visible``, abre la ventana ya minimizada (no roba el foco).
        user_agent: Identificación del navegador. Sin ventana (headless) Chrome se anuncia como
            "HeadlessChrome" y sitios como WhatsApp Web se niegan a abrir.
    """

    def __init__(self, ruta_exe: str, perfil: Path, puerto: int = 9224, visible: bool = True,
                 abrir_ws: Callable[[str], Any] = _ws_por_defecto, minimizada: bool = False,
                 user_agent: str = "") -> None:
        self.ruta_exe = ruta_exe
        self.user_agent = user_agent
        self.perfil = Path(perfil)
        self.puerto = int(puerto)
        self.visible = visible
        self.minimizada = minimizada
        self._abrir_ws = abrir_ws
        self._proceso: Optional[subprocess.Popen] = None
        self._paginas: list = []

    @property
    def _base(self) -> str:
        return f"http://127.0.0.1:{self.puerto}"

    def vivo(self) -> bool:
        try:
            return requests.get(f"{self._base}/json/version", timeout=1.5).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def abrir(self, espera: float = 25.0) -> bool:
        """Lanza el navegador (si no estaba) y espera a que el puerto responda."""
        if self.vivo():
            return True
        if not self.ruta_exe or not Path(self.ruta_exe).exists():
            logger.error("No encuentro el navegador: %s", self.ruta_exe)
            return False
        self.perfil = self.perfil.resolve()
        self.perfil.mkdir(parents=True, exist_ok=True)
        limpiar_sesiones(self.perfil)                    # que no reabra las pestañas de la vez anterior
        args = [self.ruta_exe, f"--remote-debugging-port={self.puerto}", f"--user-data-dir={self.perfil}",
                "--no-first-run", "--no-default-browser-check", "--disable-sync", "about:blank"]
        if self.user_agent:
            args.insert(-1, f"--user-agent={self.user_agent}")
        if self.visible:
            args.insert(-1, "--window-size=1150,850")
            if self.minimizada:
                args.insert(-1, "--start-minimized")
        else:
            # Sin ventana el tamaño por defecto es chico y WhatsApp Web recorta la lista de mensajes.
            args.insert(-1, "--window-size=1150,850")
            args.insert(-1, "--headless=new")
        env = dict(os.environ)
        env.pop("ELECTRON_RUN_AS_NODE", None)
        try:
            self._proceso = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError as e:
            logger.error("No pude lanzar el navegador: %s", e)
            return False
        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            if self.vivo():
                if self.visible and self.minimizada:
                    self.minimizar()
                return True
            time.sleep(0.4)
        return False

    def minimizar(self) -> bool:
        """Minimiza la ventana (``--start-minimized`` no siempre se respeta, así que también se pide por CDP)."""
        pagina = self.pagina()
        if pagina is None:
            return False
        try:
            ventana = pagina.llamar("Browser.getWindowForTarget", {"targetId": pagina.id})
            id_ventana = ((ventana.get("result") or {}).get("windowId"))
            if id_ventana is None:
                return False
            r = pagina.llamar("Browser.setWindowBounds", {"windowId": id_ventana, "bounds": {"windowState": "minimized"}})
            return "error" not in r
        finally:
            pagina.cerrar()
            if pagina in self._paginas:
                self._paginas.remove(pagina)

    def _objetivos(self) -> list:
        """Las pestañas abiertas (sin extensiones ni herramientas de desarrollo)."""
        return [t for t in requests.get(f"{self._base}/json", timeout=4).json() if t.get("type") == "page"]

    def dejar_una_pestana(self, estable: float = 1.2) -> Optional[Dict[str, Any]]:
        """Cierra todas las pestañas menos una y devuelve esa (None si no se pudo).

        Se queda con una en blanco si la hay. Se repite hasta que el número de pestañas deja de cambiar
        ``estable`` segundos, porque el navegador puede seguir abriendo pestañas un instante después de
        que el puerto responde.
        """
        limite = time.monotonic() + 12.0
        quieto_desde = time.monotonic()
        ultimo = -1
        elegido: Optional[Dict[str, Any]] = None
        while time.monotonic() < limite:
            objetivos = self._objetivos()
            if not objetivos:
                objetivos = [requests.put(f"{self._base}/json/new?about:blank", timeout=4).json()]
            en_blanco = [t for t in objetivos if str(t.get("url", "")) in ("about:blank", "chrome://newtab/",
                                                                          "brave://newtab/")]
            elegido = (en_blanco or objetivos)[0]
            sobran = [t for t in objetivos if t.get("id") != elegido.get("id")]
            for t in sobran:
                try:
                    requests.get(f"{self._base}/json/close/{t.get('id')}", timeout=4)
                except Exception as e:  # noqa: BLE001
                    logger.debug("No pude cerrar la pestaña %s: %s", t.get("id"), e)
            if len(objetivos) != ultimo or sobran:
                ultimo = len(objetivos)
                quieto_desde = time.monotonic()
            elif time.monotonic() - quieto_desde >= estable:
                break
            time.sleep(0.3)
        return elegido

    def pagina(self, unica: bool = True) -> Optional[Pagina]:
        """La pestaña de trabajo, lista para usar. Con ``unica`` (por defecto) se cierran las demás."""
        try:
            if unica:
                elegido = self.dejar_una_pestana()
            else:
                objetivos = self._objetivos()
                elegido = objetivos[0] if objetivos else requests.put(
                    f"{self._base}/json/new?about:blank", timeout=4).json()
        except Exception as e:  # noqa: BLE001
            logger.error("No pude obtener una pestaña: %s", e)
            return None
        if not elegido:
            return None
        p = Pagina(self._base, elegido, self._abrir_ws)
        self._paginas.append(p)
        return p

    def cerrar(self) -> None:
        """Cierra el navegador (solo el propio: no toca tu Brave normal)."""
        for p in self._paginas:
            p.cerrar()
        self._paginas = []
        try:
            if self.vivo():
                pagina = Pagina(self._base, {"webSocketDebuggerUrl": requests.get(
                    f"{self._base}/json/version", timeout=2).json().get("webSocketDebuggerUrl", "")}, self._abrir_ws)
                pagina.llamar("Browser.close")
                pagina.cerrar()
        except Exception:  # noqa: BLE001
            pass
        if self._proceso is not None:
            try:
                self._proceso.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._proceso.terminate()
                except Exception:  # noqa: BLE001
                    pass
            self._proceso = None
