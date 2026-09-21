"""Proveedores intercambiables: LLM local/nube, STT nube/local y motores de voz propios."""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from miku.plataforma import red
from miku.cerebro import parser as cp
from miku.voz.entrada import transcriptores
from miku.voz.entrada.escucha import SpeechToText
from miku.voz.salida import motores, tts as tts_mod

TOOLS = [{"type": "function", "function": {"name": "abrir_programa", "parameters": {}}}]


class _Resp:
    def __init__(self, contenido="hola"):
        self._d = {"choices": [{"message": {"content": contenido}}]}

    def json(self):
        return self._d


@pytest.fixture
def cerebro(cfg):
    cp._CACHE.clear()
    cp._CACHE_ORDEN.clear()
    return cp.BrainGroq(cfg)


def _consultar(cerebro, texto="hola"):
    visto = {}

    def post(url, headers=None, json=None, timeout=None):
        visto.update(url=url, headers=headers, json=json, timeout=timeout)
        return _Resp()

    with mock.patch.object(red, "post", post):
        r = cerebro.consultar(texto, {}, TOOLS)
    return r, visto


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
def test_por_defecto_usa_groq_con_su_clave(cerebro, cfg):
    cfg.valores["groq_api_key"] = "gsk_x"
    r, v = _consultar(cerebro)
    assert v["url"] == "https://api.groq.com/openai/v1/chat/completions" and v["headers"]["Authorization"] == "Bearer gsk_x"
    assert v["json"]["model"] == cfg.modelo_api_externa and v["json"]["tools"] == TOOLS


def test_sin_clave_de_groq_avisa_sin_llamar(cerebro):
    r, v = _consultar(cerebro)
    assert "error" in r and not v


def test_servidor_local_no_necesita_clave_ni_agrega_chat_completions_dos_veces(cerebro, cfg):
    cfg.valores.update(llm_base_url="http://localhost:11434/v1/", llm_modelo="qwen2.5:7b")
    r, v = _consultar(cerebro)
    assert r["respuesta"] == "hola"
    assert v["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in v["headers"] and v["json"]["model"] == "qwen2.5:7b"
    cfg.valores["llm_base_url"] = "http://x/v1/chat/completions"
    assert _consultar(cerebro, "otra")[1]["url"] == "http://x/v1/chat/completions"


def test_servidor_propio_con_clave_la_manda(cerebro, cfg):
    cfg.valores.update(llm_base_url="https://api.otro.com/v1", llm_api_key="abc")
    assert _consultar(cerebro)[1]["headers"]["Authorization"] == "Bearer abc"


def test_modelo_local_sin_tools_no_las_recibe(cerebro, cfg):
    cfg.valores.update(llm_base_url="http://localhost:1234/v1", llm_soporta_tools=False)
    j = _consultar(cerebro)[1]["json"]
    assert "tools" not in j and "tool_choice" not in j


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #
class _Audio:
    def get_wav_data(self):
        return b"RIFFfake"


def test_se_elige_el_transcriptor_segun_la_config(cfg):
    assert isinstance(transcriptores.crear_transcriptor(cfg), transcriptores.WhisperGroq)
    cfg.valores["stt_proveedor"] = "local"
    assert isinstance(transcriptores.crear_transcriptor(cfg), transcriptores.WhisperLocal)
    cfg.valores["stt_proveedor"] = "marciano"
    assert isinstance(transcriptores.crear_transcriptor(cfg), transcriptores.WhisperGroq)


def test_whisper_groq_transcribe_y_sin_clave_no_llama(cfg):
    visto = {}

    def post(url, headers=None, files=None, data=None, timeout=None):
        visto.update(url=url, data=data)
        return types.SimpleNamespace(json=lambda: {"text": " abrí discord "})

    t = transcriptores.WhisperGroq(cfg)
    with mock.patch.object(red, "post", post):
        assert t.transcribir(b"x", "whisper-large-v3", 5) == ""          # sin clave
        assert not visto
        cfg.valores["groq_api_key"] = "gsk"
        assert t.transcribir(b"x", "whisper-large-v3", 5) == "abrí discord"
    assert visto["data"]["model"] == "whisper-large-v3" and visto["data"]["language"] == "es"


def test_whisper_groq_errores_devuelven_vacio(cfg):
    cfg.valores["groq_api_key"] = "gsk"
    t = transcriptores.WhisperGroq(cfg)
    with mock.patch.object(red, "post", lambda *a, **k: types.SimpleNamespace(json=lambda: {"error": 1})):
        assert t.transcribir(b"x", "m", 5) == ""
    with mock.patch.object(red, "post", mock.Mock(side_effect=OSError("red"))):
        assert t.transcribir(b"x", "m", 5) == ""


def _falso_faster_whisper(monkeypatch, textos, cargas):
    class Segmento:
        def __init__(self, text):
            self.text = text

    class WhisperModel:
        def __init__(self, tamano, device=None, compute_type=None):
            cargas.append((tamano, device, compute_type))

        def transcribe(self, archivo, **kw):
            assert hasattr(archivo, "read") and kw["language"] == "es"
            return iter(Segmento(t) for t in textos), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))


