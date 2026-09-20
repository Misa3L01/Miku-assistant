"""ControlTidal: comandos CDP, arranque y selección del ejecutable (sin TIDAL ni red reales)."""
from __future__ import annotations

import json

import pytest

from miku.plugins.multimedia import tidal_control as tc


class WsFalso:
    """Websocket de CDP falso: responde a cada comando con lo que devuelva ``respuestas``."""

    def __init__(self, respuestas, enviados):
        self.respuestas, self.enviados, self._ultimo = respuestas, enviados, None

    def send(self, texto):
        self._ultimo = json.loads(texto)
        self.enviados.append(self._ultimo)

    def recv(self):
        m = self._ultimo
        return json.dumps({"id": m["id"], "result": {"result": {"value": self.respuestas(m)}}})

    def close(self):
        pass


@pytest.fixture
def control(monkeypatch):
    enviados = []
    c = tc.ControlTidal("C:/x/TIDAL.exe", 9223, abrir_ws=lambda url: WsFalso(lambda m: c.responder(m), enviados))
    c.enviados, c.responder = enviados, (lambda m: None)
    monkeypatch.setattr(c, "vivo", lambda: True)
    monkeypatch.setattr(c, "_pagina", lambda: {"webSocketDebuggerUrl": "ws://x", "url": "https://desktop.tidal.com/"})
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    return c


def _js(c):
    return [m["params"].get("expression", "") for m in c.enviados if m["method"] == "Runtime.evaluate"]


def _expr(m):
    return m.get("params", {}).get("expression", "")


def test_evaluar_devuelve_el_valor(control):
    control.responder = lambda m: 42
    assert control.evaluar("1+1") == 42


def test_sin_pagina_o_sin_websocket_no_rompe(control, monkeypatch):
    monkeypatch.setattr(control, "_pagina", lambda: None)
    assert control.evaluar("1") is None
    monkeypatch.setattr(control, "_pagina", lambda: {"webSocketDebuggerUrl": "ws://x"})

    def rechaza(url):
        raise OSError("rechazado")

    control._abrir_ws = rechaza
    assert control.evaluar("1") is None


def test_reproducir_un_tema_navega_espera_y_aprieta_play(control):
    estado = {"navegado": False, "clic": False}

    def responder(m):
        if m["method"] == "Page.navigate":
            estado["navegado"] = True
            return None
        js = _expr(m)
        if "footer-track-title" in js:                     # ahora(): suena otro tema
            return {"titulo": "Otro", "artista": "X", "reproduciendo": True}
        if ".length > 0" in js:
            return estado["navegado"]                      # la ficha ya cargó
        if "b.click()" in js:
            estado["clic"] = True
            return True
        if "data-test=pause" in js:
            return estado["clic"]
        return None

    control.responder = responder
    assert control.reproducir("track", "313549858", "Show", "Ado") is True
    nav = [m for m in control.enviados if m["method"] == "Page.navigate"]
    assert nav[0]["params"]["url"] == "https://desktop.tidal.com/track/313549858"
    assert any("/track/313549858" in j for j in _js(control))       # busca la fila de ESE tema


def test_si_el_tema_ya_suena_no_navega(control):
    control.responder = lambda m: ({"titulo": "Show", "artista": "Ado", "reproduciendo": True}
                                   if "footer-track-title" in _expr(m) else None)
    assert control.reproducir("track", "1", "show", "ado") is True
    assert not [m for m in control.enviados if m["method"] == "Page.navigate"]


def test_si_el_tema_esta_en_pausa_aprieta_play_sin_navegar(control):
    estado = {"suena": False}

    def responder(m):
        js = _expr(m)
        if "footer-track-title" in js:
            return {"titulo": "Show", "artista": "Ado", "reproduciendo": estado["suena"]}
        if "b.click()" in js:
            estado["suena"] = True
            return True
        if "data-test=pause" in js:
            return estado["suena"]
        return None

    control.responder = responder
    assert control.reproducir("track", "1", "Show", "Ado") is True
    assert not [m for m in control.enviados if m["method"] == "Page.navigate"]


def test_si_la_ficha_no_muestra_el_boton_devuelve_false(control, monkeypatch):
    control.responder = lambda m: None
    reloj = iter(range(0, 1000, 5))
    monkeypatch.setattr(tc.time, "monotonic", lambda: next(reloj))
    assert control.reproducir("album", "9") is False


def test_tipo_invalido(control):
    assert control.reproducir("video", "1") is False


@pytest.mark.parametrize("nombre,fragmento", [("siguiente", "next"), ("anterior", "previous"),
                                              ("play_pausa", "pause")])
def test_botones_del_reproductor(control, nombre, fragmento):
    control.responder = lambda m: True
    assert control.boton(nombre) is True
    assert fragmento in _js(control)[-1]
    assert control.boton("inventado") is False


