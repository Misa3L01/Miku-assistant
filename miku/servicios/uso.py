"""
uso.py - Cuánto tiempo pasás en cada programa y cuánto llevás jugando seguido.

Cada pocos segundos el vigía mira qué programa está en primer plano y suma esos segundos a su cuenta del
día. **Solo se guarda el nombre del programa y los minutos** (``brave``, ``cs2``...): nunca el título de la
ventana, la página ni lo que se ve en pantalla. El registro vive en ``data/uso.json`` (no se sube a GitHub).

Con eso Miku puede contestar "¿en qué gasté el tiempo hoy?" y avisarte cuando llevás varias horas de juego
seguido (ver ``HorasDeJuego`` en ``reglas_proactivas.py``).

Todo se puede probar sin esperar ni tocar Windows: el vigía recibe lo que lee del sistema y el reloj.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from miku.servicios.reglas_proactivas import duracion_en_texto, nombre_de_juego

logger = logging.getLogger("miku.servicios.uso")

#: Cada cuántos segundos se mira qué programa está en primer plano.
MUESTREO_SEG = 5.0
#: Si pasaron más de estos segundos entre dos muestras (la PC estuvo suspendida, el hilo trabado...) ese tiempo
#: no se cuenta: no se estuvo usando.
SALTO_MAX_SEG = 30.0
#: Sin tocar mouse ni teclado durante este tiempo no cuenta como uso (salvo jugando: un joystick no mueve el mouse).
INACTIVO_SEG = 300.0
#: Cuántos días de historia se guardan.
DIAS_A_GUARDAR = 90
#: Cada cuántos segundos se escribe el registro en disco (y al cerrar).
GUARDAR_CADA_SEG = 60.0

#: Pantallas del sistema que ocupan el primer plano sin ser "uso" de nada.
_IGNORADOS = frozenset({"lockapp", "logonui", "searchhost", "startmenuexperiencehost",
                        "shellexperiencehost", "textinputhost", "applicationframehost"})

#: Cómo se dicen en voz alta los programas más comunes.
_NOMBRES: Dict[str, str] = {
    "brave": "Brave", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "code": "Visual Studio Code", "discord": "Discord", "steam": "Steam", "spotify": "Spotify",
    "explorer": "el Explorador", "whatsapp": "WhatsApp", "telegram": "Telegram", "tidal": "TIDAL",
    "notepad": "el Bloc de notas", "wallpaper64": "Wallpaper Engine", "obs64": "OBS",
    "pythonw": "Miku", "python": "Python", "windowsterminal": "la terminal",
}


def nombre_amigable(proceso: str) -> str:
    """Nombre para decir en voz alta ("code" -> "Visual Studio Code", "cs2" -> "Counter-Strike 2")."""
    if proceso in _NOMBRES:
        return _NOMBRES[proceso]
    return nombre_de_juego(proceso)


# --------------------------------------------------------------------------- #
# Registro en disco
# --------------------------------------------------------------------------- #
class RegistroUso:
    """Segundos por programa y por día, guardados en un JSON chico.

    Args:
        ruta: Archivo donde se guarda (None = solo en memoria, para los tests).
        hoy: Devuelve el día de hoy (inyectable para probar el cambio de día).
    """

    def __init__(self, ruta: Optional[Path] = None, hoy: Callable[[], date] = date.today) -> None:
        self._ruta = Path(ruta) if ruta else None
        self._hoy = hoy
        self._dias: Dict[str, Dict[str, float]] = {}
        self._lock = threading.Lock()
        self._sucio = False
        self._cargar()

    def hoy(self) -> date:
        """El día de hoy según el reloj del registro."""
        return self._hoy()

    def sumar(self, programa: str, segundos: float) -> None:
        """Suma ``segundos`` de uso de ``programa`` al día de hoy."""
        if not programa or segundos <= 0:
            return
        with self._lock:
            dia = self._dias.setdefault(self._hoy().isoformat(), {})
            dia[programa] = dia.get(programa, 0.0) + segundos
            self._sucio = True

    def totales(self, dias: int = 1, hasta: Optional[date] = None) -> Dict[str, float]:
        """Segundos por programa en los últimos ``dias`` días hasta ``hasta`` (hoy si no se indica)."""
        fin = hasta or self._hoy()
        validos = {(fin - timedelta(days=i)).isoformat() for i in range(max(1, dias))}
        suma: Dict[str, float] = {}
        with self._lock:
            for fecha, programas in self._dias.items():
                if fecha in validos:
                    for programa, seg in programas.items():
                        suma[programa] = suma.get(programa, 0.0) + seg
        return suma

    def guardar(self) -> None:
        """Escribe el registro si cambió (de forma atómica: nunca queda un JSON a medias)."""
        if self._ruta is None:
            return
        with self._lock:
            if not self._sucio:
                return
            limite = (self._hoy() - timedelta(days=DIAS_A_GUARDAR)).isoformat()
            self._dias = {f: p for f, p in self._dias.items() if f >= limite}
            datos = {"dias": {f: {p: round(s, 1) for p, s in prog.items()} for f, prog in self._dias.items()}}
            self._sucio = False
        try:
            self._ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._ruta)
        except OSError as e:
            logger.warning("No pude guardar el registro de uso: %s", e)
            with self._lock:
                self._sucio = True

    def _cargar(self) -> None:
        if self._ruta is None:
            return
        try:
            crudo = json.loads(self._ruta.read_text(encoding="utf-8")).get("dias") or {}
            self._dias = {str(f): {str(p): float(s) for p, s in prog.items()}
                          for f, prog in crudo.items() if isinstance(prog, dict)}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError, TypeError):
            logger.warning("El registro de uso estaba dañado: empiezo de cero.")


# --------------------------------------------------------------------------- #
# Juego seguido
# --------------------------------------------------------------------------- #
class SesionJuego:
    """Cuánto tiempo llevás jugando "seguido".

    Cuenta solo el tiempo con el juego en primer plano. Una pausa de más de ``pausa_seg`` sin jugar (o el
    cambio de día lo decide el que la usa) corta la sesión y la cuenta vuelve a cero: un alt-tab corto no la corta.
    """

    def __init__(self, pausa_seg: float = 900.0) -> None:
        self.pausa_seg = pausa_seg
        self.juego: Optional[str] = None
        self.segundos = 0.0
        #: Número que identifica a la sesión (cambia en cada una nueva): sirve para no repetir avisos.
        self.numero = 0
        self._visto = 0.0

    def registrar(self, juego: Optional[str], mono: float, dt: float) -> None:
        """Anota una muestra: ``juego`` en primer plano (o None), a la hora ``mono`` y ``dt`` s desde la anterior."""
        if juego:
            if self.juego is None or mono - self._visto > self.pausa_seg:
                self.numero += 1
                self.segundos = 0.0
            self.juego, self._visto = juego, mono
            self.segundos += max(0.0, dt)
        elif self.juego is not None and mono - self._visto > self.pausa_seg:
            self.juego, self.segundos = None, 0.0

    def actual(self) -> Optional[Tuple[str, float, int]]:
        """``(juego, segundos jugados, número de sesión)`` o None si no hay una sesión en marcha."""
        if self.juego is None:
            return None
        return self.juego, self.segundos, self.numero


# --------------------------------------------------------------------------- #
# El vigía
# --------------------------------------------------------------------------- #
class Vigia:
    """Mira cada ``MUESTREO_SEG`` qué programa está en primer plano y lo anota.

    Args:
        registro: Dónde se suma el tiempo.
        sesion: La cuenta de juego seguido.
        primer_plano: ``f() -> programa | None`` (por defecto, el del sistema).
        inactivo: ``f() -> segundos sin tocar mouse/teclado``.
        juegos: ``f() -> {programas que son juegos}`` (se lee en cada muestra: puede cambiar en la config).
        reloj: Reloj monótono.
    """

    def __init__(self, registro: RegistroUso, sesion: SesionJuego,
                 primer_plano: Optional[Callable[[], Optional[str]]] = None,
                 inactivo: Optional[Callable[[], float]] = None,
                 juegos: Callable[[], Iterable[str]] = lambda: (),
                 reloj: Callable[[], float] = time.monotonic) -> None:
        if primer_plano is None or inactivo is None:
            from miku.plataforma import inactividad, procesos
            primer_plano = primer_plano or procesos.proceso_primer_plano
            inactivo = inactivo or inactividad.segundos_inactivo
        self.registro, self.sesion = registro, sesion
        self._primer_plano, self._inactivo, self._juegos, self._reloj = primer_plano, inactivo, juegos, reloj
        self._ultima: Optional[float] = None
        self._ultimo_guardado = reloj()
        self._detener = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    def muestrear(self) -> None:
        """Una vuelta: anota el programa actual con el tiempo transcurrido desde la vuelta anterior."""
        ahora = self._reloj()
        dt = 0.0 if self._ultima is None else ahora - self._ultima
        self._ultima = ahora
        if dt > SALTO_MAX_SEG:
            dt = 0.0                              # la PC estuvo suspendida o trabada: no cuenta
        try:
            programa = self._primer_plano()
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer el primer plano: %s", e)
            programa = None
        if programa in _IGNORADOS:
            programa = None
        es_juego = bool(programa) and programa in {str(j).lower() for j in self._juegos()}
        self.sesion.registrar(programa if es_juego else None, ahora, dt)
        if not programa:
            return
        if not es_juego:
            try:
                if self._inactivo() > INACTIVO_SEG:
                    return                        # PC sola: no es uso
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude leer la inactividad: %s", e)
        self.registro.sumar(programa, dt)

    # ------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        """Arranca el hilo (idempotente)."""
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="miku_uso")
        self._hilo.start()

    def detener(self) -> None:
        """Detiene el hilo y guarda lo pendiente."""
        self._detener.set()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=3.0)
        self._hilo = None
        self.registro.guardar()

    def _bucle(self) -> None:
        while not self._detener.is_set():
            try:
                self.muestrear()
                if self._reloj() - self._ultimo_guardado >= GUARDAR_CADA_SEG:
                    self._ultimo_guardado = self._reloj()
                    self.registro.guardar()
            except Exception:  # noqa: BLE001
                logger.exception("Error en el vigía de uso; sigo.")
            self._detener.wait(MUESTREO_SEG)


# --------------------------------------------------------------------------- #
# Resumen para decir en voz alta
# --------------------------------------------------------------------------- #
#: Programas que pasan menos de esto no se mencionan en el resumen.
_MIN_MENCION_SEG = 60.0


def resumen(registro: RegistroUso, periodo: str = "hoy", maximo: int = 4) -> str:
    """El texto de "¿en qué gasté el tiempo?": total y lo que más usaste."""
    periodo = (periodo or "hoy").strip().lower()
    if periodo == "ayer":
        totales, cuando = registro.totales(1, hasta=registro.hoy() - timedelta(days=1)), "Ayer"
    elif periodo in ("semana", "7 dias", "siete dias"):
        totales, cuando = registro.totales(7), "En los últimos 7 días"
    else:
        totales, cuando = registro.totales(1), "Hoy"
    total = sum(totales.values())
    if total < _MIN_MENCION_SEG:
        return f"{cuando} casi no tengo registro de uso." + (
            " Empecé a anotar hace poco." if periodo != "hoy" else "")
    top: List[Tuple[str, float]] = sorted(totales.items(), key=lambda kv: kv[1], reverse=True)
    partes = [f"{nombre_amigable(p)}, {duracion_en_texto(s)}" for p, s in top[:maximo] if s >= _MIN_MENCION_SEG]
    texto = f"{cuando} usaste la PC {duracion_en_texto(total)}. Lo que más usaste: " + "; ".join(partes) + "."
    if periodo in ("semana", "7 dias", "siete dias"):
        texto += f" Es un promedio de {duracion_en_texto(total / 7)} por día."
    return texto


def juegos_de_config(cfg: Any) -> List[str]:
    """Los programas que cuentan como juego: los de ``JUEGOS_BOOSTER``."""
    return [str(j).lower() for j in (getattr(cfg, "juegos_booster", None) or [])]
