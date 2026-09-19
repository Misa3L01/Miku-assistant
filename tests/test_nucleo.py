"""Núcleo: calculadora, memoria, scheduler, tono y configuración."""
from __future__ import annotations

import json
import threading

import pytest

from miku.ajustes import carga as config_mod
from miku.voz.frases import tono
from miku.cerebro.calculadora import calcular
from miku.cerebro.memoria.almacen import Memoria
from miku.servicios.scheduler import Scheduler


# ---------------------------------------------------------------- calculadora
@pytest.mark.parametrize("texto,esperado", [
    ("cuanto es 15 por 23 mas 10%", "379.5"),
    ("cuanto es 1.500 mas 500", "2000"),          # punto de miles a la argentina
    ("cuanto es 1,5 por 2", "3"),
    ("el 10% de 340", "34"),
    ("calcula (3+4)*2", "14"),
    ("2.000.000 entre 4", "500000"),
    ("cuanto es 3 x 4", "12"),
    ("cuanto es 100 menos 20%", "80"),
])
def test_calculos_validos(texto, esperado):
    assert calcular(texto) == esperado


@pytest.mark.parametrize("texto", [
    "cuanto es 5 por ciento de 200",   # antes daba 1000: descartaba palabras
    "cuanto es la mitad de 100",
    "cuanto es 1 dolar en pesos",
    "cuanto es 2 elevado a 3",
])
def test_no_inventa_resultados(texto):
    assert calcular(texto) is None


# ---------------------------------------------------------------- memoria
@pytest.fixture
def mem(tmp_path):
    m = Memoria(str(tmp_path / "memoria.db"))
    yield m
    m.cerrar()


def test_guardar_y_buscar(mem):
    mem.guardar_recuerdo("mi cumple es el 15 de julio")
    mem.guardar_recuerdo("mañana tengo turno con el dentista")
    assert "cumple" in mem.buscar_recuerdos("cuando es mi cumple")
    assert mem.cantidad() == 2


def test_no_duplica(mem):
    mem.guardar_recuerdo("algo")
    mem.guardar_recuerdo("algo")
    assert mem.cantidad() == 1


def test_olvidar_pide_precision_si_hay_varios(mem):
    mem.guardar_recuerdo("el wifi de casa es cielo_azul")
    mem.guardar_recuerdo("el wifi del trabajo es rojo")
    assert "varios" in mem.olvidar_recuerdo("el wifi")
    assert "olvidé" in mem.olvidar_recuerdo("wifi de casa")
    assert mem.cantidad() == 1


def test_olvidar_ignora_stopwords(mem):
    mem.guardar_recuerdo("mis cosas están en el placard")
    r = mem.olvidar_recuerdo("mis cosas del auto")
    assert r.intencion == "memoria.sin_recuerdos" and not r.ok


def test_memoria_es_segura_entre_hilos(mem):
    def trabajo(i):
        for k in range(15):
            mem.guardar_recuerdo(f"nota {i}-{k}")
            mem.buscar_recuerdos("nota")

    hilos = [threading.Thread(target=trabajo, args=(i,)) for i in range(4)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert mem.cantidad() == 60


def test_memoria_cerrada_no_rompe(mem):
    mem.cerrar()
    assert mem.buscar_recuerdos("x") == "" and mem.cantidad() == 0


# ---------------------------------------------------------------- scheduler
def test_scheduler_programa_y_cancela():
    s = Scheduler()
    tarea = s.programar(60, lambda: None, "x")
    assert s.hay_pendientes()
    assert s.cancelar(tarea) is True
    assert not s.hay_pendientes()


def test_scheduler_acota_demoras_enormes():
    s = Scheduler()
    s.programar(float("inf"), lambda: None, "infinito")   # no debe lanzar OverflowError
    assert s.cancelar_todos() == 1


def test_scheduler_dispara():
    hecho = threading.Event()
    Scheduler().programar(0.05, hecho.set, "rápida")
    assert hecho.wait(2)


# ---------------------------------------------------------------- tono
def test_tono_normaliza_las_claves_con_signos():
    assert tono.variar("Frase que no está en el catálogo") == "Frase que no está en el catálogo"
    assert tono.variar("Listo") in {v for v in tono._VARIANTES["listo"]}
    assert "¡hola! ¿en qué te ayudo" in tono._VARIANTES       # antes la clave nunca coincidía


# ---------------------------------------------------------------- config
def test_defaults_contienen_las_claves_basicas(cfg):
    for clave in ("groq_api_key", "carpeta_capturas", "gemini_api_key", "app_whitelist"):
        assert clave in cfg.valores


def test_microfono_index_tolerante(cfg):
    assert cfg.microfono_index is None
    cfg.valores["microfono_index"] = "2"
    assert cfg.microfono_index == 2
    cfg.valores["microfono_index"] = ""
    assert cfg.microfono_index is None
    cfg.valores["microfono_index"] = "abc"
    assert cfg.microfono_index is None


def test_guardar_preferencias_es_atomico_y_hace_merge(tmp_path):
    ruta = tmp_path / "data" / "preferences.json"
    ruta.parent.mkdir()
    ruta.write_text(json.dumps({"otra_clave": 1}), encoding="utf-8")
    c = config_mod.Config(ruta_archivo=ruta)
    assert c.guardar_preferencias({"personalidad": "formal"}) is True
    assert json.loads(ruta.read_text(encoding="utf-8")) == {"otra_clave": 1, "personalidad": "formal"}
    assert c.valores["personalidad"] == "formal"
    assert not list(ruta.parent.glob("*.tmp")), "no debe quedar un temporal"


def test_guardar_preferencias_concurrente(tmp_path):
    ruta = tmp_path / "preferences.json"
    c = config_mod.Config(ruta_archivo=ruta)
    hilos = [threading.Thread(target=lambda i=i: c.guardar_preferencias({f"k{i}": i})) for i in range(12)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert len(json.loads(ruta.read_text(encoding="utf-8"))) == 12


# ---------------------------------------------------------------- plataforma: texto
from miku.plataforma.texto import clave_compacta, normalizar, sin_acentos  # noqa: E402


@pytest.mark.parametrize("entrada,esperado", [
    ("¿Qué Hora Es?", "¿que hora es?"), ("Ñandú", "nandu"), ("  Química ", "  quimica "), (None, ""), ("", ""),
])
def test_sin_acentos(entrada, esperado):
    assert sin_acentos(entrada) == esperado


def test_normalizar_recorta_y_clave_compacta_quita_separadores():
    assert normalizar("  Visual Studio  ") == "visual studio"
    assert clave_compacta("Lista_Animes 2") == clave_compacta("lista animes2") == "listaanimes2"
