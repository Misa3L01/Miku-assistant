"""Snapshot de "salir del modo" persistente y resolución nativa (todo simulado: no toca la pantalla real)."""
from __future__ import annotations

import json
import time

import pytest

from miku.plataforma import pantalla
from miku.servicios import modos
from miku.voz.frases.respuesta import hubo_falla


@pytest.fixture
def sin_hardware(monkeypatch):
    """Capturas y restauraciones falsas; registra qué se restauró."""
    restaurado = []
    monkeypatch.setattr(modos, "_capturar_volumen", lambda: {"nivel": 0.7, "mute": False})
    monkeypatch.setattr(modos, "_capturar_resolucion", lambda: {"ancho": 1920, "alto": 1080})
    monkeypatch.setattr(modos, "_capturar_brillo", lambda: 60)
    monkeypatch.setattr(modos, "_restaurar_volumen", lambda d: restaurado.append(("volumen", d)) or True)
    monkeypatch.setattr(modos, "_restaurar_resolucion",
                        lambda a, b: restaurado.append(("resolucion", (a, b))) or True)
    monkeypatch.setattr(modos, "_restaurar_brillo", lambda n: restaurado.append(("brillo", n)) or True)
    return restaurado


def _reiniciar_proceso(monkeypatch):
    """Simula cerrar y abrir Miku: el estado en memoria desaparece, el del disco queda."""
    monkeypatch.setattr(modos, "_snapshot", None)
    monkeypatch.setattr(modos, "_cargado", False)


def test_capturar_guarda_en_disco(sin_hardware):
    modos.capturar()
    datos = json.loads(modos.RUTA_SNAPSHOT.read_text(encoding="utf-8"))
    assert datos["snapshot"]["resolucion"] == {"ancho": 1920, "alto": 1080}
    assert abs(datos["capturado"] - time.time()) < 5


def test_el_snapshot_sobrevive_a_reiniciar_miku(sin_hardware, monkeypatch):
    modos.capturar()
    _reiniciar_proceso(monkeypatch)
    assert modos.hay_snapshot()
    r = modos.salir_modo()
    assert r.ok and r.intencion == "modo.restaurado"
    assert ("resolucion", (1920, 1080)) in sin_hardware
    assert not modos.RUTA_SNAPSHOT.exists() and not modos.hay_snapshot()


def test_no_se_pisa_un_snapshot_recuperado(sin_hardware, monkeypatch):
    modos.capturar()
    _reiniciar_proceso(monkeypatch)
    monkeypatch.setattr(modos, "_capturar_resolucion", lambda: {"ancho": 800, "alto": 600})
    assert modos.capturar_si_libre() is False        # el booster no debe adueñarse del punto de partida
    assert modos.capturar()["resolucion"]["ancho"] == 1920


def test_un_snapshot_viejo_se_descarta(sin_hardware, monkeypatch):
    modos.capturar()
    datos = json.loads(modos.RUTA_SNAPSHOT.read_text(encoding="utf-8"))
    datos["capturado"] = time.time() - (modos.VIGENCIA_H + 1) * 3600
    modos.RUTA_SNAPSHOT.write_text(json.dumps(datos), encoding="utf-8")
    _reiniciar_proceso(monkeypatch)
    assert not modos.hay_snapshot() and not modos.RUTA_SNAPSHOT.exists()


@pytest.mark.parametrize("contenido", ["no es json", '{"capturado": 1}', '{"capturado": "x", "snapshot": 3}', ""])
def test_un_archivo_roto_se_ignora(contenido, monkeypatch):
    modos.RUTA_SNAPSHOT.write_text(contenido, encoding="utf-8")
    _reiniciar_proceso(monkeypatch)
    assert not modos.hay_snapshot()


def test_salir_sin_modo_no_toca_nada(sin_hardware):
    r = modos.salir_modo()
    assert hubo_falla(r) and r.intencion == "modo.sin_modo" and not sin_hardware


def test_restauracion_parcial(sin_hardware, monkeypatch):
    modos.capturar()
    monkeypatch.setattr(modos, "_restaurar_brillo", lambda n: False)
    r = modos.salir_modo()
    assert hubo_falla(r) and r.intencion == "modo.restauracion_parcial" and "brillo" in r


def test_limpiar_borra_tambien_el_archivo(sin_hardware):
    modos.capturar()
    modos.limpiar()
    assert not modos.RUTA_SNAPSHOT.exists() and not modos.hay_snapshot()


# ------------------------------------------------------------- resolución nativa
class _Monitor:
    """EnumDisplaySettingsW falso con una lista de modos."""

    def __init__(self, modos_soportados, actual):
        self.modos, self.actual = modos_soportados, actual

    def EnumDisplaySettingsW(self, _dispositivo, indice, ref):     # noqa: N802 - API de Windows
        lista = self.modos if indice >= 0 else [self.actual]
        i = 0 if indice < 0 else indice
        if i >= len(lista):
            return 0
        ref._obj.dmPelsWidth, ref._obj.dmPelsHeight = lista[i]
        return 1


def test_resolucion_nativa_es_la_mayor(monkeypatch):
    monkeypatch.setattr(pantalla, "_user32",
                        lambda: _Monitor([(800, 600), (2560, 1440), (1920, 1440), (1920, 1080)], (1920, 1440)))
    assert pantalla.resolucion_nativa() == (2560, 1440)


def test_resolucion_nativa_sin_acceso(monkeypatch):
    def falla():
        raise OSError("sin user32")
    monkeypatch.setattr(pantalla, "_user32", falla)
    assert pantalla.resolucion_nativa() is None


def test_volver_a_nativa_cambia_solo_si_hace_falta(monkeypatch):
    cambios = []
    monkeypatch.setattr(pantalla, "resolucion_nativa", lambda: (1920, 1080))
    monkeypatch.setattr(pantalla, "resolucion_actual", lambda: (1920, 1440))
    monkeypatch.setattr(pantalla, "cambiar_resolucion", lambda a, b: cambios.append((a, b)) or (pantalla.OK, 0))
    r = modos.volver_a_resolucion_nativa()
    assert r.ok and r.intencion == "modo.nativa_aplicada" and cambios == [(1920, 1080)]

    monkeypatch.setattr(pantalla, "resolucion_actual", lambda: (1920, 1080))
    cambios.clear()
    assert modos.volver_a_resolucion_nativa().intencion == "modo.ya_nativa" and not cambios


def test_volver_a_nativa_errores(monkeypatch):
    monkeypatch.setattr(pantalla, "resolucion_nativa", lambda: None)
    assert modos.volver_a_resolucion_nativa().intencion == "modo.sin_resolucion_nativa"
    monkeypatch.setattr(pantalla, "resolucion_nativa", lambda: (1920, 1080))
    monkeypatch.setattr(pantalla, "resolucion_actual", lambda: (1024, 768))
    monkeypatch.setattr(pantalla, "cambiar_resolucion", lambda a, b: (pantalla.NO_SOPORTADA, -2))
    assert hubo_falla(modos.volver_a_resolucion_nativa())


def test_la_resolucion_nativa_real_es_legible():
    nativa = pantalla.resolucion_nativa()             # solo lectura
    actual = pantalla.resolucion_actual()
    assert nativa is None or (actual is None or nativa[0] * nativa[1] >= actual[0] * actual[1])
