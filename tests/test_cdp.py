"""Navegador propio por CDP: minimizar la ventana y argumentos de lanzamiento (sin abrir nada de verdad)."""
from __future__ import annotations

from pathlib import Path

from miku.plataforma import cdp


class PaginaFalsa:
    def __init__(self, respuestas):
        self.id, self.respuestas, self.llamadas, self.cerrada = "T1", respuestas, [], False

    def llamar(self, metodo, params=None):
        self.llamadas.append((metodo, params))
        return self.respuestas.get(metodo, {})

    def cerrar(self):
        self.cerrada = True


def _nav(tmp_path, **kw):
    return cdp.Navegador("C:/x/brave.exe", tmp_path / "perfil", 9999, **kw)


def test_minimizar_pide_la_ventana_del_objetivo_y_la_minimiza(tmp_path, monkeypatch):
    pag = PaginaFalsa({"Browser.getWindowForTarget": {"result": {"windowId": 7}},
                       "Browser.setWindowBounds": {"result": {}}})
    n = _nav(tmp_path)
    monkeypatch.setattr(n, "pagina", lambda: pag)
    assert n.minimizar() is True and pag.cerrada
    assert pag.llamadas[0] == ("Browser.getWindowForTarget", {"targetId": "T1"})
    assert pag.llamadas[1] == ("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "minimized"}})


def test_minimizar_sin_ventana_o_con_error_devuelve_false(tmp_path, monkeypatch):
    n = _nav(tmp_path)
    monkeypatch.setattr(n, "pagina", lambda: None)
    assert n.minimizar() is False
    pag = PaginaFalsa({})                                  # el navegador no responde
    monkeypatch.setattr(n, "pagina", lambda: pag)
    assert n.minimizar() is False
    pag2 = PaginaFalsa({"Browser.getWindowForTarget": {"result": {"windowId": 1}},
                        "Browser.setWindowBounds": {"error": {"message": "x"}}})
    monkeypatch.setattr(n, "pagina", lambda: pag2)
    assert n.minimizar() is False


def _lanzar(tmp_path, monkeypatch, **kw):
    exe = tmp_path / "brave.exe"
    exe.write_text("x")
    n = cdp.Navegador(str(exe), tmp_path / "perfil", 9999, **kw)
    lanzados, minimizada = [], []
    monkeypatch.setattr(cdp.subprocess, "Popen", lambda args, **k: lanzados.append(args))
    estado = {"vivo": False}
    monkeypatch.setattr(n, "vivo", lambda: estado["vivo"])
    monkeypatch.setattr(n, "minimizar", lambda: minimizada.append(1) or True)

    def arrancar(args, **k):
        lanzados.append(args)
        estado["vivo"] = True

    monkeypatch.setattr(cdp.subprocess, "Popen", arrancar)
    monkeypatch.setattr(cdp.time, "sleep", lambda s: None)
    assert n.abrir() is True
    return lanzados[0], minimizada


def test_ventana_normal_minimizada_y_oculta(tmp_path, monkeypatch):
    args, minimizada = _lanzar(tmp_path, monkeypatch, visible=True)
    assert "--window-size=1150,850" in args and "--headless=new" not in args and not minimizada
    assert args[-1] == "about:blank" and any(a.startswith("--user-data-dir=") for a in args)
    assert "--remote-debugging-port=9999" in args

    args, minimizada = _lanzar(tmp_path, monkeypatch, visible=True, minimizada=True)
    assert "--start-minimized" in args and minimizada == [1]

    args, minimizada = _lanzar(tmp_path, monkeypatch, visible=False)
    assert "--headless=new" in args and "--window-size=1150,850" in args and not minimizada


def test_el_perfil_se_pasa_como_ruta_absoluta(tmp_path, monkeypatch):
    """Con una ruta relativa Brave la resolvía desde su propia carpeta y nunca abría el puerto."""
    args, _ = _lanzar(tmp_path, monkeypatch, visible=False)
    perfil = next(a for a in args if a.startswith("--user-data-dir="))[len("--user-data-dir="):]
    assert Path(perfil).is_absolute()


# --------------------------------------------------------------------------- #
# Sin pestañas restauradas: Brave reabría las de la vez anterior (varios WhatsApp Web = ninguno carga)
# --------------------------------------------------------------------------- #
def _perfil_con_sesion(base: Path) -> Path:
    perfil = base / "perfil"
    default = perfil / "Default"
    (default / "Sessions").mkdir(parents=True)
    (default / "Sessions" / "Session_1").write_text("x")
    (default / "Sessions" / "Tabs_1").write_text("x")
    for nombre in ("Current Session", "Last Tabs"):
        (default / nombre).write_text("x")
    (default / "Cookies").write_text("galleta")                      # esto NO se toca
    (default / "IndexedDB").mkdir()
    (default / "IndexedDB" / "wa").write_text("sesion de whatsapp")
    (default / "Preferences").write_text('{"profile": {"exit_type": "Crashed", "exited_cleanly": false}, "a": 1}')
    return perfil


def test_limpiar_sesiones_borra_solo_las_pestanas_guardadas(tmp_path):
    perfil = _perfil_con_sesion(tmp_path)
    cdp.limpiar_sesiones(perfil)
    default = perfil / "Default"
    assert not (default / "Sessions").exists() and not (default / "Current Session").exists()
    assert not (default / "Last Tabs").exists()
    assert (default / "Cookies").read_text() == "galleta"             # la sesión de los sitios queda
    assert (default / "IndexedDB" / "wa").read_text() == "sesion de whatsapp"
    import json
    prefs = json.loads((default / "Preferences").read_text())
    assert prefs["profile"]["exit_type"] == "Normal" and prefs["profile"]["exited_cleanly"] is True
    assert prefs["a"] == 1                                            # el resto de las preferencias, igual


def test_limpiar_sesiones_sin_perfil_o_con_preferencias_rotas_no_falla(tmp_path):
    cdp.limpiar_sesiones(tmp_path / "no_existe")
    perfil = tmp_path / "p"
    (perfil / "Default").mkdir(parents=True)
    (perfil / "Default" / "Preferences").write_text("{esto no es json")
    cdp.limpiar_sesiones(perfil)
    assert (perfil / "Default" / "Preferences").read_text() == "{esto no es json"


def test_abrir_borra_las_sesiones_antes_de_lanzar(tmp_path, monkeypatch):
    perfil = _perfil_con_sesion(tmp_path)
    exe = tmp_path / "brave.exe"
    exe.write_text("x")
    n = cdp.Navegador(str(exe), perfil, 9999, visible=False)
    visto = {}
    estado = {"vivo": False}

    def arrancar(args, **k):
        visto["sesiones_al_lanzar"] = (perfil / "Default" / "Sessions").exists()
        estado["vivo"] = True

    monkeypatch.setattr(cdp.subprocess, "Popen", arrancar)
    monkeypatch.setattr(n, "vivo", lambda: estado["vivo"])
    monkeypatch.setattr(cdp.time, "sleep", lambda s: None)
    assert n.abrir() is True
    assert visto["sesiones_al_lanzar"] is False


class _Resp:
    def __init__(self, datos):
        self._datos = datos

    def json(self):
        return self._datos


class _RequestsFalso:
    """Un navegador con pestañas: /json las lista, /json/close/<id> cierra, /json/new abre una."""

    def __init__(self, urls, aparecen_despues=()):
        self.pestanas = [{"id": f"P{i}", "type": "page", "url": u, "webSocketDebuggerUrl": f"ws://x/P{i}"}
                         for i, u in enumerate(urls)]
        self.aparecen_despues = list(aparecen_despues)
        self.cerradas = []
        self.consultas = 0

    def get(self, url, timeout=None):
        if url.endswith("/json"):
            self.consultas += 1
            if self.consultas == 2 and self.aparecen_despues:          # una pestaña restaurada tardía
                self.pestanas += [{"id": f"L{i}", "type": "page", "url": u} for i, u in enumerate(self.aparecen_despues)]
            return _Resp(list(self.pestanas))
        if "/json/close/" in url:
            ident = url.rsplit("/", 1)[1]
            self.cerradas.append(ident)
            self.pestanas = [t for t in self.pestanas if t["id"] != ident]
            return _Resp("Target is closing")
        raise AssertionError(url)

    def put(self, url, timeout=None):
        assert "/json/new" in url
        nueva = {"id": "N", "type": "page", "url": "about:blank", "webSocketDebuggerUrl": "ws://x/N"}
        self.pestanas.append(nueva)
        return _Resp(nueva)


class _RelojFalso:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _con_navegador_falso(tmp_path, monkeypatch, falso):
    monkeypatch.setattr(cdp, "requests", falso)
    monkeypatch.setattr(cdp, "time", _RelojFalso())
    return _nav(tmp_path)


def test_pagina_cierra_las_pestanas_restauradas_y_deja_la_en_blanco(tmp_path, monkeypatch):
    falso = _RequestsFalso(["https://web.whatsapp.com/", "about:blank", "https://web.whatsapp.com/"])
    n = _con_navegador_falso(tmp_path, monkeypatch, falso)
    p = n.pagina()
    assert p is not None and p.id == "P1"                             # la en blanco
    assert [t["id"] for t in falso.pestanas] == ["P1"]
    assert sorted(falso.cerradas) == ["P0", "P2"]


def test_pagina_sin_en_blanco_se_queda_con_una_sola(tmp_path, monkeypatch):
    falso = _RequestsFalso(["https://a.com/", "https://b.com/"])
    n = _con_navegador_falso(tmp_path, monkeypatch, falso)
    p = n.pagina()
    assert p is not None and len(falso.pestanas) == 1 and p.id == falso.pestanas[0]["id"]


def test_pagina_espera_a_las_pestanas_que_aparecen_un_instante_despues(tmp_path, monkeypatch):
    falso = _RequestsFalso(["about:blank"], aparecen_despues=["https://web.whatsapp.com/"])
    n = _con_navegador_falso(tmp_path, monkeypatch, falso)
    p = n.pagina()
    assert p is not None and p.id == "P0"
    assert [t["id"] for t in falso.pestanas] == ["P0"] and falso.cerradas == ["L0"]


def test_pagina_sin_pestanas_abre_una_nueva(tmp_path, monkeypatch):
    falso = _RequestsFalso([])
    n = _con_navegador_falso(tmp_path, monkeypatch, falso)
    p = n.pagina()
    assert p is not None and p.id == "N" and len(falso.pestanas) == 1


def test_pagina_sin_limpiar_devuelve_la_primera_sin_cerrar_nada(tmp_path, monkeypatch):
    falso = _RequestsFalso(["https://a.com/", "https://b.com/"])
    n = _con_navegador_falso(tmp_path, monkeypatch, falso)
    p = n.pagina(unica=False)
    assert p is not None and p.id == "P0" and falso.cerradas == []