def test_ahora_solo_acepta_un_dict(control):
    control.responder = lambda m: {"titulo": "A", "artista": "B", "reproduciendo": False}
    assert control.ahora() == {"titulo": "A", "artista": "B", "reproduciendo": False}
    control.responder = lambda m: "raro"
    assert control.ahora() is None


# ----------------------------------------------------------------- arranque
def test_asegurar_no_hace_nada_si_ya_esta_listo(control):
    assert control.asegurar() == tc.LISTO


def test_asegurar_sin_exe(monkeypatch):
    c = tc.ControlTidal("", 9223)
    monkeypatch.setattr(c, "vivo", lambda: False)
    assert c.asegurar() == tc.SIN_EXE
    c.ruta_exe = "C:/no/existe/TIDAL.exe"
    assert c.asegurar() == tc.SIN_EXE


def test_asegurar_reinicia_tidal_si_estaba_sin_puerto(tmp_path, monkeypatch):
    exe = tmp_path / "TIDAL.exe"
    exe.write_text("x")
    c = tc.ControlTidal(str(exe), 9223)
    acciones = []
    estado = {"vivo": False}

    def lanzar(e):
        acciones.append(("lanzar", e))
        estado["vivo"] = True

    monkeypatch.setattr(c, "vivo", lambda: estado["vivo"])
    monkeypatch.setattr(c, "_pagina", lambda: {"url": "https://desktop.tidal.com"} if estado["vivo"] else None)
    monkeypatch.setattr(c, "evaluar", lambda js: True)
    monkeypatch.setattr(tc.ControlTidal, "_corriendo", staticmethod(lambda: True))
    monkeypatch.setattr(tc.ControlTidal, "_cerrar_tidal", staticmethod(lambda: acciones.append("cerrar")))
    monkeypatch.setattr(c, "_lanzar", lanzar)
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert c.asegurar() == tc.LISTO
    assert acciones == ["cerrar", ("lanzar", str(exe))]


def test_asegurar_no_arranca(tmp_path, monkeypatch):
    exe = tmp_path / "TIDAL.exe"
    exe.write_text("x")
    c = tc.ControlTidal(str(exe), 9223)
    monkeypatch.setattr(c, "vivo", lambda: False)
    monkeypatch.setattr(tc.ControlTidal, "_corriendo", staticmethod(lambda: False))
    monkeypatch.setattr(c, "_lanzar", lambda e: None)
    reloj = iter(range(0, 10000, 10))
    monkeypatch.setattr(tc.time, "monotonic", lambda: next(reloj))
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert c.asegurar(espera=30) == tc.NO_ARRANCA


def test_exe_real_elige_la_version_mas_nueva(tmp_path):
    for v in ("app-2.42.1", "app-2.43.2", "app-2.9.0"):
        (tmp_path / v).mkdir()
        (tmp_path / v / "TIDAL.exe").write_text("x")
    (tmp_path / "app-3.0.0").mkdir()                     # sin exe: no cuenta
    lanzador = tmp_path / "TIDAL.exe"
    lanzador.write_text("stub")
    assert tc.exe_real(str(lanzador)) == str(tmp_path / "app-2.43.2" / "TIDAL.exe")
    assert tc.exe_real(str(tmp_path / "otra" / "TIDAL.exe")) == str(tmp_path / "otra" / "TIDAL.exe")


def test_el_entorno_de_lanzamiento_no_lleva_electron_run_as_node(monkeypatch):
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    monkeypatch.setenv("OTRA", "x")
    env = tc.entorno_sin_electron()
    assert "ELECTRON_RUN_AS_NODE" not in env and env["OTRA"] == "x"


def test_lanzar_usa_el_entorno_limpio_y_el_puerto(monkeypatch):
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    visto = {}
    monkeypatch.setattr(tc.subprocess, "Popen", lambda args, **kw: visto.update(args=args, **kw))
    tc.ControlTidal("x", 9333)._lanzar("C:/t/TIDAL.exe")
    assert visto["args"] == ["C:/t/TIDAL.exe", "--remote-debugging-port=9333"]
    assert "ELECTRON_RUN_AS_NODE" not in visto["env"]


def test_avisa_si_falta_una_libreria_de_lo_configurado(monkeypatch):
    from miku.ajustes import validacion
    monkeypatch.setattr("importlib.util.find_spec", lambda m: None)
    faltan = validacion.dependencias_faltantes({"telegram_bot_token": "x", "tidal_ruta_exe": "C:/t.exe"})
    assert {"python-telegram-bot", "tidalapi", "websocket-client"} <= {p for _, p in faltan}
    assert validacion.dependencias_faltantes({}) == []
