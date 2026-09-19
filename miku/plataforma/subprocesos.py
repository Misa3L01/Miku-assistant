"""Ejecución de subprocesos en Windows sin ventanas de consola y con PowerShell en UTF-8.

Había tres copias de ``_sin_ventana`` y tres formas distintas de llamar a PowerShell (dos de
ellas decodificaban mal los acentos porque PowerShell emite OEM/cp850 y Python leía cp1252).
"""
from __future__ import annotations

import subprocess
from typing import List

_CREATE_NO_WINDOW = 0x08000000

_PREFIJO_UTF8 = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "

#: Preámbulo para llamar a las APIs asíncronas de WinRT desde PowerShell 5.1: define
#: ``Await($operacion, $tipo)``. (PowerShell 5.1 no puede usar ``GetAwaiter()`` sobre objetos COM.)
PREAMBULO_WINRT = (
    "Add-Type -AssemblyName System.Runtime.WindowsRuntime > $null; "
    "$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | "
    "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and "
    "$_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
    "function Await($op, $t) { $m = $asTask.MakeGenericMethod($t); "
    "$task = $m.Invoke($null, @($op)); $task.Wait(); $task.Result }; "
)


def sin_ventana() -> int:
    """Flags de creación de proceso para que no aparezca una consola (``CREATE_NO_WINDOW``)."""
    return getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)


def comando_powershell(script: str) -> List[str]:
    """Argumentos para ejecutar ``script`` con ``powershell.exe`` oculto y sin interacción."""
    return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-Command", script]


def correr_powershell(script: str, timeout: float,
                      capturar: bool = True) -> "subprocess.CompletedProcess[str]":
    """Ejecuta un script de PowerShell oculto.

    Args:
        script: Código PowerShell.
        timeout: Segundos máximos (lanza ``subprocess.TimeoutExpired`` si se pasa).
        capturar: Si True devuelve stdout/stderr decodificados como UTF-8 (la salida de
            PowerShell se fuerza a UTF-8, así que los acentos llegan bien). Si False, se
            descartan.
    """
    if capturar:
        return subprocess.run(
            comando_powershell(_PREFIJO_UTF8 + script), shell=False, timeout=timeout,
            capture_output=True, encoding="utf-8", errors="replace",
            creationflags=sin_ventana())
    return subprocess.run(
        comando_powershell(script), shell=False, timeout=timeout,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=sin_ventana())