def test_whisper_local_carga_una_vez_y_une_los_segmentos(cfg, monkeypatch):
    cargas = []
    _falso_faster_whisper(monkeypatch, [" poné ", "música "], cargas)
    cfg.valores["stt_modelo_local"] = "base"
    t = transcriptores.WhisperLocal(cfg)
    assert t.transcribir(b"RIFF", "ignorado", 5) == "poné música"
    t.transcribir(b"RIFF", "ignorado", 5)
    assert cargas == [("base", "auto", "int8")]


def test_whisper_local_sin_la_libreria_falla_una_vez_y_no_reintenta(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)     # import falla
    t = transcriptores.WhisperLocal(cfg)
    assert t.transcribir(b"x", "m", 5) == "" and t._fallo
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=mock.Mock()))
    assert t.transcribir(b"x", "m", 5) == ""                       # no vuelve a intentar


def test_escucha_usa_el_transcriptor_y_filtra_alucinaciones(cfg, monkeypatch):
    class Falso:
        nombre = "falso"

        def __init__(self, texto):
            self.texto = texto
            self.pedidos = []

        def transcribir(self, wav, modelo, timeout):
            self.pedidos.append((wav, modelo, timeout))
            return self.texto

    stt = SpeechToText(cfg)
    monkeypatch.setattr(stt, "_importar_dependencias", lambda: None)
    stt._transcriptor = Falso("Miku, qué hora es")
    assert stt.transcribir_audio(_Audio()) == "Miku, qué hora es"
    assert stt._transcriptor.pedidos == [(b"RIFFfake", cfg.modelo_stt_comando, 15)]
    stt._transcriptor = Falso("Subtítulos por la comunidad de Amara.org")
    assert stt._transcribir_wake(_Audio()) == ""


# --------------------------------------------------------------------------- #
# Motor de voz por comando
# --------------------------------------------------------------------------- #
def _script(tmp_path, cuerpo):
    p = tmp_path / "motor.py"
    p.write_text(cuerpo, encoding="utf-8")
    return f'"{sys.executable}" "{p}"'


ESCRIBE_STDIN = ("import sys\ntexto = sys.stdin.read()\n"
                 "open(sys.argv[1], 'wb').write(b'WAV:' + texto.encode('utf-8'))\n")
ESCRIBE_ARG = ("import sys\nopen(sys.argv[1], 'wb').write(b'ARG:' + sys.argv[2].encode('utf-8'))\n")


def test_motor_comando_recibe_el_texto_por_stdin(tmp_path):
    m = motores.MotorComando(_script(tmp_path, ESCRIBE_STDIN) + " {salida}")
    assert m.valido and m.sintetizar("hola ñandú") == "WAV:hola ñandú".encode("utf-8")


def test_motor_comando_con_texto_como_argumento(tmp_path):
    m = motores.MotorComando(_script(tmp_path, ESCRIBE_ARG) + " {salida} {texto}")
    assert m.sintetizar("hola") == b"ARG:hola"


def test_motor_comando_fallos_devuelven_none(tmp_path):
    assert motores.MotorComando("").sintetizar("hola") is None
    assert motores.MotorComando("programa {texto}").valido is False            # falta {salida}
    assert motores.MotorComando(_script(tmp_path, "import sys\nsys.exit(3)\n") + " {salida}").sintetizar("x") is None
    assert motores.MotorComando(_script(tmp_path, "pass\n") + " {salida}").sintetizar("x") is None  # no escribe
    assert motores.MotorComando("no_existe_este_programa {salida}").sintetizar("x") is None
    lento = motores.MotorComando(_script(tmp_path, "import time\ntime.sleep(5)\n") + " {salida}", timeout=0.5)
    assert lento.sintetizar("x") is None
    assert motores.MotorComando(_script(tmp_path, ESCRIBE_STDIN) + " {salida}").sintetizar("  ") is None


