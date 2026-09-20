"""
arranque.py - Cómo se inicia Miku: con Windows y con una tecla (F13 por defecto).

Dos mecanismos independientes (los dos son opt-in: los activa el usuario desde la bandeja):

**Inicio con Windows.** Una entrada en ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.
Aparece en el Administrador de tareas > Inicio, donde también se puede desactivar. Arranca con
``--silencioso`` (sin ventanita de modo).

**Atajo de teclado.** Un acceso directo (.lnk) en el Menú Inicio con la tecla de invocación
(``TECLA_INVOCAR``, F13 por defecto) como "tecla de método abreviado". Windows lo ejecuta al presionar
esa tecla aunque Miku esté cerrada: si no está corriendo la abre
(``--silencioso --invocar``) y si ya corre, la nueva ejecución le avisa a la primera y se cierra.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from miku.ajustes.carga import BASE_DIR
from miku.plataforma.subprocesos import correr_powershell

logger = logging.getLogger("miku.arranque")

NOMBRE_RUN = "MikuAssistant"
_CLAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
TECLA_ATAJO = "F13"

# Teclas que Windows acepta como "método abreviado" de un acceso directo: F1-F24, con modificadores.
_RE_ATAJO = re.compile(r"^((ctrl|alt|shift)\+)*f([1-9]|1\d|2[0-4])$")


def atajo_valido(tecla: str) -> Optional[str]:
    """La tecla escrita como la pide Windows ("f13" -> "F13"), o None si un acceso directo no la admite
    (letras sueltas, teclas sin nombre ``sc:NN``...): esas solo funcionan con Miku abierta."""
    t = (tecla or "").strip().lower().replace(" ", "")
    return t.upper() if _RE_ATAJO.match(t) else None


def _interprete() -> str:
    """Ejecutable con el que se lanza Miku (``pythonw`` para que no abra una consola)."""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return str(exe)
    silencioso = exe.with_name("pythonw.exe")
    return str(silencioso if silencioso.exists() else exe)


def argumentos_de_arranque(extra: Optional[List[str]] = None) -> List[str]:
    """Comando completo: ``[interprete, main.py?, *extra]``."""
    partes = [_interprete()]
    if not getattr(sys, "frozen", False):
        partes.append(str(BASE_DIR / "main.py"))
    partes.extend(extra or [])
    return partes


def _q(parte: str) -> str:
    """Entrecomilla rutas; los flags (``--silencioso``) van tal cual."""
    return parte if parte.startswith("--") else f'"{parte}"'


def _como_linea(partes: List[str]) -> str:
    return " ".join(_q(p) for p in partes)


# ---------------------------------------------------------------- inicio con Windows
def inicio_windows_activo(nombre: str = NOMBRE_RUN) -> bool:
    """True si Miku está registrada para iniciar con Windows."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN) as clave:
            winreg.QueryValueEx(clave, nombre)
        return True
    except OSError:
        return False


def activar_inicio_windows(nombre: str = NOMBRE_RUN) -> bool:
    """Registra Miku para iniciar con Windows (sin ventanita: ``--silencioso``)."""
    try:
        import winreg
        comando = _como_linea(argumentos_de_arranque(["--silencioso"]))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as clave:
            winreg.SetValueEx(clave, nombre, 0, winreg.REG_SZ, comando)
        logger.info("Inicio con Windows activado.")
        return True
    except OSError:
        logger.exception("No pude activar el inicio con Windows.")
        return False


def desactivar_inicio_windows(nombre: str = NOMBRE_RUN) -> bool:
    """Quita la entrada de inicio (si no existía, también devuelve True)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as clave:
            try:
                winreg.DeleteValue(clave, nombre)
            except FileNotFoundError:
                pass
        logger.info("Inicio con Windows desactivado.")
        return True
    except OSError:
        logger.exception("No pude desactivar el inicio con Windows.")
        return False


# ---------------------------------------------------------------- atajo de teclado
def ruta_atajo() -> Path:
    """Dónde se guarda el acceso directo (Menú Inicio > Programas del usuario)."""
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Miku Assistant.lnk"


def atajo_activo(ruta: Optional[Path] = None) -> bool:
    """True si el acceso directo con la tecla de invocación existe."""
    return (ruta or ruta_atajo()).exists()


def activar_atajo(ruta: Optional[Path] = None, tecla: Optional[str] = None) -> bool:
    """Crea (o recrea) el acceso directo con ``tecla`` como tecla de método abreviado.

    Args:
        tecla: Tecla de invocación (``"f13"``); por defecto ``TECLA_ATAJO``. Si Windows no la admite
            en un acceso directo devuelve False sin crear nada.
    """
    ruta = ruta or ruta_atajo()
    hotkey = atajo_valido(tecla) if tecla else TECLA_ATAJO
    if hotkey is None:
        logger.info("La tecla '%s' no sirve para un acceso directo: solo funcionará con Miku abierta.", tecla)
        return False
    partes = argumentos_de_arranque(["--silencioso", "--invocar"])
    programa, argumentos = partes[0], partes[1:]
    args_txt = " ".join(_q(a) for a in argumentos)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps(str(ruta))}'); "
        f"$s.TargetPath = '{_ps(programa)}'; "
        f"$s.Arguments = '{_ps(args_txt)}'; "
        f"$s.WorkingDirectory = '{_ps(str(BASE_DIR))}'; "
        f"$s.Description = 'Abre o invoca a Miku'; "
        f"$s.Hotkey = '{hotkey}'; "
        "$s.Save()"
    )
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        resultado = correr_powershell(script, timeout=20)
    except Exception:  # noqa: BLE001
        logger.exception("No pude crear el atajo de teclado.")
        return False
    if resultado.returncode != 0 or not ruta.exists():
        logger.error("PowerShell no pudo crear el atajo: %s", (resultado.stderr or "")[:200])
        return False
    logger.info("Atajo %s creado en %s.", hotkey, ruta)
    return True


def desactivar_atajo(ruta: Optional[Path] = None) -> bool:
    """Borra el acceso directo (si no existía, también devuelve True)."""
    try:
        (ruta or ruta_atajo()).unlink(missing_ok=True)
        return True
    except OSError:
        logger.exception("No pude borrar el atajo de teclado.")
        return False


def _ps(texto: str) -> str:
    """Escapa comillas simples para un literal de PowerShell entre comillas simples."""
    return texto.replace("'", "''")
