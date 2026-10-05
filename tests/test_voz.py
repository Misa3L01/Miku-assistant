"""Pipeline de voz: wake word, bucle de escucha simulado y cola del TTS."""
from __future__ import annotations

import threading
import time
import types

import pytest

from miku.voz.entrada.escucha import SpeechToText
from miku.voz.salida.tts import TextoAVoz


# ---------------------------------------------------------------- wake word
@pytest.fixture
def stt(cfg):
    return SpeechToText(cfg)


@pytest.mark.parametrize("frase", [
    "mi amigo dijo que vaya al mercado", "vení conmigo ahora", "la química económica",
    "dinámica de sistemas",
])
def test_no_se_activa_con_palabras_que_contienen_miku(stt, frase):
    assert stt._contiene_miku(frase) is False


@pytest.mark.parametrize("frase", ["miku", "Miku, qué hora es", "hola mika abrí brave", "mikú abrí discord"])
def test_se_activa_con_la_palabra(stt, frase):
    assert stt._contiene_miku(frase.lower()) is True


def test_comando_en_la_misma_frase(stt):
    assert stt._extraer_comando_en_linea("miku, qué hora es") == "qué hora es"
    assert stt._extraer_comando_en_linea("Miku.") == ""            # solo el nombre: flujo de dos pasos


# ---------------------------------------------------------------- bucle de escucha simulado
class Audio:
    def __init__(self, segundos: float):
        self.frame_data = b"\0" * int(32000 * segundos)
        self.sample_rate, self.sample_width = 16000, 2


class Reconocedor:
    def __init__(self, cola):
        self.cola = list(cola)

    def listen(self, source=None, timeout=None, phrase_time_limit=None):
        if not self.cola:
            time.sleep(0.05)
            raise RuntimeError("fin de audio")
        return self.cola.pop(0)


def test_bucle_de_escucha(stt):
    frases = iter(["mi amigo dijo que no", "miku, qué hora es", "miku", "abrí brave"])
    stt._sr_mod = types.SimpleNamespace(WaitTimeoutError=TimeoutError)
    stt._transcribir_wake = lambda a: next(frases)
    stt.transcribir_audio = lambda a: next(frases)
    stt._reconocedor = Reconocedor([Audio(0.1), Audio(1), Audio(1), Audio(1), Audio(1)])
    comandos, saludos, esperas = [], [], []
    stt.on_comando, stt.on_wake = comandos.append, saludos.append
    stt.esperar_silencio = lambda t=0.5: esperas.append(1) or True

    def cortar():
        for _ in range(100):
            if len(comandos) >= 2:
                break
            time.sleep(0.05)
        stt._stop.set()

    threading.Thread(target=cortar).start()
    stt._escuchar(object())
    assert comandos == ["qué hora es", "abrí brave"]
    assert len(saludos) == 1, "el '¿Sí? Decime.' solo va en el flujo de dos pasos"
    assert len(esperas) >= 3, "debe esperar a que Miku termine de hablar antes de escuchar"


# ---------------------------------------------------------------- respuesta a una pregunta de Miku
class ReconocedorConTiempos(Reconocedor):
    """Como ``Reconocedor``, pero un ``TimeoutError`` en la cola simula que nadie habló."""

    def listen(self, source=None, timeout=None, phrase_time_limit=None):
        item = super().listen(source, timeout, phrase_time_limit)
        if isinstance(item, Exception):
            raise item
        return item


def _escuchar_hasta(stt, condicion):
    def cortar():
        for _ in range(100):
            if condicion():
                break
            time.sleep(0.05)
        stt._stop.set()

    threading.Thread(target=cortar).start()
    stt._escuchar(object())


def test_se_contesta_una_pregunta_sin_decir_miku(stt):
    """Tras "¿Lo hago?", un "sí" a secas se toma: no hace falta "Miku" ni el saludo."""
    frases = iter(["miku", "apagá la pc", "sí", "hola, ¿qué tal?"])
    stt._sr_mod = types.SimpleNamespace(WaitTimeoutError=TimeoutError)
    stt._transcribir_wake = lambda a: next(frases)
    stt.transcribir_audio = lambda a: next(frases)
    stt._reconocedor = Reconocedor([Audio(1)] * 4)
    comandos, saludos = [], []

    def al_comando(texto):
        comandos.append(texto)
        stt.esperar_respuesta()                     # lo que hace la app si Miku quedó con una pregunta

    stt.on_comando, stt.on_wake = al_comando, saludos.append
    stt.esperar_silencio = lambda t=0.5: True
    _escuchar_hasta(stt, lambda: len(comandos) >= 2)
    assert comandos == ["apagá la pc", "sí"]
    assert len(saludos) == 1, "la respuesta no vuelve a saludar con '¿Sí? Decime.'"


