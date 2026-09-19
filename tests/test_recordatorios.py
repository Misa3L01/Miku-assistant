"""Recordatorios persistentes: sobreviven a cerrar Miku; las acciones peligrosas no."""
from __future__ import annotations

import json
import time

import pytest

from miku.plugins.sistema.energia import Energia
from miku.servicios import recordatorios
from miku.servicios.scheduler import MAX_ATRASO_H, Scheduler


@pytest.fixture
def ruta(tmp_path):
    return tmp_path / "recordatorios.json"


def _guardado(ruta):
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else []


def test_un_recordatorio_persistente_se_guarda_en_disco(ruta):
    s = Scheduler(ruta)
    tid = s.programar(3600, lambda: None, "Recordatorio: pizza", persistente=recordatorios.datos_de("pizza"))
    (t,) = _guardado(ruta)
    assert t["id"] == tid and t["datos"] == {"tipo": "recordatorio", "mensaje": "pizza"}
    assert abs(t["vence"] - (time.time() + 3600)) < 5
    s.cancelar_todos()


def test_lo_no_persistente_no_se_guarda(ruta):
    s = Scheduler(ruta)
    s.programar(3600, lambda: None, "Apagar diferido")
    assert _guardado(ruta) == []
    s.cancelar_todos()


def test_cerrar_conserva_los_recordatorios_y_cancelar_los_borra(ruta):
    s = Scheduler(ruta)
    a = s.programar(3600, lambda: None, "a", persistente=recordatorios.datos_de("a"))
    s.programar(3600, lambda: None, "b", persistente=recordatorios.datos_de("b"))
    assert s.cancelar(a) is True                      # el usuario lo canceló: se olvida
    assert [t["datos"]["mensaje"] for t in _guardado(ruta)] == ["b"]
    s.cancelar_todos(olvidar=False)                   # cierre de Miku: se conserva
    assert [t["datos"]["mensaje"] for t in _guardado(ruta)] == ["b"]
    assert not s.hay_pendientes()


def test_disparar_borra_el_recordatorio_del_disco(ruta):
    s = Scheduler(ruta)
    dicho = []
    s.programar(0, lambda: dicho.append(1), "ya", persistente=recordatorios.datos_de("ya"))
    for _ in range(50):
        if dicho:
            break
        time.sleep(0.02)
    time.sleep(0.05)
    assert dicho and _guardado(ruta) == []


def test_recuperar_reprograma_y_devuelve_los_perdidos(ruta):
    ahora = time.time()
    ruta.write_text(json.dumps([
        {"id": 1, "vence": ahora + 3600, "descripcion": "futuro", "datos": recordatorios.datos_de("futuro")},
        {"id": 2, "vence": ahora - 600, "descripcion": "perdido", "datos": recordatorios.datos_de("perdido")},
        {"id": 3, "vence": ahora - (MAX_ATRASO_H + 1) * 3600, "descripcion": "viejo",
         "datos": recordatorios.datos_de("viejo")},
        {"roto": True},
    ]), encoding="utf-8")
    s = Scheduler(ruta)
    fabricados = []
    perdidos = s.recuperar(lambda datos: fabricados.append(datos["mensaje"]) or (lambda: None))
    assert [d["mensaje"] for d in perdidos] == ["perdido"]
    assert fabricados == ["futuro"]
    (pendiente,) = s.listar_pendientes()
    assert pendiente["descripcion"] == "futuro" and pendiente["segundos_restantes"] > 3500
    assert [t["datos"]["mensaje"] for t in _guardado(ruta)] == ["futuro"]     # sigue guardado
    s.cancelar_todos()


@pytest.mark.parametrize("contenido", ["no es json", "", "{}", "[1, 2]"])
def test_archivo_roto_no_rompe(ruta, contenido):
    ruta.write_text(contenido, encoding="utf-8")
    s = Scheduler(ruta)
    assert s.recuperar(lambda d: (lambda: None)) == []


def test_sin_ruta_todo_sigue_en_memoria():
    s = Scheduler()
    s.programar(3600, lambda: None, "x", persistente=recordatorios.datos_de("x"))
    assert s.recuperar(lambda d: (lambda: None)) == []
    s.cancelar_todos()


def test_texto_de_recordatorios_perdidos():
    assert recordatorios.texto_perdidos([]) == ""
    uno = recordatorios.texto_perdidos([recordatorios.datos_de("sacar la pizza")])
    assert "sacar la pizza" in uno
    varios = recordatorios.texto_perdidos([recordatorios.datos_de("a"), recordatorios.datos_de("b")])
    assert "2" in varios and "a; b" in varios


def test_el_callback_dice_el_recordatorio_con_la_voz_del_momento():
    dichas = []
    voz = {"v": None}
    cb = recordatorios.fabricar_callback(lambda: voz["v"], recordatorios.datos_de("regar las plantas"))
    voz["v"] = type("V", (), {"decir": staticmethod(dichas.append)})()     # la voz se crea DESPUÉS
    cb()
    assert len(dichas) == 1 and "regar las plantas" in dichas[0]


def test_programar_recordatorio_persiste_pero_apagar_no(ruta):
    s = Scheduler(ruta)
    ctx = {"scheduler": s, "voice": None}
    e = Energia()
    e.programar_accion("recordatorio", 30, "llamar a mamá", ctx)
    e.programar_accion("apagar", 30, "", ctx)
    assert [t["datos"]["mensaje"] for t in _guardado(ruta)] == ["llamar a mamá"]
    s.cancelar_todos()
