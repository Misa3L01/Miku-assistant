"""Escucha local: fin de frase por VAD, palabra clave en la PC y medición de latencia por etapa."""
from __future__ import annotations

import array
import logging
import math

import pytest

from miku.servicios import metricas
from miku.voz.entrada import vad as vad_mod, wake
from miku.voz.entrada.escucha import SpeechToText

SILENCIO = b"\x00\x00" * vad_mod.MUESTRAS_FRAME


def tono(n_frames: int = 1, hz: int = 200, amplitud: int = 9000) -> list:
    """``n_frames`` fragmentos de un tono CONTINUO (sin cortes entre uno y otro, que sonarían a clic)."""
    total = vad_mod.MUESTRAS_FRAME * n_frames
    muestras = array.array("h", [int(amplitud * math.sin(2 * math.pi * hz * t / vad_mod.FRECUENCIA))
                                 for t in range(total)]).tobytes()
    return [muestras[i:i + vad_mod.BYTES_FRAME] for i in range(0, len(muestras), vad_mod.BYTES_FRAME)]


FUERTE = tono()[0]


# --------------------------------------------------------------------------- #
# VAD por energía (el respaldo sin dependencias)
# --------------------------------------------------------------------------- #
def _calibrar(v, frame=SILENCIO):
    """Le da los fragmentos de arranque que el VAD usa para conocer el ruido de fondo."""
    for _ in range(vad_mod.VadEnergia.FRAMES_CALIBRACION):
        v.es_voz(frame)
    return v


def test_la_energia_distingue_silencio_de_sonido_fuerte():
    v = _calibrar(vad_mod.VadEnergia())
    assert v.es_voz(SILENCIO) is False
    assert v.es_voz(FUERTE) is True


def test_mientras_calibra_no_toma_nada_por_voz():
    """La captura arranca antes de que hables: esos primeros fragmentos son el fondo, no tu voz."""
    v = vad_mod.VadEnergia()
    assert not any(v.es_voz(FUERTE) for _ in range(vad_mod.VadEnergia.FRAMES_CALIBRACION))


