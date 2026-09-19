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
    stt.esperar_silencio = lambda: esperas.append(1)

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
