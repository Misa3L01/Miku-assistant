"""
hardware.py - Lecturas de CPU, RAM, disco, batería y GPU (best-effort, nunca lanzan).

Compartido por la tool ``estado_pc`` y por el motor de avisos proactivos, que antes tenían cada uno
su copia de la lógica del disco principal.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

from miku.plataforma.subprocesos import sin_ventana

logger = logging.getLogger("miku.plataforma.hardware")

_GB = 1024 ** 3


@dataclass
class UsoGpu:
    """Estado de una GPU NVIDIA."""

    porcentaje: float
    temperatura: Optional[float]
    memoria_usada_mb: Optional[float]
    memoria_total_mb: Optional[float]


def disco_principal(psutil_mod: Any = None) -> Optional[Any]:
    """Uso del disco del sistema (``SystemDrive``); si no se detecta, el primero que responda."""
    if psutil_mod is None:
        try:
            import psutil as psutil_mod  # type: ignore
        except Exception:  # noqa: BLE001
            return None
    objetivo = os.environ.get("SystemDrive", "C:").upper()
    particiones = psutil_mod.disk_partitions(all=False)
    for p in particiones:
        if (p.mountpoint or "").upper().startswith(objetivo):
            try:
                return psutil_mod.disk_usage(p.mountpoint)
            except Exception:  # noqa: BLE001
                continue
    for p in particiones:
        try:
            return psutil_mod.disk_usage(p.mountpoint)
        except Exception:  # noqa: BLE001
            continue
    return None


def disco_libre_gb() -> Optional[float]:
    """GB libres del disco principal, o None."""
    disco = disco_principal()
    return None if disco is None else disco.free / _GB


def cpu_porcentaje(intervalo: float = 0.0) -> Optional[float]:
    """Uso de CPU en %. Con ``intervalo=0`` no bloquea (mide desde la llamada anterior)."""
    try:
        import psutil
        return float(psutil.cpu_percent(interval=intervalo or None))
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la CPU: %s", e)
        return None


def ram_porcentaje() -> Optional[float]:
    """Uso de RAM en %."""
    try:
        import psutil
        return float(psutil.virtual_memory().percent)
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la RAM: %s", e)
        return None


def bateria() -> Optional[Any]:
    """``psutil.sensors_battery()`` o None si el equipo no tiene batería."""
    try:
        import psutil
        return psutil.sensors_battery()
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la batería: %s", e)
        return None


def gpu_nvidia() -> Optional[UsoGpu]:
    """Uso de la primera GPU NVIDIA vía ``nvidia-smi``; None si no hay o no responde."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=sin_ventana())
        if r.returncode != 0 or not r.stdout.strip():
            return None
        campos = [c.strip() for c in r.stdout.strip().splitlines()[0].split(",")]
        return UsoGpu(
            porcentaje=float(campos[0]),
            temperatura=_num(campos, 1),
            memoria_usada_mb=_num(campos, 2),
            memoria_total_mb=_num(campos, 3),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("nvidia-smi no respondió: %s", e)
        return None


def _num(campos: list, i: int) -> Optional[float]:
    try:
        return float(campos[i])
    except (IndexError, ValueError):
        return None
