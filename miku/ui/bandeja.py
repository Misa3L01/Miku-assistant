# -*- coding: utf-8 -*-
"""
bandeja.py - Icono en la bandeja del sistema (QSystemTrayIcon).

El icono de la bandeja es la "cara" de Miku mientras corre en segundo plano: muestra el estado en
el tooltip y su menú permite invocarla, cambiar de modo (voz / texto de depuración), activar el
inicio con Windows y el atajo de teclado, y salir. También hospeda la ventanita de selección de modo.

Diseño de hilos:
    Todo lo que toca Qt corre en el hilo compartido de ``core.qt_hilo`` (el
    mismo que usan los subtítulos: Qt solo admite un event loop). Esta fachada
    se puede llamar desde cualquier hilo.

Degradación elegante: si PyQt5 no está, se loguea y la bandeja NO se arranca
(el resto del asistente sigue igual).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from miku.ui import qt_hilo

logger = logging.getLogger("miku.bandeja")

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except Exception as e:  # noqa: BLE001
    QtCore = QtGui = QtWidgets = None
    logger.warning("PyQt5 no disponible, no habrá bandeja: %s", e)


class _PanelBandeja:
    """Icono, menú y ventanita. Sus métodos corren en el hilo de Qt."""

    def __init__(self, hilo: "qt_hilo.HiloQt",
                 on_salir: Optional[Callable[[], None]] = None,
                 acciones: Optional[Dict[str, Callable[..., Any]]] = None,
                 estado: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        self._hilo = hilo
        self._on_salir = on_salir
        self._acciones = acciones or {}
        self._estado = estado
        self._checks: Dict[str, Any] = {}
        self._tray: Any = None
        # Resultado de la ventanita (se devuelve de forma síncrona a quien la pidió).
        self.modo_elegido: Optional[str] = None
        self.evento_modo = threading.Event()

    @property
    def tray_creado(self) -> bool:
        """True si el icono de la bandeja se creó."""
        return self._tray is not None

    def crear_tray(self) -> bool:
        """Crea el icono y su menú (en el hilo de Qt). True si quedó creado."""
        app = self._hilo.app
        try:
            tray = QtWidgets.QSystemTrayIcon(self._icono(app), app)
            tray.setToolTip("Miku - Asistente")

            menu = QtWidgets.QMenu()
            acc_estado = menu.addAction("Miku está activa")
            acc_estado.setEnabled(False)
            self._armar_menu(menu)
            acc_salir = menu.addAction("Salir")
            acc_salir.triggered.connect(self._salir)
            menu.aboutToShow.connect(self._refrescar_checks)
            tray.setContextMenu(menu)

            tray.activated.connect(self._al_activar)
            tray.show()
            self._tray = tray
            self._menu = menu  # referencia fuerte: Qt no es dueño del QMenu
            logger.info("Bandeja lista.")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("No pude crear la bandeja.")
            return False

    def _armar_menu(self, menu: Any) -> None:
        """Agrega las acciones configurables (las que el asistente haya registrado)."""
        def _accion(texto: str, clave: str, marcable: bool = False) -> None:
            if clave not in self._acciones:
                return
            acc = menu.addAction(texto)
            if marcable:
                acc.setCheckable(True)
                self._checks[clave] = acc
            acc.triggered.connect(lambda marcado=False, c=clave, m=marcable: self._ejecutar(c, marcado, m))

        menu.addSeparator()
        _accion("Invocar ahora", "invocar")
        menu.addSeparator()
        _accion("Modo voz", "modo_voz", True)
        _accion("Modo texto (depuración)", "modo_texto", True)
        menu.addSeparator()
        _accion("Iniciar con Windows", "inicio_windows", True)
        _accion("Atajo de teclado para abrir Miku", "atajo_tecla", True)
        _accion("Elegir tecla de invocación…", "elegir_tecla")
        menu.addSeparator()

    def _ejecutar(self, clave: str, marcado: bool, marcable: bool) -> None:
        """Llama a la acción del asistente (sin bloquear el hilo de Qt)."""
        accion = self._acciones.get(clave)
        if accion is None:
            return
        threading.Thread(target=self._llamar, args=(accion, marcado, marcable),
                         name=f"miku_menu_{clave}", daemon=True).start()

    @staticmethod
    def _llamar(accion: Callable[..., Any], marcado: bool, marcable: bool) -> None:
        try:
            accion(marcado) if marcable else accion()
        except Exception:  # noqa: BLE001
            logger.exception("Falló una acción del menú de la bandeja.")

    def _refrescar_checks(self) -> None:
        """Al abrir el menú, marca cada opción según el estado real (registro, atajo, modo)."""
        if self._estado is None:
            return
        try:
            estado = self._estado()
        except Exception:  # noqa: BLE001
            logger.debug("No pude leer el estado para el menú.", exc_info=True)
            return
        marcas = {
            "modo_voz": estado.get("modo") == "voz",
            "modo_texto": estado.get("modo") == "texto",
            "inicio_windows": bool(estado.get("inicio_windows")),
            "atajo_tecla": bool(estado.get("atajo_tecla")),
        }
        for clave, accion in self._checks.items():
            accion.setChecked(marcas.get(clave, False))

    def _icono(self, app: Any) -> Any:
        """Icono de la bandeja (o uno estándar si no hay archivo propio)."""
        try:
            from miku.ajustes import carga as config_mod
            from pathlib import Path
            for nombre in ("miku.ico", "miku.png"):
                p = Path(config_mod.BASE_DIR) / "data" / nombre
                if p.exists():
                    return QtGui.QIcon(str(p))
        except Exception:  # noqa: BLE001
            logger.debug("No pude cargar el icono propio.", exc_info=True)
        return app.style().standardIcon(QtWidgets.QStyle.SP_MessageBoxInformation)

    def poner_tooltip(self, texto: str) -> None:
        """Cambia el tooltip del icono."""
        if self._tray is not None:
            self._tray.setToolTip(texto or "Miku - Asistente")

    def pedir_ventanita(self) -> None:
        """Programa la ventanita para el próximo ciclo del event loop.

        Se usa ``singleShot`` para que el ``exec_()`` del diálogo no quede
        anidado dentro del drenado de la cola (bloquearía a los subtítulos).
        """
        QtCore.QTimer.singleShot(0, self._mostrar_ventanita)

    def _mostrar_ventanita(self) -> None:
        """Construye y muestra la ventanita y guarda el modo elegido."""
        try:
            from miku.ui import selector_modo as ventanita
            dlg = ventanita.construir_dialogo()
            if dlg is None:
                self.modo_elegido = None
                return
            dlg.exec_()
            self.modo_elegido = getattr(dlg, "modo_elegido", None)
        except Exception:  # noqa: BLE001
            logger.exception("Error mostrando la ventanita.")
            self.modo_elegido = None
        finally:
            self.evento_modo.set()

    def _al_activar(self, motivo: int) -> None:
        """Clic sobre el icono (por ahora solo se registra)."""
        if motivo in (2, 3):  # DoubleClick / Trigger
            logger.info("Bandeja: clic recibido.")

    def _salir(self) -> None:
        """Dispara el cierre del asistente (callback) y oculta el icono."""
        logger.info("Bandeja: 'Salir' elegido.")
        try:
            if callable(self._on_salir):
                self._on_salir()
        except Exception:  # noqa: BLE001
            logger.exception("Error en el cierre disparado desde la bandeja.")
        self.ocultar()

    def ocultar(self) -> None:
        """Esconde el icono (en el hilo de Qt)."""
        if self._tray is not None:
            try:
                self._tray.hide()
            except Exception:  # noqa: BLE001
                logger.debug("No pude ocultar el icono.", exc_info=True)


class Bandeja:
    """Fachada pública (no bloqueante) para la bandeja del sistema."""

    def __init__(self) -> None:
        self._hilo: Optional["qt_hilo.HiloQt"] = None
        self._panel: Optional[_PanelBandeja] = None
        # Una sola ventanita a la vez (una segunda invocación con el diálogo abierto se ignora).
        self._lock_modo = threading.Lock()

    def iniciar(self, on_salir: Optional[Callable[[], None]] = None,
                acciones: Optional[Dict[str, Callable[..., Any]]] = None,
                estado: Optional[Callable[[], Dict[str, Any]]] = None) -> bool:
        """Arranca la bandeja.

        Args:
            on_salir: Se llama al elegir "Salir".
            acciones: Acciones del menú por clave: ``invocar()``, ``modo_voz(marcado)``,
                ``modo_texto(marcado)``, ``inicio_windows(marcado)``, ``atajo_tecla(marcado)``.
                Solo aparecen en el menú las que se registren.
            estado: Devuelve ``{"modo", "inicio_windows", "atajo_tecla"}`` para marcar las casillas.

        Returns:
            True si el icono quedó creado; False si no hay PyQt5 o falló.
        """
        if QtWidgets is None:
            logger.info("Bandeja no disponible (PyQt5 ausente).")
            return False
        hilo = qt_hilo.obtener()
        if hilo is None:
            logger.info("Bandeja no disponible (no arrancó el hilo de Qt).")
            return False
        panel = _PanelBandeja(hilo, on_salir=on_salir, acciones=acciones, estado=estado)
        if not hilo.ejecutar_y_esperar(panel.crear_tray, timeout=5.0):
            logger.warning("La bandeja no se creó; sigo sin ella.")
            return False
        self._hilo, self._panel = hilo, panel
        return True

    def actualizar(self, texto: str) -> None:
        """Actualiza el tooltip (best-effort)."""
        if self._hilo is not None and self._panel is not None:
            panel = self._panel
            self._hilo.ejecutar(lambda: panel.poner_tooltip(texto))

    def pedir_modo(self, timeout: float = 120.0) -> Optional[str]:
        """Pide el modo al usuario con la ventanita.

        Returns:
            ``'voz'`` / ``'texto'``, o None si no hay bandeja, el usuario cerró
            la ventanita sin elegir, venció ``timeout`` o ya hay una abierta.
        """
        hilo, panel = self._hilo, self._panel
        if hilo is None or panel is None or not hilo.disponible():
            return None
        if not self._lock_modo.acquire(blocking=False):
            logger.info("Ya hay una ventanita de modo abierta; ignoro el pedido.")
            return None
        try:
            panel.modo_elegido = None
            panel.evento_modo.clear()
            hilo.ejecutar(panel.pedir_ventanita)
            panel.evento_modo.wait(timeout)
            return panel.modo_elegido
        except Exception:  # noqa: BLE001
            logger.exception("Error pidiendo el modo por la ventanita.")
            return None
        finally:
            self._lock_modo.release()

    def detener(self) -> None:
        """Esconde el icono de la bandeja (best-effort)."""
        if self._hilo is not None and self._panel is not None:
            self._hilo.ejecutar(self._panel.ocultar)
