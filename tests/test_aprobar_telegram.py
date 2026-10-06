"""Preguntas al celular: botones Sí / No por Telegram cuando no contestás a tiempo (Telegram simulado)."""
from __future__ import annotations

import asyncio
import logging
import time
import types
from typing import Any, List, Optional, Tuple

import pytest
import requests

from miku import app
from miku.ajustes import carga as config_mod
from miku.plataforma import red
from miku.plugins.social.telegram_bot import TelegramControl

ENERGIA = {"respuesta": "", "tools_call": [{"nombre": "control_energia", "args": {"accion": "apagar"}}]}


def pedir_apagado(parser, cerebro) -> str:
    cerebro.resp = ENERGIA
    return parser.procesar("apagá la pc", {})


# --------------------------------------------------------------------------- #
# Parser: número de pregunta y botones
# --------------------------------------------------------------------------- #
def test_la_pregunta_pendiente_lleva_numero_y_texto(parser, cerebro):
    assert parser.pregunta_pendiente() is None
    pedir_apagado(parser, cerebro)
    assert parser.pregunta_pendiente() == (1, "¿Apago la PC?")
    parser.procesar("no", {})
    assert parser.pregunta_pendiente() is None
    pedir_apagado(parser, cerebro)
    assert parser.pregunta_pendiente() == (2, "¿Apago la PC?"), "cada pregunta tiene su número"


