"""Optimizaciones de latencia: caché de audio del TTS, chequeo de VOICEVOX, pausa de fin de frase del STT,
memoria sin trabajo inútil y reintento del LLM."""
from __future__ import annotations

from unittest import mock

import pytest
import requests

from miku.cerebro import parser as cp
from miku.cerebro.memoria import almacen
from miku.cerebro.memoria.almacen import Memoria
from miku.plataforma import red
from miku.servicios.eventos import EventBus
from miku.voz.entrada.escucha import SpeechToText
from miku.voz.salida import tts as tts_mod

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20


# --------------------------------------------------------------------------- #
# TTS: caché de audio
# --------------------------------------------------------------------------- #
@pytest.fixture
def voz(cfg, monkeypatch):
    """TTS con VOICEVOX "disponible" y contadores de lo que se traduce y se sintetiza."""
    v = tts_mod.TextoAVoz(cfg)
    v.cuenta = {"traducir": 0, "sintetizar": 0, "chequeos": 0}
    monkeypatch.setattr(v, "_asegurar_voicevox", lambda: v.cuenta.__setitem__("chequeos", v.cuenta["chequeos"] + 1) or True)

    def traducir(texto):
        v.cuenta["traducir"] += 1
        return f"ja:{texto}"

    def sintetizar(texto_ja):
        v.cuenta["sintetizar"] += 1
        return WAV + texto_ja.encode("utf-8")

    monkeypatch.setattr(v, "_traducir_a_japones", traducir)
    monkeypatch.setattr(v, "_sintetizar_voicevox", sintetizar)
    return v


def test_una_frase_repetida_no_se_traduce_ni_se_sintetiza_de_nuevo(voz):
    primera = voz._preparar_audio_frase("Listo")
    segunda = voz._preparar_audio_frase("Listo")
    assert primera == segunda and primera["motor"] == "voicevox" and primera["idioma"] == "ja"
    assert voz.cuenta["traducir"] == 1 and voz.cuenta["sintetizar"] == 1


def test_una_frase_de_la_cache_ni_siquiera_consulta_a_voicevox(voz):
    voz._preparar_audio_frase("Listo")
    chequeos = voz.cuenta["chequeos"]
    voz._preparar_audio_frase("Listo")
    assert voz.cuenta["chequeos"] == chequeos


def test_la_cache_sobrevive_entre_sesiones(cfg, voz):
    voz._preparar_audio_frase("Listo")
    otra = tts_mod.TextoAVoz(cfg)                       # "reinicio": otra instancia, misma carpeta
    assert otra._cache_audio().obtener("Listo", f"vv{otra.speaker_id}") is not None


def test_distintas_voces_no_comparten_audio(cfg, voz):
    voz._preparar_audio_frase("Listo")
    cfg.valores["voicevox_speaker_id"] = 99
    otra = tts_mod.TextoAVoz(cfg)
    assert otra._cache_audio().obtener("Listo", f"vv{otra.speaker_id}") is None


def test_con_la_cache_apagada_siempre_sintetiza(voz, cfg):
    cfg.valores["tts_cache"] = False
    voz._preparar_audio_frase("Listo")
    voz._preparar_audio_frase("Listo")
    assert voz._cache_audio() is None and voz.cuenta["sintetizar"] == 2


def test_si_voicevox_falla_no_se_guarda_nada(voz, monkeypatch):
    monkeypatch.setattr(voz, "_sintetizar_voicevox", lambda t: None)
    assert voz._preparar_audio_frase("Listo")["motor"] == "sistema"
    assert voz._cache_audio().obtener("Listo", f"vv{voz.speaker_id}") is None


def test_no_se_cachean_las_respuestas_largas(voz):
    larga = "palabra " * 40
    voz._preparar_audio_frase(larga)
    voz._preparar_audio_frase(larga)
    assert voz.cuenta["sintetizar"] == 2


