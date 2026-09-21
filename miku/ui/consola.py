"""
consola.py - Ventana de depuración del "modo texto".

Una ventanita con un registro y una línea para escribirle a Miku. Reemplaza a la consola
``input()`` (que no existe cuando Miku arranca con ``pythonw``, p. ej. con Windows o con la tecla de invocación).

Corre en el hilo de Qt compartido (``ui/qt_hilo``). Lo que se escribe se entrega a ``al_enviar`` en un
hilo aparte, para no congelar la ventana mientras el cerebro piensa.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from miku.ui import qt_hilo

logger = logging.getLogger("miku.consola")

try:
    from PyQt5 import QtWidgets
except Exception:  # noqa: BLE001
    QtWidgets = None


class _PanelConsola:
    """Widgets de la ventana. Sus métodos corren en el hilo de Qt."""

    def __init__(self, al_enviar: Callable[[str], None]) -> None:
        self._al_enviar = al_enviar
        self._ventana: Any = None
        self._registro: Any = None
        self._entrada: Any = None

    def _crear(self) -> None:
        ventana = QtWidgets.QWidget()
        ventana.setWindowTitle("Miku - Modo texto (depuración)")
        ventana.resize(560, 380)
        capas = QtWidgets.QVBoxLayout(ventana)
        registro = QtWidgets.QTextEdit()
        registro.setReadOnly(True)
        entrada = QtWidgets.QLineEdit()
        entrada.setPlaceholderText("Escribile a Miku y apretá Enter…")
        entrada.returnPressed.connect(self._enviar)
        capas.addWidget(registro)
        capas.addWidget(entrada)
        self._ventana, self._registro, self._entrada = ventana, registro, entrada

    def _enviar(self) -> None:
        texto = self._entrada.text().strip()
        self._entrada.clear()
        if not texto:
            return
        self.agregar("Vos", texto)
        threading.Thread(target=self._entregar, args=(texto,), name="miku_consola_envio",
                         daemon=True).start()

    def _entregar(self, texto: str) -> None:
        try:
            self._al_enviar(texto)
        except Exception:  # noqa: BLE001
            logger.exception("Falló el envío desde la consola de depuración.")

    def mostrar(self) -> None:
        if self._ventana is None:
            self._crear()
        self._ventana.show()
        self._ventana.raise_()
        self._ventana.activateWindow()
        self._entrada.setFocus()

    def ocultar(self) -> None:
        if self._ventana is not None:
            self._ventana.hide()

    def agregar(self, quien: str, texto: str) -> None:
        if self._ventana is None:
            self._crear()
        self._registro.append(f"<b>{quien}:</b> {_escapar(texto)}")


def _escapar(texto: str) -> str:
    return (texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br>"))


class ConsolaDebug:
    """Fachada thread-safe de la ventana de depuración.

    Args:
        al_enviar: Se llama (en un hilo aparte) con cada mensaje que el usuario escribe.

    Raises:
        RuntimeError: si PyQt5 no está disponible.
    """

    def __init__(self, al_enviar: Callable[[str], None]) -> None:
        hilo = qt_hilo.obtener() if QtWidgets is not None else None
        if hilo is None:
            raise RuntimeError("PyQt5 es necesario para la consola de depuración.")
        self._hilo = hilo
        self._panel = _PanelConsola(al_enviar)

    def mostrar(self) -> None:
        """Muestra (y trae al frente) la ventana."""
        self._hilo.ejecutar(self._panel.mostrar)

    def ocultar(self) -> None:
        """Oculta la ventana (se conserva el historial)."""
        self._hilo.ejecutar(self._panel.ocultar)

    def agregar(self, quien: str, texto: str) -> None:
        """Agrega una línea al registro."""
        self._hilo.ejecutar(lambda: self._panel.agregar(quien, texto))

    def visible(self) -> Optional[bool]:
        """True si la ventana está a la vista (None si aún no se creó)."""
        resultado = self._hilo.ejecutar_y_esperar(
            lambda: None if self._panel._ventana is None else self._panel._ventana.isVisible(), 3.0)
        return resultado
