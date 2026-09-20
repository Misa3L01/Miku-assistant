"""Visión de pantalla: modelo retirado, créditos agotados, respaldo de Groq y elección de monitor."""
from __future__ import annotations

import pytest

from miku.ajustes import carga as config_mod
from miku.plugins.pantalla import vision as mod
from miku.plugins.pantalla.vision import Vision


def _ok(texto):
    return {"candidates": [{"content": {"parts": [{"text": texto}]}}]}


def _error(codigo, estado):
    return {"error": {"code": codigo, "status": estado, "message": "x"}}


@pytest.fixture
def vision(cfg, monkeypatch):
    cfg.valores.update(gemini_api_key="g", groq_api_key="gsk_test", gemini_modelo="gemini-2.0-flash")
    monkeypatch.setattr(config_mod, "config", cfg)
    v = Vision()
    v.gemini_llamadas, v.groq_llamadas, v.gemini_responde = [], [], [_ok("Veo un escritorio.")]
    v.groq_responde = "Veo VS Code."
    monkeypatch.setattr(v, "_capturar_comprimida", lambda bbox=None, **k: v.capturas.append(bbox) or "IMG")
    v.capturas = []

    def gemini(modelo, payload, clave):
        v.gemini_llamadas.append(modelo)
        return v.gemini_responde.pop(0) if v.gemini_responde else _error(500, "INTERNAL")

    monkeypatch.setattr(Vision, "_consultar", staticmethod(gemini))
    monkeypatch.setattr(v, "_via_groq", lambda pregunta, img: v.groq_llamadas.append(pregunta) or v.groq_responde)
    return v


def test_usa_gemini_y_avisa_que_la_captura_sale_a_google(vision):
    r = vision.ver_pantalla("¿qué ves?")
    assert "escritorio" in r and "Google" in r and not vision.groq_llamadas


def test_modelo_retirado_reintenta_con_el_alias(vision):
    vision.gemini_responde = [_error(404, "NOT_FOUND"), _ok("Con el alias funciona.")]
    assert "alias" in vision.ver_pantalla("x")
    assert vision.gemini_llamadas == ["gemini-2.0-flash", mod.MODELO_ALIAS]


def test_sin_creditos_sigue_con_groq_y_aparta_a_gemini(vision):
    vision.gemini_responde = [_error(402, "RESOURCE_EXHAUSTED")]
    r = vision.ver_pantalla("x")
    assert "VS Code" in r and "Groq" in r and vision.groq_llamadas == ["x"]
    vision.ver_pantalla("otra")                      # Gemini quedó apartado: ni se prueba
    assert len(vision.gemini_llamadas) == 1 and len(vision.groq_llamadas) == 2


def test_clave_rechazada_tambien_cae_a_groq(vision):
    vision.gemini_responde = [_error(403, "PERMISSION_DENIED")]
    assert "VS Code" in vision.ver_pantalla("x")


def test_si_todo_falla_el_mensaje_explica_el_problema(vision):
    vision.gemini_responde = [_error(402, "RESOURCE_EXHAUSTED")]
    vision.groq_responde = None
    assert "créditos" in vision.ver_pantalla("x")
    vision._gemini_caido_hasta = 0
    vision.gemini_responde = [_error(403, "PERMISSION_DENIED")]
    assert "clave" in vision.ver_pantalla("x")
    vision._gemini_caido_hasta = 0
    vision.gemini_responde = [_error(404, "NOT_FOUND"), _error(404, "NOT_FOUND")]
    assert "gemini-flash-latest" in vision.ver_pantalla("x")


def test_proveedor_forzado(vision, cfg):
    cfg.valores["vision_proveedor"] = "groq"
    assert "VS Code" in vision.ver_pantalla("x") and not vision.gemini_llamadas
    cfg.valores["vision_proveedor"] = "gemini"
    assert "escritorio" in vision.ver_pantalla("x") and len(vision.groq_llamadas) == 1


def test_sin_ninguna_clave_lo_dice(vision, cfg):
    cfg.valores.update(gemini_api_key="", groq_api_key="")
    assert "GEMINI_API_KEY" in vision.ver_pantalla("x")


def test_un_monitor_puntual_se_captura_solo_a_el(vision, monkeypatch):
    from miku.plugins.pantalla.captura import Captura
    monkeypatch.setattr(Captura, "_bbox_monitor", staticmethod(lambda m: (1920, 0, 3840, 1080) if m == 2 else None))
    vision.ver_pantalla("x", 2)
    assert vision.capturas[-1] == (1920, 0, 3840, 1080)
    r = vision.ver_pantalla("x", 9)
    assert r.intencion == "captura.monitor_invalido"


def test_el_monitor_viene_de_la_tool(vision, monkeypatch):
    vistos = []
    monkeypatch.setattr(vision, "ver_pantalla", lambda p, m=None: vistos.append((p, m)) or "ok")
    vision.manejar_tool("ver_pantalla", {"pregunta": "hola", "monitor": "2"}, {})
    vision.manejar_tool("ver_pantalla", {"monitor": "abc"}, {})
    assert vistos[0] == ("hola", 2) and vistos[1][1] is None
