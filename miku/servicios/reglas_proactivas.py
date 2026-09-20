"""
reglas_proactivas.py - Las reglas concretas del motor proactivo (clima y estado de la PC).

Cada regla decide *si hay algo para decir*; el motor (``proactivo.py``) decide *si se dice ahora*.
Los umbrales se leen de la config en cada evaluación, y las lecturas externas (pronóstico, hardware)
son inyectables para poder probar sin red ni hardware.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from miku.plataforma import hardware, inactividad, openmeteo
from miku.servicios.proactivo import Aviso, Contexto, Regla

logger = logging.getLogger("miku.servicios.reglas_proactivas")

# Nombres de proceso de juegos conocidos -> cómo se dicen en voz alta.
_NOMBRES_JUEGO: Dict[str, str] = {
    "cs2": "Counter-Strike 2",
    "fortniteclient-win64-shipping": "Fortnite",
    "genshinimpact": "Genshin Impact",
    "valorant-win64-shipping": "Valorant",
}


def _num(cfg: Any, clave: str, defecto: float) -> float:
    """Lee un número de la config; si está mal escrito usa ``defecto``."""
    try:
        return float(cfg.get(clave, defecto))
    except (TypeError, ValueError):
        return defecto


def nombre_de_juego(proceso: str) -> str:
    """Nombre legible de un proceso de juego ("genshinimpact" -> "Genshin Impact")."""
    if proceso in _NOMBRES_JUEGO:
        return _NOMBRES_JUEGO[proceso]
    base = re.split(r"[-_.]", proceso)[0]
    return base.capitalize() or proceso


# --------------------------------------------------------------------------- #
# Batería y disco (las reglas de siempre, ahora con frases que rotan)
# --------------------------------------------------------------------------- #
class BateriaBaja(Regla):
    """Avisa cuando la notebook baja del umbral y no está enchufada."""

    nombre = "bateria"

    def __init__(self, cfg: Any, leer: Callable[[], Any] = hardware.bateria) -> None:
        self.intervalo = max(1.0, _num(cfg, "proactivo_intervalo_min", 5)) * 60
        self._leer = leer

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        bat = self._leer()
        if bat is None or bat.power_plugged:
            return ()
        if bat.percent <= _num(ctx.cfg, "proactivo_bateria_min", 20):
            return [Aviso("bateria_baja", "pc.bateria", {"porcentaje": round(bat.percent)})]
        return ()


class DiscoLleno(Regla):
    """Avisa cuando el disco del sistema tiene poco espacio libre."""

    nombre = "disco"

    def __init__(self, cfg: Any, leer: Callable[[], Optional[float]] = hardware.disco_libre_gb) -> None:
        self.intervalo = max(1.0, _num(cfg, "proactivo_intervalo_min", 5)) * 60
        self._leer = leer

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        libre = self._leer()
        if libre is not None and libre <= _num(ctx.cfg, "proactivo_disco_gb", 5.0):
            return [Aviso("disco_lleno", "pc.disco", {"libre": round(libre)})]
        return ()


# --------------------------------------------------------------------------- #
# Clima
# --------------------------------------------------------------------------- #
class ClimaAvisos(Regla):
    """Avisa de lluvia, frío o calor. Como mucho una vez por día por condición.

    Consulta el pronóstico cada ``proactivo_clima_intervalo_min`` y devuelve las condiciones
    ordenadas por importancia (lluvia > calor > frío); el motor dice solo una por vuelta y las
    otras quedan para la siguiente consulta.
    """

    nombre = "clima"

    def __init__(self, cfg: Any,
                 obtener: Optional[Callable[[], Optional[openmeteo.Pronostico]]] = None) -> None:
        self.intervalo = max(1.0, _num(cfg, "proactivo_clima_intervalo_min", 120)) * 60
        self._obtener = obtener or self._consultar
        self._ubicacion: Optional[Tuple[float, float, str]] = None
        self._cfg = cfg

    def _consultar(self) -> Optional[openmeteo.Pronostico]:
        if self._ubicacion is None:
            lat, lon, nombre = openmeteo.resolver_ubicacion(self._cfg)
            if lat is None or lon is None:
                return None
            self._ubicacion = (lat, lon, nombre)
        return openmeteo.obtener_pronostico(*self._ubicacion)

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        cfg = ctx.cfg
        if not cfg.get("proactivo_clima", True):
            return ()
        p = self._obtener()
        if p is None:
            return ()
        avisos: List[Aviso] = []

        if p.esta_lloviendo:
            avisos.append(Aviso("clima.lluvia", "clima.lloviendo", una_vez_por_dia=True,
                                cooldown_min=0))
        elif p.proxima_lluvia and p.proxima_lluvia[0] >= _num(cfg, "proactivo_lluvia_prob", 60):
            prob, horas = p.proxima_lluvia
            cuando = ("en la próxima hora" if horas <= 1
                      else f"en unas {horas} horas")
            avisos.append(Aviso("clima.lluvia", "clima.lluvia",
                                {"cuando": cuando, "prob": round(prob)},
                                una_vez_por_dia=True, cooldown_min=0))

        sensacion = p.sensacion if p.sensacion is not None else p.temperatura
        if sensacion is not None:
            if sensacion >= _num(cfg, "proactivo_calor_c", 32):
                avisos.append(Aviso("clima.calor", "clima.calor", {"sensacion": round(sensacion)},
                                    una_vez_por_dia=True, cooldown_min=0))
            elif sensacion <= _num(cfg, "proactivo_frio_c", 12):
                avisos.append(Aviso("clima.frio", "clima.frio", {"sensacion": round(sensacion)},
                                    una_vez_por_dia=True, cooldown_min=0))
        return avisos


# --------------------------------------------------------------------------- #
# Estado de la PC
# --------------------------------------------------------------------------- #
def _detalle_pc(cpu: Optional[float], ram: Optional[float], gpu: Optional[hardware.UsoGpu]) -> str:
    """"CPU al 35%, RAM al 60% y GPU al 70% a 65 grados" con lo que se haya podido leer."""
    partes: List[str] = []
    if cpu is not None:
        partes.append(f"CPU al {cpu:.0f}%")
    if ram is not None:
        partes.append(f"RAM al {ram:.0f}%")
    if gpu is not None:
        gpu_txt = f"GPU al {gpu.porcentaje:.0f}%"
        if gpu.temperatura is not None:
            gpu_txt += f" a {gpu.temperatura:.0f} grados"
        partes.append(gpu_txt)
    if not partes:
        return "no pude leer los números"
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


class EstadoAlJugar(Regla):
    """Cuenta cómo está la PC un rato después de que arranca un juego (una vez por sesión de juego)."""

    nombre = "estado_juego"
    intervalo = 10.0
    #: Segundos que espera tras detectar el juego para que las cargas se estabilicen.
    ESPERA_SEG = 45.0

    def __init__(self, leer_cpu: Callable[[], Optional[float]] = lambda: hardware.cpu_porcentaje(0.5),
                 leer_ram: Callable[[], Optional[float]] = hardware.ram_porcentaje,
                 leer_gpu: Callable[[], Optional[hardware.UsoGpu]] = hardware.gpu_nvidia) -> None:
        self._cpu, self._ram, self._gpu = leer_cpu, leer_ram, leer_gpu
        self._juego: Optional[str] = None
        self._desde = 0.0
        self._contado = False

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        if not ctx.cfg.get("proactivo_estado_juego", True):
            return ()
        if ctx.juego != self._juego:
            self._juego, self._desde, self._contado = ctx.juego, ctx.mono, False
        if not self._juego or self._contado or ctx.mono - self._desde < self.ESPERA_SEG:
            return ()
        self._contado = True
        cpu, ram, gpu = self._cpu(), self._ram(), self._gpu()
        limite = _num(ctx.cfg, "proactivo_carga_pct", 92)
        limite_gpu = _num(ctx.cfg, "proactivo_gpu_temp_c", 85)
        exigida = ((cpu or 0) >= limite or (ram or 0) >= limite
                   or (gpu is not None and (gpu.temperatura or 0) >= limite_gpu))
        intencion = "pc.juego_exigente" if exigida else "pc.juego_tranquilo"
        return [Aviso(f"juego.{self._juego}", intencion,
                      {"juego": nombre_de_juego(self._juego), "detalle": _detalle_pc(cpu, ram, gpu)},
                      cooldown_min=10, ignora_juego=True)]


class CargaSostenida(Regla):
    """Avisa si la CPU o la RAM pasan mucho tiempo por encima del umbral (no picos)."""

    nombre = "carga"
    intervalo = 30.0
    #: Lecturas seguidas por encima del umbral (con intervalo de 30 s: 5 minutos).
    LECTURAS = 10

    def __init__(self, leer_cpu: Callable[[], Optional[float]] = hardware.cpu_porcentaje,
                 leer_ram: Callable[[], Optional[float]] = hardware.ram_porcentaje) -> None:
        self._lecturas = {"CPU": leer_cpu, "RAM": leer_ram}
        self._seguidas: Dict[str, int] = {"CPU": 0, "RAM": 0}
        hardware.cpu_porcentaje(0)  # la primera medición de psutil no sirve; la descartamos

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        umbral = _num(ctx.cfg, "proactivo_carga_pct", 92)
        avisos: List[Aviso] = []
        for recurso, leer in self._lecturas.items():
            valor = leer()
            if valor is None or valor < umbral:
                self._seguidas[recurso] = 0
                continue
            self._seguidas[recurso] += 1
            if self._seguidas[recurso] == self.LECTURAS:
                avisos.append(Aviso(f"carga.{recurso.lower()}", "pc.carga",
                                    {"recurso": recurso, "valor": round(valor)}))
        return avisos


class GpuCaliente(Regla):
    """Avisa (incluso jugando) si la GPU NVIDIA pasa el umbral de temperatura."""

    nombre = "gpu"
    intervalo = 60.0

    def __init__(self, leer: Callable[[], Optional[hardware.UsoGpu]] = hardware.gpu_nvidia) -> None:
        self._leer = leer

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        gpu = self._leer()
        if gpu is None or gpu.temperatura is None:
            return ()
        if gpu.temperatura >= _num(ctx.cfg, "proactivo_gpu_temp_c", 85):
            return [Aviso("gpu.temp", "pc.gpu_caliente", {"temp": round(gpu.temperatura)},
                          cooldown_min=15, urgente=True, ignora_juego=True)]
        return ()


class BriefingAlVolver(Regla):
    """Cuando volvés tras un rato ausente (AFK), te da la hora, el clima y tus pendientes.

    Se considera "ausente" tras ``briefing_afk_min`` minutos sin tocar teclado ni mouse; al volver
    a usar la PC se arma el resumen. El cooldown (``briefing_cooldown_h``) evita que lo repita cada
    vez que te levantás un rato: es "de vez en cuando", no en cada regreso.
    """

    nombre = "briefing_afk"
    intervalo = 15.0

    def __init__(self, contexto: Callable[[], Optional[dict]] = lambda: None,
                 inactivo: Callable[[], float] = inactividad.segundos_inactivo) -> None:
        self._contexto = contexto
        self._inactivo = inactivo
        self._estuvo_ausente = False

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        if not ctx.cfg.get("briefing_al_volver", True):
            return ()
        idle = self._inactivo()
        if idle >= _num(ctx.cfg, "briefing_afk_min", 30) * 60:
            self._estuvo_ausente = True
            return ()
        if not (self._estuvo_ausente and idle < 30):
            return ()
        self._estuvo_ausente = False
        from miku.servicios import briefing
        from miku.voz.frases.banco import frases
        try:
            texto = briefing.generar(self._contexto(), encabezado=frases.elegir("briefing.vuelta"))
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude armar el resumen al volver: %s", e)
            return ()
        return [Aviso("briefing.afk", "briefing.texto", {"texto": texto},
                      cooldown_min=_num(ctx.cfg, "briefing_cooldown_h", 4) * 60)]


class ComedorDiario(Regla):
    """Cada día, a partir de ``comedor_hora``, avisa (o con ``comedor_auto`` hace) la inscripción al
    comedor de mañana. Solo si mañana es un día de semana (de domingo a jueves) y una sola vez por día."""

    nombre = "comedor"
    intervalo = 60.0
    #: Intentos automáticos por día y minutos mínimos entre uno y otro (la comida puede cargarse tarde).
    MAX_INTENTOS = 3
    ESPERA_ENTRE_INTENTOS_MIN = 30

    def __init__(self, obtener_plugin: Callable[[], Any] = lambda: None,
                 inactivo: Callable[[], float] = inactividad.segundos_inactivo) -> None:
        self._plugin = obtener_plugin
        self._inactivo = inactivo
        self._intentos: Dict[str, List[datetime]] = {}

    def evaluar(self, ctx: Contexto) -> Iterable[Aviso]:
        cfg = ctx.cfg
        hora = str(cfg.get("comedor_hora", "") or "").strip()
        if not hora or not str(cfg.get("comedor_usuario", "") or "").strip():
            return ()
        manana = ctx.ahora.date() + timedelta(days=1)
        if manana.weekday() > 4:                       # sábado/domingo: el comedor no abre
            return ()
        try:
            hh, mm = hora.split(":")
            desde = ctx.ahora.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except ValueError:
            return ()
        if ctx.ahora < desde:
            return ()
        plugin = self._plugin()
        if plugin is None or plugin.ya_resuelto(manana):
            return ()
        if cfg.get("comedor_auto", False):
            # Solo si estás usando la PC (no ausente) y no jugando. Hasta MAX_INTENTOS por día, separados,
            # y ninguno si el último falló por algo que reintentar no arregla (usuario o contraseña).
            hechos = self._intentos.setdefault(manana.isoformat(), [])
            if ctx.juego or self._inactivo() > 300 or not plugin.puede_reintentar(manana):
                return ()
            if len(hechos) >= self.MAX_INTENTOS or (
                    hechos and ctx.ahora - hechos[-1] < timedelta(minutes=self.ESPERA_ENTRE_INTENTOS_MIN)):
                return ()
            hechos.append(ctx.ahora)
            plugin.inscribir_en_segundo_plano(automatico=True)
            return [Aviso("comedor.auto", "comedor.auto", {"hora": hora}, una_vez_por_dia=True, cooldown_min=0)]
        return [Aviso("comedor.aviso", "comedor.es_hora", {"hora": hora}, una_vez_por_dia=True, cooldown_min=0)]


def reglas_por_defecto(cfg: Any, contexto: Callable[[], Optional[dict]] = lambda: None,
                       plugin: Callable[[str], Any] = lambda nombre: None) -> List[Regla]:
    """Conjunto de reglas del asistente según la configuración.

    Args:
        contexto: ``f() -> dict`` con el contexto de runtime (scheduler...) para el resumen al volver.
        plugin: ``f(nombre) -> plugin`` para las reglas que dependen de otro plugin (comedor).
    """
    reglas: List[Regla] = [BateriaBaja(cfg), DiscoLleno(cfg), EstadoAlJugar(), CargaSostenida(),
                           GpuCaliente(), BriefingAlVolver(contexto),
                           ComedorDiario(lambda: plugin("comedor"))]
    if cfg.get("proactivo_clima", True):
        reglas.append(ClimaAvisos(cfg))
    return reglas
