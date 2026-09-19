# -*- coding: utf-8 -*-
"""
ventanita.py - Ventanita de seleccion de modo (Voz / Texto).

Paso 2 del lanzador (Z7): una ventana chica con dos botones para elegir como
usar Miku hoy:
    - Modo Voz   : escucha continua con wake word (luego -> bandeja).
    - Modo Texto : consola, siempre con voz.

Sin F22 todavia (eso es el paso 4). Sin "texto sin voz" (eso se quita en el 3).

IMPORTANTE (hilos y QApplication): Qt solo permite UNA QApplication y los
widgets deben crearse en el hilo que la posee. Por eso este modulo NO crea la
app: expone una funcion que CONSTRUYE el dialogo, y quien lo use (el hilo de la
bandeja, que ya posee la app) lo muestra con ``exec_()``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("miku.ventanita")

try:
    from PyQt5 import QtWidgets
    from PyQt5.QtCore import Qt
except Exception as e:  # noqa: BLE001
    QtWidgets = None
    Qt = None
    logger.warning("PyQt5 no disponible, no habra ventanita: %s", e)

# Resultados posibles.
MODO_VOZ = "voz"
MODO_TEXTO = "texto"


def disponible() -> bool:
    """True si PyQt5 esta disponible para construir el dialogo."""
    return QtWidgets is not None


def construir_dialogo() -> Optional[Any]:
    """Crea (sin mostrar) el dialogo de seleccion y devuelve el widget.

    El dialogo guarda el modo elegido en su atributo ``modo_elegido``
    (None si se cierra sin elegir). Debe llamarse EN el hilo que posee la
    QApplication.
    """
    if QtWidgets is None:
        return None
    try:
        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle("Miku - Elegir modo")
        dlg.setModal(True)
        dlg.setFixedSize(320, 190)
        dlg.modo_elegido = None  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(dlg)
        titulo = QtWidgets.QLabel("¿Como querés usar a Miku?")
        titulo.setAlignment(Qt.AlignCenter)
        titulo.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(titulo)

        btn_voz = QtWidgets.QPushButton("Modo Voz")
        btn_voz.setToolTip("Escucha continua: decí 'Miku' para activarla.")
        btn_voz.clicked.connect(lambda: _elegir(dlg, MODO_VOZ))
        layout.addWidget(btn_voz)

        btn_texto = QtWidgets.QPushButton("Modo Texto (depuración)")
        btn_texto.setToolTip("Ventana para escribirle a Miku (útil para probar). Habla igual.")
        btn_texto.clicked.connect(lambda: _elegir(dlg, MODO_TEXTO))
        layout.addWidget(btn_texto)

        dlg.rejected.connect(lambda: _elegir(dlg, None))
        return dlg
    except Exception:  # noqa: BLE001
        logger.exception("No pude construir la ventanita.")
        return None


def _elegir(dlg: Any, modo: Optional[str]) -> None:
    """Guarda el modo elegido y cierra el dialogo."""
    try:
        dlg.modo_elegido = modo  # type: ignore[attr-defined]
        dlg.accept()
    except Exception:  # noqa: BLE001
        try:
            dlg.close()
        except Exception:  # noqa: BLE001
            pass