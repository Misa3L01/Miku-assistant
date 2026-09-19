"""
qt_hilo.py - Único hilo de Qt compartido por la bandeja, los subtítulos y la ventanita.

Qt exige que TODO lo que toca la GUI (``QApplication``, widgets, timers) viva y
corra en un mismo hilo, y que ``exec_()`` se llame desde el hilo que creó la
``QApplication``. Antes la bandeja y el overlay de subtítulos arrancaban cada
uno su propio hilo: el segundo reutilizaba la ``QApplication`` del primero y su
``exec_()`` devolvía -1 de inmediato, por lo que los subtítulos nunca se veían.

Ahora hay UN solo hilo (``HiloQt``) que posee la ``QApplication`` y drena una
cola de callables con un ``QTimer``. Cualquier otro hilo le encola trabajo con
``ejecutar()`` (no bloqueante) o ``ejecutar_y_esperar()``.

Degradación elegante: si PyQt5 no está instalado, ``obtener()`` devuelve None y
el resto del asistente sigue funcionando sin bandeja ni subtítulos.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("miku.qt")

try:
    from PyQt5 import QtCore, QtWidgets
except Exception as e:  # noqa: BLE001
    QtCore = QtWidgets = None
    logger.warning("PyQt5 no disponible, no habrá bandeja ni subtítulos: %s", e)

# Intervalo (ms) con el que el hilo de Qt revisa la cola de trabajo.
_INTERVALO_MS = 30


class HiloQt(threading.Thread):
    """Hilo que posee la ``QApplication`` y ejecuta el trabajo que le encolan."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="miku_qt")
        self._cola: "queue.Queue[Callable[[], Any]]" = queue.Queue()
        self._listo = threading.Event()
        self._app: Any = None
        self._timer: Any = None
        self._drenando = False
        #: Excepción por la que el hilo no pudo arrancar (None si todo bien).
        self.error: Optional[BaseException] = None

    # ---------------- Estado ----------------
    @property
    def app(self) -> Any:
        """La ``QApplication`` (solo usarla desde el hilo de Qt)."""
        return self._app

    def esperar_listo(self, timeout: float = 5.0) -> bool:
        """Espera a que el hilo termine de arrancar (o venza ``timeout``)."""
        return self._listo.wait(timeout)

    def disponible(self) -> bool:
        """True si el event loop de Qt está corriendo y acepta trabajo."""
        return self._listo.is_set() and self.error is None and self.is_alive()

    # ---------------- API para otros hilos ----------------
    def ejecutar(self, funcion: Callable[[], Any]) -> None:
        """Encola ``funcion`` para que corra en el hilo de Qt (no bloqueante)."""
        self._cola.put(funcion)

    def ejecutar_y_esperar(self, funcion: Callable[[], Any],
                           timeout: float = 5.0) -> Any:
        """Ejecuta ``funcion`` en el hilo de Qt y devuelve su resultado.

        Devuelve None si venció ``timeout`` o la función lanzó una excepción
        (queda registrada en el log).
        """
        hecho = threading.Event()
        resultado: dict = {}

        def _envuelta() -> None:
            try:
                resultado["valor"] = funcion()
            finally:
                hecho.set()

        self._cola.put(_envuelta)
        hecho.wait(timeout)
        return resultado.get("valor")

    def detener(self) -> None:
        """Pide al hilo de Qt que pare su timer y salga del event loop."""
        def _parar() -> None:
            if self._timer is not None:
                self._timer.stop()
            if self._app is not None:
                self._app.quit()

        self.ejecutar(_parar)

    # ---------------- Hilo ----------------
    def run(self) -> None:
        """Crea la ``QApplication`` y corre el event loop de Qt."""
        if QtWidgets is None:
            self.error = RuntimeError("PyQt5 no disponible")
            self._listo.set()
            return
        try:
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            app.setQuitOnLastWindowClosed(False)
            self._app = app
            self._timer = QtCore.QTimer()
            self._timer.timeout.connect(self._drenar)
            self._timer.start(_INTERVALO_MS)
        except Exception as e:  # noqa: BLE001
            logger.exception("No pude crear la QApplication.")
            self.error = e
            self._listo.set()
            return

        self._listo.set()
        try:
            app.exec_()
        except Exception:  # noqa: BLE001
            logger.exception("El event loop de Qt terminó con error.")

    def _drenar(self) -> None:
        """Ejecuta el trabajo encolado. Nunca deja escapar una excepción.

        Una excepción no capturada dentro de un slot de PyQt5 aborta todo el
        proceso, así que cada callable va protegido. El flag evita que un
        ``processEvents()`` dentro de un callable reentre acá y reordene el
        trabajo.
        """
        if self._drenando:
            return
        self._drenando = True
        try:
            while True:
                try:
                    funcion = self._cola.get_nowait()
                except queue.Empty:
                    break
                try:
                    funcion()
                except Exception:  # noqa: BLE001
                    logger.exception("Error ejecutando trabajo en el hilo de Qt.")
        finally:
            self._drenando = False


_hilo: Optional[HiloQt] = None
_lock = threading.Lock()


def obtener(timeout: float = 5.0) -> Optional[HiloQt]:
    """Devuelve el hilo de Qt compartido, creándolo la primera vez.

    Returns:
        El ``HiloQt`` listo para usar, o None si PyQt5 no está disponible o la
        ``QApplication`` no pudo crearse.
    """
    global _hilo
    with _lock:
        if _hilo is None:
            hilo = HiloQt()
            hilo.start()
            hilo.esperar_listo(timeout)
            _hilo = hilo
        return _hilo if _hilo.disponible() else None
