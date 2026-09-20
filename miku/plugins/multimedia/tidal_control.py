"""
tidal_control.py - Controlar TIDAL de escritorio por el puerto de depuración de Electron (CDP).

TIDAL de escritorio es una app Electron: abierta con ``--remote-debugging-port`` se puede manejar
como una página web. Es lo que permite lo que el sistema no da:

    * **Reproducir un tema/álbum/artista puntual** ("poné Show de Ado"). El enlace ``tidal://track/<id>``
      solo navega a la ficha del tema, no lo reproduce; acá se abre esa ficha y se aprieta su botón de
      play (los ``data-test`` de la interfaz de TIDAL).
    * Play/pausa/siguiente/anterior **de TIDAL** (las teclas multimedia van al último reproductor que
      sonó, que puede ser el navegador) y leer qué suena.

Si TIDAL estaba abierto **sin** el puerto, hay que reiniciarlo una vez (se cierra y se abre con el
puerto; no pierde la sesión). ``ELECTRON_RUN_AS_NODE`` se quita del entorno al lanzarlo: si Miku se
abrió desde VS Code esa variable hace que las apps Electron mueran al instante sin abrir ventana.

Los selectores dependen de la interfaz web de TIDAL (versión 2.43 al escribir esto): si TIDAL los
cambia, ``reproducir`` devuelve False y Miku lo dice en vez de fingir.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger("miku.plugins.tidal.control")

#: Resultados de ``asegurar``.
LISTO = "listo"
SIN_EXE = "sin_exe"
NO_ARRANCA = "no_arranca"

_URL_WEB = "https://desktop.tidal.com"
_PROCESOS = ("tidal.exe", "tidalplayer.exe")

# Botón de play de lo que se está mirando: la fila del tema (o el "reproducir todo" de álbum/artista).
_JS_CLIC_TEMA = """(() => {
  const filas = [...document.querySelectorAll('[data-test=tracklist-row]')];
  const fila = filas.find(f => f.innerHTML.includes('/track/%(id)s')) || filas[0];
  const b = fila && fila.querySelector('[data-test=play-button]');
  if (!b) return false; b.click(); return true; })()"""
_JS_CLIC_TODO = """(() => {
  const b = document.querySelector('[data-test=play-all]') ||
            document.querySelector('[data-test=header-controls] [data-test*=play]');
  if (!b) return false; b.click(); return true; })()"""
_JS_LISTO_TEMA = "document.querySelectorAll('[data-test=tracklist-row] [data-test=play-button]').length > 0"
_JS_LISTO_TODO = "!!document.querySelector('[data-test=play-all]')"
_JS_CLIC_ALEATORIO_TODO = """(() => {
  const b = document.querySelector('[data-test=shuffle-all]');
  if (!b) return false; b.click(); return true; })()"""
_JS_ALEATORIO_ESTADO = """(() => {
  const b = document.querySelector('[data-test=play-controls] [data-test=shuffle]');
  return b ? b.getAttribute('aria-checked') === 'true' : null; })()"""
_JS_CLIC_ALEATORIO = """(() => {
  const b = document.querySelector('[data-test=play-controls] [data-test=shuffle]');
  if (!b) return false; b.click(); return true; })()"""
_JS_SONANDO = "!!document.querySelector('[data-test=play-controls] [data-test=pause]')"
_JS_AHORA = """(() => {
  const t = document.querySelector('[data-test=footer-track-title]');
  const a = document.querySelector('[data-test=footer-artist-name]');
  return {titulo: t ? t.innerText : '', artista: a ? a.innerText : '',
          reproduciendo: !!document.querySelector('[data-test=play-controls] [data-test=pause]')}; })()"""


def entorno_sin_electron() -> Dict[str, str]:
    """Copia del entorno sin ``ELECTRON_RUN_AS_NODE`` (con esa variable Electron se comporta como Node)."""
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    return env


def exe_real(ruta: str) -> str:
    """El ``TIDAL.exe`` de la versión más nueva instalada (``app-x.y.z``); si no hay, ``ruta`` tal cual.

    El ``TIDAL.exe`` de la raíz es un lanzador de Squirrel que no reenvía argumentos como
    ``--remote-debugging-port``.
    """
    base = Path(ruta).parent if ruta else None
    if base and base.is_dir():
        def version(p: Path) -> tuple:
            return tuple(int(x) if x.isdigit() else 0 for x in p.name[4:].split("."))
        apps = sorted((p for p in base.glob("app-*") if (p / "TIDAL.exe").exists()), key=version)
        if apps:
            return str(apps[-1] / "TIDAL.exe")
    return ruta


def _mismo_tema(ahora: Dict[str, Any], titulo: str, artista: str) -> bool:
    """True si lo que muestra el reproductor es ese tema (título y, si se sabe, artista)."""
    if str(ahora.get("titulo", "")).strip().lower() != titulo.strip().lower():
        return False
    return not artista or artista.strip().lower() in str(ahora.get("artista", "")).lower()


class ControlTidal:
    """Maneja TIDAL de escritorio por CDP.

    Args:
        ruta_exe: ``TIDAL.exe`` configurado (``TIDAL_RUTA_EXE``).
        puerto: Puerto de depuración a usar.
        abrir_ws: ``f(url) -> conexión`` (los tests inyectan una falsa; por defecto ``websocket-client``).
    """

    def __init__(self, ruta_exe: str = "", puerto: int = 9223,
                 abrir_ws: Optional[Callable[[str], Any]] = None) -> None:
        self.ruta_exe = ruta_exe
        self.puerto = int(puerto)
        self._abrir_ws = abrir_ws or self._ws_por_defecto

    # ------------------------------------------------------------------ conexión
    @staticmethod
    def _ws_por_defecto(url: str) -> Any:
        import websocket  # type: ignore  # websocket-client
        # Sin cabecera Origin: Chromium rechaza los websockets de CDP que traen una.
        return websocket.create_connection(url, timeout=8, suppress_origin=True)

    @property
    def _base(self) -> str:
        return f"http://127.0.0.1:{self.puerto}"

    def vivo(self) -> bool:
        """True si TIDAL responde en el puerto de depuración."""
        try:
            return requests.get(f"{self._base}/json/version", timeout=1.5).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _pagina(self) -> Optional[Dict[str, Any]]:
        try:
            paginas = requests.get(f"{self._base}/json", timeout=3).json()
        except Exception:  # noqa: BLE001
            return None
        for p in paginas:
            if p.get("type") == "page" and "tidal.com" in str(p.get("url", "")):
                return p
        return None

    def _llamar(self, metodo: str, params: Optional[dict] = None) -> Dict[str, Any]:
        """Un comando CDP contra la página de TIDAL; ``{}`` si no se pudo."""
        pagina = self._pagina()
        if not pagina or not pagina.get("webSocketDebuggerUrl"):
            return {}
        try:
            ws = self._abrir_ws(pagina["webSocketDebuggerUrl"])
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude abrir el websocket de TIDAL: %s", e)
            return {}
        try:
            ws.send(json.dumps({"id": 1, "method": metodo, "params": params or {}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == 1:
                    return r
        except Exception as e:  # noqa: BLE001
            logger.debug("Falló el comando CDP %s: %s", metodo, e)
            return {}
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def evaluar(self, js: str) -> Any:
        """Ejecuta JavaScript en la página de TIDAL y devuelve el valor (None si falla)."""
        r = self._llamar("Runtime.evaluate", {"expression": js, "returnByValue": True})
        return ((r.get("result") or {}).get("result") or {}).get("value")

    # ------------------------------------------------------------------ arranque
    @staticmethod
    def _cerrar_tidal() -> None:
        """Cierra TIDAL (todos sus procesos)."""
        import psutil
        procesos = [p for p in psutil.process_iter(["name"])
                    if (p.info.get("name") or "").lower() in _PROCESOS]
        for p in procesos:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        _, vivos = psutil.wait_procs(procesos, timeout=5)
        for p in vivos:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _corriendo() -> bool:
        import psutil
        return any((p.info.get("name") or "").lower() in _PROCESOS
                   for p in psutil.process_iter(["name"]))

    def _lanzar(self, exe: str) -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        subprocess.Popen([exe, f"--remote-debugging-port={self.puerto}"], env=entorno_sin_electron(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)

    def asegurar(self, espera: float = 30.0) -> str:
        """Deja TIDAL abierto con el puerto de depuración (reiniciándolo si estaba sin él).

        Returns:
            ``LISTO``, ``SIN_EXE`` (no hay ``TIDAL_RUTA_EXE`` válido) o ``NO_ARRANCA``.
        """
        if self.vivo() and self._pagina():
            return LISTO
        exe = exe_real(self.ruta_exe)
        if not exe or not Path(exe).exists():
            return SIN_EXE
        if self._corriendo():
            logger.info("TIDAL estaba abierto sin puerto de control: lo reinicio.")
            self._cerrar_tidal()
            time.sleep(1.0)
        try:
            self._lanzar(exe)
        except OSError as e:
            logger.error("No pude lanzar TIDAL: %s", e)
            return NO_ARRANCA
        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            if self.vivo() and self._pagina() and self.evaluar("!!document.querySelector('[data-test=footer-player]')"):
                return LISTO
            time.sleep(0.5)
        return NO_ARRANCA

    # ------------------------------------------------------------------ acciones
    def _esperar(self, js: str, segundos: float) -> bool:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            if self.evaluar(js):
                return True
            time.sleep(0.4)
        return False

    def aleatorio(self, activar: bool) -> bool:
        """Deja el modo aleatorio del reproductor en ``activar``. True si quedó como se pidió."""
        estado = self.evaluar(_JS_ALEATORIO_ESTADO)
        if estado is None:
            return False
        if bool(estado) == activar:
            return True
        if not self.evaluar(_JS_CLIC_ALEATORIO):
            return False
        return self._esperar(_JS_ALEATORIO_ESTADO if activar else "!(%s)" % _JS_ALEATORIO_ESTADO, 4)

    def reproducir(self, tipo: str, id_: str, titulo: str = "", artista: str = "",
                   aleatorio: Optional[bool] = None) -> bool:
        """Abre la ficha de ``tipo``/``id_`` en TIDAL y aprieta su play. True si empezó a sonar.

        ``tipo``: ``track``, ``album``, ``artist`` o ``playlist``. ``titulo``/``artista`` (de un tema)
        sirven para reconocer que ya está sonando o en pausa: en ese caso su ficha no muestra el botón
        de play (muestra pausa), así que no se navega: se deja sonando y listo.

        ``aleatorio``: True = queda en modo aleatorio (en álbum/playlist/artista se usa su botón
        "Aleatorio"; en un tema se activa el aleatorio del reproductor, así lo que sigue es al azar);
        False = se apaga; None = no se toca.
        """
        if tipo not in ("track", "album", "artist", "playlist"):
            return False
        if tipo == "track" and titulo:
            ya = self.ahora()
            if ya and _mismo_tema(ya, titulo, artista):
                if not ya.get("reproduciendo") and not (self.boton("play_pausa") and self._esperar(_JS_SONANDO, 8)):
                    return False
                return aleatorio is None or self.aleatorio(aleatorio)
        ruta = f"{_URL_WEB}/{tipo}/{id_}"
        self._llamar("Page.navigate", {"url": ruta})
        es_tema = tipo == "track"
        if not self._esperar(_JS_LISTO_TEMA if es_tema else _JS_LISTO_TODO, 15):
            logger.warning("La ficha de TIDAL (%s) no mostró el botón de play.", ruta)
            return False
        time.sleep(0.5)      # que termine de renderizar antes de apretar
        if es_tema:
            js = _JS_CLIC_TEMA % {"id": id_}
        else:
            js = _JS_CLIC_ALEATORIO_TODO if aleatorio else _JS_CLIC_TODO
        if not self.evaluar(js):
            return False
        if not self._esperar(_JS_SONANDO, 8):
            return False
        return aleatorio is None or self.aleatorio(aleatorio)

    def boton(self, nombre: str) -> bool:
        """Aprieta un botón del reproductor: ``play_pausa``, ``siguiente`` o ``anterior``."""
        selector = {"play_pausa": "[data-test=play-controls] [data-test=play], [data-test=play-controls] [data-test=pause]",
                    "siguiente": "[data-test=play-controls] [data-test=next]",
                    "anterior": "[data-test=play-controls] [data-test=previous]"}.get(nombre)
        if selector is None:
            return False
        return bool(self.evaluar(f"(() => {{ const b = document.querySelector({json.dumps(selector)}); "
                                 f"if (!b) return false; b.click(); return true; }})()"))

    def ahora(self) -> Optional[Dict[str, Any]]:
        """``{titulo, artista, reproduciendo}`` de lo que muestra el reproductor (None si no se pudo leer)."""
        datos = self.evaluar(_JS_AHORA)
        return datos if isinstance(datos, dict) else None
