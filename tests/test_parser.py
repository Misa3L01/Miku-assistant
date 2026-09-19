"""Cerebro: confirmaciones, fast-path, herramientas inventadas, caché del LLM y concurrencia."""
from __future__ import annotations

import threading
import time
from unittest import mock

import pytest
import requests

from miku.cerebro import parser as cp

ENERGIA = {"respuesta": "", "tools_call": [{"nombre": "control_energia", "args": {"accion": "apagar"}}]}


# ---------------------------------------------------------------- confirmaciones
def pedir_apagado(parser, cerebro):
    cerebro.resp = ENERGIA
    return parser.procesar("apagá la pc", {})


def test_pide_confirmacion_antes_de_apagar(parser, cerebro, plugin):
    r = pedir_apagado(parser, cerebro)
    assert "Confirmás" in r
    assert plugin.llamadas == []          # todavía NO se ejecutó


@pytest.mark.parametrize("frase", ["no, dejalo así", "no dale", "cancelá", "no lo hagas, ok"])
def test_cancelar_tiene_prioridad_y_no_ejecuta(parser, cerebro, plugin, frase):
    pedir_apagado(parser, cerebro)
    assert "Cancelado" in parser.procesar(frase, {})
    assert plugin.llamadas == []


@pytest.mark.parametrize("frase", ["mostrame el sistema", "así nomás", "silenciá Discord"])
def test_no_confirma_por_substring(parser, cerebro, plugin, frase):
    """'sistema', 'así' y 'silenciá' contienen 'si' pero NO son un sí."""
    pedir_apagado(parser, cerebro)
    assert "Todavía no me confirmaste" in parser.procesar(frase, {})
    assert plugin.llamadas == []


@pytest.mark.parametrize("frase", ["sí", "Sí, dale", "confirmo", "ok"])
def test_confirma_con_palabras_completas(parser, cerebro, plugin, frase):
    pedir_apagado(parser, cerebro)
    assert parser.procesar(frase, {}) == "hice control_energia"
    assert plugin.llamadas == [("control_energia", {"accion": "apagar"})]


