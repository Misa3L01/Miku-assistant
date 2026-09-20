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
    assert "--headless=new" in args and "--window-size=1150,850" not in args and not minimizada


def test_el_perfil_se_pasa_como_ruta_absoluta(tmp_path, monkeypatch):
    """Con una ruta relativa Brave la resolvía desde su propia carpeta y nunca abría el puerto."""
    args, _ = _lanzar(tmp_path, monkeypatch, visible=False)
    perfil = next(a for a in args if a.startswith("--user-data-dir="))[len("--user-data-dir="):]
    assert Path(perfil).is_absolute()
