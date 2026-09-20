"""Envío de archivos por Telegram (la API de Telegram se simula: nada sale de la PC)."""
from __future__ import annotations

import os
import time

import pytest
import requests

from miku.ajustes import carga as config_mod
from miku.plugins.social import telegram_envio as mod
from miku.plugins.social.telegram_envio import TelegramEnvio


@pytest.fixture
def envio(cfg, tmp_path, monkeypatch):
    capturas = tmp_path / "capturas"
    capturas.mkdir()
    cfg.valores.update(telegram_bot_token="123:ABC", telegram_chat_id="777", carpeta_capturas=str(capturas),
                       telegram_contactos={"Juan": "555"})
    monkeypatch.setattr(config_mod, "config", cfg)
    enviados = []

    class R:
        def __init__(self, ok=True):
            self.ok = ok

        def json(self):
            return {"ok": self.ok, "description": "chat not found"}

    estado = {"ok": True}

    def post(url, data=None, files=None, timeout=None):
        campo = next(iter(files))
        enviados.append({"url": url, "chat": data["chat_id"], "campo": campo, "nombre": files[campo][0]})
        return R(estado["ok"])

    monkeypatch.setattr(requests, "post", post)
    p = TelegramEnvio()
    p.enviados, p.capturas, p.estado, p.tmp = enviados, capturas, estado, tmp_path
    return p


def _archivo(carpeta, nombre, antiguedad=0, tamano=10):
    ruta = carpeta / nombre
    ruta.write_bytes(b"x" * tamano)
    t = time.time() - antiguedad
    os.utime(ruta, (t, t))
    return ruta


def test_manda_la_ultima_captura_como_foto_a_tu_chat(envio):
    _archivo(envio.capturas, "vieja.png", antiguedad=500)
    _archivo(envio.capturas, "nueva.png", antiguedad=5)
    r = envio.manejar_tool("enviar_a_telegram", {"archivo": "la ultima captura"}, {})
    assert r.ok and r.intencion == "telegram.enviado" and "nueva.png" in r
    (e,) = envio.enviados
    assert e["chat"] == "777" and e["campo"] == "photo" and e["nombre"] == "nueva.png"
    assert e["url"].endswith("/sendPhoto") and "123:ABC" in e["url"]


def test_sin_argumento_es_la_ultima_captura(envio):
    _archivo(envio.capturas, "a.png")
    assert envio.manejar_tool("enviar_a_telegram", {}, {}).ok


def test_un_archivo_que_no_es_imagen_va_como_documento(envio):
    ruta = _archivo(envio.tmp, "informe.pdf")
    envio.manejar_tool("enviar_a_telegram", {"archivo": str(ruta)}, {})
    assert envio.enviados[0]["campo"] == "document"


def test_una_imagen_muy_grande_va_como_documento(envio):
    ruta = _archivo(envio.tmp, "enorme.png", tamano=mod._MAX_FOTO_BYTES + 1)
    envio.enviar(str(ruta), None)
    assert envio.enviados[0]["campo"] == "document"


def test_la_ultima_descarga(envio, monkeypatch):
    descargas = envio.tmp / "Downloads"
    descargas.mkdir()
    _archivo(descargas, "a.zip", antiguedad=100)
    _archivo(descargas, "b.zip", antiguedad=1)
    _archivo(descargas, "c.crdownload", antiguedad=0)              # a medio bajar: no cuenta
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: envio.tmp))
    envio.enviar("mi ultima descarga", None)
    assert envio.enviados[0]["nombre"] == "b.zip"


def test_archivos_que_no_existen_y_limites(envio, monkeypatch):
    assert envio.enviar("ultima captura", None).intencion == "telegram.archivo_no_encontrado"   # carpeta vacía
    assert envio.enviar("no_existe_para_nada.xyz", None).intencion == "telegram.archivo_no_encontrado"
    ruta = _archivo(envio.tmp, "gigante.bin")
    monkeypatch.setattr(mod, "_MAX_BYTES", 5)
    r = envio.enviar(str(ruta), None)
    assert r.intencion == "telegram.archivo_grande" and not envio.enviados


def test_sin_token_o_sin_chat(envio, cfg):
    _archivo(envio.capturas, "a.png")
    cfg.valores["telegram_chat_id"] = ""
    assert envio.enviar("captura", None).intencion == "telegram.sin_chat"
    cfg.valores["telegram_bot_token"] = ""
    assert envio.enviar("captura", None).intencion == "telegram.sin_token"
    assert not envio.enviados


def test_a_un_contacto_es_peligroso_y_usa_su_id(envio):
    assert "enviar_a_contacto_telegram" in envio.peligrosas and "enviar_a_telegram" not in envio.peligrosas
    _archivo(envio.capturas, "a.png")
    r = envio.manejar_tool("enviar_a_contacto_telegram", {"contacto": "juan", "archivo": "captura"}, {})
    assert r.ok and " a juan" in r and envio.enviados[0]["chat"] == "555"
    assert envio.manejar_tool("enviar_a_contacto_telegram", {"contacto": "pedro"}, {}).intencion == "telegram.sin_contacto"


def test_errores_de_red_y_rechazo(envio, monkeypatch):
    _archivo(envio.capturas, "a.png")
    envio.estado["ok"] = False
    r = envio.enviar("captura", None)
    assert r.intencion == "telegram.rechazado" and "chat not found" in r

    def cae(*a, **k):
        raise OSError("sin red")

    monkeypatch.setattr(requests, "post", cae)
    assert envio.enviar("captura", None).intencion == "telegram.error_red"


def test_la_confirmacion_menciona_a_quien_y_que(parser):
    pregunta = parser._encolar_confirmacion("enviar_a_contacto_telegram", {"contacto": "Juan", "archivo": "la última captura"})
    assert "Juan" in pregunta and "captura" in pregunta and "Telegram" in pregunta


def test_el_plugin_se_carga_solo_con_token(cfg):
    from miku.plugins import registro
    assert "telegram_envio" not in {p.nombre for p in registro.instanciar_plugins(cfg)}
    cfg.valores["telegram_bot_token"] = "x"
    assert "telegram_envio" in {p.nombre for p in registro.instanciar_plugins(cfg)}
