"""Modo gaming: la entrada es inmediata y la salida espera unos segundos (un alt-tab corto no cuenta)."""
from __future__ import annotations

import pytest

from miku.ajustes import carga as config_mod
from miku.plugins.gaming import game_booster as gb


class _Detener:
    """Reemplaza al Event del plugin: cada ``wait`` adelanta el reloj falso y, tras los pasos, corta el bucle."""

    def __init__(self, reloj, intervalo, primer_plano):
        self.reloj, self.intervalo, self.primer_plano, self.pasos = reloj, intervalo, primer_plano, 0

    def is_set(self):
        return self.pasos >= len(self.primer_plano)

    def wait(self, segundos):
        self.reloj[0] += segundos
        self.pasos += 1


@pytest.fixture
def booster(monkeypatch, cfg):
    cfg.valores["juegos_booster"] = ["cs2"]
    monkeypatch.setattr(config_mod, "config", cfg)
    b = gb.GameBooster()
    b.eventos = []
    monkeypatch.setattr(b, "_activar", lambda p: (b.eventos.append(("entra", p)), setattr(b, "_juego_activo", p)))
    monkeypatch.setattr(b, "_desactivar", lambda: (b.eventos.append(("sale",)), setattr(b, "_juego_activo", None),
                                                    setattr(b, "_fuera_desde", None)))
    return b


def _correr(b, monkeypatch, primer_plano, intervalo=3.0):
    """Recorre el bucle con un primer plano por paso (cada paso = ``intervalo`` s) y devuelve los eventos."""
    reloj = [1000.0]
    monkeypatch.setattr(gb.time, "monotonic", lambda: reloj[0])
    monkeypatch.setattr(b, "_proceso_primer_plano", lambda: primer_plano[b._detener.pasos])
    b._detener = _Detener(reloj, intervalo, primer_plano)
    b._bucle()
    return b.eventos


def test_un_alt_tab_corto_no_saca_del_modo_gaming(booster, monkeypatch):
    # 3 s jugando, 3 s en el navegador (en la 1ª revisión fuera y en la 2ª ya volvió): no se sale.
    eventos = _correr(booster, monkeypatch, ["cs2", "brave", "brave", "cs2", "cs2"])
    assert eventos == [("entra", "cs2")]


def test_un_alt_tab_de_casi_diez_segundos_tampoco(booster, monkeypatch):
    eventos = _correr(booster, monkeypatch, ["cs2", "brave", "brave", "brave", "cs2"])   # 9 s fuera
    assert eventos == [("entra", "cs2")]


def test_fuera_mas_de_diez_segundos_si_sale(booster, monkeypatch):
    eventos = _correr(booster, monkeypatch, ["cs2", "brave", "brave", "brave", "brave", "brave"])
    assert eventos == [("entra", "cs2"), ("sale",)]


def test_volver_al_juego_reinicia_la_cuenta(booster, monkeypatch):
    # 9 s fuera, vuelve 3 s, y otra vez 9 s fuera: son dos salidas cortas, no una larga.
    fuera = ["brave"] * 4
    eventos = _correr(booster, monkeypatch, ["cs2"] + fuera + ["cs2"] + fuera + ["cs2"])
    assert eventos == [("entra", "cs2")]


def test_la_entrada_es_inmediata(booster, monkeypatch):
    eventos = _correr(booster, monkeypatch, ["brave", "cs2"])
    assert eventos == [("entra", "cs2")]


def test_la_espera_se_puede_cambiar(booster, monkeypatch, cfg):
    cfg.valores["booster_espera_salida"] = 3
    eventos = _correr(booster, monkeypatch, ["cs2", "brave", "brave"])
    assert eventos == [("entra", "cs2"), ("sale",)]


@pytest.mark.parametrize("valor,esperado", [(None, 10.0), ("rara", 10.0), (-5, 0.0), (25, 25.0)])
def test_la_espera_acepta_solo_valores_validos(monkeypatch, cfg, valor, esperado):
    cfg.valores["booster_espera_salida"] = valor
    monkeypatch.setattr(config_mod, "config", cfg)
    assert gb.GameBooster._espera_salida() == esperado
