"""
metricas.py - Cuánto tarda cada etapa de una orden (escuchar, transcribir, pensar, hablar).

Sin esto, optimizar el asistente es adivinar: la latencia se repartía entre el micrófono, Whisper, el
LLM y VOICEVOX sin que ningún log dijera cuál pesaba. Acá cada etapa se mide y, al final de la orden,
queda **una sola línea** en el log con el reparto:

    latencia | stt 812 ms · llm 534 ms · tts 208 ms | hasta la voz 1.6 s

Uso::

    from miku.servicios import metricas

    metricas.nuevo_turno("qué hora es")        # al tener el audio del usuario
    with metricas.etapa("stt"):
        texto = transcribir(audio)
    with metricas.etapa("llm"):
        respuesta = brain.consultar(texto)
    metricas.fin_turno()                       # cuando Miku empieza a hablar

El turno se cierra cuando la respuesta ya salió hacia la voz, sin esperar a que termine de sonar. Por
eso la etapa ``tts`` (sintetizar la primera frase, que ocurre en el hilo del reproductor) aparece solo
si alcanzó a terminar antes: cuando está, el total se lee "hasta la voz"; cuando no, dice "total" y se
refiere a tener la respuesta lista. Las dos cosas son ciertas y el rótulo las distingue.

Reglas de la casa:
    * **Nunca falla ni frena nada**: si algo sale mal, se ignora y el código medido sigue igual.
    * Un turno sin cerrar no se pierde: lo cierra el turno siguiente (así una orden que terminó en
      error igual deja su línea).
    * Es seguro entre hilos: la orden pasa por el hilo de escucha, el del cerebro y el del TTS.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("miku.metricas")

#: Etapa que marca "el usuario ya escucha a Miku": el total se mide hasta acá.
ETAPA_VOZ = "tts"


class Turno:
    """Las etapas de UNA orden, con cuánto tardó cada una."""

    def __init__(self, texto: str = "") -> None:
        self.texto = (texto or "").strip()
        self.inicio = time.perf_counter()
        self._etapas: List[Tuple[str, float]] = []
        self._lock = threading.Lock()
        self.cerrado = False

    def anotar(self, nombre: str, segundos: float) -> None:
        """Suma una etapa medida."""
        with self._lock:
            self._etapas.append((nombre, segundos))

    def etapas(self) -> Dict[str, float]:
        """Total por nombre de etapa, en orden de aparición (varias medidas se suman)."""
        totales: Dict[str, float] = {}
        with self._lock:
            for nombre, segundos in self._etapas:
                totales[nombre] = totales.get(nombre, 0.0) + segundos
        return totales

    def resumen(self) -> str:
        """La línea que va al log (vacía si no se midió nada)."""
        etapas = self.etapas()
        if not etapas:
            return ""
        detalle = " · ".join(f"{nombre} {segundos * 1000:.0f} ms" for nombre, segundos in etapas.items())
        total = time.perf_counter() - self.inicio
        # "hasta la voz" solo tiene sentido si la orden llegó a hablar.
        etiqueta = "hasta la voz" if ETAPA_VOZ in etapas else "total"
        linea = f"latencia | {detalle} | {etiqueta} {total:.1f} s"
        return f"{linea} | {self.texto[:40]!r}" if self.texto else linea


_lock = threading.Lock()
_turno: Optional[Turno] = None


def _activas() -> bool:
    """False si la config apaga las métricas (``METRICAS_LATENCIA = False``)."""
    try:
        from miku.ajustes import carga as config_mod
        return bool(config_mod.config.get("metricas_latencia", True))
    except Exception:  # noqa: BLE001
        return True


def nuevo_turno(texto: str = "") -> Optional[Turno]:
    """Empieza a medir una orden. Cierra la anterior si quedó abierta."""
    global _turno
    if not _activas():
        return None
    try:
        with _lock:
            anterior, _turno = _turno, Turno(texto)
            nuevo = _turno
        if anterior is not None and not anterior.cerrado:
            _emitir(anterior)
        return nuevo
    except Exception:  # noqa: BLE001
        logger.debug("No pude abrir el turno de métricas.", exc_info=True)
        return None


def fin_turno() -> None:
    """Cierra la orden en curso y deja su línea en el log."""
    global _turno
    try:
        with _lock:
            actual, _turno = _turno, None
        if actual is not None:
            _emitir(actual)
    except Exception:  # noqa: BLE001
        logger.debug("No pude cerrar el turno de métricas.", exc_info=True)


def _emitir(turno: Turno) -> None:
    """Escribe el resumen del turno (una vez)."""
    if turno.cerrado:
        return
    turno.cerrado = True
    linea = turno.resumen()
    if linea:
        logger.info("%s", linea)


def anotar(nombre: str, segundos: float) -> None:
    """Suma una etapa ya medida por fuera (p. ej. un tiempo que reportó otra librería)."""
    try:
        with _lock:
            actual = _turno
        if actual is not None:
            actual.anotar(nombre, segundos)
        logger.debug("[%s] %.0f ms", nombre, segundos * 1000)
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def etapa(nombre: str) -> Iterator[None]:
    """Mide el bloque y lo suma al turno en curso. No se traga los errores del bloque."""
    inicio = time.perf_counter()
    try:
        yield
    finally:
        anotar(nombre, time.perf_counter() - inicio)


def turno_actual() -> Optional[Turno]:
    """El turno abierto (o None). Para tests y diagnóstico."""
    with _lock:
        return _turno
