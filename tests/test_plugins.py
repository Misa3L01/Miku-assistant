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


# ---------------------------------------------------------------- plataforma: pantalla / subprocesos
from miku.plataforma import pantalla, subprocesos  # noqa: E402


class _User32Falso:
    """user32 simulado: registra qué se pidió sin tocar la pantalla real."""

    def __init__(self, prueba=0, real=0, tiene_modo=True):
        self.prueba, self.real, self.tiene_modo = prueba, real, tiene_modo
        self.llamadas = []

    def EnumDisplaySettingsW(self, dispositivo, modo, ref):
        return 1 if self.tiene_modo else 0

    def ChangeDisplaySettingsW(self, ref, flags):
        self.llamadas.append(flags)
        return self.prueba if flags == pantalla._CDS_TEST else self.real


@pytest.mark.parametrize("prueba,real,esperado", [
    (0, 0, pantalla.OK), (0, 1, pantalla.REINICIO), (-2, 0, pantalla.NO_SOPORTADA),
    (-1, 0, pantalla.ERROR), (0, -1, pantalla.ERROR),
])
def test_cambiar_resolucion_estados(monkeypatch, prueba, real, esperado):
    falso = _User32Falso(prueba, real)
    monkeypatch.setattr(pantalla, "_user32", lambda: falso)
    assert pantalla.cambiar_resolucion(1920, 1440)[0] == esperado


def test_cambiar_resolucion_no_aplica_si_el_modo_no_existe(monkeypatch):
    falso = _User32Falso(prueba=-2)
    monkeypatch.setattr(pantalla, "_user32", lambda: falso)
    pantalla.cambiar_resolucion(1234, 567)
    assert falso.llamadas == [pantalla._CDS_TEST], "solo se probó: nunca se aplicó"


def test_solo_probar_no_cambia_la_pantalla(monkeypatch):
    falso = _User32Falso()
    monkeypatch.setattr(pantalla, "_user32", lambda: falso)
    assert pantalla.cambiar_resolucion(1920, 1440, solo_probar=True)[0] == pantalla.OK
    assert falso.llamadas == [pantalla._CDS_TEST]


def test_sin_acceso_a_la_pantalla_devuelve_error(monkeypatch):
    monkeypatch.setattr(pantalla, "_user32", lambda: _User32Falso(tiene_modo=False))
    assert pantalla.cambiar_resolucion(1920, 1080)[0] == pantalla.ERROR
    assert pantalla.resolucion_actual() is None


def test_la_resolucion_real_se_puede_leer():
    actual = pantalla.resolucion_actual()          # solo lectura
    assert actual is None or (actual[0] > 0 and actual[1] > 0)


def test_macro_de_resolucion_usa_la_capa_de_plataforma(monkeypatch):
    from miku.plugins.productividad import macros
    monkeypatch.setattr(pantalla, "cambiar_resolucion", lambda a, b: (pantalla.NO_SOPORTADA, -2))
    assert "no soporta" in macros.cambiar_resolucion(1, 1)


def test_restaurar_resolucion_de_los_snapshots(monkeypatch):
    from miku.servicios import modos
    monkeypatch.setattr(pantalla, "cambiar_resolucion", lambda a, b: (pantalla.REINICIO, 1))
    assert modos._restaurar_resolucion("1920", 1080) is True
    monkeypatch.setattr(pantalla, "cambiar_resolucion", lambda a, b: (pantalla.ERROR, -1))
    assert modos._restaurar_resolucion(1920, 1080) is False
    assert modos._restaurar_resolucion("x", 1080) is False


def test_powershell_utf8_y_sin_ventana(monkeypatch):
    capturado = {}

    def falso(cmd, **kw):
        capturado.update(cmd=cmd, **kw)
        return "ok"

    monkeypatch.setattr(subprocess, "run", falso)
    subprocesos.correr_powershell("Write-Output 'hola'", timeout=5)
    assert capturado["cmd"][-1].startswith("[Console]::OutputEncoding")
    assert capturado["encoding"] == "utf-8" and capturado["creationflags"] == subprocesos.sin_ventana()
    assert capturado["shell"] is False
