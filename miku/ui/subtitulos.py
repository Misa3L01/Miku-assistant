"""
subtitles.py - Overlay de subtítulos estilo anime con PyQt5.

Mientras Miku "habla", se muestra una ventana transparente y sin bordes
(siempre encima) con el texto en español. La ventana:

    - sin bordes                 -> Qt.FramelessWindowHint
    - siempre encima             -> Qt.WindowStaysOnTopHint
    - sin barra de tareas        -> Qt.Tool
    - fondo transparente         -> WA_TranslucentBackground
    - no captura mouse/teclado   -> WA_TransparentForMouseEvents / WindowDoesNotAcceptFocus

Diseño de hilos (importante para PyQt):
    Todo el código que toca Qt corre en el hilo compartido de ``core.qt_hilo``
    (el mismo que usa la bandeja: Qt solo admite un event loop). Para
    mostrar/ocultar se encola trabajo en ese hilo, así la "afinidad de hilos"
    queda consistente y no hay crashes.

Así se puede llamar a ``mostrar()/ocultar()`` desde el hilo del TTS sin romper.
"""
from __future__ import annotations

import logging

from miku.ui import qt_hilo

logger = logging.getLogger("miku.subtitles")

# Import de PyQt5 protegido para no romper el arranque (p. ej. modo texto).
try:
    from PyQt5 import QtWidgets
    from PyQt5.QtCore import Qt
except Exception as e:  # noqa: BLE001
    QtWidgets = None
    Qt = None
    logger.warning("PyQt5 no disponible, no habrá subtítulos: %s", e)

# Comandos aceptados por el panel.
_VER = "ver"
_ACTUALIZAR = "actualizar"
_OCULTAR = "ocultar"
_CERRAR = "cerrar"


# Ancho MÁXIMO (px) del overlay de subtítulos. Se usa como ancho FIJO del
# widget para evitar el resize horizontal dinámico (causa conocida de glitches
# de repintado con WA_TranslucentBackground en Windows) y para que el texto
# haga word-wrap en varias líneas en vez de recortarse.
_ANCHO_MAX = 1100


