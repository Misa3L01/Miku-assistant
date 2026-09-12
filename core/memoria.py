"""
memoria.py - Memoria persistente de recuerdos (SQLite, stdlib).

Reactivado con un backend liviano basado en **SQLite** (viene en la stdlib de
Python, no suma dependencias). Guarda "recuerdos" del usuario y los consulta
por coincidencia de texto (LIKE), sin embeddings ni búsqueda semántica por
ahora — eso queda para más adelante si hace falta.

Base de datos: ``data/miku_memoria.db`` (la carpeta data/ está gitignored).

Contrato (el mismo que ya consumía el parser):
    - guardar_recuerdo(texto, categoria="general") -> guarda una fila.
    - buscar_recuerdos(consulta, cantidad=3) -> texto con los recuerdos que
      coinciden (para inyectar en el prompt del LLM).
    - olvidar_recuerdo(texto_aproximado) -> borra la fila que mejor coincida.
    - cantidad() -> COUNT(*).

Diseño defensivo: si SQLite falla por cualquier motivo, la clase se degrada a
"inoperante" (los métodos devuelven valores neutros) en vez de tumbar el
asistente. La creación de la BD/tabla es perezosa (en ``__init__``).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger("miku.memoria")

# Ruta por defecto de la base (relativa a la raíz del proyecto).
_RUTA_DB_DEFECTO = Path(__file__).resolve().parent.parent / "data" / "miku_memoria.db"


class Memoria:
    """Memoria persistente de recuerdos sobre SQLite.

    Args:
        ruta_db: Ruta del archivo .db. Si se omite, usa
            ``data/miku_memoria.db`` en la raíz del proyecto.
    """

    _DESACTIVADA = False  # tiene backend real

    def __init__(self, ruta_db: Optional[str] = None) -> None:
        self.ruta_db = Path(ruta_db) if ruta_db else _RUTA_DB_DEFECTO
        # Conexión SQLite. check_same_thread=False porque el parser puede
        # llamar desde distintos hilos (voz/push); SQLite serializa con su
        # propio lock interno en modo default.
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self._inicializar()
        except Exception as e:  # noqa: BLE001
            # Degradación elegante: no tumbamos el arranque por la memoria.
            logger.error("No se pudo inicializar la memoria SQLite: %s. "
                         "Se desactiva la memoria.", e)
            self._conn = None
            self._DESACTIVADA = True

    # ---------------- Inicialización ----------------
    def _inicializar(self) -> None:
        """Crea la carpeta/data y la tabla si no existen."""
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
        if self._conn is None:
            logger.debug("Memoria inactiva: se ignora 'guardar'.")
            return None

        texto = (texto_recuerdo or "").strip()
        if not texto:
            return None
        categoria = (categoria or "general").strip() or "general"

        try:
            # Anti-duplicado exacto.
            cur = self._conn.execute(
                "SELECT id FROM recuerdos WHERE texto = ? AND categoria = ? "
                "LIMIT 1", (texto, categoria))
            if cur.fetchone() is not None:
                logger.debug("Recuerdo duplicado ignorado: %r", texto)
                return texto

            self._conn.execute(
                "INSERT INTO recuerdos (texto, categoria) VALUES (?, ?)",
                (texto, categoria))
            self._conn.commit()
            logger.info("Recuerdo guardado [%s]: %r", categoria, texto[:60])
            return texto
        except Exception as e:  # noqa: BLE001
            logger.error("No pude guardar el recuerdo: %s", e)
            return None

    def buscar_recuerdos(self, consulta: str, cantidad: int = 3) -> str:
        """Busca recuerdos que contengan alguna palabra de `consulta`.

        Búsqueda simple por LIKE (case-insensitive, con COLLATE NOCASE).
        Devuelve un texto listo para inyectar en el prompt del LLM, o "" si no
        hay coincidencias. Si `consulta` está vacía, devuelve los más recientes.
        """
        if self._conn is None:
            return ""

        consulta = (consulta or "").strip()
        try:
            if consulta:
                # Partimos la consulta en palabras (>=3 letras) y buscamos las
                # filas que contengan CUALQUIERA de ellas. Uso placeholders
                # para evitar inyección SQL.
                palabras = [p for p in _tokenizar(consulta) if len(p) >= 3]
                if palabras:
                    condiciones = " OR ".join(
                        "texto LIKE ?" for _ in palabras)
                    params = [f"%{p}%" for p in palabras]
                    params.append(int(cantidad))
                    cur = self._conn.execute(
                        f"SELECT texto FROM recuerdos WHERE {condiciones} "
                        f"ORDER BY id DESC LIMIT ?", params)
                else:
                    cur = self._conn.execute(
                        "SELECT texto FROM recuerdos ORDER BY id DESC LIMIT ?",
                        (int(cantidad),))
            else:
                cur = self._conn.execute(
                    "SELECT texto FROM recuerdos ORDER BY id DESC LIMIT ?",
                    (int(cantidad),))

            filas = [f[0] for f in cur.fetchall()]
            if not filas:
                return ""
            # Formato natural para el prompt del LLM.
            return "\n".join(f"- {f}" for f in filas)
        except Exception as e:  # noqa: BLE001
            logger.error("Error buscando recuerdos: %s", e)
            return ""

    def olvidar_recuerdo(self, texto_aproximado: str) -> str:
        """Borra el recuerdo que mejor coincida con `texto_aproximado`.

        Estrategia: LIKE por palabras; si hay 1 sola coincidencia, se borra;
        si hay varias, NO se borra ninguna y se pide precisión (para no borrar
        de más). Si no hay, se avisa.
        """
        if self._conn is None:
            return "La memoria no está activa."

        texto_aproximado = (texto_aproximado or "").strip()
        if not texto_aproximado:
            return "¿Qué recuerdo querés que olvide?"

        try:
            palabras = [p for p in _tokenizar(texto_aproximado) if len(p) >= 3]
            if palabras:
                condiciones = " OR ".join("texto LIKE ?" for _ in palabras)
                params = [f"%{p}%" for p in palabras]
            else:
                condiciones = "texto LIKE ?"
                params = [f"%{texto_aproximado}%"]

            cur = self._conn.execute(
                f"SELECT id, texto FROM recuerdos WHERE {condiciones}",
                params)
            coincidencias = cur.fetchall()

            if not coincidencias:
                return f"No encontré ningún recuerdo sobre '{texto_aproximado}'."
            if len(coincidencias) > 1:
                listado = ", ".join(t for _, t in coincidencias[:5])
                return (f"Tengo varios recuerdos que coinciden: {listado}. "
                        f"Decime cuál con más detalle.")

            rec_id, rec_texto = coincidencias[0]
            self._conn.execute("DELETE FROM recuerdos WHERE id = ?", (rec_id,))
            self._conn.commit()
            logger.info("Recuerdo olvidado: %r", rec_texto[:60])
            return f"Listo, olvidé: {rec_texto}"
        except Exception as e:  # noqa: BLE001
            logger.error("Error olvidando recuerdo: %s", e)
            return "No pude olvidar ese recuerdo."

    def cantidad(self) -> int:
        """Devuelve la cantidad de recuerdos guardados."""
        if self._conn is None:
            return 0
        try:
            cur = self._conn.execute("SELECT COUNT(*) FROM recuerdos")
            return int(cur.fetchone()[0])
        except Exception:  # noqa: BLE001
            return 0

    def cerrar(self) -> None:
        """Cierra la conexión (opcional, para limpieza al salir)."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None


def _tokenizar(texto: str) -> list:
    """Separa un texto en palabras (solo alfanuméricas, minúsculas).

    Se usa para armar la búsqueda LIKE por palabras. Simple a propósito.
    """
    import re
    return [w for w in re.findall(r"\w+", (texto or "").lower())]