def test_no_se_interpreta_un_shell(tmp_path):
    """Un ``&`` en la plantilla no ejecuta nada más: son argumentos, no un shell."""
    marca = tmp_path / "hackeado.txt"
    m = motores.MotorComando(_script(tmp_path, ESCRIBE_STDIN) + f' {{salida}} & echo x > "{marca}"')
    m.sintetizar("hola")
    assert not marca.exists()


def test_dividir_comando_respeta_comillas_y_rutas_de_windows():
    assert motores.dividir_comando(r'piper --model "C:\voces\mi voz.onnx" --output_file {salida}') == [
        "piper", "--model", r"C:\voces\mi voz.onnx", "--output_file", "{salida}"]


def test_nombres_de_idioma():
    assert motores.nombre_de_idioma("JA") == "japonés" and motores.nombre_de_idioma("xx") == "xx"


# --------------------------------------------------------------------------- #
# TTS: motor elegido, idioma y subtítulos
# --------------------------------------------------------------------------- #
@pytest.fixture
def voz(cfg):
    return tts_mod.TextoAVoz(cfg)


def test_motor_por_defecto_es_voicevox_y_valores_raros_tambien(voz, cfg):
    assert voz._motor_elegido() == "voicevox"
    cfg.valores["tts_motor"] = "  COMANDO "
    assert voz._motor_elegido() == "comando"
    cfg.valores["tts_motor"] = "inventado"
    assert voz._motor_elegido() == "voicevox"


def test_motor_sistema_no_sintetiza_ni_traduce(voz, cfg):
    cfg.valores["tts_motor"] = "sistema"
    assert voz._preparar_audio_frase("hola") == {"motor": "sistema", "wav": None, "idioma": "es"}


def test_motor_comando_en_espanol(voz, cfg, monkeypatch):
    cfg.valores["tts_motor"] = "comando"
    pedidos = []
    monkeypatch.setattr(voz._motor_comando, "sintetizar", lambda t: pedidos.append(t) or b"WAV")
    art = voz._preparar_audio_frase("Hola, ¿cómo estás?")
    assert art == {"motor": "comando", "wav": b"WAV", "idioma": "es"} and pedidos == ["Hola, ¿cómo estás?"]


def test_motor_comando_en_otro_idioma_traduce_antes(voz, cfg, monkeypatch):
    cfg.valores.update(tts_motor="comando", tts_idioma="pt")
    monkeypatch.setattr(voz, "_traducir_a", lambda texto, idioma: f"[{idioma}] {texto}")
    pedidos = []
    monkeypatch.setattr(voz._motor_comando, "sintetizar", lambda t: pedidos.append(t) or b"WAV")
    art = voz._preparar_audio_frase("hola")
    assert art["idioma"] == "pt" and pedidos == ["[pt] hola"]


def test_si_el_comando_o_la_traduccion_fallan_cae_a_la_voz_del_sistema(voz, cfg, monkeypatch):
    cfg.valores["tts_motor"] = "comando"
    monkeypatch.setattr(voz._motor_comando, "sintetizar", lambda t: None)
    assert voz._preparar_audio_frase("hola")["motor"] == "sistema"
    cfg.valores["tts_idioma"] = "en"
    monkeypatch.setattr(voz, "_traducir_a", lambda t, i: None)
    assert voz._preparar_audio_frase("hola")["motor"] == "sistema"


def test_reproduce_wav_de_comando(voz, monkeypatch):
    sonados, dichos = [], []
    monkeypatch.setattr(voz, "_reproducir_bytes", lambda wav, texto="": sonados.append(wav))
    monkeypatch.setattr(voz, "_hablar_sistema", lambda t: dichos.append(t))
    voz._reproducir_frase("hola", {"motor": "comando", "wav": b"W", "idioma": "es"})
    voz._reproducir_frase("chau", {"motor": "sistema", "wav": None, "idioma": "es"})
    assert sonados == [b"W"] and dichos == ["chau"]


@pytest.mark.parametrize("politica,idioma,esperado", [
    ("auto", "ja", True), ("auto", "es", False), ("auto", "pt", True),
    ("siempre", "es", True), ("nunca", "ja", False), ("rara", "es", False), ("", "ja", True),
])
def test_politica_de_subtitulos(voz, cfg, politica, idioma, esperado):
    cfg.valores["subtitulos"] = politica
    assert voz._mostrar_subtitulo({"idioma": idioma}) is esperado


def test_subtitulos_por_defecto_con_artefacto_desconocido_se_muestran(voz):
    assert voz._mostrar_subtitulo(None) is True
