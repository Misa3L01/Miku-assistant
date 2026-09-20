"""Arranque: instancia única, invocación, inicio con Windows y atajo F22."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid

import pytest

from miku.servicios import arranque
from miku.servicios.instancia import InstanciaUnica


@pytest.fixture
def nombre():
    """Nombre único: nunca choca con una Miku real que esté corriendo."""
    return f"MikuTest{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------- instancia única
def test_solo_la_primera_ejecucion_es_la_instancia(nombre):
    primera, segunda = InstanciaUnica(nombre), InstanciaUnica(nombre)
    try:
        assert primera.adquirir() is True
        assert segunda.adquirir() is False
    finally:
        primera.liberar()
        segunda.liberar()


def test_se_puede_volver_a_adquirir_tras_liberar(nombre):
    a = InstanciaUnica(nombre)
    assert a.adquirir()
    a.liberar()
    b = InstanciaUnica(nombre)
    try:
        assert b.adquirir() is True
    finally:
        b.liberar()


def test_invocar_sin_instancia_devuelve_false(nombre):
    assert InstanciaUnica(nombre).invocar_existente() is False


def test_la_segunda_ejecucion_invoca_a_la_primera(nombre):
    primera, segunda = InstanciaUnica(nombre), InstanciaUnica(nombre)
    invocada = threading.Event()
    try:
        assert primera.adquirir()
        primera.escuchar(invocada.set)
        assert segunda.adquirir() is False
        assert segunda.invocar_existente() is True
        assert invocada.wait(3), "la primera instancia debe recibir la invocación"
    finally:
        primera.liberar()


def test_main_py_no_abre_una_segunda_miku(nombre):
    """El main.py real, con una 'Miku' ya corriendo, la invoca y termina sin arrancar nada."""
    primera = InstanciaUnica(nombre)
    invocada = threading.Event()
    try:
        assert primera.adquirir()
        primera.escuchar(invocada.set)
        entorno = dict(os.environ, MIKU_INSTANCIA=nombre)
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "main.py", "--silencioso"], cwd=raiz, env=entorno,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        assert invocada.wait(3)
    finally:
        primera.liberar()


# ---------------------------------------------------------------- inicio con Windows
def test_inicio_con_windows_activar_y_desactivar():
    nombre_run = f"MikuTest{uuid.uuid4().hex[:8]}"
    try:
        assert arranque.inicio_windows_activo(nombre_run) is False
        assert arranque.activar_inicio_windows(nombre_run) is True
        assert arranque.inicio_windows_activo(nombre_run) is True
        assert arranque.desactivar_inicio_windows(nombre_run) is True
        assert arranque.inicio_windows_activo(nombre_run) is False
        assert arranque.desactivar_inicio_windows(nombre_run) is True, "borrar dos veces no falla"
    finally:
        arranque.desactivar_inicio_windows(nombre_run)


def test_el_comando_de_inicio_es_silencioso_y_usa_rutas_entrecomilladas():
    partes = arranque.argumentos_de_arranque(["--silencioso"])
    linea = arranque._como_linea(partes)
    assert linea.endswith("--silencioso") and linea.startswith('"')
    assert "main.py" in linea


# ---------------------------------------------------------------- atajo de teclado
@pytest.mark.parametrize("tecla,esperada", [(None, "F13"), ("f22", "F22"), ("ctrl+alt+f5", "Alt+Ctrl+F5")])
def test_atajo_se_crea_con_la_tecla_pedida(tmp_path, tecla, esperada):
    ruta = tmp_path / "Miku de prueba.lnk"
    assert arranque.atajo_activo(ruta) is False
    assert arranque.activar_atajo(ruta, tecla) is True
    assert arranque.atajo_activo(ruta) is True
    leido = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(New-Object -ComObject WScript.Shell).CreateShortcut('{ruta}') | "
         "ForEach-Object { $_.Hotkey + '|' + $_.Arguments }"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    leida, argumentos = leido.split("|", 1)
    assert leida == esperada
    assert "--silencioso" in argumentos and "--invocar" in argumentos
    assert arranque.desactivar_atajo(ruta) is True
    assert arranque.atajo_activo(ruta) is False


@pytest.mark.parametrize("tecla,esperado", [("f13", "F13"), ("F24", "F24"), (" ctrl + alt + f5 ", "CTRL+ALT+F5"),
                                            ("f25", None), ("f0", None), ("m", None), ("sc:57", None),
                                            ("insert", None), ("", None)])
def test_atajo_valido(tecla, esperado):
    assert arranque.atajo_valido(tecla) == esperado


def test_una_tecla_que_un_acceso_directo_no_admite_no_crea_nada(tmp_path):
    ruta = tmp_path / "x.lnk"
    assert arranque.activar_atajo(ruta, "sc:57") is False and not ruta.exists()
