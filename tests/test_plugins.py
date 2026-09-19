"""Plugins: contrato, seguridad de system_control/video y comportamiento de Qt."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from miku.plugins import registro
from miku.plugins.sistema.system_control import SystemControl, _a_entero
from miku.plugins.productividad.video import VideoInterpolador


# ---------------------------------------------------------------- contrato / registro
@pytest.fixture(scope="module")
def plugins_cargados():
    from miku.ajustes import carga as config_mod
    return registro.instanciar_plugins(config_mod.Config())      # sin config: solo los "siempre activos"


def test_todos_los_plugins_del_catalogo_cargan(cfg, plugins_cargados):
    """Un error de import/instanciación en cualquier plugin se ve acá."""
    esperados = {e.modulo for e in registro._CATALOGO if e.condicion is None}
    assert len(plugins_cargados) >= len(esperados)


def test_nombres_de_tools_unicos_y_bien_formados(plugins_cargados):
    nombres = []
    for p in plugins_cargados:
        for t in p.tools:
            assert t["type"] == "function"
            f = t["function"]
            assert f["name"] and f["description"] and "parameters" in f, f"tool mal armada en {p.nombre}"
            nombres.append(f["name"])
    assert len(nombres) == len(set(nombres)), "hay tools repetidas entre plugins"


def test_las_peligrosas_son_tools_del_propio_plugin(plugins_cargados):
    for p in plugins_cargados:
        propias = {t["function"]["name"] for t in p.tools}
        assert set(p.peligrosas) <= propias, f"{p.nombre} declara peligrosas que no son suyas"


def test_tools_conocidas_de_riesgo_exigen_confirmacion(plugins_cargados):
    todas = set().union(*(set(p.peligrosas) for p in plugins_cargados))
    for tool in ("control_energia", "programar_accion", "expulsar_usuario_discord",
                 "silenciar_usuario_discord", "volumen_usuario_discord"):
        assert tool in todas


def test_tool_desconocida_devuelve_none(plugins_cargados):
    for p in plugins_cargados:
        assert p.manejar_tool("tool_que_no_existe", {}, {}) is None


# ---------------------------------------------------------------- system_control
@pytest.fixture
def sc():
    return SystemControl()


def test_alias_por_palabras_no_por_substring(sc):
    assert sc._buscar_alias("") is None
    assert sc._buscar_alias("brave") == "brave"
    assert sc._buscar_alias("edge of eternity") is None       # antes abría Edge
    assert sc._buscar_alias("xbravex") is None


@pytest.mark.parametrize("nombre", ["explorer", "explorador", "cmd", "powershell", ""])
def test_cerrar_programa_rechaza_lo_no_permitido(sc, monkeypatch, nombre):
    llamadas = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: llamadas.append(a))
    assert "No está permitido" in sc.cerrar_programa(nombre)
    assert llamadas == [], "nunca debe llegar a taskkill"


def test_cerrar_programa_permitido_usa_taskkill(sc, monkeypatch):
    comandos = []

    class R:
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: comandos.append(cmd) or R())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert "Cerré" in sc.cerrar_programa("notepad")
    assert comandos[0][:2] == ["taskkill", "/IM"]


def test_ruta_elegida_solo_si_fue_ofrecida(sc):
    sc.initialize(None)
    assert "no es una de las que te ofrecí" in sc.manejar_tool(
        "abrir_programa", {"ruta_elegida": r"C:\Windows\System32\cmd.exe"}, {})
    sc._registrar_ofrecidas([r"C:\Juegos\a.exe"])
    assert sc._fue_ofrecida(r"c:\juegos\A.EXE")               # sin distinguir mayúsculas (Windows)


def test_buscar_archivo_no_lanza_ejecutables(sc, monkeypatch):
    abierto = []
    monkeypatch.setattr(sc, "_abrir_resultado", lambda ruta, carpeta: abierto.append((ruta, carpeta)) or "ok")
    sc._abrir_archivo_buscado(r"C:\x\instalador.exe", False)
    sc._abrir_archivo_buscado(r"C:\x\notas.txt", False)
    assert abierto == [(r"C:\x\instalador.exe", True), (r"C:\x\notas.txt", False)]


@pytest.mark.parametrize("valor,defecto,esperado", [(5, 1, 5), ("7", 1, 7), ("diez", 3, 3), (None, 4, 4), (2.9, 1, 2)])
def test_a_entero(valor, defecto, esperado):
    assert _a_entero(valor, defecto) == esperado


# ---------------------------------------------------------------- video_interpolador
@pytest.fixture
def entorno_video(tmp_path):
    bat = tmp_path / "x.bat"
    bat.write_text("@echo off", encoding="utf-8")
    carpeta = tmp_path / "videos"
    carpeta.mkdir()
    return bat, carpeta


def test_video_valido(entorno_video):
    bat, carpeta = entorno_video
    v = carpeta / "capitulo.mp4"
    v.write_text("x")
    assert VideoInterpolador._validar_video(str(v), str(carpeta), str(bat)) is None


def test_video_con_caracteres_de_cmd(entorno_video):
    bat, carpeta = entorno_video
    v = carpeta / "AT&T.mp4"
    v.write_text("x")
    assert "caracteres" in VideoInterpolador._validar_video(str(v), str(carpeta), str(bat))


def test_video_fuera_de_la_carpeta(entorno_video, tmp_path):
    bat, carpeta = entorno_video
    afuera = tmp_path / "otro.mp4"
    afuera.write_text("x")
    assert "carpeta de videos" in VideoInterpolador._validar_video(str(afuera), str(carpeta), str(bat))


def test_video_inexistente_o_no_video(entorno_video):
    bat, carpeta = entorno_video
    assert VideoInterpolador._validar_video(str(carpeta / "no.mp4"), str(carpeta), str(bat))
    txt = carpeta / "a.txt"
    txt.write_text("x")
    assert "video" in VideoInterpolador._validar_video(str(txt), str(carpeta), str(bat))


def test_no_dice_listo_si_no_se_genero_el_video(entorno_video, monkeypatch):
    """El .bat viejo devolvía 0 aunque fallara: se verifica el video de salida."""
    bat, carpeta = entorno_video
    v = carpeta / "cap.mp4"
    v.write_text("x")

    class R:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    avisos = []
    p = VideoInterpolador()
    p._notificar = lambda frase, ctx: avisos.append(frase)
    p._correr_pipeline(str(v), str(bat), "cap.mp4", {})
    assert "falló" in avisos[0] and "no se generó" in avisos[0]

    (carpeta / "cap-2x.mkv").write_text("video")                    # ahora sí existe la salida
    p._correr_pipeline(str(v), str(bat), "cap.mp4", {})
    assert "Listo" in avisos[1]


# ---------------------------------------------------------------- Qt (bandeja + subtítulos)
def test_bandeja_y_subtitulos_conviven():
    pytest.importorskip("PyQt5")
    from miku.ui import bandeja as bandeja_mod
    from miku.ui.subtitulos import SubtitulosOverlay

    b = bandeja_mod.Bandeja()
    assert b.iniciar(on_salir=lambda: None)
    ov = SubtitulosOverlay()
    ov.actualizar_texto("Hola, prueba de subtítulo")
    time.sleep(0.5)
    panel = ov._panel
    assert panel._widget is not None and panel._widget.isVisible()
    assert panel._label.text() == "Hola, prueba de subtítulo"
    ov.ocultar()
    time.sleep(0.3)
    assert not panel._widget.isVisible()
    ov.actualizar_texto("Otra frase")
    time.sleep(0.3)
    assert panel._widget.isVisible() and panel._label.text() == "Otra frase"
    b.detener()