def test_despues_de_la_respuesta_vuelve_a_pedir_miku(stt):
    """Si contestás otra cosa, la pregunta sigue pendiente pero el micrófono no se abre de nuevo solo."""
    frases = iter(["miku", "apagá la pc", "poné música", "charla de la tele"])
    stt._sr_mod = types.SimpleNamespace(WaitTimeoutError=TimeoutError)
    stt._transcribir_wake = lambda a: next(frases)
    stt.transcribir_audio = lambda a: next(frases)
    stt._reconocedor = Reconocedor([Audio(1)] * 4)
    comandos = []

    def al_comando(texto):
        comandos.append(texto)
        stt.esperar_respuesta()                     # la pregunta sigue pendiente

    stt.on_comando, stt.on_wake = al_comando, lambda t: None
    stt.esperar_silencio = lambda t=0.5: True
    _escuchar_hasta(stt, lambda: len(stt._reconocedor.cola) == 0)
    assert comandos == ["apagá la pc", "poné música"], "la charla de la tele no se toma como respuesta"
    assert stt._respuesta_esperada.is_set() is False


def test_si_no_contestas_vuelve_a_esperar_la_palabra_clave(stt):
    frases = iter(["miku", "apagá la pc", "miku, qué hora es"])
    stt._sr_mod = types.SimpleNamespace(WaitTimeoutError=TimeoutError)
    stt._transcribir_wake = lambda a: next(frases)
    stt.transcribir_audio = lambda a: next(frases)
    stt._reconocedor = ReconocedorConTiempos([Audio(1), Audio(1), TimeoutError(), Audio(1)])
    comandos = []

    def al_comando(texto):
        comandos.append(texto)
        if len(comandos) == 1:
            stt.esperar_respuesta()

    stt.on_comando, stt.on_wake = al_comando, lambda t: None
    stt.esperar_silencio = lambda t=0.5: True
    _escuchar_hasta(stt, lambda: len(comandos) >= 2)
    assert comandos == ["apagá la pc", "qué hora es"]


def test_esperar_respuesta_se_ignora_mientras_se_entrega_una_respuesta(stt):
    stt._en_seguimiento = True
    stt.esperar_respuesta()
    assert stt._respuesta_esperada.is_set() is False
    stt._en_seguimiento = False
    stt.esperar_respuesta()
    assert stt._respuesta_esperada.is_set() is True


def test_la_invocacion_con_tecla_anula_una_respuesta_pendiente(stt):
    stt._respuesta_esperada.set()
    stt.invocar()
    stt._sr_mod = types.SimpleNamespace(WaitTimeoutError=TimeoutError)
    stt._reconocedor = Reconocedor([Audio(1)])
    stt.transcribir_audio = lambda a: "abrí brave"
    comandos = []
    stt.on_comando, stt.on_wake = comandos.append, lambda t: None
    stt.esperar_silencio = lambda t=0.5: True
    _escuchar_hasta(stt, lambda: len(comandos) >= 1)
    assert comandos == ["abrí brave"] and stt._respuesta_esperada.is_set() is False


# ---------------------------------------------------------------- TTS
def test_decir_concurrente_usa_un_solo_hilo_reproductor(cfg):
    voz = TextoAVoz(cfg, subtitulos_activos=False)
    dichas = []

    def hablar(texto):
        time.sleep(0.2)
        dichas.append(texto)

    voz._reproducir_texto_por_frases = hablar
    antes = {t for t in threading.enumerate() if t.name == "tts_player"}
    hilos = [threading.Thread(target=lambda i=i: voz.decir(f"frase {i}")) for i in range(8)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    nuevos = {t for t in threading.enumerate() if t.name == "tts_player"} - antes
    assert len(nuevos) == 1, "8 decir() simultáneos no deben crear 8 hilos reproductores"
    assert voz.esperar_libre(timeout=0.01) is False, "mientras habla, esperar_libre bloquea"
    assert voz.esperar_libre(timeout=10) is True
    assert len(dichas) == 8
