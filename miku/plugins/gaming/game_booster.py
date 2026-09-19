# -*- coding: utf-8 -*-
"""
game_booster.py - Auto-Game Booster: detecta el juego en primer plano y activa
el "modo gaming" solo (bajar volumen del navegador, etc.), sin que lo pidan.

Cómo funciona:
    - Un HILO daemon liviano hace polling (cada ~3s) de la ventana en PRIMER
      PLANO (win32gui.GetForegroundWindow + psutil para el nombre del proceso).
    - Si el proceso está en la lista configurable ``JUEGOS_BOOSTER``:
        * Captura un SNAPSHOT (volumen/resolución/brillo) vía ``miku/servicios/modos``.
        * Baja el volumen de las apps de ``BOOSTER_APPS_VOLUMEN`` al nivel
          ``BOOSTER_VOLUMEN_OBJETIVO`` (usa el plugin ``audio``/pycaw).
        * Avisa (toast + voz corta) si ``BOOSTER_AVISO``.
    - Al SALIR del juego (el primer plano deja de ser ese proceso durante un
      par de chequeos), revierte con ``core.modos.salir_modo`` si hay snapshot.
    - SIN SPAM: solo actúa en las TRANSICIONES (entrar/salir), no en cada tick.

Es tolerante: si falta win32/psutil, el hilo no arranca y el plugin queda
inactivo (no rompe el arranque). Imports pesados SIEMPRE lazy.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.game_booster")

# Cada cuántos segundos revisamos el primer plano.
_INTERVALO = 3.0
# Cuántos chequeos seguidos "sin juego" para considerar que salió (evita
# parpadeos por alt-tab).
_CHEQUEOS_SALIDA = 2


class GameBooster(Plugin):
    """Activa/desactiva el modo gaming al detectar un juego en primer plano."""

    nombre = "game_booster"
    descripcion = "Modo gaming automático al detectar un juego en primer plano."

    # Sin tools propias: se activa solo. (El diseño pedido es automático y sin
    # fricción; basta con que corra en segundo plano.)
    tools: List[dict] = []

    def __init__(self) -> None:
        super().__init__()
        self._hilo: Optional[threading.Thread] = None
        self._detener = threading.Event()
        # Estado del booster: proceso activo y contador de "no juego".
        self._juego_activo: Optional[str] = None
        self._contador_salida = 0
        # True SOLO si el snapshot lo creamos NOSOTROS (si ya había uno de una
        # macro, es de ella y no lo revertimos).
        self._snapshot_activo = False
        # Volúmenes por app guardados antes de bajarlos: {app: {pid: nivel}}.
        self._volumenes_previos: dict = {}
        # ``_activar``/``_desactivar`` las llaman el hilo de vigilancia y
        # ``cerrar()``: el lock evita que corran a la vez.
        self._lock = threading.RLock()
        # Referencia al plugin de audio YA registrado en el bus (se
        # resuelve en runtime; evita instanciar pycaw/win32 de nuevo).
        self._audio: Optional[Any] = None
        # Para avisar UNA sola vez si no encontramos el plugin de audio.
        self._avisado_sin_sc = False

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._event_bus = event_bus
        juegos = config_mod.config.juegos_booster
        if not juegos:
            logger.info("GameBooster inactivo: sin JUEGOS_BOOSTER configurados.")
            return
        # Arrancamos el hilo de vigilancia (daemon, no bloquea el cierre).
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True,
                                      name="miku_game_booster")
        self._hilo.start()
        logger.info("GameBooster activo. Vigilando: %s", ", ".join(juegos))

    def cerrar(self) -> None:
        """Detiene el hilo de vigilancia (lo llama main al cerrar)."""
        self._detener.set()
        if self._hilo is not None:
            try:
                self._hilo.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            self._hilo = None
        # Si el asistente se cierra mientras había un juego activo, hay que
        # REVERTIR lo que bajamos nosotros además de parar el hilo. Si no, el
        # estado del sistema queda cambiado para siempre. El lock espera a que
        # el hilo termine una activación en curso. Best-effort.
        with self._lock:
            if self._juego_activo is not None:
                try:
                    self._desactivar()
                except Exception:  # noqa: BLE001
                    logger.exception("GameBooster: no pude revertir al cerrar.")
        logger.debug("GameBooster detenido.")

    # ---------------- Bucle de vigilancia ---------------- #
    def _bucle(self) -> None:
        """Polling del primer plano; actúa solo en transiciones."""
        while not self._detener.is_set():
            try:
                proceso = self._proceso_primer_plano()
            except Exception as e:  # noqa: BLE001
                logger.debug("GameBooster: error leyendo primer plano: %s", e)
                proceso = None

            juegos = set(config_mod.config.juegos_booster)
            if proceso and proceso in juegos:
                # Hay juego en primer plano.
                self._contador_salida = 0
                if self._juego_activo != proceso:
                    self._activar(proceso)
            else:
                # No hay juego: si había uno, contamos para salir.
                if self._juego_activo is not None:
                    self._contador_salida += 1
                    if self._contador_salida >= _CHEQUEOS_SALIDA:
                        self._desactivar()

            self._detener.wait(_INTERVALO)

    # ---------------- Activación / desactivación ---------------- #
    def _activar(self, proceso: str) -> None:
        """Activa el modo gaming para `proceso` (una sola vez por transición)."""
        with self._lock:
            # Si el asistente está cerrando, NO arrancamos un modo gaming nuevo:
            # lo que bajáramos ahora quedaría sin revertir.
            if self._detener.is_set():
                logger.debug("GameBooster: cierre en curso, no activo '%s'.",
                             proceso)
                return
            # Marcamos el juego ANTES de tocar nada: si algo falla a mitad de
            # camino, ``_desactivar`` igual revierte lo que se haya cambiado.
            self._juego_activo = proceso
            logger.info("GameBooster: juego detectado '%s'.", proceso)

            # 1) Snapshot (solo si no había uno ajeno) para poder revertir.
            try:
                from miku.servicios import modos as modos_core
                self._snapshot_activo = modos_core.capturar_si_libre()
            except Exception as e:  # noqa: BLE001
                logger.debug("GameBooster: sin snapshot: %s", e)
                self._snapshot_activo = False

            # 2) Bajar volumen de las apps configuradas (guardando el previo).
            self._bajar_volumen_apps()

        # 3) Aviso (toast + voz) si está habilitado.
        if config_mod.config.booster_aviso:
            self._avisar("Modo gaming activado",
                         f"Detecté {proceso}: bajé el volumen de las apps.")

    def _desactivar(self) -> None:
        """Sale del modo gaming y revierte lo que hizo el booster."""
        with self._lock:
            proceso = self._juego_activo
            self._juego_activo = None
            self._contador_salida = 0
            logger.info("GameBooster: fin de juego '%s'.", proceso)

            # Volumen por app: el snapshot solo guarda el master.
            self._restaurar_volumen_apps()

            # Revertimos el snapshot solo si lo creamos NOSOTROS.
            if self._snapshot_activo:
                try:
                    from miku.servicios import modos as modos_core
                    modos_core.salir_modo()
                except Exception as e:  # noqa: BLE001
                    logger.debug("GameBooster: no pude revertir: %s", e)
                self._snapshot_activo = False

        if config_mod.config.booster_aviso:
            self._avisar("Modo gaming desactivado",
                         "Saliste del juego: restauré el estado.")

    # ---------------- Volumen por app ---------------- #
    def _bajar_volumen_apps(self) -> None:
        """Baja el volumen de las apps configuradas al nivel objetivo.

        Reusa la instancia del plugin ``audio`` YA registrada en el bus: crear
        una nueva en cada ciclo reinicializa pycaw/win32 sin necesidad. Si no
        la encuentra, avisa por log y sigue (el hilo de vigilancia nunca debe
        morir por esto).
        """
        apps = config_mod.config.booster_apps_volumen
        objetivo = config_mod.config.booster_volumen_objetivo
        if not apps:
            return
        sc = self._obtener_audio()
        if sc is None:
            return
        for app in apps:
            try:
                # Guardamos el nivel actual ANTES de bajarlo, para devolverlo.
                previos = sc.volumenes_de_app(app)
                if previos:
                    self._volumenes_previos[app] = previos
                sc.ajustar_volumen("fijar", valor=objetivo, app=app)
            except Exception as e:  # noqa: BLE001
                logger.debug("GameBooster: no pude fijar el volumen de '%s': %s",
                             app, e)
                continue

    def _restaurar_volumen_apps(self) -> None:
        """Devuelve cada app al volumen que tenía antes del modo gaming."""
        previos, self._volumenes_previos = self._volumenes_previos, {}
        if not previos:
            return
        sc = self._obtener_audio()
        if sc is None:
            return
        for app, niveles in previos.items():
            try:
                sc.restaurar_volumenes_app(app, niveles)
            except Exception as e:  # noqa: BLE001
                logger.debug("GameBooster: no pude restaurar '%s': %s", app, e)

    def _obtener_audio(self) -> Optional[Any]:
        """Devuelve el plugin ``audio`` ya registrado en el bus.

        Se busca por ``nombre == "audio"`` en ``event_bus.plugins`` y
        se cachea la referencia (no se recorre el bus en cada ciclo).

        Returns:
            La instancia registrada, o None si no está (se avisa una sola vez
            por log y el llamador sigue sin romper nada).
        """
        if self._audio is not None:
            return self._audio
        plugins = getattr(self._event_bus, "plugins", None) \
            if self._event_bus is not None else None
        for plugin in plugins or []:
            if getattr(plugin, "nombre", "") == "audio":
                self._audio = plugin
                logger.debug("GameBooster: reusando el plugin de audio del bus.")
                return plugin
        if not self._avisado_sin_sc:
            self._avisado_sin_sc = True
            logger.warning(
                "GameBooster: no encontré el plugin 'audio' en el "
                "bus; no voy a bajar el volumen por app (sigo sin eso).")
        return None

    # ---------------- Utilidades ---------------- #
    def _proceso_primer_plano(self) -> Optional[str]:
        """Nombre del proceso (sin .exe) de la ventana en primer plano."""
        try:
            import win32gui  # type: ignore
            import win32process  # type: ignore
            import psutil  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.debug("GameBooster: falta win32/psutil: %s", e)
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return None
            proc = psutil.Process(pid)
            nombre = (proc.name() or "").lower()
            return nombre[:-4] if nombre.endswith(".exe") else nombre
        except Exception as e:  # noqa: BLE001
            logger.debug("GameBooster: no pude obtener el proceso: %s", e)
            return None

    def _avisar(self, titulo: str, mensaje: str) -> None:
        """Muestra un toast y, si hay voz, dice una frase corta."""
        # Toast (best-effort).
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar(titulo, mensaje)
        except Exception as e:  # noqa: BLE001
            logger.debug("GameBooster: sin toast: %s", e)
        # Voz (si el bus/contexto tiene voice), best-effort.
        try:
            voz = getattr(self._event_bus, "voice", None) if self._event_bus else None
            if voz is not None:
                voz.decir(mensaje)
        except Exception:  # noqa: BLE001
            pass