"""Caché de audio en disco del TTS: guardar, recuperar, descartar lo dañado y podar lo menos usado."""
from __future__ import annotations

import os

import pytest

from miku.voz.salida.cache_audio import CacheAudio

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20


@pytest.fixture
def cache(tmp_path):
    return CacheAudio(tmp_path / "cache", max_archivos=10, max_caracteres=40)


def test_lo_guardado_se_recupera(cache):
    assert cache.obtener("Listo", "vv1") is None
    assert cache.guardar("Listo", "vv1", WAV) is True
    assert cache.obtener("Listo", "vv1") == WAV


def test_depende_del_texto_y_de_la_voz(cache):
    cache.guardar("Listo", "vv1", WAV)
    assert cache.obtener("Listo", "vv2") is None, "otra voz no reutiliza el audio"
    assert cache.obtener("Ya está", "vv1") is None
    assert cache.obtener("  Listo  ", "vv1") == WAV, "los espacios de los bordes no cuentan"


def test_no_guarda_lo_largo_ni_lo_que_no_es_un_wav(cache):
    assert cache.guardar("x" * 41, "vv1", WAV) is False
    assert cache.guardar("Listo", "vv1", b"esto no es audio") is False
    assert cache.guardar("Listo", "vv1", b"") is False
    assert cache.guardar("   ", "vv1", WAV) is False
    assert not list(cache.carpeta.glob("*.wav")) if cache.carpeta.exists() else True


def test_una_entrada_danada_se_descarta(cache):
    cache.guardar("Listo", "vv1", WAV)
    archivo = next(cache.carpeta.glob("*.wav"))
    archivo.write_bytes(b"basura")
    assert cache.obtener("Listo", "vv1") is None
    assert not archivo.exists(), "se borra para no volver a leerla"


def test_al_pasarse_del_tope_borra_las_menos_usadas(cache):
    for i in range(10):
        cache.guardar(f"frase {i}", "vv1", WAV)
    # frase 0 es la más vieja, salvo que la usemos: entonces se salva.
    for n, archivo in enumerate(sorted(cache.carpeta.glob("*.wav"))):
        os.utime(archivo, (1000 + n, 1000 + n))
    cache.obtener("frase 0", "vv1")                       # la "usa" ahora
    cache.guardar("frase nueva", "vv1", WAV)              # 11 entradas > tope 10: poda al 90 % (9)
    quedan = list(cache.carpeta.glob("*.wav"))
    assert len(quedan) == 9
    assert cache.obtener("frase 0", "vv1") == WAV, "la recién usada sobrevive a la poda"
    assert cache.obtener("frase nueva", "vv1") == WAV


def test_no_deja_archivos_temporales(cache):
    cache.guardar("Listo", "vv1", WAV)
    assert [p.suffix for p in cache.carpeta.iterdir()] == [".wav"]


def test_una_carpeta_imposible_no_rompe(tmp_path):
    ocupado = tmp_path / "archivo"
    ocupado.write_text("soy un archivo, no una carpeta")
    c = CacheAudio(ocupado / "sub")
    assert c.guardar("Listo", "vv1", WAV) is False and c.obtener("Listo", "vv1") is None