def test_precalentar_prepara_el_saludo_una_sola_vez(voz, monkeypatch):
    monkeypatch.setattr(voz, "asegurar_voicevox_inicial", lambda: True)
    voz.precalentar(["¿Sí? Decime."])
    hechas = voz.cuenta["sintetizar"]
    assert hechas == 2, "dividir_en_frases parte '¿Sí?' y 'Decime.'"
    voz.precalentar(["¿Sí? Decime."])
    assert voz.cuenta["sintetizar"] == hechas, "la segunda vez sale todo de la caché"


def test_precalentar_no_toca_un_motor_de_voz_propio(voz, cfg, monkeypatch):
    """Con TTS_MOTOR = "comando" no se va a ejecutar el comando del usuario solo para calentar."""
    cfg.valores["tts_motor"] = "comando"
    monkeypatch.setattr(voz, "asegurar_voicevox_inicial", lambda: True)
    voz.precalentar(["¿Sí? Decime."])
    assert voz.cuenta["sintetizar"] == 0 and voz.cuenta["traducir"] == 0


def test_precalentar_no_hace_nada_si_voicevox_no_esta(voz, monkeypatch):
    monkeypatch.setattr(voz, "asegurar_voicevox_inicial", lambda: False)
    voz.precalentar(["¿Sí? Decime."])
    assert voz.cuenta["sintetizar"] == 0


# --------------------------------------------------------------------------- #
# TTS: no preguntarle a VOICEVOX por cada frase si ya respondió hace instantes
# --------------------------------------------------------------------------- #
def test_no_se_vuelve_a_verificar_voicevox_por_cada_frase(cfg, monkeypatch):
    v = tts_mod.TextoAVoz(cfg)
    verificaciones = []
    monkeypatch.setattr(v, "_verificar_voicevox", lambda: verificaciones.append(1) or True)
    assert v._asegurar_voicevox() and v._asegurar_voicevox() and v._asegurar_voicevox()
    assert len(verificaciones) == 1


def test_tras_una_sintesis_fallida_se_vuelve_a_verificar(cfg, monkeypatch):
    v = tts_mod.TextoAVoz(cfg)
    verificaciones = []
    monkeypatch.setattr(v, "_verificar_voicevox", lambda: verificaciones.append(1) or True)
    v._asegurar_voicevox()

    def cae(*a, **k):
        raise requests.exceptions.ConnectionError("se cayó")

    with mock.patch.object(red, "post", cae):
        assert v._sintetizar_voicevox("こんにちは") is None
    v._asegurar_voicevox()
    assert len(verificaciones) == 2, "el fallo invalida el chequeo anterior"


