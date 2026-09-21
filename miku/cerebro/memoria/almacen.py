"""
memoria.py - Memoria persistente de recuerdos (SQLite, stdlib).

Backend liviano basado en **SQLite** (viene en la stdlib de Python, no suma
dependencias). Guarda "recuerdos" del usuario y los consulta por SIGNIFICADO
(embeddings de ``miku.cerebro.memoria.embeddings``, opcional) o, si no hay motor de embeddings,
por coincidencia de texto (LIKE).

Base de datos: ``data/miku_memoria.db`` (la carpeta data/ está gitignored).

Contrato (el mismo que ya consumía el parser):
    - guardar_recuerdo(texto, categoria="general") -> guarda una fila.
    - buscar_recuerdos(consulta, cantidad=3) -> texto con los recuerdos que
      coinciden (para inyectar en el prompt del LLM).
    - olvidar_recuerdo(texto_aproximado) -> borra la fila que mejor coincida.
    - cantidad() -> COUNT(*).

Hilos: la conexión SQLite se comparte entre hilos (voz, tecla de invocación, Telegram),
así que TODOS los accesos van bajo un ``RLock``.

Diseño defensivo: si SQLite falla por cualquier motivo, la clase se degrada a
"inoperante" (los métodos devuelven valores neutros) en vez de tumbar el
asistente. La creación de la BD/tabla ocurre en ``__init__``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from miku.ajustes.carga import BASE_DIR
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.memoria")

# Ruta por defecto de la base (relativa a la raíz del proyecto).
_RUTA_DB_DEFECTO = BASE_DIR / "data" / "miku_memoria.db"

# Palabras vacías: no sirven para buscar/olvidar ("que", "mis", "del"...) y
# hacían que casi cualquier frase coincidiera con casi todos los recuerdos.
_STOPWORDS = frozenset({
    "que", "los", "las", "del", "con", "por", "para", "una", "uno", "unos",
    "unas", "mis", "mas", "muy", "como", "cuando", "donde", "quien", "cual",
    "esto", "esta", "este", "ese", "esa", "eso", "sus", "nos", "les", "ser",
    "hay", "fue", "son", "tengo", "tenes", "tiene", "acordate", "recordame",
    "olvida", "olvidate", "sobre", "cosa",
})

# Cuántos recuerdos sin vector se indexan por búsqueda (para no trabar una
# consulta si hay muchos pendientes).
_LOTE_BACKFILL = 20


def _emb():
    """Devuelve el módulo de embeddings (o None si no está disponible).

    Import tardío y defensivo: la memoria NUNCA debe romperse si `fastembed`
    no está instalado; simplemente cae a la búsqueda por texto (LIKE).
    """
    try:
        from miku.cerebro.memoria import embeddings  # import tardío
        return embeddings
    except Exception:  # noqa: BLE001
        return None


def _escapar_like(palabra: str) -> str:
    """Escapa ``%`` y ``_`` para usar la palabra literal dentro de un LIKE."""
    return palabra.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tokenizar(texto: str) -> List[str]:
    """Palabras útiles de un texto: minúsculas, >= 3 letras y sin stopwords."""
    palabras = re.findall(r"[^\W_]+", (texto or "").lower())
    return [p for p in palabras if len(p) >= 3 and p not in _STOPWORDS]


class Memoria:
    """Memoria persistente de recuerdos sobre SQLite.

    Args:
        ruta_db: Ruta del archivo .db. Si se omite, usa
            ``data/miku_memoria.db`` en la raíz del proyecto.
    """

    def __init__(self, ruta_db: Optional[str] = None) -> None:
        self.ruta_db = Path(ruta_db) if ruta_db else _RUTA_DB_DEFECTO
        # Conexión SQLite compartida entre hilos (check_same_thread=False) y
        # protegida con este lock: sin él, dos hilos intercalan SELECT/INSERT/
        # commit y ``cerrar()`` podía dejar la conexión en None a mitad de uso.
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self._inicializar()
        except Exception as e:  # noqa: BLE001
            # Degradación elegante: no tumbamos el arranque por la memoria.
            logger.error("No se pudo inicializar la memoria SQLite: %s. "
                         "Se desactiva la memoria.", e)
            if self._conn is not None:
                try:
                    self._conn.close()  # no dejar la conexión abierta
                except Exception:  # noqa: BLE001
                    pass
            self._conn = None

    # ---------------- Inicialización ----------------
    def _inicializar(self) -> None:
        """Crea la carpeta/data y la tabla si no existen."""
        with self._lock:
            self.ruta_db.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.ruta_db),
                                         check_same_thread=False)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recuerdos (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto           TEXT NOT NULL,
                    categoria       TEXT NOT NULL DEFAULT 'general',
                    fecha_creacion  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            # Migración incremental: `embedding` (JSON del vector) y `embedding_modelo` (con qué
            # modelo se calculó, para recalcularlo si cambia). Es tolerante: si ya están, no hace nada.
            try:
                columnas = [f[1] for f in self._conn.execute(
                    "PRAGMA table_info(recuerdos)").fetchall()]
                if "embedding" not in columnas:
                    self._conn.execute(
                        "ALTER TABLE recuerdos ADD COLUMN embedding TEXT")
                if "embedding_modelo" not in columnas:
                    self._conn.execute(
                        "ALTER TABLE recuerdos ADD COLUMN embedding_modelo TEXT")
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude verificar/crear las columnas de embeddings: %s", e)
            self._conn.commit()
            logger.info("Memoria SQLite lista en %s (%d recuerdo(s)).",
                        self.ruta_db, self.cantidad())

    # ---------------- API principal ----------------
    def guardar_recuerdo(self, texto_recuerdo: str,
                         categoria: str = "general") -> Optional[str]:
        """Guarda un recuerdo. Devuelve el texto guardado (o None si falló).

        Evita duplicados exactos (mismo texto + categoría): si ya existe, no
        inserta de nuevo y avisa por log.
        """
        texto = (texto_recuerdo or "").strip()
        if not texto:
            return None
        categoria = (categoria or "general").strip() or "general"

        # El embedding puede tardar (carga del modelo): se calcula FUERA del lock.
        emb_json: Optional[str] = None
        emb_modelo: Optional[str] = None
        modulo_emb = _emb()
        if modulo_emb is not None:
            vector = modulo_emb.embeber(texto)
            if vector is not None:
                emb_json = json.dumps(vector)
                emb_modelo = modulo_emb.version_modelo()

        with self._lock:
            if self._conn is None:
                logger.debug("Memoria inactiva: se ignora 'guardar'.")
                return None
            try:
                # Anti-duplicado exacto.
                cur = self._conn.execute(
                    "SELECT id FROM recuerdos WHERE texto = ? AND categoria = ? "
                    "LIMIT 1", (texto, categoria))
                if cur.fetchone() is not None:
                    logger.debug("Recuerdo duplicado ignorado: %r", texto)
                    return texto
                self._conn.execute(
                    "INSERT INTO recuerdos (texto, categoria, embedding, embedding_modelo) "
                    "VALUES (?, ?, ?, ?)",
                    (texto, categoria, emb_json, emb_modelo))
                self._conn.commit()
                logger.info("Recuerdo guardado [%s]: %r", categoria, texto[:60])
                return texto
            except Exception as e:  # noqa: BLE001
                logger.error("No pude guardar el recuerdo: %s", e)
                return None

    def buscar_recuerdos(self, consulta: str, cantidad: int = 3) -> str:
        """Busca recuerdos relacionados con `consulta`.

        Con motor de embeddings busca por SIGNIFICADO y además suma coincidencias
        de texto en los recuerdos que todavía no tienen vector. Sin motor, usa
        solo coincidencia de texto (LIKE por palabras útiles). Devuelve un texto
        listo para inyectar en el prompt del LLM, o "" si no hay coincidencias.
        Si `consulta` está vacía, devuelve los más recientes.
        """
        consulta = (consulta or "").strip()
        cantidad = max(1, int(cantidad))
        with self._lock:
            if self._conn is None:
                return ""
            try:
                if not self._hay_recuerdos():
                    return ""              # nada que buscar: ni embeddings (carga del modelo) ni LIKE
                if not consulta:
                    encontrados = self._recientes(cantidad)
                else:
                    semanticos = self._buscar_semantico(consulta, cantidad)
                    if semanticos is None:
                        # Sin motor: LIKE sobre todos los recuerdos.
                        encontrados = self._buscar_like(consulta, cantidad, False)
                    else:
                        # Con motor: los semánticos + LIKE solo sobre los que
                        # NO tienen vector (viejos o guardados sin motor).
                        extra = self._buscar_like(consulta, cantidad, True)
                        encontrados = list(dict.fromkeys(semanticos + extra))
                        encontrados = encontrados[:cantidad]
            except Exception as e:  # noqa: BLE001
                logger.error("Error buscando recuerdos: %s", e)
                return ""
        return "\n".join(f"- {t}" for t in encontrados)

    def _hay_recuerdos(self) -> bool:
        """True si hay al menos un recuerdo guardado. Llamar con el lock."""
        return self._conn.execute("SELECT 1 FROM recuerdos LIMIT 1").fetchone() is not None

    def _recientes(self, cantidad: int) -> List[str]:
        """Los recuerdos más recientes (consulta vacía). Llamar con el lock."""
        cur = self._conn.execute(
            "SELECT texto FROM recuerdos ORDER BY id DESC LIMIT ?",
            (int(cantidad),))
        return [f[0] for f in cur.fetchall()]

    def _buscar_like(self, consulta: str, cantidad: int,
                     solo_sin_embedding: bool) -> List[str]:
        """Búsqueda por palabras (LIKE, OR) sobre los recuerdos. Llamar con el lock.

        Con ``solo_sin_embedding`` se limita a los recuerdos SIN vector vigente (los que todavía no
        se indexaron, o los que quedaron con un modelo viejo): a esos la búsqueda por significado no
        los encuentra, así que se los busca por palabras hasta que el backfill los ponga al día.
        """
        palabras = _tokenizar(consulta)
        if not palabras:
            return []
        condiciones = " OR ".join("texto LIKE ? ESCAPE '\\'" for _ in palabras)
        params: list = [f"%{_escapar_like(p)}%" for p in palabras]
        filtro = ""
        if solo_sin_embedding:
            filtro = "AND (embedding IS NULL OR embedding_modelo IS NOT ?) "
            params.append(self._version_vigente())
        params.append(int(cantidad))
        cur = self._conn.execute(
            f"SELECT texto FROM recuerdos WHERE ({condiciones}) {filtro}"
            f"ORDER BY id DESC LIMIT ?", params)
        return [f[0] for f in cur.fetchall()]

    def _version_vigente(self) -> Optional[str]:
        """Con qué modelo se calculan hoy los vectores (None si no hay motor)."""
        modulo_emb = _emb()
        return modulo_emb.version_modelo() if modulo_emb is not None else None

    def _indexar_pendientes(self, modulo_emb) -> None:
        """Calcula el vector de los recuerdos que no lo tienen VIGENTE (backfill por lotes).

        Llamar con el lock. Cubre dos casos: los recuerdos guardados antes de instalar fastembed (o
        cuando el motor falló), y los que tienen un vector de un modelo anterior, que ya no es
        comparable con las consultas de hoy. Se hace de a lotes para no colgar una búsqueda.
        """
        version = modulo_emb.version_modelo()
        filas = self._conn.execute(
            "SELECT id, texto FROM recuerdos WHERE embedding IS NULL OR embedding_modelo IS NOT ? "
            "ORDER BY id DESC LIMIT ?", (version, _LOTE_BACKFILL)).fetchall()
        hechos = 0
        for rec_id, texto in filas:
            vector = modulo_emb.embeber(texto)
            if vector is None:
                break  # el motor no responde: reintentamos en otra búsqueda
            self._conn.execute(
                "UPDATE recuerdos SET embedding = ?, embedding_modelo = ? WHERE id = ?",
                (json.dumps(vector), version, rec_id))
            hechos += 1
        if hechos:
            self._conn.commit()
            logger.info("Memoria: indexé %d recuerdo(s) con %s.", hechos, version)

    def _buscar_semantico(self, consulta: str,
                          cantidad: int) -> Optional[List[str]]:
        """Busca por SIGNIFICADO con embeddings. None si no hay motor.

        Rankea por similitud coseno contra los recuerdos con vector y aplica el
        umbral de config. Llamar con el lock.
        """
        modulo_emb = _emb()
        if modulo_emb is None or not modulo_emb.disponible():
            return None
        vector_consulta = modulo_emb.embeber(consulta)
        if vector_consulta is None:
            return None

        try:
            self._indexar_pendientes(modulo_emb)
            # Solo los vectores del modelo VIGENTE: comparar contra uno viejo da parecidos falsos.
            filas = self._conn.execute(
                "SELECT texto, embedding FROM recuerdos "
                "WHERE embedding IS NOT NULL AND embedding_modelo IS ?",
                (modulo_emb.version_modelo(),)).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer embeddings: %s", e)
            return None

        try:
            from miku.ajustes import carga as config_mod
            umbral = config_mod.config.embeddings_umbral
        except Exception:  # noqa: BLE001
            umbral = 0.35

        puntuados: List[tuple] = []
        for texto, emb_json in filas:
            try:
                vector = json.loads(emb_json) if emb_json else None
            except Exception:  # noqa: BLE001
                vector = None
            score = modulo_emb.similitud(vector_consulta, vector)
            if score >= umbral:
                puntuados.append((score, texto))

        puntuados.sort(key=lambda p: p[0], reverse=True)
        return [t for _, t in puntuados[:int(cantidad)]]

    def olvidar_recuerdo(self, texto_aproximado: str) -> str:
        """Borra el recuerdo que mejor coincida con `texto_aproximado`.

        Estrategia: el recuerdo debe contener TODAS las palabras útiles del
        pedido (sin stopwords); si hay 1 sola coincidencia, se borra; si hay
        varias, NO se borra ninguna y se pide precisión (para no borrar de más).
        Si no hay, se avisa.
        """
        texto_aproximado = (texto_aproximado or "").strip()
        if not texto_aproximado:
            return falla("memoria.olvidar_sin_texto")

        with self._lock:
            if self._conn is None:
                return falla("memoria.inactiva")
            try:
                palabras = _tokenizar(texto_aproximado)
                if palabras:
                    condiciones = " AND ".join(
                        "texto LIKE ? ESCAPE '\\'" for _ in palabras)
                    params = [f"%{_escapar_like(p)}%" for p in palabras]
                else:
                    condiciones = "texto LIKE ? ESCAPE '\\'"
                    params = [f"%{_escapar_like(texto_aproximado)}%"]

                coincidencias = self._conn.execute(
                    f"SELECT id, texto FROM recuerdos WHERE {condiciones}",
                    params).fetchall()

                if not coincidencias:
                    return falla("memoria.sin_recuerdos", consulta=texto_aproximado)
                if len(coincidencias) > 1:
                    listado = "; ".join(t for _, t in coincidencias[:5])
                    return falla("memoria.varios", listado=listado)

                rec_id, rec_texto = coincidencias[0]
                self._conn.execute("DELETE FROM recuerdos WHERE id = ?", (rec_id,))
                self._conn.commit()
                logger.info("Recuerdo olvidado: %r", rec_texto[:60])
                return exito("memoria.olvidado", recuerdo=rec_texto)
            except Exception as e:  # noqa: BLE001
                logger.error("Error olvidando recuerdo: %s", e)
                return falla("memoria.error_olvidar")

    def listar_recuerdos(self, cantidad: int = 5) -> Tuple[int, List[str]]:
        """``(total, los N más recientes)``; ``(0, [])`` si la memoria está inactiva."""
        with self._lock:
            if self._conn is None:
                return 0, []
            try:
                return self.cantidad(), self._recientes(max(1, int(cantidad)))
            except Exception as e:  # noqa: BLE001
                logger.error("No pude listar los recuerdos: %s", e)
                return 0, []

    def cantidad(self) -> int:
        """Devuelve la cantidad de recuerdos guardados."""
        with self._lock:
            if self._conn is None:
                return 0
            try:
                cur = self._conn.execute("SELECT COUNT(*) FROM recuerdos")
                return int(cur.fetchone()[0])
            except Exception:  # noqa: BLE001
                return 0

    def cerrar(self) -> None:
        """Cierra la conexión (limpieza al salir)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None