def test_el_piso_de_ruido_se_adapta_a_la_habitacion():
    """En una habitación ruidosa, ese ruido deja de contar como voz."""
    v = vad_mod.VadEnergia()
    ruido = array.array("h", [600, -600] * (vad_mod.MUESTRAS_FRAME // 2)).tobytes()
    _calibrar(v, ruido)
    for _ in range(50):
        v.es_voz(ruido)
    assert v.es_voz(ruido) is False, "ese nivel constante es el fondo de la habitación"
    assert v.es_voz(FUERTE) is True, "pero una voz por encima del fondo se sigue oyendo"
    v.reiniciar()
    assert v._ruido == vad_mod.VadEnergia.MINIMO, "reiniciar vuelve a empezar de cero"


def test_un_fragmento_vacio_no_rompe():
    assert _calibrar(vad_mod.VadEnergia()).es_voz(b"") is False
    assert vad_mod.VadEnergia.rms(b"") == 0.0


# --------------------------------------------------------------------------- #
# VAD silero (solo si el modelo está bajado: no viaja en el repositorio)
# --------------------------------------------------------------------------- #
@pytest.fixture
def silero():
    ruta = vad_mod.ruta_modelo()
    if not ruta:
        pytest.skip("falta el modelo de silero (python -m miku.voz.entrada.vad descargar)")
    return vad_mod.VadSilero(ruta)


def test_silero_no_confunde_el_silencio_con_voz(silero):
    assert not any(silero.es_voz(SILENCIO) for _ in range(20))


def test_silero_no_confunde_un_tono_con_voz(silero):
    """Un pitido sostenido tiene mucha energía pero no es voz: acá se ve la diferencia con VadEnergia."""
    continuo = tono(20)
    assert not any(silero.es_voz(f) for f in continuo)
    energia = _calibrar(vad_mod.VadEnergia())
    assert any(energia.es_voz(f) for f in continuo), "la energía sí se confunde: por eso silero es mejor"


def test_silero_mira_el_final_del_fragmento_anterior(silero):
    """Sin ese contexto el modelo devuelve casi 0 siempre (pasó de verdad: 0 % de aciertos con voz)."""
    silero.es_voz(FUERTE)
    assert len(silero._contexto) == vad_mod.VadSilero.CONTEXTO
    silero.reiniciar()
    assert not silero._contexto.any(), "una frase nueva no arrastra la anterior"


def test_si_silero_falla_se_sigue_con_energia(silero, monkeypatch, caplog):
    """Un modelo roto no puede dejar a Miku sorda: se sigue con el VAD por energía."""
    monkeypatch.setattr(silero, "_cargar", lambda: (_ for _ in ()).throw(RuntimeError("modelo roto")))
    with caplog.at_level(logging.ERROR):
        assert silero.es_voz(FUERTE) is False      # el respaldo arranca calibrando, no inventa voz
    assert "silero" in caplog.text


# --------------------------------------------------------------------------- #
# Elección del VAD
# --------------------------------------------------------------------------- #
def test_apagar_el_vad_devuelve_la_pausa_fija_de_siempre(cfg):
    cfg.valores["stt_vad"] = False
    assert vad_mod.crear_vad(cfg) is None


def test_se_elige_el_motor_pedido(cfg, monkeypatch, caplog):
    monkeypatch.setattr(vad_mod, "ruta_modelo", lambda c=None: None)
    cfg.valores["vad_proveedor"] = "energia"
    assert vad_mod.crear_vad(cfg).nombre == "energia"
    cfg.valores["vad_proveedor"] = "auto"
    assert vad_mod.crear_vad(cfg).nombre == "energia", "sin modelo, auto usa energía"
    with caplog.at_level(logging.WARNING):
        cfg.valores["vad_proveedor"] = "silero"
        assert vad_mod.crear_vad(cfg).nombre == "energia"
    assert "descargar" in caplog.text, "dice cómo conseguir el modelo"


def test_un_motor_desconocido_avisa_y_no_rompe(cfg, caplog):
    cfg.valores["vad_proveedor"] = "inventado"
    with caplog.at_level(logging.WARNING):
        assert vad_mod.crear_vad(cfg).nombre == "energia"
    assert "inventado" in caplog.text


def test_con_modelo_disponible_auto_usa_silero(cfg, monkeypatch, tmp_path):
    falso = tmp_path / "silero.onnx"
    falso.write_bytes(b"x")
    monkeypatch.setattr(vad_mod, "ruta_modelo", lambda c=None: str(falso))
    assert vad_mod.crear_vad(cfg).nombre == "silero"


def test_la_ruta_del_modelo_prefiere_la_configurada(cfg, tmp_path, monkeypatch):
    mio = tmp_path / "mio.onnx"
    mio.write_bytes(b"x")
    cfg.valores["vad_modelo"] = str(mio)
    assert vad_mod.ruta_modelo(cfg) == str(mio)
    cfg.valores["vad_modelo"] = str(tmp_path / "no_existe.onnx")
    monkeypatch.setattr(vad_mod, "_ruta_propia", lambda: tmp_path / "tampoco.onnx")
    assert vad_mod.ruta_modelo(cfg) is None, "una ruta que no existe no vale"


# --------------------------------------------------------------------------- #
# Capturar una frase con el VAD
# --------------------------------------------------------------------------- #
class FuenteFalsa:
    """Micrófono de mentira: entrega los fragmentos que se le pasan y después silencio."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.leidos = 0

        class _Stream:
            def read(_self, muestras):
                self.leidos += 1
                return self._frames.pop(0) if self._frames else SILENCIO

        self.stream = _Stream()


class VadDeGuion:
    """VAD que sigue un guion de True/False (para no depender de audio real)."""

    nombre = "guion"

    def __init__(self, guion):
        self._guion = list(guion)
        self.reinicios = 0

    def reiniciar(self):
        self.reinicios += 1

    def es_voz(self, frame):
        return self._guion.pop(0) if self._guion else False


@pytest.fixture
def stt(cfg):
    cfg.valores["stt_pausa_fin"] = 0.32           # 10 fragmentos de 32 ms
    return SpeechToText(cfg)


def test_graba_desde_que_hablas_hasta_que_terminas(stt):
    # 3 de silencio, 5 de voz, 10 de silencio (= la pausa) y uno más
    guion = [False] * 3 + [True] * 5 + [False] * 11
    fuente = FuenteFalsa([FUERTE] * len(guion))
    vad = VadDeGuion(guion)
    audio = stt._capturar_con_vad(fuente, vad, timeout=5, limite=30)
    frames = len(audio.frame_data) // vad_mod.BYTES_FRAME
    assert vad.reinicios == 1
    # 5 de voz + 10 de la pausa + hasta 0,3 s de colchón previo (no más de 9 fragmentos)
    assert 15 <= frames <= 24
    assert audio.sample_rate == vad_mod.FRECUENCIA and audio.sample_width == 2


def test_se_guarda_el_arranque_de_la_palabra(stt):
    """Sin colchón se perdería el principio de la primera sílaba (empieza antes de que el VAD reaccione)."""
    guion = [False] * 5 + [True] * 3 + [False] * 11
    distintos = [bytes([i]) * vad_mod.BYTES_FRAME for i in range(len(guion))]
    audio = stt._capturar_con_vad(FuenteFalsa(distintos), VadDeGuion(guion), timeout=5, limite=30)
    assert distintos[4] in audio.frame_data, "el fragmento anterior al habla tiene que estar"


def test_si_nadie_habla_se_agota_el_tiempo(stt):
    import speech_recognition as sr
    with pytest.raises(sr.WaitTimeoutError):
        stt._capturar_con_vad(FuenteFalsa([]), VadDeGuion([False] * 200), timeout=0.05, limite=30)


def test_una_frase_larguisima_se_corta_en_el_limite(stt):
    audio = stt._capturar_con_vad(FuenteFalsa([]), VadDeGuion([True] * 500), timeout=5, limite=0.5)
    assert len(audio.frame_data) // vad_mod.BYTES_FRAME <= 20


def test_sin_vad_se_usa_la_escucha_de_siempre(stt, monkeypatch):
    monkeypatch.setattr(stt, "_obtener_vad", lambda: None)
    llamadas = []
    stt._reconocedor = type("R", (), {"listen": lambda _s, source=None, timeout=None, phrase_time_limit=None:
                                      llamadas.append((timeout, phrase_time_limit)) or "audio"})()
    assert stt._capturar(object(), 6, 12) == "audio" and llamadas == [(6, 12)]


# --------------------------------------------------------------------------- #
# Palabra clave
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("frase,activa", [
    ("miku abrí discord", True), ("Miku", True), ("mika qué hora es", True),
    ("mi amigo dijo que sí", False), ("la química es difícil", False), ("", False),
])
def test_solo_activa_con_la_palabra_completa(frase, activa):
    assert wake.contiene_miku(frase) is activa


class TranscriptorFalso:
    def __init__(self, texto=""):
        self.texto, self.pedidos = texto, []

    def transcribir(self, wav, modelo, timeout):
        self.pedidos.append((modelo, timeout))
        return self.texto


class AudioFalso:
    def __init__(self, roto=False):
        self.roto = roto

    def get_wav_data(self):
        if self.roto:
            raise OSError("audio ilegible")
        return b"RIFF"


def test_el_detector_que_transcribe_devuelve_tambien_el_texto():
    t = TranscriptorFalso("miku qué hora es")
    d = wake.DetectorTranscripcion(t, "tiny", 15.0, "local")
    activo, texto = d.detectar(AudioFalso())
    assert activo and texto == "miku qué hora es" and t.pedidos == [("tiny", 15.0)]


def test_si_no_se_dijo_miku_no_activa():
    d = wake.DetectorTranscripcion(TranscriptorFalso("pasame la sal"), "tiny", 15.0, "local")
    assert d.detectar(AudioFalso()).activo is False


def test_un_audio_ilegible_no_rompe_la_escucha():
    d = wake.DetectorTranscripcion(TranscriptorFalso("miku"), "tiny", 15.0, "local")
    assert d.detectar(AudioFalso(roto=True)) == wake.Deteccion(False, "")


def test_por_defecto_la_palabra_clave_se_resuelve_donde_se_pueda(cfg, monkeypatch):
    """Sin nada instalado se sigue con la nube: Miku nunca se queda sorda por una opción nueva."""
    monkeypatch.setattr(wake, "_detector_local", lambda c: None)
    assert wake.crear_detector_local(cfg) is None


def test_pedir_la_nube_no_arma_ningun_detector_local(cfg):
    cfg.valores["wake_proveedor"] = "nube"
    assert wake.crear_detector_local(cfg) is None


def test_un_proveedor_desconocido_avisa_y_usa_la_nube(cfg, caplog):
    cfg.valores["wake_proveedor"] = "inventado"
    with caplog.at_level(logging.WARNING):
        assert wake.crear_detector_local(cfg) is None
    assert "inventado" in caplog.text


def test_si_falta_faster_whisper_lo_dice(cfg, monkeypatch, caplog):
    cfg.valores["wake_proveedor"] = "local"
    monkeypatch.setattr(wake, "_detector_local", lambda c: None)
    with caplog.at_level(logging.WARNING):
        assert wake.crear_detector_local(cfg) is None
    assert "faster-whisper" in caplog.text


def test_si_falta_el_modelo_de_openwakeword_lo_dice(cfg, caplog):
    cfg.valores["wake_proveedor"] = "openwakeword"
    with caplog.at_level(logging.WARNING):
        assert wake.crear_detector_local(cfg) is None
    assert "WAKE_MODELO" in caplog.text


def test_openwakeword_necesita_libreria_y_modelo(tmp_path):
    assert wake.DetectorOpenWakeWord.disponible("") is False
    assert wake.DetectorOpenWakeWord.disponible(str(tmp_path / "no_existe.onnx")) is False


def test_openwakeword_usa_el_modelo_y_el_umbral(monkeypatch, tmp_path):
    modelo = tmp_path / "miku.onnx"
    modelo.write_bytes(b"x")
    d = wake.DetectorOpenWakeWord(str(modelo), umbral=0.8)
    monkeypatch.setattr(d, "_cargar", lambda: type("M", (), {"predict": staticmethod(lambda m: {"miku": 0.9})})())
    assert d.detectar(_AudioCrudo()).activo is True
    monkeypatch.setattr(d, "_cargar", lambda: type("M", (), {"predict": staticmethod(lambda m: {"miku": 0.7})})())
    assert d.detectar(_AudioCrudo()).activo is False, "0,7 no llega al umbral de 0,8"


def test_si_openwakeword_falla_no_activa_por_las_dudas(tmp_path):
    d = wake.DetectorOpenWakeWord(str(tmp_path / "x.onnx"))
    assert d.detectar(AudioFalso()).activo is False


class _AudioCrudo:
    @staticmethod
    def get_raw_data(convert_rate=None, convert_width=None):
        return SILENCIO


def test_el_stt_usa_el_detector_local_cuando_lo_hay(stt, monkeypatch):
    monkeypatch.setattr(stt, "_transcribir_wake", lambda a: (_ for _ in ()).throw(
        AssertionError("con detector local no se transcribe en la nube")))
    monkeypatch.setattr(wake, "crear_detector_local",
                        lambda cfg: type("D", (), {"nombre": "x",
                                                   "detectar": staticmethod(lambda a: wake.Deteccion(True, ""))})())
    assert stt._detectar_wake(AudioFalso()).activo is True


def test_sin_detector_local_se_transcribe_como_siempre(stt, monkeypatch):
    monkeypatch.setattr(wake, "crear_detector_local", lambda cfg: None)
    monkeypatch.setattr(stt, "_transcribir_wake", lambda a: "miku abrí brave")
    assert stt._detectar_wake(AudioFalso()) == wake.Deteccion(True, "miku abrí brave")


# --------------------------------------------------------------------------- #
# Métricas de latencia
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _turno_limpio():
    metricas.fin_turno()
    yield
    metricas.fin_turno()


def test_cada_etapa_queda_en_el_resumen(caplog):
    metricas.nuevo_turno("qué hora es")
    with metricas.etapa("stt"):
        pass
    with metricas.etapa("llm"):
        pass
    with caplog.at_level(logging.INFO, logger="miku.metricas"):
        metricas.fin_turno()
    assert "latencia" in caplog.text and "stt" in caplog.text and "llm" in caplog.text
    assert "qué hora es" in caplog.text


def test_las_etapas_repetidas_se_suman():
    metricas.nuevo_turno()
    for _ in range(3):
        metricas.anotar("tts", 0.1)
    assert metricas.turno_actual().etapas()["tts"] == pytest.approx(0.3)


def test_el_total_dice_hasta_la_voz_solo_si_llego_a_hablar():
    turno = metricas.Turno("x")
    turno.anotar("stt", 0.5)
    assert "total" in turno.resumen() and "hasta la voz" not in turno.resumen()
    turno.anotar(metricas.ETAPA_VOZ, 0.2)
    assert "hasta la voz" in turno.resumen()


def test_un_turno_sin_cerrar_lo_cierra_el_siguiente(caplog):
    metricas.nuevo_turno("primera orden")
    metricas.anotar("stt", 0.1)
    with caplog.at_level(logging.INFO, logger="miku.metricas"):
        metricas.nuevo_turno("segunda orden")
    assert "primera orden" in caplog.text


def test_un_turno_no_se_resume_dos_veces(caplog):
    metricas.nuevo_turno("una vez")
    metricas.anotar("stt", 0.1)
    with caplog.at_level(logging.INFO, logger="miku.metricas"):
        metricas.fin_turno()
        metricas.fin_turno()
    assert caplog.text.count("latencia") == 1


def test_medir_no_se_traga_los_errores_del_bloque():
    metricas.nuevo_turno()
    with pytest.raises(ValueError):
        with metricas.etapa("stt"):
            raise ValueError("algo falló")
    assert "stt" in metricas.turno_actual().etapas(), "igual se mide lo que tardó antes de fallar"


def test_medir_sin_turno_abierto_no_rompe():
    metricas.fin_turno()
    with metricas.etapa("suelta"):
        pass
    metricas.anotar("otra", 0.1)


def test_se_pueden_apagar(cfg, monkeypatch, caplog):
    from miku.ajustes import carga as config_mod
    cfg.valores["metricas_latencia"] = False
    monkeypatch.setattr(config_mod, "config", cfg)
    assert metricas.nuevo_turno("x") is None
    with caplog.at_level(logging.INFO, logger="miku.metricas"):
        with metricas.etapa("stt"):
            pass
        metricas.fin_turno()
    assert "latencia" not in caplog.text


def test_un_turno_vacio_no_ensucia_el_log(caplog):
    metricas.nuevo_turno("sin etapas")
    with caplog.at_level(logging.INFO, logger="miku.metricas"):
        metricas.fin_turno()
    assert "latencia" not in caplog.text
