"""
cache_audio.py - Caché en disco de frases ya sintetizadas por VOICEVOX.

Miku repite muchas frases cortas ("¿Sí? Decime.", "Listo", los saludos del banco de frases). Cada vez que
las dice tiene que traducirlas al japonés (una llamada al LLM) y sintetizarlas (VOICEVOX): cientos de
milisegundos antes de que suene la primera palabra. Acá se guardan los WAV ya generados, por texto y por
voz, y se reutilizan entre sesiones: una frase repetida suena de inmediato y sin tocar la red.

Se guarda solo lo corto (las respuestas largas del LLM casi nunca se repiten) y la carpeta tiene un tope de
archivos: al pasarse se borran los menos usados.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("miku.tts.cache")

#: Cambiar si cambia el formato o el criterio de la clave (invalida lo guardado).
_VERSION = "1"


class CacheAudio:
    """Caché LRU en disco: ``{sha1(voz + texto)}.wav`` dentro de ``carpeta``.

    Args:
        carpeta: Dónde guardar los WAV (se crea al primer uso).
        max_archivos: Tope de entradas; al superarlo se poda hasta el 90 %.
        max_caracteres: Solo se guardan frases de hasta este largo.
    """

    def __init__(self, carpeta: Path, max_archivos: int = 300, max_caracteres: int = 160) -> None:
        self.carpeta = Path(carpeta)
        self.max_archivos = max(10, int(max_archivos))
        self.max_caracteres = int(max_caracteres)
        self._lock = threading.Lock()

    @staticmethod
    def _clave(texto: str, voz: str) -> str:
        crudo = f"{_VERSION}|{voz}|{texto.strip()}".encode("utf-8")
        return hashlib.sha1(crudo).hexdigest()

    def _ruta(self, texto: str, voz: str) -> Path:
        return self.carpeta / f"{self._clave(texto, voz)}.wav"

    def obtener(self, texto: str, voz: str) -> Optional[bytes]:
        """El WAV guardado para ``texto`` con esa ``voz``, o None si no está (o está dañado)."""
        ruta = self._ruta(texto, voz)
        try:
            datos = ruta.read_bytes()
        except OSError:
            return None
        if not datos.startswith(b"RIFF"):
            logger.debug("Entrada de caché dañada, se descarta: %s", ruta.name)
            self._borrar(ruta)
            return None
        try:
            os.utime(ruta)                       # "usada ahora": la poda borra las menos usadas
        except OSError:
            pass
        return datos

    def guardar(self, texto: str, voz: str, wav: bytes) -> bool:
        """Guarda el WAV de ``texto``. False si no corresponde guardarlo o no se pudo."""
        if not wav or not wav.startswith(b"RIFF") or not texto.strip() or len(texto) > self.max_caracteres:
            return False
        ruta = self._ruta(texto, voz)
        with self._lock:
            try:
                self.carpeta.mkdir(parents=True, exist_ok=True)
                temporal = ruta.with_suffix(".tmp")
                temporal.write_bytes(wav)
                os.replace(temporal, ruta)         # atómico: nunca queda un WAV a medias
            except OSError as e:
                logger.debug("No pude guardar en la caché de audio: %s", e)
                return False
            self._podar()
        return True

    def _podar(self) -> None:
        """Si hay demasiadas entradas, borra las menos usadas hasta el 90 % del tope."""
        try:
            entradas = [(e.stat().st_mtime, Path(e.path)) for e in os.scandir(self.carpeta)
                        if e.name.endswith(".wav")]
        except OSError:
            return
        if len(entradas) <= self.max_archivos:
            return
        entradas.sort(key=lambda par: par[0])
        sobran = len(entradas) - int(self.max_archivos * 0.9)
        for _, ruta in entradas[:sobran]:
            self._borrar(ruta)

    @staticmethod
    def _borrar(ruta: Path) -> None:
        try:
            ruta.unlink()
        except OSError:
            pass
