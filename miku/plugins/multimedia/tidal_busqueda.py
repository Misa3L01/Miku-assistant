"""
tidal_busqueda.py - Buscar música en TIDAL por nombre (para "poné X de Y").

TIDAL de escritorio no tiene API de reproducción, pero sí abre enlaces ``tidal://track/<id>``. Lo que
falta es **encontrar el id** de "Bohemian Rhapsody": para eso se usa la librería opcional
``tidalapi`` (``pip install tidalapi``) con **tu** cuenta, iniciando sesión una sola vez desde el
navegador (flujo de "dispositivo": TIDAL te muestra un código y aprobás). La sesión queda en
``data/tidal_sesion.json`` (⚠️ contiene tokens: está en ``.gitignore``; no lo compartas).

Todo es perezoso y tolerante: sin ``tidalapi`` o sin sesión el resto de TIDAL (pausa, siguiente, qué
suena) sigue funcionando y Miku explica qué falta en vez de fallar.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("miku.plugins.tidal.busqueda")

#: Tipos que se pueden pedir -> (nombre del modelo de tidalapi, clave en ``SearchResults``, segmento del enlace).
TIPOS: Dict[str, tuple] = {
    "cancion": ("Track", "tracks", "track"),
    "album": ("Album", "albums", "album"),
    "artista": ("Artist", "artists", "artist"),
    "playlist": ("Playlist", "playlists", "playlist"),
}

#: Sinónimos que puede mandar el LLM.
_SINONIMOS = {"canción": "cancion", "tema": "cancion", "track": "cancion", "song": "cancion",
              "disco": "album", "álbum": "album", "artist": "artista", "cantante": "artista",
              "banda": "artista", "lista": "playlist", "lista de reproducción": "playlist"}


def normalizar_tipo(tipo: str) -> str:
    """Lleva lo que dijo el usuario/LLM a una clave de ``TIPOS`` (por defecto, canción)."""
    t = (tipo or "").strip().lower()
    t = _SINONIMOS.get(t, t)
    return t if t in TIPOS else "cancion"


@dataclass
class Resultado:
    """Algo encontrado en TIDAL."""

    tipo: str
    id: str
    titulo: str
    artista: str = ""

    @property
    def enlace(self) -> str:
        """Enlace que abre el elemento en TIDAL de escritorio."""
        return f"tidal://{TIPOS[self.tipo][2]}/{self.id}"


def libreria_disponible() -> bool:
    """True si ``tidalapi`` está instalada."""
    try:
        import tidalapi  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class BuscadorTidal:
    """Búsqueda en TIDAL con una sesión guardada.

    Args:
        ruta_sesion: Archivo JSON con la sesión (se crea al conectar).
        fabrica_sesion: ``f() -> Session`` (los tests inyectan una falsa).
    """

    def __init__(self, ruta_sesion: Path,
                 fabrica_sesion: Optional[Callable[[], Any]] = None) -> None:
        self.ruta_sesion = Path(ruta_sesion)
        self._fabrica = fabrica_sesion
        self._sesion: Any = None
        self._conectando = threading.Event()

    # ------------------------------------------------------------------ sesión
    def _nueva_sesion(self) -> Any:
        if self._fabrica is not None:
            return self._fabrica()
        import tidalapi
        return tidalapi.Session()

    def sesion(self) -> Optional[Any]:
        """Sesión válida cargada del archivo, o None (sin librería / sin conectar / vencida)."""
        if self._sesion is not None:
            return self._sesion
        if self._fabrica is None and not libreria_disponible():
            return None
        if not self.ruta_sesion.exists():
            return None
        try:
            s = self._nueva_sesion()
            if s.load_session_from_file(self.ruta_sesion) and s.check_login():
                self._sesion = s
                return s
        except Exception as e:  # noqa: BLE001
            logger.warning("No pude cargar la sesión de TIDAL: %s", e)
        return None

    def conectar(self, al_terminar: Callable[[bool], None],
                 abrir_url: Callable[[str], Any]) -> Optional[str]:
        """Empieza el inicio de sesión: abre el navegador y espera la aprobación en segundo plano.

        Args:
            al_terminar: ``f(exito)`` cuando termina (aprobado, vencido o error).
            abrir_url: ``f(url)`` para mostrar el enlace de aprobación (abre el navegador).

        Returns:
            El enlace de aprobación, o None si no se pudo empezar.
        """
        if self._conectando.is_set():
            return None
        try:
            sesion = self._nueva_sesion()
            enlace, futuro = sesion.login_oauth()
            url = str(enlace.verification_uri_complete)
            if not url.startswith("http"):
                url = "https://" + url
        except Exception as e:  # noqa: BLE001
            logger.error("No pude empezar el inicio de sesión en TIDAL: %s", e)
            return None

        self._conectando.set()

        def esperar() -> None:
            exito = False
            try:
                futuro.result(timeout=300)
                if sesion.check_login():
                    self.ruta_sesion.parent.mkdir(parents=True, exist_ok=True)
                    sesion.save_session_to_file(self.ruta_sesion)
                    self._sesion = sesion
                    exito = True
            except Exception as e:  # noqa: BLE001
                logger.warning("El inicio de sesión en TIDAL no se completó: %s", e)
            finally:
                self._conectando.clear()
                try:
                    al_terminar(exito)
                except Exception:  # noqa: BLE001
                    logger.debug("Error avisando el resultado de TIDAL.", exc_info=True)

        threading.Thread(target=esperar, daemon=True, name="tidal_login").start()
        try:
            abrir_url(url)
        except Exception:  # noqa: BLE001
            logger.debug("No pude abrir el navegador para TIDAL.", exc_info=True)
        return url

    # ---------------------------------------------------------------- búsqueda
    def _modelos(self, modelo: str) -> List[Any]:
        """Clase de ``tidalapi`` a buscar (con una sesión inyectada se pasa el nombre tal cual)."""
        if self._fabrica is not None:
            return [modelo]
        import tidalapi
        return [getattr(tidalapi, modelo)]

    def buscar(self, consulta: str, tipo: str = "cancion") -> Optional[Resultado]:
        """Mejor coincidencia para ``consulta`` (None si no hay sesión, no hay resultados o falla)."""
        s = self.sesion()
        consulta = (consulta or "").strip()
        if s is None or not consulta:
            return None
        tipo = normalizar_tipo(tipo)
        modelo, clave, _ = TIPOS[tipo]
        try:
            respuesta = s.search(consulta, models=self._modelos(modelo), limit=5)
            elementos: List[Any] = list(respuesta.get(clave) or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("Falló la búsqueda en TIDAL: %s", e)
            return None
        if not elementos:
            return None
        e = elementos[0]
        artista = ""
        if tipo != "artista":
            artista = str(getattr(getattr(e, "artist", None), "name", "") or "")
        return Resultado(tipo, str(e.id), str(getattr(e, "name", "") or consulta), artista)
