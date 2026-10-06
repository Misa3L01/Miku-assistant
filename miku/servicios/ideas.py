"""
ideas.py - Ideas guardadas: una captura o foto de algo que querés revisar más tarde, con su recordatorio.

Ejemplo: ves una idea para un proyecto en un foro, le sacás una captura y se la pasás a Miku (por Telegram o
diciéndole "guardá la última captura para después"). Ella la mira, le pone un título y un resumen, la guarda y te
la recuerda cuando quedaron: mañana, el sábado, en una semana...

Qué se guarda y dónde (todo en tu PC, en ``data/``, que no se sube a GitHub):
    * ``data/ideas.db``: título, resumen, tu nota, cuándo recordártela y si ya la viste.
    * ``data/ideas/<n>.jpg``: la imagen, achicada.
La imagen se manda UNA vez a la visión (Gemini o Groq) para que la describa; después no sale de tu PC.

Este módulo no toca Telegram, voz ni Windows: es el almacén, el intérprete de fechas y la búsqueda de la última
captura. El plugin (``miku/plugins/productividad/ideas.py``) los usa.
"""
from __future__ import annotations

import io
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

from miku.plataforma.texto import normalizar

logger = logging.getLogger("miku.servicios.ideas")

#: A qué hora se recuerda una idea cuando no se dijo otra ("mañana", "en 3 días"...).
HORA_AVISO = "18:00"
#: A qué hora se recuerda una idea el fin de semana.
HORA_FINDE = "11:00"
#: Una captura de hasta tantos minutos de antigüedad es "la última captura" sin dudar.
FRESCURA_MIN = 15.0
#: Extensiones de imagen que cuentan como captura.
EXTENSIONES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
#: Lado más largo de la imagen guardada (más grande no ayuda a la visión y pesa).
MAX_LADO = 1280

ESTADOS = ("pendiente", "hecha", "descartada")


# --------------------------------------------------------------------------- #
# "¿Cuándo te lo recuerdo?"
# --------------------------------------------------------------------------- #
_DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
_NOMBRES_DIA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_NUMEROS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
            "ocho": 8, "nueve": 9, "diez": 10, "quince": 15, "veinte": 20, "treinta": 30}
_UNIDADES = {"minuto": "minutes", "minutos": "minutes", "hora": "hours", "horas": "hours", "dia": "days",
             "dias": "days", "semana": "weeks", "semanas": "weeks", "mes": "months", "meses": "months"}
#: Frases que dicen "no quiero recordatorio". El "nunca" suelto no está acá: en el pie de una foto ("nunca había
#: visto esto") no quiere decir eso; solo vale si es TODO lo que se dijo (ver ``parsear_cuando``).
_NUNCA = re.compile(r"\b(sin recordatorio|sin aviso|no me (lo |la )?recuerdes|"
                    r"no quiero (que me (lo |la )?recuerdes|recordatorio))\b")


def _hora(texto: str) -> Tuple[int, int]:
    h, _, m = str(texto or "").partition(":")
    try:
        return max(0, min(23, int(h))), max(0, min(59, int(m or 0)))
    except ValueError:
        return 18, 0


