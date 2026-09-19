# -*- coding: utf-8 -*-
"""
embeddings.py - Motor de embeddings liviano (opcional) para búsqueda semántica.

Objetivo: dar similitud por SIGNIFICADO ("cuándo es mi cumpleaños" encuentra
"nací el 15 de julio") sin meter dependencias pesadas (nada de torch).

Motor preferido (liviano y real): **fastembed** (ONNX Runtime), modelo
multilingüe chico. Es OPCIONAL: si no está instalado, este módulo se degrada
(``disponible()`` devuelve False) y quien lo use cae a su búsqueda por texto.

RAM/deps: fastembed + onnxruntime suman ~100-200 MB en disco y cargan el modelo
en RAM (decenas de MB) la primera vez. Por eso el cargado es LAZY (recién en el
primer uso) y todo va envuelto en try/except: nunca debe romper el arranque.

API pública:
    - disponible() -> bool
    - embeber(texto) -> Optional[list[float]]
    - similitud(a, b) -> float  (coseno; 0.0 si algo falla)
    - ruta_cache_modelos() -> str  (carpeta de caché del modelo)
"""
from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("miku.embeddings")

# Modelo multilingüe chico de fastembed (soporta español).
_MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Instancia del modelo cacheada (se crea en el primer uso).
_modelo = None
# fastembed no está instalado: no se reintenta (no va a aparecer solo).
_sin_libreria = False
# Instante (time.monotonic) del último fallo al CARGAR el modelo (p. ej. sin
# red para descargarlo): se reintenta pasado ``_ESPERA_REINTENTO``.
_fallo_en = 0.0
_ESPERA_REINTENTO = 60.0
# Serializa la carga: sin lock, mientras un hilo descarga el modelo otro
# recibía None y guardaba su recuerdo sin vector.
_lock_modelo = threading.Lock()


def _ruta_cache() -> str:
    """Carpeta de caché del modelo (bajo data/embeddings)."""
    try:
        from miku.ajustes import carga as config_mod
        base = Path(config_mod.BASE_DIR) / "data" / "embeddings"
    except Exception:  # noqa: BLE001
        base = Path(".miku_embeddings")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return str(base)


def ruta_cache_modelos() -> str:
    """Expone la carpeta de caché (para diagnóstico/logs)."""
    return _ruta_cache()


def _activados() -> bool:
    """False si la config desactiva los embeddings (``embeddings_activos``)."""
    try:
        from miku.ajustes import carga as config_mod
        return bool(config_mod.config.embeddings_activos)
    except Exception:  # noqa: BLE001
        return True


def _cargar_modelo():
    """Carga (lazy) el modelo de fastembed. Devuelve el modelo o None.

    Respeta ``embeddings_activos`` (desactivado = ni se importa ni se descarga
    el modelo). Si falla la carga, reintenta pasado ``_ESPERA_REINTENTO``.
    """
    global _modelo, _sin_libreria, _fallo_en
    if _modelo is not None:
        return _modelo
    if _sin_libreria or not _activados():
        return None
    with _lock_modelo:
        if _modelo is not None:  # lo cargó otro hilo mientras esperábamos
            return _modelo
        if _fallo_en and (time.monotonic() - _fallo_en) < _ESPERA_REINTENTO:
            return None
        try:
            from fastembed import TextEmbedding  # import tardío (opcional)
        except Exception as e:  # noqa: BLE001
            logger.info("fastembed no disponible (%s): sin búsqueda semántica.", e)
            _sin_libreria = True
            return None
        try:
            # cache_dir deja el modelo en data/embeddings (no ensucia el repo).
            _modelo = TextEmbedding(model_name=_MODELO, cache_dir=_ruta_cache())
            _fallo_en = 0.0
            logger.info("Modelo de embeddings cargado (fastembed).")
            return _modelo
        except Exception as e:  # noqa: BLE001
            logger.warning("No pude cargar el modelo de embeddings (reintento en "
                           "%d s): %s", int(_ESPERA_REINTENTO), e)
            _fallo_en = time.monotonic()
            return None


def disponible() -> bool:
    """True si hay motor de embeddings listo para usar."""
    return _cargar_modelo() is not None


def embeber(texto: str) -> Optional[List[float]]:
    """Devuelve el vector de `texto` (o None si no hay motor / está vacío)."""
    texto = (texto or "").strip()
    if not texto:
        return None
    modelo = _cargar_modelo()
    if modelo is None:
        return None
    try:
        # fastembed.embed devuelve un generador de numpy arrays.
        vectores = list(modelo.embed([texto]))
        if not vectores:
            return None
        return [float(x) for x in vectores[0]]
    except Exception as e:  # noqa: BLE001
        logger.debug("Fallo embebiendo texto: %s", e)
        return None


def similitud(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    """Similitud coseno entre dos vectores (0.0 si alguno falta o falla)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        punto = sum(x * y for x, y in zip(a, b))
        return float(punto / (na * nb))
    except Exception:  # noqa: BLE001
        return 0.0