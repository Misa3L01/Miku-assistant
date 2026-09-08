"""
memoria.py - Memoria persistente del asistente (chromadb).

Guarda "recuerdos" (datos sobre el usuario) en una colección local y
permite buscarlos por similitud vectorial con embeddings de sentence
transformers. Es una capa independiente que consume el núcleo (parser) y
los plugins.

La base de datos vive debajo de la carpeta data/ para respetar la nueva
estructura (y se ignora en git junto a los .json de preferencias).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import chromadb  # type: ignore

logger = logging.getLogger("miku.memoria")

# Ruta por defecto a la base (bajo data/, relativa a la raíz del proyecto).
_RUTA_BASE_DEFECTO = Path(__file__).resolve().parent.parent / "data" / "miku_memoria"

# Nombre de la colección y embebedor usado (igual que el proyecto original).
_COLECCION = "recuerdos_usuario"
_MODELO_EMBEDDING = "paraphrase-multilingual-MiniLM-L12-v2"


class Memoria:
    """Encapsula el acceso a chromadb para la memoria del usuario.

    Args:
        ruta: Carpeta persistente (por defecto ``data/miku_memoria``).
    """

    def __init__(self, ruta: Optional[Path | str] = None) -> None:
        self.ruta = Path(ruta) if ruta else _RUTA_BASE_DEFECTO
        self.ruta.mkdir(parents=True, exist_ok=True)
        self._cliente = chromadb.PersistentClient(path=str(self.ruta))
        self._coleccion = self._cliente.get_or_create_collection(
            name=_COLECCION,
            embedding_function=self._crear_embedding(),
        )
        logger.info("Memoria lista en %s", self.ruta)

    @staticmethod
    def _crear_embedding():
        """Crea la función de embeddings (import tardío y liviano)."""
        from chromadb.utils import embedding_functions
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=_MODELO_EMBEDDING
        )

    # ---------------- Escritura ---------------- #
    def guardar_recuerdo(self, texto_recuerdo: str,
                         categoria: str = "general") -> Optional[str]:
        """Guarda un recuerdo nuevo con fecha. Devuelve el id o None."""
        texto_recuerdo = (texto_recuerdo or "").strip()
        if not texto_recuerdo:
            return None

        id_recuerdo = str(uuid.uuid4())
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            self._coleccion.upsert(
                documents=[texto_recuerdo],
                ids=[id_recuerdo],
                metadatas=[{"fecha": fecha, "categoria": categoria}],
            )
            logger.info("Memoria guardada [%s]: %s", fecha, texto_recuerdo)
            return id_recuerdo
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo guardar el recuerdo.")
            return None

    def buscar_recuerdos(self, consulta: str, cantidad: int = 3) -> str:
        """Busca `cantidad` recuerdos relevantes a `consulta`.

        Returns:
            Un texto multilinea con los recuerdos, o "" si no hay ninguno.
        """
        if self._coleccion.count() == 0:
            return ""

        try:
            resultados = self._coleccion.query(
                query_texts=[consulta],
                n_results=min(cantidad, self._coleccion.count()),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Error buscando recuerdos.")
            return ""

        documentos = resultados.get("documents") or [[]]
        metadatos = resultados.get("metadatas") or [[None]]

        if not documentos or not documentos[0]:
            return ""

        lineas = []
        docs = documentos[0]
        metas = metadatos[0] or [None] * len(docs)
        for doc, meta in zip(docs, metas):
            fecha = ""
            if meta and isinstance(meta, dict):
                fecha = meta.get("fecha", "")
            fecha = f"[{fecha}] " if fecha else ""
            lineas.append(f"{fecha}{doc}")
        return "\n".join(lineas)

    def olvidar_recuerdo(self, texto_aproximado: str) -> str:
        """Borrr el recuerdo más parecido a `texto_aproximado`."""
        if self._coleccion.count() == 0:
            return "No tengo recuerdos guardados."
        try:
            resultados = self._coleccion.query(
                query_texts=[texto_aproximado], n_results=1
            )
        except Exception:  # noqa: BLE001
            logger.exception("Error buscando para olvidar.")
            return "Tuve un problema buscando el recuerdo."

        ids = resultados.get("ids") or [[]]
        docs = resultados.get("documents") or [[]]
        if not ids or not ids[0] or not docs or not docs[0]:
            return "No encontré ningún recuerdo parecido para borrar."

        id_a_borrar = ids[0][0]
        texto = docs[0][0]
        try:
            self._coleccion.delete(ids=[id_a_borrar])
            return f'Listo, olvidé esto: "{texto}"'
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo eliminar el recuerdo.")
            return "No pude borrar el recuerdo."

    def cantidad(self) -> int:
        """Cantidad total de recuerdos almacenados."""
        try:
            return self._coleccion.count()
        except Exception:  # noqa: BLE001
            return 0
