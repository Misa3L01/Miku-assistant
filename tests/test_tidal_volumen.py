"""Volumen interno de TIDAL: se lee del log del reproductor y se cambia con Ctrl+flechas por CDP."""
from __future__ import annotations

import pytest

from miku.plugins.multimedia import tidal_control as tc
from miku.plugins.multimedia.tidal import Tidal


def _linea(v):
    return f'(Sun Sep 20 2026 12:05:21) [TRACE] -  Console recieved:  {{"command":"media.volume","volume":{v}}}\n'


@pytest.fixture
def control(tmp_path, monkeypatch):
    log = tmp_path / "player.log"
    log.write_text("ruido\n" + _linea(30) + "más ruido\n", encoding="utf-8")
    c = tc.ControlTidal("x", 9223, ruta_log_player=log)
    c.log, c.teclas = log, []
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)

    def llamar(metodo, params=None):
        if metodo == "Input.dispatchKeyEvent" and params["type"] == "rawKeyDown":
            subir = params["key"] == "ArrowUp"
            c.teclas.append("arriba" if subir else "abajo")
            actual = c.volumen_actual() or 0
            nuevo = max(0, min(100, actual + (10 if subir else -10)))
            with open(log, "a", encoding="utf-8") as f:            # TIDAL escribe el nuevo volumen en su log
                f.write(_linea(nuevo))
        return {}

    monkeypatch.setattr(c, "_llamar", llamar)
    monkeypatch.setattr(c, "vivo", lambda: True)
    return c


def test_lee_el_ultimo_volumen_del_log(control):
    assert control.volumen_actual() == 30
    control.log.write_text(_linea(30) + _linea(70), encoding="utf-8")
    assert control.volumen_actual() == 70


def test_sin_log_o_sin_volumen_devuelve_none(tmp_path):
    assert tc.ControlTidal("x", ruta_log_player=tmp_path / "no_existe.log").volumen_actual() is None
    vacio = tmp_path / "v.log"
    vacio.write_text("nada\n", encoding="utf-8")
    assert tc.ControlTidal("x", ruta_log_player=vacio).volumen_actual() is None


def test_cambiar_volumen_por_pasos_de_diez(control):
    assert control.cambiar_volumen(2) == 50 and control.teclas == ["arriba", "arriba"]
    control.teclas.clear()
    assert control.cambiar_volumen(-3) == 20 and control.teclas == ["abajo"] * 3


def test_fijar_volumen_calcula_los_pasos(control):
    assert control.fijar_volumen(60) == 60 and len(control.teclas) == 3
    control.teclas.clear()
    assert control.fijar_volumen(60) == 60 and control.teclas == []           # ya estaba
    assert control.fijar_volumen(400) == 100                                    # se acota
    assert control.fijar_volumen(-5) == 0


def test_fijar_sin_poder_leer_el_volumen(tmp_path):
    c = tc.ControlTidal("x", ruta_log_player=tmp_path / "no.log")
    assert c.fijar_volumen(50) is None


# ------------------------------------------------------------------ plugin
class ControlFalso:
    def __init__(self):
        self.en_vivo, self.nivel, self.llamadas = True, 30, []

    def vivo(self):
        return self.en_vivo

    def cambiar_volumen(self, pasos):
        self.llamadas.append(("cambiar", pasos))
        self.nivel += pasos * 10
        return self.nivel

    def fijar_volumen(self, v):
        self.llamadas.append(("fijar", v))
        self.nivel = v
        return v


@pytest.fixture
def tidal():
    t = Tidal()
    t._control = ControlFalso()
    return t


def test_subir_y_bajar_por_defecto_diez_puntos(tidal):
    r = tidal.manejar_tool("volumen_tidal", {"accion": "subir"}, {})
    assert r.ok and "40" in r and tidal._control.llamadas == [("cambiar", 1)]
    tidal.manejar_tool("volumen_tidal", {"accion": "bajar", "valor": 20}, {})
    assert tidal._control.llamadas[-1] == ("cambiar", -2)


def test_fijar_el_volumen(tidal):
    r = tidal.manejar_tool("volumen_tidal", {"accion": "fijar", "valor": 55}, {})
    assert r.ok and "55" in r
    assert tidal.manejar_tool("volumen_tidal", {"accion": "fijar"}, {}).intencion == "tidal.volumen_sin_valor"
    assert tidal.manejar_tool("volumen_tidal", {"accion": "girar"}, {}).intencion == "tidal.volumen_sin_valor"


def test_sin_control_de_tidal_explica_que_falta(tidal):
    tidal._control.en_vivo = False
    r = tidal.manejar_tool("volumen_tidal", {"accion": "subir"}, {})
    assert r.intencion == "tidal.volumen_sin_control" and not tidal._control.llamadas


def test_el_valor_del_llm_puede_venir_raro(tidal):
    assert tidal.manejar_tool("volumen_tidal", {"accion": "subir", "valor": "mucho"}, {}).ok
    assert tidal._control.llamadas == [("cambiar", 1)]
