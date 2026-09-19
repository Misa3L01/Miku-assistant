"""
proactivo.py - Motor de avisos proactivos: reglas + política para no molestar.

El motor corre en un hilo daemon y cada pocos segundos le pregunta a cada regla si tiene algo que
decir (``miku/servicios/reglas_proactivas.py``). Lo que la regla devuelve pasa por una política
antes de mostrarse:

    * **Horario de silencio** (``proactivo_silencio_desde/hasta``): no avisa nada.
    * **No molestar en juego**: mientras hay un juego de ``juegos_booster`` en primer plano solo
      pasan los avisos marcados ``ignora_juego`` (estado al empezar, GPU muy caliente).
    * **Cooldown** por clave y, opcionalmente, **una vez por día** (el clima), recordado en disco
      para que reiniciar Miku no repita el aviso.
    * **Presupuesto por hora** (``proactivo_max_por_hora``) salvo los avisos urgentes.

Cada intención tiene varias frases que rotan (``miku/voz/frases``), así no suena repetitivo.
Todo es inyectable (reloj, detector de juego, salida) para poder probarlo sin esperar ni hablar.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time as hora_del_dia
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

from miku.voz.frases import catalogo_proactivo
from miku.voz.frases.banco import Banco, frases

logger = logging.getLogger("miku.servicios.proactivo")

#: Cada cuántos segundos despierta el motor a revisar qué reglas tocan.
TICK_SEG = 10.0
#: Tras dejar de ver el juego en primer plano se lo considera "en juego" este tiempo (alt-tab).
GRACIA_JUEGO_SEG = 60.0
#: Ventana del presupuesto de avisos.
_HORA_SEG = 3600.0


@dataclass
class Aviso:
    """Algo que Miku quiere decir.

    Attributes:
        clave: Identifica el aviso para el cooldown ("clima.lluvia", "bateria_baja").
        intencion: Clave del banco de frases.
        datos: Valores para completar la frase.
        cooldown_min: Minutos mínimos entre dos avisos con la misma clave (None = el de config).
        una_vez_por_dia: Como mucho una vez por día calendario (recordado en disco).
        urgente: Ignora el presupuesto por hora.
        ignora_juego: Se dice aunque haya un juego en primer plano.
    """

    clave: str
    intencion: str
    datos: Dict[str, Any] = field(default_factory=dict)
    cooldown_min: Optional[float] = None
    una_vez_por_dia: bool = False
    urgente: bool = False
    ignora_juego: bool = False


@dataclass
class Contexto:
    """Lo que ven las reglas en cada evaluación."""

    cfg: Any
    ahora: datetime
    mono: float
    #: Proceso del juego en primer plano (o el último visto hace poco), o None.
    juego: Optional[str] = None


class Regla:
    """Una fuente de avisos. Las subclases fijan ``nombre``/``intervalo`` y implementan ``evaluar``."""

    nombre = "regla"
    #: Segundos entre evaluaciones.
    intervalo = 60.0

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:  # pragma: no cover - interfaz
        """Devuelve los avisos posibles, del más al menos importante (sale como mucho uno por vuelta)."""
        return ()


def parsear_hora(texto: Any) -> Optional[hora_del_dia]:
    """``"23:00"`` -> ``time(23, 0)``; None si está vacío o mal escrito."""
    try:
        h, m = str(texto or "").strip().split(":")
        return hora_del_dia(int(h), int(m))
    except (ValueError, TypeError):
        return None


def en_horario_de_silencio(ahora: datetime, desde: Any, hasta: Any) -> bool:
    """True si ``ahora`` cae en el tramo de silencio (que puede cruzar la medianoche)."""
    d, h = parsear_hora(desde), parsear_hora(hasta)
    if d is None or h is None or d == h:
        return False
    t = ahora.time()
    return (d <= t < h) if d < h else (t >= d or t < h)


def detectar_juego(cfg: Any) -> Optional[str]:
    """Proceso en primer plano si es uno de ``juegos_booster``."""
    from miku.plataforma import procesos
    juegos = {str(j).lower() for j in (cfg.juegos_booster or [])}
    if not juegos:
        return None
    proceso = procesos.proceso_primer_plano()
    return proceso if proceso in juegos else None


class MotorProactivo:
    """Evalúa las reglas y decide qué avisos llegan a mostrarse.

    Args:
        cfg: Configuración del asistente.
        reglas: Reglas a evaluar.
        emitir: ``emitir(titulo, mensaje)`` muestra y/o dice el aviso.
        detector_juego: ``f(cfg) -> proceso | None``; por defecto mira el primer plano.
        ahora: Reloj de pared (para el horario y el "una vez por día").
        reloj: Reloj monótono (para intervalos y cooldowns).
        banco: Banco de frases.
        ruta_estado: JSON donde se recuerda qué avisos diarios ya salieron (None = sin persistir).
    """

    def __init__(self, cfg: Any, reglas: List[Regla], emitir: Callable[[str, str], None],
                 detector_juego: Optional[Callable[[Any], Optional[str]]] = None,
                 ahora: Callable[[], datetime] = datetime.now,
                 reloj: Callable[[], float] = time.monotonic,
                 banco: Banco = frases, ruta_estado: Optional[Path] = None) -> None:
        catalogo_proactivo.registrar(banco)
        self.cfg = cfg
        self.reglas = reglas
        self._emitir = emitir
        self._detector = detector_juego or detectar_juego
        self._ahora = ahora
        self._reloj = reloj
        self._banco = banco
        self._ruta_estado = Path(ruta_estado) if ruta_estado else None
        self._debido: Dict[str, float] = {}
        self._ultimo: Dict[str, float] = {}
        self._dia: Dict[str, str] = self._cargar_estado()
        self._emitidos: Deque[float] = deque()
        self._juego: Optional[str] = None
        self._juego_visto: float = float("-inf")
        self._detener = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        """Arranca el hilo del motor (idempotente)."""
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="miku_proactivo")
        self._hilo.start()

    def detener(self) -> None:
        """Detiene el hilo."""
        self._detener.set()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=3.0)
        self._hilo = None

    def _bucle(self) -> None:
        while not self._detener.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("Error en el motor proactivo; sigo.")
            self._detener.wait(TICK_SEG)

    # ------------------------------------------------------------------ evaluación
    def juego_activo(self) -> Optional[str]:
        """Juego en primer plano, con un margen de gracia para el alt-tab."""
        mono = self._reloj()
        try:
            visto = self._detector(self.cfg)
        except Exception as e:  # noqa: BLE001
            logger.debug("Proactivo: no pude detectar el juego: %s", e)
            visto = None
        if visto:
            self._juego, self._juego_visto = visto, mono
        elif self._juego and mono - self._juego_visto > GRACIA_JUEGO_SEG:
            self._juego = None
        return self._juego

    def tick(self) -> int:
        """Evalúa las reglas que tocan. Devuelve cuántos avisos se emitieron."""
        mono = self._reloj()
        ctx = Contexto(self.cfg, self._ahora(), mono, self.juego_activo())
        emitidos = 0
        for regla in self.reglas:
            if mono < self._debido.get(regla.nombre, float("-inf")):
                continue
            self._debido[regla.nombre] = mono + max(1.0, float(regla.intervalo))
            try:
                avisos = list(regla.evaluar(ctx))
            except Exception as e:  # noqa: BLE001
                logger.debug("Proactivo: la regla '%s' falló: %s", regla.nombre, e)
                continue
            for aviso in avisos:
                if self._intentar(aviso, ctx):
                    emitidos += 1
                    break  # como mucho un aviso por regla y vuelta
        return emitidos

    # ------------------------------------------------------------------- política
    def _cooldown_seg(self, aviso: Aviso) -> float:
        minutos = aviso.cooldown_min
        if minutos is None:
            try:
                minutos = float(self.cfg.proactivo_cooldown_min)
            except (TypeError, ValueError, AttributeError):
                minutos = 30.0
        return max(0.0, minutos) * 60.0

    def _max_por_hora(self) -> int:
        try:
            return int(self.cfg.get("proactivo_max_por_hora", 4) or 0)
        except (TypeError, ValueError):
            return 4

    def permitido(self, aviso: Aviso, ctx: Contexto) -> bool:
        """Aplica la política (silencio, juego, cooldown, día, presupuesto)."""
        cfg = self.cfg
        if en_horario_de_silencio(ctx.ahora, cfg.get("proactivo_silencio_desde"),
                                  cfg.get("proactivo_silencio_hasta")):
            return False
        if (ctx.juego and not aviso.ignora_juego
                and cfg.get("proactivo_no_molestar_en_juego", True)):
            return False
        if aviso.una_vez_por_dia and self._dia.get(aviso.clave) == ctx.ahora.date().isoformat():
            return False
        ultimo = self._ultimo.get(aviso.clave)
        if ultimo is not None and ctx.mono - ultimo < self._cooldown_seg(aviso):
            return False
        limite = self._max_por_hora()
        if limite > 0 and not aviso.urgente:
            while self._emitidos and ctx.mono - self._emitidos[0] > _HORA_SEG:
                self._emitidos.popleft()
            if len(self._emitidos) >= limite:
                return False
        return True

    def _intentar(self, aviso: Aviso, ctx: Contexto) -> bool:
        if not self.permitido(aviso, ctx):
            return False
        try:
            mensaje = self._banco.elegir(aviso.intencion, **aviso.datos)
        except KeyError:
            logger.error("El aviso '%s' usa una intención sin frases: %s",
                         aviso.clave, aviso.intencion)
            return False
        titulo = catalogo_proactivo.TITULOS.get(aviso.intencion, "Miku")
        self._ultimo[aviso.clave] = ctx.mono
        self._emitidos.append(ctx.mono)
        if aviso.una_vez_por_dia:
            self._dia[aviso.clave] = ctx.ahora.date().isoformat()
            self._guardar_estado()
        logger.info("Proactivo [%s]: %s", aviso.clave, mensaje)
        try:
            self._emitir(titulo, mensaje)
        except Exception as e:  # noqa: BLE001
            logger.debug("Proactivo: no pude emitir el aviso: %s", e)
        return True

    # ------------------------------------------------------------------- estado
    def _cargar_estado(self) -> Dict[str, str]:
        if self._ruta_estado is None:
            return {}
        try:
            datos = json.loads(self._ruta_estado.read_text(encoding="utf-8"))
            hoy = date.today().isoformat()
            return {k: v for k, v in (datos.get("dia") or {}).items() if v == hoy}
        except (OSError, ValueError, AttributeError):
            return {}

    def _guardar_estado(self) -> None:
        if self._ruta_estado is None:
            return
        try:
            self._ruta_estado.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ruta_estado.with_suffix(".tmp")
            tmp.write_text(json.dumps({"dia": self._dia}), encoding="utf-8")
            os.replace(tmp, self._ruta_estado)
        except OSError as e:
            logger.debug("Proactivo: no pude guardar el estado: %s", e)