class _PanelSubtitulos:
    """Widgets de los subtítulos.

    Los métodos ``*_sync`` y ``_ejecutar`` corren SIEMPRE en el hilo de Qt;
    desde cualquier otro hilo se usa ``emitir()``.
    """

    def __init__(self, hilo: "qt_hilo.HiloQt") -> None:
        self._hilo = hilo
        self._widget = None
        self._label = None

    @property
    def _app(self):
        """La ``QApplication`` compartida."""
        return self._hilo.app

    def emitir(self, comando: str, *args) -> None:
        """Pide al hilo de Qt ejecutar un comando (no bloqueante)."""
        self._hilo.ejecutar(lambda: self._ejecutar(comando, args))

    # ---------------- procesamiento ----------------
    def _ejecutar(self, comando: str, args: tuple) -> None:
        """Despacha un comando sobre los widgets (corre en el hilo de Qt)."""
        if comando == _VER:
            self._mostrar_sync(args[0] if args else "")
        elif comando == _ACTUALIZAR:
            self._actualizar_sync(args[0] if args else "")
        elif comando == _OCULTAR:
            self._ocultar_sync()
        elif comando == _CERRAR:
            self._cerrar_sync()

    # ---------------- Qt en este hilo ----------------
    def _crear_widget(self) -> None:
        widget = QtWidgets.QWidget()
        widget.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        widget.setAttribute(Qt.WA_TranslucentBackground)
        widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        widget.setAttribute(Qt.WA_ShowWithoutActivating, True)

        label = QtWidgets.QLabel(widget)
        label.setAlignment(Qt.AlignCenter)
        # Word-wrap: frases largas se envuelven en varias líneas en lugar de
        # recortarse (era la causa del efecto "rendija"/clipping).
        label.setWordWrap(True)

        from PyQt5.QtGui import QColor, QFont
        from PyQt5.QtWidgets import QGraphicsDropShadowEffect

        fuente = QFont("Arial", 22)
        fuente.setBold(True)
        label.setFont(fuente)

        # Borde negro con un único efecto de sombra desplazado; combinado con
        # un segundo QPainter (outline) en el paintEvent del label para borde.
        efe = QGraphicsDropShadowEffect(widget)
        efe.setBlurRadius(2)
        efe.setColor(QColor(0, 0, 0, 255))
        efe.setOffset(3, 3)
        label.setGraphicsEffect(efe)

        self._widget = widget
        self._label = label
        self._reemplazar_paint(label)

    def _reemplazar_paint(self, label) -> None:
        """Reemplaza paintEvent del label para un borde grueso negro."""
        direcciones = [(-2, 0), (2, 0), (0, -2), (0, 2),
                       (-2, -2), (2, -2), (-2, 2), (2, 2)]

        def nuevo_paint(event):  # noqa: ANN001
            from PyQt5.QtGui import QPainter, QColor
            painter = QPainter(label)
            painter.setRenderHint(QPainter.Antialiasing)
            color_borde = QColor(0, 0, 0, 255)
            # Flags con word-wrap + centrado: el contorno debe envolver igual
            # que el label para no dibujar el borde recortado.
            flags = int(Qt.AlignCenter) | int(Qt.TextWordWrap)
            for dx, dy in direcciones:
                painter.save()
                painter.translate(dx, dy)
                painter.setPen(color_borde)
                painter.drawText(label.rect(), flags, label.text())
                painter.restore()
            painter.setPen(QColor(255, 255, 255, 255))
            painter.drawText(label.rect(), flags, label.text())
            painter.end()

        label.paintEvent = nuevo_paint

    def _ajustar_tamano(self, texto: str) -> None:
        """Ajusta el texto y la ALTURA del widget con word-wrap.

        Estrategia anti-"rendija" (clipping):
          - El ANCHO es FIJO (_ANCHO_MAX): NO se redimensiona horizontalmente en
            cada frase. Esto evita el glitch de repintado de Qt con
            WA_TranslucentBackground + resize dinámico en Windows.
          - El TEXTO se envuelve en varias líneas (setWordWrap).
          - La ALTURA se recalcula según las líneas reales que ocupa.

        Tras el resize se fuerza update()/repaint() para asegurar el redibujado
        (opción (a) del diagnóstico).
        """
        from PyQt5.QtCore import QRect, Qt as _Qt
        from PyQt5.QtGui import QFontMetrics

        texto_n = (texto or "").replace("\n", " ").strip()
        self._label.setText(texto_n)

        fm = QFontMetrics(self._label.font())
        # Ancho fijo (menos un pequeño padding interno).
        ancho = _ANCHO_MAX
        ancho_texto = max(1, ancho - 40)

        # Altura necesaria para el texto envuelto a ese ancho.
        rect = fm.boundingRect(
            QRect(0, 0, ancho_texto, 0),
            int(_Qt.AlignCenter) | int(_Qt.TextWordWrap), texto_n)
        alto = max(fm.lineSpacing() + 24, rect.height() + 24)

        self._label.resize(ancho, alto)
        self._widget.resize(ancho, alto)

        # Forzar el repintado explícito (evita superficies compuestas viejas).
        try:
            self._label.update()
            self._widget.update()
            self._widget.repaint()
            # Refuerzo (opción 2 del diagnóstico): bombear los eventos de Qt YA
            # para que el repintado se entregue aunque el compositor (DWM) haya
            # perdido el evento tras mostrar la ventana translúcida.
            if self._app is not None:
                self._app.processEvents()
        except Exception:  # noqa: BLE001
            pass

    def _mostrar_sync(self, texto: str) -> None:
        if self._widget is None:
            self._crear_widget()
        self._ajustar_tamano(texto)
        self._posicionar()
        self._widget.show()
        self._widget.raise_()
        # Bombear eventos para asegurar el primer repintado inmediato.
        if self._app is not None:
            self._app.processEvents()

    def _actualizar_sync(self, texto: str) -> None:
        """Cambia el texto/tamaño SIN parpadeos dentro de un mismo turno.

        IMPORTANTE: si el widget existe pero está OCULTO (porque el turno
        anterior terminó con _ocultar_subtitulos() -> hide()), hay que
        volver a mostrarlo. Si no, el subtítulo aparece UNA vez y después
        nunca más (bug real detectado en pruebas: cada turno terminaba en
        hide() y el siguiente _actualizar_sync no re-mostraba la ventana).
        """
        if self._widget is None:
            # Todavía no se mostró nunca: comportarse como mostrar().
            self._mostrar_sync(texto)
            return
        self._ajustar_tamano(texto)
        self._posicionar()  # re-centra (altura puede haber cambiado)
        # Si quedó oculto (fin del turno anterior), lo mostramos de nuevo.
        if not self._widget.isVisible():
            self._widget.show()
        self._widget.raise_()

    def _ocultar_sync(self) -> None:
        if self._widget is not None:
            try:
                self._widget.hide()
            except Exception:  # noqa: BLE001
                pass

    def _cerrar_sync(self) -> None:
        if self._widget is not None:
            try:
                self._widget.close()
            except Exception:  # noqa: BLE001
                pass

    def _posicionar(self) -> None:
        if not self._app or self._widget is None:
            return
        pantalla = self._app.primaryScreen()
        if not pantalla:
            return
        geo = pantalla.availableGeometry()
        w = self._widget.width()
        h = self._widget.height()
        x = geo.center().x() - w // 2
        y = geo.bottom() - h - 80
        self._widget.move(x, y)


class SubtitulosOverlay:
    """API pública para mostrar/ocultar subtítulos desde cualquier hilo.

    Usa el hilo de Qt compartido (``core.qt_hilo``), que también posee la
    bandeja. ``mostrar()`` y ``ocultar()`` son seguras desde el hilo del TTS.

    Raises:
        RuntimeError: si PyQt5 no está disponible.
    """

    def __init__(self) -> None:
        hilo = qt_hilo.obtener()
        if hilo is None:
            raise RuntimeError("PyQt5 es necesario para los subtítulos.")
        self._panel = _PanelSubtitulos(hilo)

    def mostrar(self, texto: str) -> None:
        """Crea (si hace falta) y muestra el overlay con `texto`."""
        self._panel.emitir(_VER, texto)

    def actualizar_texto(self, texto: str) -> None:
        """Reemplaza SOLO el contenido del label, sin ocultar/mostrar la ventana.

        Pensado para subtítulos progresivos (frase por frase): evita el
        parpadeo de hide/show y ajusta el tamaño al nuevo texto.
        """
        self._panel.emitir(_ACTUALIZAR, texto)

    def ocultar(self) -> None:
        """Oculta el overlay (queda listo para volver a mostrarse)."""
        self._panel.emitir(_OCULTAR)

    def cerrar(self) -> None:
        """Cierra la ventana del overlay."""
        self._panel.emitir(_CERRAR)