def parsear_cuando(texto: str, ahora: datetime, hora_aviso: str = HORA_AVISO,
                   hora_finde: str = HORA_FINDE) -> Tuple[bool, Optional[datetime]]:
    """Entiende "mañana", "el sábado", "en una semana", "esta noche", "a las 9"... dentro de ``texto``.

    Devuelve ``(reconocido, cuándo)``:
        * ``(False, None)``: no encontró ninguna referencia a un momento (quien llama usa el de por defecto).
        * ``(True, None)``: dijiste que no querés recordatorio ("sin recordatorio", "nunca").
        * ``(True, fecha)``: el momento, siempre en el futuro.

    Busca la expresión en cualquier parte del texto, así sirve tanto para un "mañana" suelto como para el pie de
    una foto ("buenísima idea, recordámela el sábado").
    """
    t = normalizar(texto)
    if not t:
        return False, None
    if t in ("nunca", "no") or _NUNCA.search(t):
        return True, None
    ha, ma = _hora(hora_aviso)
    hf, mf = _hora(hora_finde)
    hoy = ahora.replace(hour=ha, minute=ma, second=0, microsecond=0)
    cuando: Optional[datetime] = None

    # --- "a las 9", "a las 21:30": pisa la hora de lo que se haya entendido
    explicita = re.search(r"\ba las (\d{1,2})(?::(\d{2}))?\b", t)
    if explicita:
        ha_, ma_ = int(explicita.group(1)), int(explicita.group(2) or 0)
        if 0 <= ha_ <= 23 and 0 <= ma_ <= 59:
            hoy = ahora.replace(hour=ha_, minute=ma_, second=0, microsecond=0)
            hf, mf = ha_, ma_
    elif re.search(r"\ba la (noche)\b|\besta noche\b", t):
        hoy = ahora.replace(hour=21, minute=0, second=0, microsecond=0)
    elif re.search(r"\ba la (manana)\b", t):
        hoy = ahora.replace(hour=9, minute=0, second=0, microsecond=0)

    # --- relativos: "en 3 horas", "en una semana", "en media hora"
    rel = re.search(r"\ben (\d+|[a-z]+) (minuto|minutos|hora|horas|dia|dias|semana|semanas|mes|meses)\b", t)
    cantidad = (int(rel.group(1)) if rel.group(1).isdigit() else _NUMEROS.get(rel.group(1))) if rel else None
    if rel and cantidad:
        unidad = _UNIDADES[rel.group(2)]
        if unidad == "minutes":
            cuando = ahora + timedelta(minutes=cantidad)
        elif unidad == "hours":
            cuando = ahora + timedelta(hours=cantidad)
        elif unidad == "days":
            cuando = hoy + timedelta(days=cantidad)
        elif unidad == "weeks":
            cuando = hoy + timedelta(weeks=cantidad)
        else:
            cuando = hoy + timedelta(days=30 * cantidad)
    elif re.search(r"\ben media hora\b", t):
        cuando = ahora + timedelta(minutes=30)
    elif re.search(r"\b(mas tarde|en un rato)\b", t):
        cuando = ahora + timedelta(hours=2)
    elif re.search(r"\bpasado manana\b", t):
        cuando = hoy + timedelta(days=2)
    elif re.search(r"\b(la )?semana (que viene|proxima)\b|\bla proxima semana\b", t):
        cuando = hoy + timedelta(days=7)
    elif re.search(r"\b(fin de semana|finde)\b", t):
        dias = (5 - ahora.weekday()) % 7                    # días hasta el sábado (0 si hoy es sábado)
        cuando = ahora.replace(hour=hf, minute=mf, second=0, microsecond=0) + timedelta(days=dias)
        if cuando <= ahora:
            cuando += timedelta(days=7)                     # es sábado y la hora ya pasó: el próximo
    else:
        for nombre, numero in _DIAS.items():
            if re.search(rf"\b{nombre}\b", t):
                dias = (numero - ahora.weekday()) % 7 or 7
                base = ahora.replace(hour=hf, minute=mf, second=0, microsecond=0) if numero >= 5 else hoy
                cuando = base + timedelta(days=dias)
                break
        else:
            # "mañana" es el día; "a la mañana" es la parte del día ("mañana a la mañana" son las dos cosas).
            if re.search(r"\bmanana\b", t.replace("a la manana", "")):
                cuando = hoy + timedelta(days=1)
            elif re.search(r"\b(hoy|esta noche|esta tarde|a la noche|a la manana)\b", t) or explicita:
                cuando = hoy
                if re.search(r"\besta noche\b", t):
                    cuando = ahora.replace(hour=21, minute=0, second=0, microsecond=0)
    if cuando is None:
        return False, None
    if cuando <= ahora:
        if explicita and cuando.date() == ahora.date() and not re.search(r"\bhoy\b", t):
            cuando += timedelta(days=1)                     # "a las 9" a las 20: se entiende mañana a las 9
        else:
            cuando = ahora + timedelta(hours=1)             # "hoy" pero esa hora ya pasó: en una hora
    return True, cuando


def cuando_en_texto(cuando: Optional[datetime], ahora: datetime) -> str:
    """"hoy a las 21", "mañana a las 18", "el sábado a las 11", "el 14/10 a las 18": para decirlo en voz alta."""
    if cuando is None:
        return "sin recordatorio"
    hora = f"{cuando.hour}" if cuando.minute == 0 else f"{cuando.hour}:{cuando.minute:02d}"
    dias = (cuando.date() - ahora.date()).days
    if dias <= 0:
        dia = "hoy"
    elif dias == 1:
        dia = "mañana"
    elif dias < 7:
        dia = f"el {_NOMBRES_DIA[cuando.weekday()]}"
    else:
        dia = f"el {cuando.day}/{cuando.month}"
    return f"{dia} a las {hora}"


# --------------------------------------------------------------------------- #
# La idea y el almacén
# --------------------------------------------------------------------------- #
@dataclass
class Idea:
    """Una idea guardada."""

    id: int
    creada: datetime
    titulo: str
    resumen: str
    nota: str
    imagen: str                         # ruta del JPEG ("" si no hay)
    recordar_en: Optional[datetime]
    avisada: bool
    estado: str
    origen: str                         # "telegram", "captura", "pantalla"


_COLUMNAS = "id, creada, titulo, resumen, nota, imagen, recordar_en, avisada, estado, origen"


