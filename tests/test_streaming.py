"""Streaming del LLM: Miku habla con la primera oración mientras el modelo sigue escribiendo."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from miku.cerebro import parser as cp
from miku.cerebro.parser import frases_completas
from miku.plataforma import red


# --------------------------------------------------------------------------- #
# Troceo incremental
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("buffer,frases,resto", [
    ("Hola. Todo bi", ["Hola."], "Todo bi"),
    ("Sin puntuación todavía", [], "Sin puntuación todavía"),
    ("¿Qué hora es? Son las tres. Y ", ["¿Qué hora es?", "Son las tres."], "Y "),
    ("Son 3.5 grados", [], "Son 3.5 grados"),                  # el decimal NO corta la oración
    ("Primera! Segunda? Tercera; ", ["Primera!", "Segunda?", "Tercera;"], ""),
    ("", [], ""),
])
def test_frases_completas(buffer, frases, resto):
    assert frases_completas(buffer) == (frases, resto)


def test_el_troceo_no_pierde_ni_inventa_texto():
    buffer = "Uno. Dos! Tres sin terminar"
    frases, resto = frases_completas(buffer)
    assert "".join(frases).replace(" ", "") + resto.replace(" ", "") == buffer.replace(" ", "")


# --------------------------------------------------------------------------- #
# Lectura del stream (SSE)
# --------------------------------------------------------------------------- #
def sse(*trozos: dict) -> list:
    """Arma las líneas ``data: {...}`` tal como las manda el LLM."""
    lineas = [b"data: " + json.dumps({"choices": [{"delta": d}]}).encode("utf-8") for d in trozos]
    return lineas + [b"", b"data: [DONE]"]


class RespuestaStream:
    def __init__(self, lineas, status=200):
        self.status_code, self._lineas = status, lineas
        self.cerrada = False

    def iter_lines(self):
        return iter(self._lineas)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.cerrada = True
        return False


@pytest.fixture
def brain(cfg):
    cfg.valores["groq_api_key"] = "gsk_fake"
    cp._CACHE.clear()
    cp._CACHE_ORDEN.clear()
    return cp.BrainGroq(cfg)


def consultar(brain, lineas, tools=(), status=200):
    """Consulta con streaming contra una respuesta simulada; devuelve (resultado, frases habladas)."""
    dichas = []
    respuesta = RespuestaStream(lineas, status)
    with mock.patch.object(red, "post", lambda *a, **k: respuesta):
        r = brain.consultar("contame algo", {"miku_memoria": "x"}, list(tools), al_fragmento=dichas.append)
    return r, dichas


def test_habla_cada_oracion_apenas_esta_terminada(brain):
    r, dichas = consultar(brain, sse({"content": "Hola. "}, {"content": "Todo bien"},
                                       {"content": ". Chau."}))
    assert dichas == ["Hola.", "Todo bien.", "Chau."]
    assert r["respuesta"] == "Hola. Todo bien. Chau."
    assert r["dicho"] == "Hola. Todo bien. Chau."


def test_la_ultima_oracion_sin_punto_final_igual_se_habla(brain):
    r, dichas = consultar(brain, sse({"content": "Una. "}, {"content": "Otra sin punto"}))
    assert dichas == ["Una.", "Otra sin punto"] and r["respuesta"] == "Una. Otra sin punto"


def test_una_sola_oracion_se_habla_una_sola_vez(brain):
    _, dichas = consultar(brain, sse({"content": "Son las tres."}))
    assert dichas == ["Son las tres."]


def test_los_trozos_vacios_y_las_lineas_basura_no_molestan(brain):
    lineas = [b": comentario", b"", b"data: {roto", *sse({"content": "Bien."}), b"otra cosa"]
    r, dichas = consultar(brain, lineas)
    assert dichas == ["Bien."] and r["respuesta"] == "Bien."


# --------------------------------------------------------------------------- #
# Tool calls en streaming (llegan partidas)
# --------------------------------------------------------------------------- #
def _tc(indice, nombre=None, args=None):
    funcion = {}
    if nombre:
        funcion["name"] = nombre
    if args is not None:
        funcion["arguments"] = args
    return {"tool_calls": [{"index": indice, "function": funcion}]}


def test_una_tool_con_los_argumentos_partidos_se_arma_entera(brain):
    """Varios modelos mandan los argumentos de a pedazos: hay que pegarlos antes de interpretarlos."""
    r, dichas = consultar(brain, sse(_tc(0, "abrir_programa", '{"nom'), _tc(0, args='bre": "Dis'),
                                       _tc(0, args='cord"}')))
    assert r["tools_call"] == [{"nombre": "abrir_programa", "args": {"nombre": "Discord"}}]
    assert dichas == [], "si el LLM pide una acción, no se habla nada por adelantado"


def test_varias_tools_del_mismo_turno_no_se_mezclan(brain):
    r, _ = consultar(brain, sse(_tc(0, "abrir_programa", '{"nombre":'), _tc(1, "controlar_brillo", '{"nivel":'),
                                  _tc(0, args=' "Brave"}'), _tc(1, args=" 30}")))
    assert r["tools_call"] == [{"nombre": "abrir_programa", "args": {"nombre": "Brave"}},
                               {"nombre": "controlar_brillo", "args": {"nivel": 30}}]


def test_argumentos_ilegibles_no_rompen_la_tool(brain):
    r, _ = consultar(brain, sse(_tc(0, "abrir_programa", "{esto no es json")))
    assert r["tools_call"] == [{"nombre": "abrir_programa", "args": {}}]


def test_texto_y_tool_en_el_mismo_turno(brain):
    """El orden que oye el usuario es el mismo del camino sin streaming: primero el texto, después la acción."""
    r, dichas = consultar(brain, sse({"content": "Dale, lo abro."}, _tc(0, "abrir_programa", '{"nombre": "Brave"}')))
    assert dichas == ["Dale, lo abro."]
    assert r["tools_call"][0]["nombre"] == "abrir_programa"


# --------------------------------------------------------------------------- #
# Degradación: si el streaming no sirve, se usa el camino normal
# --------------------------------------------------------------------------- #
class _Http:
    def __init__(self, datos):
        self.status_code, self._datos, self.headers = 200, datos, {}

    def json(self):
        return self._datos


OK = {"choices": [{"message": {"content": "Respuesta entera", "tool_calls": []}}]}


def test_si_el_servidor_rechaza_el_streaming_se_pide_la_respuesta_entera(brain):
    """Nada se habló todavía, así que se puede reintentar sin que el usuario oiga nada dos veces."""
    llamadas = []

    def post(url, **kw):
        llamadas.append(kw.get("stream", False))
        if kw.get("stream"):
            return RespuestaStream([], status=400)
        return _Http(OK)

    dichas = []
    with mock.patch.object(red, "post", post):
        r = brain.consultar("hola", {"miku_memoria": "x"}, [], al_fragmento=dichas.append)
    assert llamadas == [True, False] and r["respuesta"] == "Respuesta entera" and dichas == []


def test_un_stream_vacio_cae_al_camino_normal(brain):
    def post(url, **kw):
        return RespuestaStream(sse()) if kw.get("stream") else _Http(OK)

    dichas = []
    with mock.patch.object(red, "post", post):
        r = brain.consultar("hola", {"miku_memoria": "x"}, [], al_fragmento=dichas.append)
    assert r["respuesta"] == "Respuesta entera"


def test_si_se_corta_a_mitad_se_queda_con_lo_ya_hablado_y_no_repite(brain):
    """Repetir la consulta haría que el usuario oiga dos veces lo que ya se dijo."""
    def lineas():
        yield from sse({"content": "Primera parte. "})[:1]
        raise OSError("se cortó la conexión")

    llamadas = []

    def post(url, **kw):
        llamadas.append(kw.get("stream", False))
        return RespuestaStream(lineas())

    dichas = []
    with mock.patch.object(red, "post", post):
        r = brain.consultar("hola", {"miku_memoria": "x"}, [], al_fragmento=dichas.append)
    assert dichas == ["Primera parte."] and r["respuesta"] == "Primera parte."
    assert llamadas == [True], "no se reintenta: ya se habló"


def test_sin_emisor_o_con_streaming_apagado_no_se_pide_en_vivo(brain, cfg):
    def post(url, **kw):
        assert not kw.get("stream"), "no debía pedirse en streaming"
        return _Http(OK)

    with mock.patch.object(red, "post", post):
        assert brain.consultar("hola", {"miku_memoria": "x"}, [])["respuesta"] == "Respuesta entera"
        cfg.valores["llm_streaming"] = False
        assert brain.consultar("hola", {}, [], al_fragmento=lambda f: None)["respuesta"] == "Respuesta entera"


def test_la_respuesta_transmitida_se_cachea_igual(brain):
    dichas = []
    with mock.patch.object(red, "post", lambda *a, **k: RespuestaStream(sse({"content": "Todo bien."}))):
        r = brain.consultar("contame algo", {}, [], al_fragmento=dichas.append)
    assert r["respuesta"] == "Todo bien."
    llamadas = []
    with mock.patch.object(red, "post", lambda *a, **k: llamadas.append(1) or _Http(OK)):
        repetida = brain.consultar("contame algo", {}, [], al_fragmento=lambda f: None)
    assert repetida["respuesta"] == "Todo bien." and not llamadas, "la segunda vez sale de la caché"


def test_una_respuesta_con_tools_no_se_cachea(brain):
    consultar(brain, sse(_tc(0, "abrir_programa", '{"nombre": "Brave"}')))
    llamadas = []
    with mock.patch.object(red, "post", lambda *a, **k: llamadas.append(1) or _Http(OK)):
        brain.consultar("contame algo", {}, [], al_fragmento=lambda f: None)
    assert llamadas, "repetir una acción tiene que volver a ejecutarla"


# --------------------------------------------------------------------------- #
# Integración con el parser: no repetir lo ya hablado
# --------------------------------------------------------------------------- #
def test_el_parser_habla_en_vivo_y_avisa_que_falta_nada(parser, cerebro_falso):
    cerebro_falso.resp = {"respuesta": "Hola. Todo bien.", "tools_call": []}
    cerebro_falso.partir_en = ["Hola.", "Todo bien."]
    dichas = []
    contexto = {"voice": _Voz(dichas)}
    final = parser.procesar("contame algo", contexto)
    assert dichas == ["Hola.", "Todo bien."]
    assert final == "Hola. Todo bien."
    assert contexto["por_decir"] == "", "el texto ya se habló entero: no queda nada"


def test_lo_que_agrega_una_tool_despues_del_texto_si_hay_que_hablarlo(parser, cerebro_falso):
    cerebro_falso.resp = {"respuesta": "Dale, lo abro.",
                          "tools_call": [{"nombre": "abrir_programa", "args": {"nombre": "brave"}}]}
    cerebro_falso.partir_en = ["Dale, lo abro."]
    contexto = {"voice": _Voz([])}
    final = parser.procesar("abrí brave", contexto)
    assert final == "Dale, lo abro. hice abrir_programa"
    assert contexto["por_decir"] == "hice abrir_programa"


def test_sin_voz_no_hay_streaming(parser, cerebro_falso):
    cerebro_falso.resp = {"respuesta": "Hola.", "tools_call": []}
    cerebro_falso.partir_en = ["Hola."]
    contexto = {}
    assert parser.procesar("hola de nuevo", contexto) == "Hola."
    assert "por_decir" not in contexto and not cerebro_falso.recibio_emisor


class _Voz:
    def __init__(self, dichas):
        self.dichas = dichas

    def decir(self, texto):
        self.dichas.append(texto)


@pytest.fixture
def cerebro_falso(parser):
    """El cerebro simulado que ya usa la fixture ``parser`` de conftest."""
    return parser.brain