class _Reloj:
    """Reloj falso: ``sleep`` adelanta el tiempo, así las esperas de los tests no tardan de verdad."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t

    def sleep(self, segundos):
        self.t += segundos


class _Proceso:
    def __init__(self, termina_solo=False):
        self.pid, self.returncode = 4242, 1
        self._vivo = not termina_solo

    def poll(self):
        return None if self._vivo else self.returncode


@pytest.fixture
def arranque(cfg, monkeypatch):
    """TTS a punto de lanzar VOICEVOX: el motor "existe", el lanzamiento y el reloj son falsos."""
    v = tts_mod.TextoAVoz(cfg)
    reloj = _Reloj()
    monkeypatch.setattr(tts_mod, "time", reloj)
    monkeypatch.setattr(v, "_buscar_run_voicevox", lambda: "C:/x/run.exe")
    monkeypatch.setattr(v, "_es_engine_voicevox", lambda ruta: True)
    monkeypatch.setattr(tts_mod.subprocess, "Popen", lambda *a, **k: v.proceso)
    v.proceso, v.reloj, v.sondeos = _Proceso(), reloj, []
    return v


def test_arranca_voicevox_y_lo_da_por_listo_apenas_responde(arranque, monkeypatch):
    respuestas = iter([False, False, False, False, True])          # el chequeo previo + 3 sondeos en frío + listo

    def verificar(timeout=2.0):
        arranque.sondeos.append(timeout)
        return next(respuestas)

    monkeypatch.setattr(arranque, "_verificar_voicevox", verificar)
    assert arranque._asegurar_voicevox() is True
    assert len(arranque.sondeos) == 5 and arranque.sondeos[1:] == [1.0] * 4
    assert arranque.reloj.t - 1000.0 < 3, "sondea cada medio segundo: no se pierde tiempo tras estar listo"
    assert arranque._voicevox_lo_lanzamos


def test_si_voicevox_nunca_levanta_se_rinde_por_tiempo_y_no_para_siempre(arranque, monkeypatch):
    monkeypatch.setattr(arranque, "_verificar_voicevox", lambda timeout=2.0: False)
    assert arranque._asegurar_voicevox() is False
    transcurrido = arranque.reloj.t - 1000.0
    assert tts_mod._ESPERA_ARRANQUE_VOICEVOX <= transcurrido < tts_mod._ESPERA_ARRANQUE_VOICEVOX + 2


def test_si_el_proceso_de_voicevox_muere_no_se_espera_en_vano(arranque, monkeypatch):
    arranque.proceso = _Proceso(termina_solo=True)
    monkeypatch.setattr(arranque, "_verificar_voicevox", lambda timeout=2.0: False)
    assert arranque._asegurar_voicevox() is False
    assert arranque.reloj.t - 1000.0 < 3


@pytest.mark.parametrize("configurada,esperadas", [
    ("http://localhost:50021", ["http://127.0.0.1:50021", "http://localhost:50021"]),
    ("http://127.0.0.1:50021", ["http://127.0.0.1:50021", "http://localhost:50021"]),
    ("http://otra-pc:50021", ["http://otra-pc:50021"]),
])
def test_orden_de_las_urls_de_voicevox(configurada, esperadas):
    """Con localhost se prueba primero 127.0.0.1: mientras no responde, localhost cuesta el doble."""
    assert tts_mod.TextoAVoz._construir_urls_a_probar(configurada) == esperadas


# --------------------------------------------------------------------------- #
# STT: pausa que cierra la frase
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("valor,esperado", [(None, 0.6), (0.6, 0.6), ("0.9", 0.9), (0.1, 0.3), (5, 1.5), ("rara", 0.6)])
def test_la_pausa_de_fin_de_frase_se_acota(cfg, valor, esperado):
    if valor is not None:
        cfg.valores["stt_pausa_fin"] = valor
    assert SpeechToText(cfg)._pausa_fin() == esperado


def test_el_reconocedor_usa_la_pausa_configurada(cfg):
    cfg.valores["stt_pausa_fin"] = 0.5
    stt = SpeechToText(cfg)
    stt._importar_dependencias()
    assert stt._reconocedor.pause_threshold == 0.5
    assert stt._reconocedor.non_speaking_duration <= 0.5, "la librería exige non_speaking <= pause_threshold"


# --------------------------------------------------------------------------- #
# Memoria: sin recuerdos no hay nada que buscar (ni modelo que cargar)
# --------------------------------------------------------------------------- #
class _MotorProhibido:
    """Falla si alguien intenta usar embeddings: con la memoria vacía no debería."""

    def __getattr__(self, nombre):
        raise AssertionError(f"no se debía tocar el motor de embeddings ({nombre})")


def test_buscar_en_una_memoria_vacia_no_carga_el_motor_de_embeddings(tmp_path, monkeypatch):
    monkeypatch.setattr(almacen, "_emb", lambda: _MotorProhibido())
    m = Memoria(str(tmp_path / "memoria.db"))
    try:
        assert m.buscar_recuerdos("cuándo es mi cumple") == ""
        assert m.buscar_recuerdos("") == ""
    finally:
        m.cerrar()


def test_con_recuerdos_sigue_buscando_por_palabras_si_no_hay_motor(tmp_path, monkeypatch):
    monkeypatch.setattr(almacen, "_emb", lambda: None)
    m = Memoria(str(tmp_path / "memoria.db"))
    try:
        m.guardar_recuerdo("mi cumple es el 15 de julio")
        assert "cumple" in m.buscar_recuerdos("cumple")
    finally:
        m.cerrar()


def test_precalentar_embeddings_sin_motor_no_falla(monkeypatch):
    from miku.cerebro.memoria import embeddings
    monkeypatch.setattr(embeddings, "_cargar_modelo", lambda: None)
    embeddings.precalentar()


# --------------------------------------------------------------------------- #
# LLM: un reintento ante 429/5xx pasajeros
# --------------------------------------------------------------------------- #
class _Http:
    def __init__(self, codigo, datos, cabeceras=None):
        self.status_code, self._datos, self.headers = codigo, datos, cabeceras or {}

    def json(self):
        return self._datos


OK = {"choices": [{"message": {"content": "Todo bien", "tool_calls": []}}]}
LIMITE = {"error": {"message": "rate limit"}}


@pytest.fixture
def cerebro(cfg, monkeypatch):
    cfg.valores["groq_api_key"] = "gsk_fake"
    cp._CACHE.clear()
    cp._CACHE_ORDEN.clear()
    esperas = []
    monkeypatch.setattr(cp.time, "sleep", lambda s: esperas.append(s))
    b = cp.BrainGroq(cfg)
    b.esperas = esperas
    return b


def _post_con(respuestas, llamadas):
    it = iter(respuestas)

    def post(url, **kw):
        llamadas.append(url)
        return next(it)

    return post


def test_ante_un_limite_pasajero_reintenta_una_vez_y_responde(cerebro):
    llamadas = []
    post = _post_con([_Http(429, LIMITE, {"retry-after": "1"}), _Http(200, OK)], llamadas)
    with mock.patch.object(red, "post", post):
        r = cerebro.consultar("cómo estás", {}, [])
    assert r["respuesta"] == "Todo bien" and len(llamadas) == 2 and cerebro.esperas == [1.0]


def test_un_error_del_servidor_tambien_se_reintenta(cerebro):
    llamadas = []
    post = _post_con([_Http(503, {"error": "caído"}), _Http(200, OK)], llamadas)
    with mock.patch.object(red, "post", post):
        assert cerebro.consultar("hola", {}, [])["respuesta"] == "Todo bien"
    assert len(llamadas) == 2


def test_si_el_limite_es_largo_no_espera_y_lo_dice(cerebro):
    """Un límite diario (retry-after de minutos u horas) no se arregla esperando unos segundos."""
    llamadas = []
    post = _post_con([_Http(429, LIMITE, {"retry-after": "3600"})], llamadas)
    with mock.patch.object(red, "post", post):
        r = cerebro.consultar("hola", {}, [])
    assert len(llamadas) == 1 and not cerebro.esperas
    assert "límite" in r["error"]


def test_no_reintenta_mas_de_una_vez(cerebro):
    llamadas = []
    post = _post_con([_Http(429, LIMITE, {"retry-after": "0"}), _Http(429, LIMITE, {"retry-after": "0"})], llamadas)
    with mock.patch.object(red, "post", post):
        r = cerebro.consultar("hola", {}, [])
    assert len(llamadas) == 2 and "límite" in r["error"]


def test_una_respuesta_buena_no_se_reintenta_ni_espera(cerebro):
    llamadas = []
    with mock.patch.object(red, "post", _post_con([_Http(200, OK)], llamadas)):
        cerebro.consultar("hola", {}, [])
    assert len(llamadas) == 1 and not cerebro.esperas, "ya no hay espera artificial de 250 ms entre llamadas"


def test_dos_consultas_seguidas_no_esperan_entre_si(cerebro):
    llamadas = []
    with mock.patch.object(red, "post", _post_con([_Http(200, OK), _Http(200, OK)], llamadas)):
        cerebro.consultar("uno", {"miku_memoria": "x"}, [])
        cerebro.consultar("dos", {"miku_memoria": "x"}, [])
    assert not cerebro.esperas


# --------------------------------------------------------------------------- #
# Registro de plugins (lo único que queda del "bus")
# --------------------------------------------------------------------------- #
class _P:
    def __init__(self, nombre):
        self.nombre = nombre


def test_el_bus_registra_sin_duplicar_y_lista_los_nombres():
    bus = EventBus()
    a, b = _P("a"), _P("b")
    for p in (a, b, a):
        bus.registrar_plugin(p)
    assert bus.plugins == [a, b] and bus.listar_plugins() == ["a", "b"]
    assert bus.voice is None and bus.parser is None and bus.contexto_base is None