def _fecha(texto: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def _a_idea(fila: Sequence[Any]) -> Idea:
    return Idea(int(fila[0]), _fecha(fila[1]) or datetime.now(), fila[2] or "", fila[3] or "", fila[4] or "",
                fila[5] or "", _fecha(fila[6]), bool(fila[7]), fila[8] or "pendiente", fila[9] or "")


class AlmacenIdeas:
    """Las ideas guardadas, en un SQLite chico (más los JPEG al lado).

    Args:
        ruta_db: Archivo de la base (None = en memoria, para los tests).
        carpeta_imagenes: Dónde van los JPEG (None = no se guardan imágenes).
        ahora: Reloj de pared (inyectable).
    """

    def __init__(self, ruta_db: Optional[Path] = None, carpeta_imagenes: Optional[Path] = None,
                 ahora: Callable[[], datetime] = datetime.now) -> None:
        self._carpeta = Path(carpeta_imagenes) if carpeta_imagenes else None
        self._ahora = ahora
        self._lock = threading.RLock()
        if ruta_db is not None:
            Path(ruta_db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(ruta_db) if ruta_db else ":memory:", check_same_thread=False)
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS ideas ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, creada TEXT NOT NULL, titulo TEXT NOT NULL DEFAULT '', "
                "resumen TEXT NOT NULL DEFAULT '', nota TEXT NOT NULL DEFAULT '', imagen TEXT NOT NULL DEFAULT '', "
                "recordar_en TEXT, avisada INTEGER NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'pendiente', "
                "origen TEXT NOT NULL DEFAULT '')")

    def cerrar(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------ escritura
    def guardar(self, titulo: str, resumen: str = "", nota: str = "", imagen_jpeg: Optional[bytes] = None,
                recordar_en: Optional[datetime] = None, origen: str = "") -> Idea:
        """Guarda una idea nueva (y su imagen, si hay) y la devuelve."""
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO ideas (creada, titulo, resumen, nota, recordar_en, origen) VALUES (?,?,?,?,?,?)",
                (self._ahora().isoformat(timespec="seconds"), titulo.strip(), resumen.strip(), nota.strip(),
                 recordar_en.isoformat(timespec="seconds") if recordar_en else None, origen))
            identificador = int(cur.lastrowid)
            if imagen_jpeg and self._carpeta is not None:
                ruta = self._carpeta / f"{identificador}.jpg"
                try:
                    self._carpeta.mkdir(parents=True, exist_ok=True)
                    ruta.write_bytes(imagen_jpeg)
                    self._db.execute("UPDATE ideas SET imagen=? WHERE id=?", (str(ruta), identificador))
                except OSError as e:
                    logger.warning("No pude guardar la imagen de la idea %d: %s", identificador, e)
        return self.obtener(identificador)  # type: ignore[return-value]

    def recordar(self, identificador: int, cuando: Optional[datetime]) -> bool:
        """Cambia cuándo se recuerda una idea (None = sin recordatorio). Vuelve a dejarla sin avisar."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE ideas SET recordar_en=?, avisada=0 WHERE id=?",
                (cuando.isoformat(timespec="seconds") if cuando else None, identificador))
            return cur.rowcount > 0

    def marcar_avisada(self, identificador: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE ideas SET avisada=1 WHERE id=?", (identificador,))

    def cambiar_estado(self, identificador: int, estado: str) -> bool:
        """``hecha`` o ``descartada`` (o ``pendiente`` para reabrirla)."""
        if estado not in ESTADOS:
            raise ValueError(f"estado desconocido: {estado}")
        with self._lock, self._db:
            return self._db.execute("UPDATE ideas SET estado=? WHERE id=?", (estado, identificador)).rowcount > 0

    # ------------------------------------------------------------ lectura
    def obtener(self, identificador: int) -> Optional[Idea]:
        with self._lock:
            fila = self._db.execute(f"SELECT {_COLUMNAS} FROM ideas WHERE id=?", (identificador,)).fetchone()
        return _a_idea(fila) if fila else None

    def ultima(self) -> Optional[Idea]:
        """La última idea que se guardó que sigue pendiente."""
        with self._lock:
            fila = self._db.execute(
                f"SELECT {_COLUMNAS} FROM ideas WHERE estado='pendiente' ORDER BY id DESC LIMIT 1").fetchone()
        return _a_idea(fila) if fila else None

    def pendientes(self) -> List[Idea]:
        """Las ideas sin revisar, de la más vieja a la más nueva."""
        with self._lock:
            filas = self._db.execute(
                f"SELECT {_COLUMNAS} FROM ideas WHERE estado='pendiente' ORDER BY id").fetchall()
        return [_a_idea(f) for f in filas]

    def vencidas(self, ahora: Optional[datetime] = None) -> List[Idea]:
        """Las pendientes cuyo recordatorio ya llegó y todavía no se avisó."""
        limite = (ahora or self._ahora()).isoformat(timespec="seconds")
        with self._lock:
            filas = self._db.execute(
                f"SELECT {_COLUMNAS} FROM ideas WHERE estado='pendiente' AND avisada=0 AND recordar_en IS NOT NULL "
                "AND recordar_en <= ? ORDER BY recordar_en", (limite,)).fetchall()
        return [_a_idea(f) for f in filas]


# --------------------------------------------------------------------------- #
# Imágenes
# --------------------------------------------------------------------------- #
def comprimir_imagen(datos: bytes, max_lado: int = MAX_LADO, calidad: int = 75) -> Optional[bytes]:
    """Achica una imagen (cualquier formato) y la devuelve como JPEG; None si no es una imagen."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(datos)) as img:
            img.load()
            return jpeg_de(img, max_lado, calidad)
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la imagen: %s", e)
        return None