def test_la_confirmacion_vence(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    parser._confirmacion_desde -= 120                     # pasaron 2 minutos
    cerebro.resp = {"respuesta": "hola che", "tools_call": []}
    assert parser.procesar("sí", {}) == "hola che"        # se trató como mensaje nuevo
    assert plugin.llamadas == []


# ---------------------------------------------------------------- fast-path
def test_hora_con_acentos_y_signos(parser):
    assert "son las" in parser.procesar("¿Qué hora es?", {})


def test_saludo_con_puntuacion(parser):
    assert parser.procesar("Hola.", {}).startswith("¡Hola")


def test_calculadora_local(parser):
    assert parser.procesar("Cuánto es 15 por 23", {}) == "Son 345."


@pytest.mark.parametrize("frase,esperado", [
    ("Recordame que compre pan", "compre pan"),
    ("recordá que mañana tengo turno", "mañana tengo turno"),
    ("Guardá que mi cumple es el 15 de julio", "mi cumple es el 15 de julio"),
    ("acordate que Sí importa", "Sí importa"),          # conserva acentos y mayúsculas
])
def test_guardar_recuerdo(parser, memoria, frase, esperado):
    assert parser.procesar(frase, {}) == "Anotado, no me olvido."
    assert memoria.datos[-1] == esperado


def test_recordatorio_diferido_no_lo_captura_la_memoria(parser, memoria, cerebro):
    cerebro.resp = {"respuesta": "va", "tools_call": []}
    parser.procesar("recordame sacar la basura en 10 minutos", {})
    assert memoria.datos == []


# ---------------------------------------------------------------- tools
def test_tool_inventada_no_dice_listo(parser, cerebro):
    cerebro.resp = {"respuesta": "", "tools_call": [{"nombre": "inventada", "args": {}}]}
    r = parser.procesar("hacé algo raro", {})
    assert "No tengo una herramienta" in r and "Listo" not in r


def test_tools_peligrosas_las_declaran_los_plugins(parser):
    assert parser._tools_peligrosas() == {"control_energia"}


def test_tools_repetidas_se_ignoran(parser, plugin):
    otro = type(plugin)()
    parser.bus.plugins.append(otro)
    nombres = [t["function"]["name"] for t in parser.recopilar_tools()]
    assert len(nombres) == len(set(nombres))


# ---------------------------------------------------------------- caché del LLM
class _Resp:
    def __init__(self, mensaje):
        self._m = mensaje

    def json(self):
        return {"choices": [{"message": self._m}]}


@pytest.fixture
def cerebro_real(cfg):
    cfg.valores["groq_api_key"] = "gsk_fake"
    cp._CACHE.clear()
    cp._CACHE_ORDEN.clear()
    return cp.BrainGroq(cfg)


TOOLS = [{"type": "function", "function": {"name": "abrir_programa", "parameters": {}}}]


def test_respuesta_con_tools_no_se_cachea(cerebro_real):
    llamadas = []

    def post(url, **kw):
        llamadas.append(1)
        return _Resp({"content": "Abriendo Discord",
                      "tool_calls": [{"function": {"name": "abrir_programa",
                                                   "arguments": '{"nombre": "discord"}'}}]})

    with mock.patch.object(requests, "post", post):
        cerebro_real.consultar("abrí discord", {}, TOOLS)
        segunda = cerebro_real.consultar("abrí discord", {}, TOOLS)
    assert len(llamadas) == 2 and segunda["tools_call"], "repetir una acción debe volver a ejecutarla"


def test_charla_pura_si_se_cachea(cerebro_real):
    llamadas = []

    def post(url, **kw):
        llamadas.append(1)
        return _Resp({"content": "Todo bien", "tool_calls": []})

    with mock.patch.object(requests, "post", post):
        cerebro_real.consultar("cómo estás", {}, TOOLS)
        cerebro_real.consultar("cómo estás", {}, TOOLS)
        assert len(llamadas) == 1
        cerebro_real.consultar("cómo estás", {"miku_memoria": "- algo"}, TOOLS)
        assert len(llamadas) == 2, "con recuerdos en el contexto no se usa la caché"


def test_sin_api_key_avisa(cfg):
    r = cp.BrainGroq(cfg).consultar("hola", {}, TOOLS)
    assert "error" in r


# ---------------------------------------------------------------- hilos
def test_procesar_concurrente(parser, cerebro):
    cerebro.resp = {"respuesta": "ok", "tools_call": []}
    errores = []

    def trabajo():
        try:
            for _ in range(40):
                parser.procesar("hola che", {})
        except Exception as e:  # noqa: BLE001
            errores.append(e)

    hilos = [threading.Thread(target=trabajo) for _ in range(6)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert not errores


# --------------------------------------------------------------------------- #
# Historial de conversación y enrutado de tools en el parser
# --------------------------------------------------------------------------- #
def test_el_llm_recibe_los_turnos_anteriores(parser, cerebro):
    vistos = []
    cerebro.consultar = lambda texto, ctx, tools: vistos.append(ctx.get("historial")) or {
        "respuesta": f"resp {len(vistos)}", "tools_call": []}
    parser.procesar("cómo está el clima en Rosario", {})
    parser.procesar("y mañana?", {})
    assert vistos[0] == []
    assert vistos[1] == [{"role": "user", "content": "cómo está el clima en Rosario"},
                         {"role": "assistant", "content": "resp 1"}]


def test_el_historial_tiene_tope_y_vencimiento(parser, cerebro, monkeypatch):
    vistos = []
    cerebro.consultar = lambda texto, ctx, tools: vistos.append(ctx["historial"]) or {
        "respuesta": "ok", "tools_call": []}
    parser.cfg.valores["historial_turnos"] = 2
    for i in range(5):
        parser.procesar(f"pregunta {i}", {})
    assert [m["content"] for m in vistos[-1] if m["role"] == "user"] == ["pregunta 2", "pregunta 3"]
    ahora = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: ahora + 3600)      # una hora después
    parser.procesar("otra cosa", {})
    assert vistos[-1] == []
    parser.cfg.valores["historial_turnos"] = 0
    parser.procesar("sin historial", {})
    assert vistos[-1] == []


def test_olvidar_historial(parser, cerebro):
    cerebro.resp = {"respuesta": "hola", "tools_call": []}
    parser.procesar("hola", {})
    assert parser._turnos_recientes()
    parser.olvidar_historial()
    assert parser._turnos_recientes() == []


def test_con_historial_no_se_usa_la_cache_del_llm(cerebro_real):
    llamadas = []

    def post(url, **kw):
        llamadas.append(kw["json"]["messages"])
        return _Resp({"content": "Mañana llueve", "tool_calls": []})

    previo = [{"role": "user", "content": "clima"}, {"role": "assistant", "content": "sol"}]
    with mock.patch.object(requests, "post", post):
        cerebro_real.consultar("y mañana?", {"historial": previo}, TOOLS)
        cerebro_real.consultar("y mañana?", {"historial": previo}, TOOLS)
    assert len(llamadas) == 2
    roles = [m["role"] for m in llamadas[0]]
    assert roles == ["system", "user", "assistant", "user"]


def test_el_parser_manda_al_llm_solo_las_tools_relacionadas(parser, cerebro):
    def tool(nombre, desc=""):
        return {"type": "function", "function": {"name": nombre, "description": desc, "parameters": {}}}

    from miku.plugins.base import Plugin

    class Muchas(Plugin):
        nombre = "muchas"
        tools = [tool("abrir_programa", "Abre un programa")] + [tool(f"otra_cosa_{i}", "x") for i in range(20)]

        def manejar_tool(self, n, a, c):
            return f"hice {n}"

    parser.bus.plugins = [Muchas()]
    enviadas = []
    cerebro.consultar = lambda texto, ctx, tools: enviadas.append([t["function"]["name"] for t in tools]) or {
        "respuesta": "", "tools_call": [{"nombre": "otra_cosa_3", "args": {}}]}
    r = parser.procesar("abrí discord", {})
    assert enviadas[0] == ["abrir_programa"]
    assert r == "hice otra_cosa_3"       # una tool del catálogo completo sigue siendo válida
    parser.cfg.valores["enrutar_tools"] = False
    parser.procesar("abrí discord", {})
    assert len(enviadas[1]) == 21