def test_un_boton_si_ejecuta_la_accion(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    assert parser.resolver_confirmacion(1, True, {}) == "hice control_energia"
    assert plugin.llamadas == [("control_energia", {"accion": "apagar"})]
    assert parser.pregunta_pendiente() is None


def test_un_boton_no_cancela(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    assert parser.resolver_confirmacion(1, False, {}) == "Cancelado, no hice nada."
    assert plugin.llamadas == [] and parser.pregunta_pendiente() is None


def test_un_boton_de_una_pregunta_ya_contestada_no_hace_nada(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    parser.procesar("no", {})                                  # la contestaste en la PC
    assert parser.resolver_confirmacion(1, True, {}) is None
    assert plugin.llamadas == []


def test_un_boton_viejo_no_resuelve_una_pregunta_nueva(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    parser.procesar("no", {})
    pedir_apagado(parser, cerebro)                             # pregunta 2
    assert parser.resolver_confirmacion(1, True, {}) is None
    assert plugin.llamadas == [] and parser.pregunta_pendiente() == (2, "¿Apago la PC?")


def test_sin_pregunta_pendiente_un_boton_no_hace_nada(parser, plugin):
    assert parser.resolver_confirmacion(1, True, {}) is None
    assert plugin.llamadas == []


def test_sin_habilitar_el_boton_vence_con_el_si_suelto_al_minuto(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    parser._confirmacion_desde -= 120
    assert parser.resolver_confirmacion(1, True, {}) is None
    assert plugin.llamadas == [] and parser.pregunta_pendiente() is None


def test_los_botones_habilitados_valen_mas_que_el_si_suelto(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    assert parser.habilitar_boton(1, 300) is True
    parser._confirmacion_desde -= 120                          # pasaron 2 minutos
    assert parser.pregunta_pendiente() is None and parser.espera_respuesta() is False
    cerebro.resp = {"respuesta": "hola che", "tools_call": []}
    assert parser.procesar("sí", {}) == "hola che", "un 'sí' suelto ya no confirma"
    assert plugin.llamadas == []
    assert parser.resolver_confirmacion(1, True, {}) == "hice control_energia", "pero el botón todavía sí"
    assert plugin.llamadas == [("control_energia", {"accion": "apagar"})]


def test_los_botones_tambien_vencen(parser, cerebro, plugin):
    pedir_apagado(parser, cerebro)
    parser.habilitar_boton(1, 300)
    parser._confirmacion_desde -= 120
    parser._boton_hasta = time.monotonic() - 1
    assert parser.resolver_confirmacion(1, True, {}) is None
    assert plugin.llamadas == [] and parser._espera_confirmacion is False


def test_no_se_habilitan_botones_de_otra_pregunta_ni_de_ninguna(parser, cerebro):
    assert parser.habilitar_boton(1, 300) is False
    pedir_apagado(parser, cerebro)
    assert parser.habilitar_boton(5, 300) is False
    parser._confirmacion_desde -= 120
    assert parser.habilitar_boton(1, 300) is False, "ya vencida: no se la resucita"


# --------------------------------------------------------------------------- #
# Plugin de Telegram
# --------------------------------------------------------------------------- #
class Respuesta:
    def __init__(self, codigo=200, datos=None):
        self.status_code, self._datos = codigo, datos if datos is not None else {"ok": True, "result": {"message_id": 42}}

    def json(self):
        return self._datos


@pytest.fixture
def telegram(cfg, monkeypatch):
    cfg.valores.update(telegram_bot_token="123:ABC", telegram_chat_id="777")
    monkeypatch.setattr(config_mod, "config", cfg)
    enviados: List[dict] = []
    estado = {"respuesta": Respuesta()}

    def post(url, **kw):
        enviados.append({"url": url, **kw})
        r = estado["respuesta"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(red, "post", post)
    p = TelegramControl()
    p.enviados, p.estado = enviados, estado
    return p


def test_la_pregunta_sale_con_botones_si_y_no(telegram):
    assert telegram.preguntar(7, "¿Apago la PC?") is True
    envio = telegram.enviados[0]
    assert envio["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert envio["json"]["chat_id"] == "777" and "¿Apago la PC?" in envio["json"]["text"]
    botones = envio["json"]["reply_markup"]["inline_keyboard"][0]
    assert [(b["text"], b["callback_data"]) for b in botones] == [("✅ Sí", "ok:7"), ("❌ No", "no:7")]
    assert telegram._preguntas[7][1] == 42, "recuerda el mensaje para poder quitarle los botones"


def test_sin_token_o_sin_chat_no_manda_nada(telegram, cfg):
    cfg.valores["telegram_bot_token"] = ""
    assert telegram.preguntar(7, "¿Apago la PC?") is False
    cfg.valores.update(telegram_bot_token="123:ABC", telegram_chat_id="")
    assert telegram.preguntar(7, "¿Apago la PC?") is False
    assert telegram.enviados == []


def test_si_telegram_rechaza_el_mensaje_devuelve_false(telegram):
    telegram.estado["respuesta"] = Respuesta(400, {"ok": False, "description": "chat not found"})
    assert telegram.preguntar(7, "¿Apago la PC?") is False and 7 not in telegram._preguntas


def test_un_error_de_red_no_filtra_el_token_al_log(telegram, caplog):
    telegram.estado["respuesta"] = requests.ConnectionError(
        "HTTPSConnectionPool: Max retries exceeded with url: /bot123:ABC/sendMessage")
    with caplog.at_level(logging.DEBUG):
        assert telegram.preguntar(7, "¿Apago la PC?") is False
    assert "123:ABC" not in caplog.text and "ConnectionError" in caplog.text


def test_cerrar_una_pregunta_le_quita_los_botones_con_una_nota(telegram):
    telegram.preguntar(7, "¿Apago la PC?")
    telegram.enviados.clear()
    telegram.cerrar_pregunta(7, "Resuelto en la PC.")
    envio = telegram.enviados[0]
    assert envio["url"].endswith("/editMessageText")
    assert envio["json"]["message_id"] == 42 and envio["json"]["text"].endswith("Resuelto en la PC.")
    assert "reply_markup" not in envio["json"], "sin teclado: Telegram lo quita"
    telegram.cerrar_pregunta(7, "otra vez")                    # ya no está: no vuelve a editar
    assert len(telegram.enviados) == 1


class Consulta:
    """Lo que Telegram entrega al tocar un botón."""

    def __init__(self, datos: str, texto: str = "🎙️ Miku pregunta:\n¿Apago la PC?") -> None:
        self.data, self.message = datos, types.SimpleNamespace(text=texto)
        self.respondida: Optional[str] = "sin responder"
        self.editado: Optional[str] = None

    async def answer(self, texto: Optional[str] = None) -> None:
        self.respondida = texto

    async def edit_message_text(self, texto: str) -> None:
        self.editado = texto


class ParserDeBotones:
    def __init__(self, resultado: Optional[str] = "Apagando la PC.") -> None:
        self.resultado, self.llamadas = resultado, []

    def resolver_confirmacion(self, numero: int, acepta: bool, contexto: Any) -> Optional[str]:
        self.llamadas.append((numero, acepta))
        return self.resultado


def _tocar(telegram, datos: str, usuario: int = 777, parser: Optional[ParserDeBotones] = None) -> Tuple[Consulta, Any]:
    parser = parser or ParserDeBotones()
    telegram._parser = parser
    telegram._event_bus = types.SimpleNamespace(parser=parser)
    consulta = Consulta(datos)
    update = types.SimpleNamespace(callback_query=consulta, effective_user=types.SimpleNamespace(id=usuario))
    asyncio.run(telegram._on_boton(update, None))
    return consulta, parser


def test_tocar_si_resuelve_en_la_pc_y_muestra_el_resultado(telegram):
    telegram._preguntas[7] = ("777", 42, "x")
    consulta, parser = _tocar(telegram, "ok:7")
    assert parser.llamadas == [(7, True)]
    assert consulta.respondida == "Listo" and consulta.editado.endswith("✅ Sí: Apagando la PC.")
    assert 7 not in telegram._preguntas


def test_tocar_no_cancela(telegram):
    consulta, parser = _tocar(telegram, "no:7", parser=ParserDeBotones("Cancelado, no hice nada."))
    assert parser.llamadas == [(7, False)] and consulta.editado.endswith("❌ No: Cancelado, no hice nada.")


def test_tocar_un_boton_de_una_pregunta_ya_resuelta_lo_avisa(telegram):
    consulta, parser = _tocar(telegram, "ok:7", parser=ParserDeBotones(resultado=None))
    assert consulta.respondida == "Eso ya estaba resuelto."
    assert consulta.editado.endswith("Ya estaba resuelto en la PC.")


def test_solo_el_usuario_autorizado_puede_tocar_los_botones(telegram):
    consulta, parser = _tocar(telegram, "ok:7", usuario=999)
    assert parser.llamadas == [] and consulta.editado is None


@pytest.mark.parametrize("datos", ["", "ok", "ok:abc", "raro:7", "ok:"])
def test_un_boton_mal_formado_se_ignora(telegram, datos):
    consulta, parser = _tocar(telegram, datos)
    assert parser.llamadas == [] and consulta.editado is None


def test_si_el_parser_falla_al_resolver_avisa_sin_romper(telegram):
    class Roto:
        def resolver_confirmacion(self, *a):
            raise RuntimeError("boom")

    consulta, _ = _tocar(telegram, "ok:7", parser=Roto())
    assert consulta.editado.endswith("Tuve un problema ejecutándolo.")


# --------------------------------------------------------------------------- #
# La app: a los 20 s sin respuesta, la pregunta va al celular
# --------------------------------------------------------------------------- #
class ParserDePregunta:
    def __init__(self) -> None:
        self.pendiente: Optional[Tuple[int, str]] = (3, "¿Apago la PC?")
        self.habilitados: List[Tuple[int, float]] = []
        self.respuesta = "Listo."

    def pregunta_pendiente(self):
        return self.pendiente

    def habilitar_boton(self, numero: int, segundos: float) -> bool:
        self.habilitados.append((numero, segundos))
        return True

    def espera_respuesta(self) -> bool:
        return self.pendiente is not None

    def procesar(self, texto: str, contexto: Any) -> str:
        return self.respuesta


class TelegramFalso:
    nombre = "telegram_control"

    def __init__(self, sale: bool = True) -> None:
        self.sale, self.preguntas, self.cerradas = sale, [], []

    def preguntar(self, numero: int, texto: str) -> bool:
        self.preguntas.append((numero, texto))
        return self.sale

    def cerrar_pregunta(self, numero: int, nota: str) -> None:
        self.cerradas.append((numero, nota))


class Temporizador:
    creados: List["Temporizador"] = []

    def __init__(self, espera, funcion, args=()) -> None:
        self.espera, self.funcion, self.args, self.iniciado = espera, funcion, args, False
        self.daemon, self.name = False, ""
        Temporizador.creados.append(self)

    def start(self) -> None:
        self.iniciado = True


@pytest.fixture
def asistente(cfg, monkeypatch):
    Temporizador.creados = []
    monkeypatch.setattr(app.threading, "Timer", Temporizador)
    a = app.Asistente(cfg)
    a.parser, a.telegram = ParserDePregunta(), TelegramFalso()
    a.bus.plugins = [a.telegram]
    return a


def test_a_los_veinte_segundos_sin_respuesta_la_pregunta_va_al_celular(asistente):
    asistente._pregunta_al_celular()
    (t,) = Temporizador.creados
    assert t.iniciado and t.daemon and t.espera == 20.0 and t.args == (3, "¿Apago la PC?")
    t.funcion(*t.args)                                         # pasaron los 20 s
    assert asistente.telegram.preguntas == [(3, "¿Apago la PC?")]
    assert asistente.parser.habilitados == [(3, 300.0)], "los botones valen 5 minutos"
    assert asistente._pregunta_enviada == 3


def test_si_contestas_antes_no_se_manda_nada(asistente):
    asistente._pregunta_al_celular()
    asistente.parser.pendiente = None                          # la contestaste por voz
    t = Temporizador.creados[0]
    t.funcion(*t.args)
    assert asistente.telegram.preguntas == [] and asistente._pregunta_enviada is None


def test_si_ya_es_otra_pregunta_no_se_manda_la_vieja(asistente):
    asistente._pregunta_al_celular()
    asistente.parser.pendiente = (4, "¿Reinicio la PC?")
    t = Temporizador.creados[0]
    t.funcion(*t.args)
    assert asistente.telegram.preguntas == []


def test_el_tiempo_de_espera_y_la_validez_se_configuran(asistente, cfg):
    cfg.valores.update(telegram_aprobar_seg=45, telegram_aprobar_vence_min=2)
    asistente._pregunta_al_celular()
    t = Temporizador.creados[0]
    assert t.espera == 45.0
    t.funcion(*t.args)
    assert asistente.parser.habilitados == [(3, 120.0)]


def test_con_cero_no_se_manda_nunca(asistente, cfg):
    cfg.valores["telegram_aprobar_seg"] = 0
    asistente._pregunta_al_celular()
    assert Temporizador.creados == []


def test_sin_pregunta_pendiente_no_se_programa_nada(asistente):
    asistente.parser.pendiente = None
    asistente._pregunta_al_celular()
    assert Temporizador.creados == []


def test_sin_telegram_cargado_no_pasa_nada(asistente):
    asistente.bus.plugins = []
    asistente._mandar_pregunta(3, "¿Apago la PC?")
    assert asistente._pregunta_enviada is None and asistente.parser.habilitados == []


def test_si_telegram_no_pudo_mandarla_no_se_habilitan_botones(asistente):
    asistente.telegram.sale = False
    asistente._mandar_pregunta(3, "¿Apago la PC?")
    assert asistente._pregunta_enviada is None and asistente.parser.habilitados == []


def test_si_la_contestas_en_la_pc_se_le_quitan_los_botones_al_celular(asistente):
    asistente._mandar_pregunta(3, "¿Apago la PC?")
    asistente.parser.pendiente = None                          # la resolviste por voz
    asistente.responder("sí", origen="voz")
    assert asistente.telegram.cerradas == [(3, "Resuelto en la PC.")]
    assert asistente._pregunta_enviada is None
    asistente.responder("hola")
    assert len(asistente.telegram.cerradas) == 1, "una sola vez"


def test_si_sigue_pendiente_los_botones_se_quedan(asistente):
    asistente._mandar_pregunta(3, "¿Apago la PC?")
    asistente.responder("mmm", origen="voz")
    assert asistente.telegram.cerradas == [] and asistente._pregunta_enviada == 3


def test_una_orden_por_voz_que_deja_una_pregunta_la_programa(asistente, monkeypatch):
    monkeypatch.setattr(asistente, "responder", lambda texto, origen="texto": "¿Apago la PC?")
    asistente.stt = types.SimpleNamespace(esperar_respuesta=lambda: None)
    asistente._al_comando_voz("apagá la pc")
    assert len(Temporizador.creados) == 1