def jpeg_de(img: Any, max_lado: int = MAX_LADO, calidad: int = 75) -> bytes:
    """Una imagen de Pillow como JPEG, achicada a ``max_lado`` de lado más largo."""
    ancho, alto = img.size
    if max(ancho, alto) > max_lado:
        factor = max_lado / float(max(ancho, alto))
        img = img.resize((max(1, int(ancho * factor)), max(1, int(alto * factor))))
    if img.mode != "RGB":
        img = img.convert("RGB")
    salida = io.BytesIO()
    img.save(salida, format="JPEG", quality=calidad)
    return salida.getvalue()


@dataclass
class Captura:
    """Lo que se encontró como "la última captura"."""

    datos: bytes                        # la imagen original (el archivo, o el JPEG del portapapeles)
    origen: str                         # de dónde salió, para decírtelo
    minutos: Optional[float]            # antigüedad (None si viene del portapapeles)


def buscar_ultima_captura(carpetas: Iterable[Path], leer_portapapeles: Callable[[], Any] = lambda: None,
                          ahora: Callable[[], float] = time.time, frescura_min: float = FRESCURA_MIN
                          ) -> Optional[Captura]:
    """"La última captura": un archivo reciente de tus carpetas de capturas, o la imagen del portapapeles.

    Orden: (1) el archivo de imagen más nuevo si es de los últimos ``frescura_min`` minutos; (2) la imagen copiada
    en el portapapeles (``Win+Shift+S`` no guarda archivo, la deja ahí); (3) el archivo más nuevo aunque sea viejo,
    diciendo de hace cuánto es, para que no guardes sin querer una captura de ayer.
    """
    mejor: Optional[Path] = None
    for carpeta in carpetas:
        try:
            for p in Path(carpeta).iterdir():
                if p.is_file() and p.suffix.lower() in EXTENSIONES and not p.name.startswith("."):
                    if mejor is None or p.stat().st_mtime > mejor.stat().st_mtime:
                        mejor = p
        except OSError:
            continue
    minutos = (ahora() - mejor.stat().st_mtime) / 60.0 if mejor is not None else None
    if mejor is not None and minutos is not None and minutos <= frescura_min:
        return Captura(mejor.read_bytes(), f"el archivo {mejor.name}", minutos)
    imagen = leer_portapapeles()
    if imagen is not None:
        try:
            return Captura(jpeg_de(imagen), "la imagen que tenés copiada", None)
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude usar la imagen del portapapeles: %s", e)
    if mejor is not None:
        return Captura(mejor.read_bytes(), f"el archivo {mejor.name}", minutos)
    return None


# --------------------------------------------------------------------------- #
# Lo que contesta la visión
# --------------------------------------------------------------------------- #
PROMPT_IDEA = (
    "Esta imagen es una captura o foto de algo que el usuario quiere recordar para revisar más tarde (una idea de "
    "proyecto, una publicación, un producto, un tutorial, algo para probar). {nota}"
    "Respondé en español EXACTAMENTE con este formato, sin nada más: "
    "TITULO: <hasta 8 palabras> | RESUMEN: <una o dos frases con la idea y qué habría que hacer>")


def separar_titulo_resumen(texto: Optional[str]) -> Tuple[str, str]:
    """Saca ``(título, resumen)`` de la respuesta de la visión; si no vino en el formato pedido, se las arregla."""
    limpio = re.sub(r"[*_`#]+", "", texto or "").strip()
    if not limpio:
        return "", ""
    m = re.search(r"TITULO\s*:\s*(.+?)\s*(?:\||\n)\s*RESUMEN\s*:\s*(.+)", limpio, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:90], re.sub(r"\s+", " ", m.group(2)).strip()[:400]
    primera, _, resto = limpio.partition("\n")
    if not resto.strip():
        corte = re.split(r"(?<=[.!?])\s+", primera, maxsplit=1)
        primera, resto = corte[0], corte[1] if len(corte) > 1 else ""
    return primera.strip()[:90], re.sub(r"\s+", " ", resto).strip()[:400]
