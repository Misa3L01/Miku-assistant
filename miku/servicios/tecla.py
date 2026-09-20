"""
tecla.py - Elegir y diagnosticar la tecla que invoca a Miku (por defecto F22).

Un teclado de notebook no tiene F22: teclas especiales como la de OMEN (HP Victus/Omen), las de
volumen o las de macros llegan de otra forma. Este módulo permite **detectar qué manda la tecla que
vos apretás** y usarla como tecla de invocación (``TECLA_INVOCAR``):

    * ``"f22"``, ``"f13"``, ``"ctrl+alt+m"``: nombres normales de teclas.
    * ``"sc:NN"``: una tecla sin nombre, identificada por su *scan code* (típico de teclas especiales).

Desde la bandeja: *Elegir tecla de invocación…*. Para diagnosticar a mano (muestra todo lo que llega
durante 15 segundos):  ``python -m miku.servicios.tecla``

Limitación: hay teclas (algunas de fabricantes) que el driver del fabricante consume antes de que
Windows las reparta a los programas; si no aparece nada al apretarla, esa tecla no se puede usar.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional, Union

logger = logging.getLogger("miku.tecla")

#: Tecla por defecto.
POR_DEFECTO = "f22"
_PREFIJO_SC = "sc:"


def a_hotkey(valor: str) -> Union[str, int]:
    """Valor de config -> lo que entiende ``keyboard.add_hotkey`` (``"sc:57"`` -> 57)."""
    valor = (valor or "").strip().lower() or POR_DEFECTO
    if valor.startswith(_PREFIJO_SC):
        try:
            return int(valor[len(_PREFIJO_SC):])
        except ValueError:
            return POR_DEFECTO
    return valor


def nombre_legible(valor: str) -> str:
    """Cómo decirle al usuario cuál es la tecla ("f22" -> "F22", "sc:57" -> "la tecla especial (código 57)")."""
    valor = (valor or "").strip().lower() or POR_DEFECTO
    if valor.startswith(_PREFIJO_SC):
        return f"la tecla especial (código {valor[len(_PREFIJO_SC):]})"
    return valor.upper() if len(valor) <= 3 else valor


def clave_de_evento(evento: Any) -> str:
    """Valor de config para un evento de ``keyboard``: su nombre si es fiable, si no ``sc:<scan_code>``."""
    nombre = str(getattr(evento, "name", "") or "").strip().lower()
    scan = getattr(evento, "scan_code", None)
    # Las teclas sin nombre (o con nombres que solo son un número) se identifican por scan code.
    if nombre and not nombre.isdigit() and nombre != "unknown":
        return nombre
    return f"{_PREFIJO_SC}{scan}" if scan is not None else POR_DEFECTO


def descripcion_evento(evento: Any) -> str:
    """Una línea con todo lo que se sabe del evento (para el diagnóstico)."""
    return (f"{getattr(evento, 'event_type', '?'):5} nombre={getattr(evento, 'name', None)!r} "
            f"scan_code={getattr(evento, 'scan_code', None)} teclado_numerico={getattr(evento, 'is_keypad', None)} "
            f"dispositivo={getattr(evento, 'device', None)!r}  -> valor de config: {clave_de_evento(evento)}")


def detectar(timeout: float = 10.0, teclado: Any = None) -> Optional[str]:
    """Espera a que se apriete una tecla y devuelve su valor de config (None si no llegó nada).

    Args:
        timeout: Segundos de espera.
        teclado: Módulo ``keyboard`` (los tests pasan uno falso).
    """
    if teclado is None:
        try:
            import keyboard as teclado  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.warning("No puedo leer el teclado sin la librería 'keyboard': %s", e)
            return None
    capturado: List[Any] = []
    listo = threading.Event()

    def _al_evento(evento: Any) -> None:
        if getattr(evento, "event_type", "down") == "down" and not capturado:
            capturado.append(evento)
            listo.set()

    manejador = teclado.hook(_al_evento)
    try:
        listo.wait(timeout)
    finally:
        try:
            teclado.unhook(manejador)
        except Exception:  # noqa: BLE001
            pass
    return clave_de_evento(capturado[0]) if capturado else None


def diagnosticar(segundos: float = 15.0) -> List[str]:
    """Muestra (y devuelve) todos los eventos de teclado durante ``segundos``."""
    import time

    import keyboard  # type: ignore
    lineas: List[str] = []

    def _al_evento(evento: Any) -> None:
        linea = descripcion_evento(evento)
        lineas.append(linea)
        print(linea, flush=True)

    manejador = keyboard.hook(_al_evento)
    try:
        time.sleep(segundos)
    finally:
        keyboard.unhook(manejador)
    return lineas


if __name__ == "__main__":
    print("Apretá la tecla que querés usar (y otras, para comparar). Escucho 15 segundos...\n")
    encontrados = diagnosticar(15.0)
    if not encontrados:
        print("\nNo llegó NINGÚN evento de teclado. Si apretaste la tecla y no aparece nada, esa tecla la "
              "consume un programa del fabricante (p. ej. OMEN Hub) y Miku no puede verla.")
