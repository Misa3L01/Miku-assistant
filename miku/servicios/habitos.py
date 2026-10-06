"""
habitos.py - Control de hábitos: Miku te rezonga si te ve en una página de tu lista y pregunta si la cierra.

Cada pocos segundos se mira la **ventana en primer plano**: si es un navegador y el título de la pestaña que
tenés a la vista contiene alguna de tus palabras (``HABITOS_VIGILAR``), y seguís ahí ``HABITOS_ESPERA_SEG``
segundos, Miku te lo dice y te pregunta "¿la cierro?". Con tu "sí" se cierra esa pestaña.

Qué mira y qué guarda:
    * Mira solo el título de la ventana en primer plano, en el momento. **No guarda qué mirabas**: lo único que
      queda en disco es cuántas veces saltó cada regla por día (``data/habitos.json``).
    * No avisa si la PC está sola (varios minutos sin tocar nada): una página abierta mientras no estás no es
      un hábito.

Esta parte es la lógica, sin Windows ni hilos (``Vigilante``): se prueba con relojes falsos. El hilo y el cierre de
la pestaña están en ``miku/plugins/asistente/habitos.py``.

Para ver cómo se llama la ventana de tu navegador (y ajustar las palabras o las marcas de incógnito):

    venv\\Scripts\\python.exe -m miku.servicios.habitos
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from miku.plataforma.texto import normalizar
from miku.servicios.uso import RegistroUso

logger = logging.getLogger("miku.servicios.habitos")

#: Programas que cuentan como navegador (nombre del proceso, sin ``.exe``).
NAVEGADORES = ("brave", "chrome", "msedge", "firefox", "opera", "vivaldi")
#: Textos que aparecen en el título de una ventana privada. Cada navegador (y cada idioma) usa los suyos: si el
#: tuyo no está, se agrega en ``HABITOS_MARCAS_INCOGNITO`` (el diagnóstico muestra cómo se llama).
MARCAS_INCOGNITO = ("incognito", "inprivate", "(private)", "private browsing", "navegacion privada",
                    "(privado)", "(privada)")
#: Segundos seguidos en la página antes de rezongar (un vistazo o un alt-tab no cuentan).
ESPERA_SEG = 10.0
#: Minutos sin volver a preguntar por la misma regla.
COOLDOWN_MIN = 30.0
#: Minutos de cooldown tras cerrarla: si la reabrís enseguida, se vuelve a rezongar pronto.
COOLDOWN_TRAS_CERRAR_MIN = 2.0
#: Segundos sin tocar mouse ni teclado a partir de los cuales se asume que no estás frente a la PC.
INACTIVO_MAX_SEG = 180.0
#: Clave de la regla "cualquier ventana de incógnito".
INCOGNITO = "@incognito"

#: ``(hwnd, proceso, título)`` de la ventana en primer plano.
Ventana = Tuple[int, str, str]


@dataclass(frozen=True)
class Vigilada:
    """Una palabra o página de tu lista."""

    nombre: str                  # como la escribiste: así la dice Miku ("facebook")
    clave: str                   # normalizada (sin acentos, minúsculas): con esto se compara


@dataclass(frozen=True)
class Disparo:
    """Algo para rezongar."""

    clave: str                   # la regla que saltó (``Vigilada.clave`` o ``INCOGNITO``)
    nombre: str                  # cómo decirla en voz alta
    incognito: bool              # la ventana es privada
    hwnd: int
    proceso: str


def cargar_reglas(valor: Any) -> List[Vigilada]:
    """Lee ``HABITOS_VIGILAR`` (una lista, o un texto con comas) sin repetidos ni vacíos."""
    if isinstance(valor, str):
        valor = valor.split(",")
    reglas: List[Vigilada] = []
    vistas = set()
    for crudo in valor or []:
        nombre = str(crudo or "").strip()
        clave = normalizar(nombre)
        if clave and clave not in vistas:
            vistas.add(clave)
            reglas.append(Vigilada(nombre, clave))
    return reglas


def lista_normalizada(valor: Any, por_defecto: Sequence[str]) -> Tuple[str, ...]:
    """Una opción de lista normalizada; si está vacía o mal escrita, la de por defecto."""
    if isinstance(valor, str):
        valor = valor.split(",")
    try:
        propios = tuple(normalizar(v) for v in (valor or ()) if normalizar(v))
    except TypeError:
        propios = ()
    return propios or tuple(por_defecto)


def es_incognito(titulo: str, marcas: Iterable[str] = MARCAS_INCOGNITO) -> bool:
    """True si el título de la ventana trae alguna marca de ventana privada."""
    t = normalizar(titulo)
    return any(m in t for m in marcas)


class Vigilante:
    """Decide cuándo una ventana merece un rezongo. Sin hilos ni Windows: todo entra por ``revisar``.

    Args:
        reglas: Las páginas o palabras de tu lista.
        vigilar_incognito: Rezongar por cualquier ventana de incógnito, aunque la página no esté en la lista.
        navegadores / marcas_incognito: Ver ``NAVEGADORES`` y ``MARCAS_INCOGNITO``.
        espera_seg / cooldown_min: Ver ``ESPERA_SEG`` y ``COOLDOWN_MIN``.
        reloj: Reloj monótono (inyectable para probar).
    """

    def __init__(self, reglas: Sequence[Vigilada], vigilar_incognito: bool = False,
                 navegadores: Sequence[str] = NAVEGADORES, marcas_incognito: Sequence[str] = MARCAS_INCOGNITO,
                 espera_seg: float = ESPERA_SEG, cooldown_min: float = COOLDOWN_MIN,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self.reglas = list(reglas)
        self.vigilar_incognito = vigilar_incognito
        self.navegadores = tuple(navegadores)
        self.marcas_incognito = tuple(marcas_incognito)
        self.espera_seg = max(0.0, espera_seg)
        self.cooldown_seg = max(0.0, cooldown_min) * 60.0
        self._reloj = reloj
        self._actual: Optional[Tuple[str, float]] = None       # (regla, desde cuándo está seguida a la vista)
        self._ultimo: dict = {}                                 # regla -> cuándo se la rezongó por última vez

    @property
    def a_la_vista(self) -> Optional[str]:
        """La regla que está a la vista ahora (esperando los segundos de gracia), o None."""
        return self._actual[0] if self._actual else None

    def coincidencia(self, ventana: Optional[Ventana]) -> Optional[Disparo]:
        """La regla que cumple esta ventana (sin tener en cuenta tiempos ni cooldowns), o None."""
        if ventana is None:
            return None
        hwnd, proceso, titulo = ventana
        if proceso not in self.navegadores:
            return None
        privada = es_incognito(titulo, self.marcas_incognito)
        t = normalizar(titulo)
        for regla in self.reglas:
            if regla.clave in t:
                return Disparo(regla.clave, regla.nombre, privada, hwnd, proceso)
        if self.vigilar_incognito and privada:
            return Disparo(INCOGNITO, "incógnito", True, hwnd, proceso)
        return None

    def revisar(self, ventana: Optional[Ventana], inactivo: float = 0.0) -> Optional[Disparo]:
        """Una vuelta de vigilancia. Devuelve el rezongo si ya toca, o None.

        Args:
            ventana: Lo que hay en primer plano (``None`` si no se pudo leer).
            inactivo: Segundos sin tocar mouse ni teclado.
        """
        ahora = self._reloj()
        visto = self.coincidencia(ventana) if inactivo <= INACTIVO_MAX_SEG else None
        if visto is None:
            self._actual = None
            return None
        if self._actual is None or self._actual[0] != visto.clave:
            self._actual = (visto.clave, ahora)                 # recién llegó: empieza a contar
            return None
        if ahora - self._actual[1] < self.espera_seg:
            return None
        ultimo = self._ultimo.get(visto.clave)
        if ultimo is not None and ahora - ultimo < self.cooldown_seg:
            return None
        self._ultimo[visto.clave] = ahora
        return visto

    def olvidar(self, clave: str) -> None:
        """Anula el rezongo de ``clave`` (no se pudo preguntar): se vuelve a intentar en la próxima vuelta."""
        self._ultimo.pop(clave, None)

    def tras_cerrar(self, clave: str) -> None:
        """La cerraste: si la reabrís, se vuelve a rezongar en ``COOLDOWN_TRAS_CERRAR_MIN`` minutos, no en media hora."""
        self._ultimo[clave] = self._reloj() - self.cooldown_seg + COOLDOWN_TRAS_CERRAR_MIN * 60.0
        self._actual = None


# --------------------------------------------------------------------------- #
# Resumen
# --------------------------------------------------------------------------- #
def resumen(registro: RegistroUso, periodo: str = "hoy") -> str:
    """"¿Cuántas veces entré a páginas de mi lista?": las veces por regla, de más a menos."""
    periodo = (periodo or "hoy").strip().lower()
    if periodo == "ayer":
        cuentas, cuando = registro.totales(1, hasta=registro.hoy() - timedelta(days=1)), "Ayer"
    elif periodo in ("semana", "7 dias", "siete dias"):
        cuentas, cuando = registro.totales(7), "En los últimos 7 días"
    else:
        cuentas, cuando = registro.totales(1), "Hoy"
    veces = {nombre: int(round(n)) for nombre, n in cuentas.items() if round(n) >= 1}
    if not veces:
        return f"{cuando} no te agarré en ninguna página de tu lista. Así me gusta."
    orden = sorted(veces.items(), key=lambda kv: kv[1], reverse=True)
    partes = [f"{nombre}, {n} " + ("vez" if n == 1 else "veces") for nombre, n in orden[:5]]
    return f"{cuando} te agarré en: " + "; ".join(partes) + "."


# --------------------------------------------------------------------------- #
# Diagnóstico: cómo se llama la ventana de tu navegador
# --------------------------------------------------------------------------- #
def diagnostico(segundos: int = 20, salida: Callable[[str], None] = print) -> int:
    """Muestra, una vez por segundo, qué ve Miku en la ventana en primer plano y qué regla saltaría.

    Sirve para ajustar ``HABITOS_VIGILAR`` (qué palabras aparecen en el título) y
    ``HABITOS_MARCAS_INCOGNITO`` (cómo se llama una ventana privada en tu navegador). Los títulos se muestran
    solo acá, en tu pantalla: no se guardan en ningún lado.
    """
    from miku.ajustes import carga as config_mod
    from miku.plataforma import procesos

    config_mod.cargar()
    cfg = config_mod.config
    vigilante = Vigilante(
        cargar_reglas(cfg.get("habitos_vigilar")), bool(cfg.get("habitos_incognito", False)),
        lista_normalizada(cfg.get("habitos_navegadores"), NAVEGADORES),
        lista_normalizada(cfg.get("habitos_marcas_incognito"), MARCAS_INCOGNITO))
    salida("Lista vigilada: " + (", ".join(r.nombre for r in vigilante.reglas) or "(vacía)")
           + f" | incógnito: {'sí' if vigilante.vigilar_incognito else 'no'}")
    salida(f"Durante {segundos} s mostraré la ventana que tengas en primer plano. Pasá al navegador, abrí una "
           "página de tu lista y una ventana de incógnito.\n")
    for _ in range(max(1, segundos)):
        ventana = procesos.ventana_primer_plano()
        if ventana is None:
            salida("  (no pude leer la ventana)")
        else:
            _, proceso, titulo = ventana
            visto = vigilante.coincidencia(ventana)
            es_nav = "navegador" if proceso in vigilante.navegadores else "no es navegador"
            privada = "incógnito" if es_incognito(titulo, vigilante.marcas_incognito) else "normal"
            salida(f"  [{proceso} | {es_nav} | {privada}] {titulo[:90]!r} -> "
                   + (f"SALTARÍA: {visto.nombre}" if visto else "no coincide"))
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(diagnostico(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 20))
