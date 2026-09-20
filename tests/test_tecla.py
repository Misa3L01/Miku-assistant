"""Tecla de invocación configurable y detección de teclas especiales (teclado simulado)."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from miku.servicios import arranque, tecla


def ev(nombre, scan, tipo="down"):
    return SimpleNamespace(name=nombre, scan_code=scan, event_type=tipo, is_keypad=False, device=None)


class TecladoFalso:
    """Módulo ``keyboard`` falso: ``eventos`` se entregan al engancharse."""

    def __init__(self, eventos=()):
        self.eventos, self.altas, self.bajas = list(eventos), [], []
        self.enganchado = None

    def hook(self, cb):
        self.enganchado = cb
        for e in self.eventos:
            cb(e)
        return "h"

    def unhook(self, h):
        self.enganchado = None

    def add_hotkey(self, t, cb):
        self.altas.append(t)
        return f"hk-{len(self.altas)}"

    def remove_hotkey(self, h):
        self.bajas.append(h)


@pytest.mark.parametrize("valor,esperado", [("f22", "f22"), ("F13", "f13"), ("ctrl+alt+m", "ctrl+alt+m"),
                                            ("sc:57", 57), ("", "f22"), ("sc:xx", "f22")])
def test_a_hotkey(valor, esperado):
    assert tecla.a_hotkey(valor) == esperado


def test_clave_de_evento_usa_el_nombre_o_el_scan_code():
    assert tecla.clave_de_evento(ev("f13", 100)) == "f13"
    assert tecla.clave_de_evento(ev(None, 57)) == "sc:57"
    assert tecla.clave_de_evento(ev("", 57)) == "sc:57"
    assert tecla.clave_de_evento(ev("187", 57)) == "sc:57"          # solo un número: no es un nombre


def test_detectar_devuelve_la_primera_tecla_que_se_aprieta():
    assert tecla.detectar(1, TecladoFalso([ev("a", 30, "up"), ev(None, 57), ev("b", 48)])) == "sc:57"


def test_detectar_sin_teclas_devuelve_none():
    assert tecla.detectar(0.05, TecladoFalso()) is None


def test_nombres_legibles():
    assert tecla.nombre_legible("f22") == "F22" and "57" in tecla.nombre_legible("sc:57")


def test_la_descripcion_muestra_todo_para_diagnosticar():
    d = tecla.descripcion_evento(ev(None, 57))
    assert "scan_code=57" in d and "sc:57" in d


# ---------------------------------------------------------------- integración con la app
@pytest.fixture
def teclado(monkeypatch):
    falso = TecladoFalso()
    monkeypatch.setitem(sys.modules, "keyboard", falso)
    return falso


@pytest.fixture
def asistente(cfg, monkeypatch):
    from miku import app
    a = app.Asistente(cfg)
    dichos = []
    monkeypatch.setattr(a, "decir", dichos.append)
    a.dichos = dichos
    monkeypatch.setattr(arranque, "atajo_f22_activo", lambda ruta=None: False)
    return a


def test_otra_tecla_se_registra_siempre_dentro_de_miku(asistente, teclado, cfg, monkeypatch):
    cfg.valores["tecla_invocar"] = "sc:57"
    monkeypatch.setattr(arranque, "atajo_f22_activo", lambda ruta=None: True)     # el atajo es solo de F22
    asistente.actualizar_hotkey_f22()
    assert teclado.altas == [57]


def test_cambiar_de_tecla_reemplaza_el_hotkey(asistente, teclado, cfg):
    asistente.actualizar_hotkey_f22()
    cfg.valores["tecla_invocar"] = "f13"
    asistente.actualizar_hotkey_f22()
    assert teclado.altas == ["f22", "f13"] and teclado.bajas == ["hk-1"]


def test_elegir_tecla_guarda_activa_y_avisa(asistente, teclado, cfg, monkeypatch):
    guardado = []
    monkeypatch.setattr(cfg, "guardar_preferencias", lambda d: guardado.append(d) or cfg.valores.update(d) or True)
    monkeypatch.setattr(tecla, "detectar", lambda segundos, teclado=None: "sc:57")
    assert asistente.elegir_tecla() == "sc:57"
    assert guardado == [{"tecla_invocar": "sc:57"}] and teclado.altas == [57]
    assert "código 57" in asistente.dichos[-1]


def test_elegir_tecla_sin_deteccion_no_cambia_nada(asistente, teclado, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "guardar_preferencias", lambda d: pytest.fail("no debe guardar"))
    monkeypatch.setattr(tecla, "detectar", lambda segundos, teclado=None: None)
    assert asistente.elegir_tecla() is None and not teclado.altas
    assert "No detecté" in asistente.dichos[-1]
