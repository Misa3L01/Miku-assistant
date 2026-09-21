"""Configuración desde la bandeja: escribir en config_local.py, AivisSpeech y la ventana de ajustes."""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from miku.ajustes import escritura, esquema
from miku.plataforma import red
from miku.voz.salida import tts as tts_mod


# --------------------------------------------------------------------------- #
# Escribir en config_local.py sin romper nada
# --------------------------------------------------------------------------- #
ORIGINAL = '''# Mis opciones
GROQ_API_KEY = "gsk_secreto"          # esta no se toca
STT_VAD = True
# CONFIRMACION_SONORA = True
JUEGOS_BOOSTER = [
    "cs2",
    "genshin",
]
'''


def _valores(codigo: str) -> dict:
    """Lo que quedaría definido al importar el archivo (sin importarlo)."""
    espacio: dict = {}
    exec(compile(codigo, "config_local.py", "exec"), {}, espacio)  # noqa: S102  (texto de prueba propio)
    return espacio


def test_una_opcion_activa_se_reemplaza_en_su_lugar():
    nuevo = escritura.aplicar_cambios(ORIGINAL, {"stt_vad": False})
    assert "STT_VAD = False" in nuevo and "STT_VAD = True" not in nuevo
    assert nuevo.index("STT_VAD") < nuevo.index("CONFIRMACION_SONORA"), "queda en el mismo lugar"


def test_una_opcion_comentada_se_activa_sin_duplicarla():
    nuevo = escritura.aplicar_cambios(ORIGINAL, {"confirmacion_sonora": False})
    assert "CONFIRMACION_SONORA = False" in nuevo and "# CONFIRMACION_SONORA" not in nuevo
    assert nuevo.count("CONFIRMACION_SONORA") == 1


def test_una_opcion_que_no_estaba_se_agrega_al_final():
    nuevo = escritura.aplicar_cambios(ORIGINAL, {"voz_velocidad": 1.25})
    assert nuevo.rstrip().endswith("VOZ_VELOCIDAD = 1.25")
    assert escritura._ENCABEZADO in nuevo


def test_el_resto_del_archivo_queda_igual():
    nuevo = escritura.aplicar_cambios(ORIGINAL, {"stt_vad": False, "voz_velocidad": 1.25})
    v = _valores(nuevo)
    assert v["GROQ_API_KEY"] == "gsk_secreto" and v["JUEGOS_BOOSTER"] == ["cs2", "genshin"]
    assert "# Mis opciones" in nuevo and "# esta no se toca" in nuevo


def test_un_valor_de_varias_lineas_se_reemplaza_entero():
    nuevo = escritura.aplicar_cambios(ORIGINAL, {"juegos_booster": ["valorant"]})
    assert _valores(nuevo)["JUEGOS_BOOSTER"] == ["valorant"]
    assert '"genshin"' not in nuevo


def test_textos_con_comillas_barras_y_acentos_quedan_bien_escritos():
    raro = 'C:\\Programas\\"Miku" ñandú'
    assert _valores(escritura.aplicar_cambios(ORIGINAL, {"ruta_rara": raro}))["RUTA_RARA"] == raro


def test_si_el_archivo_ya_estaba_roto_no_se_toca():
    with pytest.raises(ValueError):
        escritura.aplicar_cambios("STT_VAD = (\n", {"stt_vad": False})


def test_guardar_hace_copia_respeta_crlf_y_aplica_en_memoria(tmp_path, cfg):
    ruta = tmp_path / "config_local.py"
    ruta.write_bytes(ORIGINAL.replace("\n", "\r\n").encode("utf-8"))
    escritura.guardar({"stt_vad": False}, ruta=ruta, config=cfg)
    crudo = ruta.read_bytes()
    assert b"STT_VAD = False\r\n" in crudo and b"\r\r\n" not in crudo
    assert ruta.with_name("config_local.py.bak").read_bytes() == ORIGINAL.replace("\n", "\r\n").encode("utf-8")
    assert cfg.get("stt_vad") is False and cfg.origenes["stt_vad"] == "config_local"
    assert not list(tmp_path.glob("*.tmp")), "no deja archivos temporales"


def test_guardar_sin_archivo_lo_crea(tmp_path, cfg):
    ruta = tmp_path / "config_local.py"
    escritura.guardar({"stt_vad": False}, ruta=ruta, config=cfg)
    assert _valores(ruta.read_text(encoding="utf-8"))["STT_VAD"] is False


def test_guardar_sin_cambios_no_escribe(tmp_path, cfg):
    ruta = tmp_path / "config_local.py"
    escritura.guardar({}, ruta=ruta, config=cfg)
    assert not ruta.exists()


def test_un_error_no_deja_el_archivo_a_medias(tmp_path, cfg):
    ruta = tmp_path / "config_local.py"
    ruta.write_text("STT_VAD = (\n", encoding="utf-8")
    with pytest.raises(ValueError):
        escritura.guardar({"stt_vad": False}, ruta=ruta, config=cfg)
    assert ruta.read_text(encoding="utf-8") == "STT_VAD = (\n"
    assert cfg.get("stt_vad") is not False, "si no se guardó, tampoco se aplica"


def test_lo_guardado_lo_lee_miku_al_arrancar(tmp_path, cfg):
    """El archivo sigue siendo el config_local.py de siempre: Python válido que Miku importa."""
    ruta = tmp_path / "config_local.py"
    ruta.write_text(ORIGINAL, encoding="utf-8")
    escritura.guardar({"stt_vad": False, "confirmacion_sonora": False, "voz_velocidad": 1.3}, ruta=ruta, config=cfg)
    ast.parse(ruta.read_text(encoding="utf-8"))
    v = _valores(ruta.read_text(encoding="utf-8"))
    assert (v["STT_VAD"], v["CONFIRMACION_SONORA"], v["VOZ_VELOCIDAD"]) == (False, False, 1.3)


# --------------------------------------------------------------------------- #
# AivisSpeech
# --------------------------------------------------------------------------- #
class _Http:
    def __init__(self, codigo=200, datos=None):
        self.status_code, self._datos = codigo, datos

    def json(self):
        return self._datos


VOCES = [{"name": "まお", "styles": [{"name": "ノーマル", "id": 888753760}, {"name": "あまあま", "id": 888753761}]},
         {"name": "花音", "styles": [{"name": "ノーマル", "id": 1325133120}]}]


def test_aivisspeech_usa_su_puerto_y_su_nombre(cfg):
    cfg.valores["tts_motor"] = "aivisspeech"
    v = tts_mod.TextoAVoz(cfg)
    assert v.motor_jp.nombre == "AivisSpeech" and v.voicevox_url == "http://127.0.0.1:10101"
    assert v._motor_elegido() == "aivisspeech"


def test_la_url_de_aivisspeech_se_puede_cambiar(cfg):
    cfg.valores.update(tts_motor="aivisspeech", aivisspeech_url="http://127.0.0.1:12345/")
    assert tts_mod.TextoAVoz(cfg).voicevox_url == "http://127.0.0.1:12345"


def test_voicevox_sigue_igual_por_defecto(cfg):
    v = tts_mod.TextoAVoz(cfg)
    assert v.motor_jp.nombre == "VOICEVOX" and v.voicevox_url == "http://localhost:50021" and v.speaker_id == 6


def test_encuentra_el_run_exe_de_aivisspeech(cfg, tmp_path, monkeypatch):
    cfg.valores["tts_motor"] = "aivisspeech"
    instalado = tmp_path / "AivisSpeech-Engine" / "run.exe"
    instalado.parent.mkdir()
    instalado.write_text("x")
    motor = tts_mod.MotorJapones("aivisspeech", "AivisSpeech", "http://127.0.0.1:10101",
                                 (tmp_path / "no_esta" / "run.exe", instalado))
    monkeypatch.setitem(tts_mod.MOTORES_JAPONESES, "aivisspeech", motor)
    v = tts_mod.TextoAVoz(cfg)
    assert v._buscar_run_voicevox() == str(instalado)
    mio = tmp_path / "mio.exe"
    mio.write_text("x")
    cfg.valores["aivisspeech_run_exe"] = str(mio)
    assert v._buscar_run_voicevox() == str(mio), "la ruta configurada gana"


def test_las_rutas_tipicas_incluyen_el_instalador_y_extern():
    rutas = [str(r) for r in tts_mod.MOTORES_JAPONESES["aivisspeech"].rutas_run]
    assert any("Program Files" in r and "AivisSpeech-Engine" in r for r in rutas)
    assert any(r.replace("\\", "/").endswith("extern/AivisSpeech-Engine/run.exe") for r in rutas)


def test_lista_las_voces_del_motor(cfg, monkeypatch):
    monkeypatch.setattr(red, "get", lambda url, **k: _Http(200, VOCES))
    voces = tts_mod.TextoAVoz(cfg).voces()
    assert voces == [(888753760, "まお (ノーマル)"), (888753761, "まお (あまあま)"), (1325133120, "花音 (ノーマル)")]


def test_si_el_motor_no_responde_no_hay_voces(cfg, monkeypatch):
    monkeypatch.setattr(red, "get", lambda url, **k: (_ for _ in ()).throw(OSError("apagado")))
    assert tts_mod.TextoAVoz(cfg).voces() == []
    monkeypatch.setattr(red, "get", lambda url, **k: _Http(500))
    assert tts_mod.TextoAVoz(cfg).voces() == []


def test_sin_voz_elegida_aivisspeech_usa_la_primera_que_tengas(cfg, monkeypatch):
    cfg.valores["tts_motor"] = "aivisspeech"
    pedidos = []
    monkeypatch.setattr(red, "get", lambda url, **k: pedidos.append(url) or _Http(200, VOCES))
    v = tts_mod.TextoAVoz(cfg)
    assert v.speaker_id == 888753760 and v.speaker_id == 888753760
    assert len(pedidos) == 1, "la lista se pide una sola vez"
    cfg.valores["aivisspeech_speaker_id"] = 1325133120
    assert v.speaker_id == 1325133120, "una voz elegida se usa al instante"


def test_los_ajustes_de_voz_se_acotan(cfg):
    cfg.valores.update(voz_velocidad=9, voz_tono=-3, voz_entonacion="rara", voz_volumen=0.5)
    p = tts_mod.TextoAVoz(cfg)._parametros_voz()
    assert p["speedScale"] == 2.0 and p["pitchScale"] == -0.15
    assert p["intonationScale"] == 1.0 and p["volumeScale"] == 0.5


def _sintetizar_con(cfg, monkeypatch, consulta):
    """Sintetiza con un motor simulado y devuelve el audio_query que le llegó a /synthesis."""
    enviado = {}

    def post(url, **kw):
        if url.endswith("/audio_query"):
            return _Http(200, dict(consulta))
        enviado.update(kw["json"])
        r = _Http(200)
        r.content = b"RIFF"
        return r

    monkeypatch.setattr(red, "post", post)
    assert tts_mod.TextoAVoz(cfg)._sintetizar_voicevox("テスト") == b"RIFF"
    return enviado


def test_los_ajustes_llegan_al_motor(cfg, monkeypatch):
    cfg.valores.update(voz_velocidad=1.4, voz_volumen=0.8)
    consulta = {"speedScale": 1.0, "pitchScale": 0.0, "intonationScale": 1.0, "volumeScale": 1.0,
                "accent_phrases": [{"x": 1}], "kana": "テスト"}
    enviado = _sintetizar_con(cfg, monkeypatch, consulta)
    assert enviado["speedScale"] == 1.4 and enviado["volumeScale"] == 0.8
    assert enviado["accent_phrases"] == [{"x": 1}] and enviado["kana"] == "テスト", "el resto viaja igual"


def test_un_ajuste_que_el_motor_no_tiene_no_se_inventa(cfg, monkeypatch):
    """VOICEVOX no tiene tempoDynamicsScale (es de AivisSpeech): mandárselo podría hacerlo fallar."""
    cfg.valores["voz_ritmo"] = 1.8
    enviado = _sintetizar_con(cfg, monkeypatch, {"speedScale": 1.0})
    assert "tempoDynamicsScale" not in enviado
    enviado = _sintetizar_con(cfg, monkeypatch, {"speedScale": 1.0, "tempoDynamicsScale": 1.0})
    assert enviado["tempoDynamicsScale"] == 1.8


def test_la_cache_de_audio_distingue_motor_voz_y_ajustes(cfg):
    v = tts_mod.TextoAVoz(cfg)
    base = v._firma_voz()
    cfg.valores["voz_velocidad"] = 1.2
    assert v._firma_voz() != base, "otra velocidad es otro audio"
    cfg.valores["voz_velocidad"] = 1.0
    cfg.valores["voicevox_speaker_id"] = 3
    assert v._firma_voz() != base
    cfg.valores.update(tts_motor="aivisspeech", aivisspeech_speaker_id=6, voicevox_speaker_id=6)
    assert tts_mod.TextoAVoz(cfg)._firma_voz() != base, "el mismo número en otro motor es otra voz"


def test_si_el_motor_no_informa_sus_dispositivos_no_se_avisa_nada(cfg, monkeypatch, caplog):
    """AivisSpeech puede no tener /supported_devices: no se puede afirmar que sea "solo CPU"."""
    cfg.valores["voicevox_gpu"] = True
    monkeypatch.setattr(red, "get", lambda url, **k: _Http(404))
    tts_mod.TextoAVoz(cfg)._avisar_si_la_gpu_no_sirve()
    assert "solo soporta CPU" not in caplog.text


# --------------------------------------------------------------------------- #
# Qué muestra la ventana
# --------------------------------------------------------------------------- #
def test_aparecen_todas_las_opciones_de_si_o_no_salvo_las_que_maneja_miku():
    from miku.ui import configuracion as conf
    mostradas = {o.clave for _, opciones in conf.opciones_de_ajustes() for o in opciones}
    booleanas = {o.clave for o in esquema.OPCIONES.values() if o.tipo == "booleano"}
    assert booleanas - conf.EXCLUIDAS <= mostradas
    assert not mostradas & {"modo_entrada", "personalidad", "tecla_invocar"}, \
        "las que Miku guarda sola no se fijan desde acá"
    assert {"vad_proveedor", "wake_proveedor", "subtitulos"} <= mostradas, "también las de elegir"
    assert not any(o.tipo in ("secreto", "ruta", "lista", "mapa") for _, ops in conf.opciones_de_ajustes()
                   for o in ops), "las claves y rutas se siguen editando en el archivo"


def test_la_ayuda_avisa_si_hace_falta_reiniciar():
    from miku.ui import configuracion as conf
    assert "reiniciar" in conf.ayuda(esquema.OPCIONES["stt_vad"])
    assert "reiniciar" not in conf.ayuda(esquema.OPCIONES["confirmacion_sonora"])
    assert conf.ayuda(esquema.OPCIONES["confirmacion_sonora"]).startswith(
        esquema.OPCIONES["confirmacion_sonora"].descripcion)


def test_todos_los_deslizadores_son_opciones_del_esquema():
    from miku.ui import configuracion as conf
    for clave, _, minimo, maximo, defecto in conf.DESLIZADORES:
        assert clave in esquema.OPCIONES and minimo <= defecto <= maximo
        assert esquema.OPCIONES[clave].default == defecto


# --------------------------------------------------------------------------- #
# La ventana de verdad (Qt sin pantalla)
# --------------------------------------------------------------------------- #
class VozFalsa:
    def __init__(self, voces=None):
        self._voces = voces if voces is not None else [(2, "四国めたん (ノーマル)"), (6, "四国めたん (ツンツン)")]
        self.dichas = []

    def voces(self):
        return self._voces

    def decir(self, texto):
        self.dichas.append(texto)


@pytest.fixture
def ventana(cfg):
    pytest.importorskip("PyQt5")
    from miku.ui.configuracion import VentanaConfiguracion
    voz = VozFalsa()
    v = VentanaConfiguracion(cfg, lambda: voz)
    v.mostrar()
    v.voz = voz
    _esperar(lambda: v._hilo.ejecutar_y_esperar(lambda: v._panel._combo_voz.currentData()) is not None)
    yield v
    v._hilo.ejecutar_y_esperar(lambda: v._panel.ventana.hide())


def _esperar(condicion, segundos=3.0):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


def en_qt(v, funcion):
    return v._hilo.ejecutar_y_esperar(funcion, 5.0)


def test_cada_opcion_tiene_su_control_y_su_signo_de_ayuda(ventana):
    from PyQt5 import QtWidgets
    from miku.ui import configuracion as conf
    panel = ventana._panel
    esperadas = {o.clave for _, ops in conf.opciones_de_ajustes() for o in ops}
    assert esperadas <= set(panel.controles)

    def signos():
        return [e for e in panel.ventana.findChildren(QtWidgets.QLabel) if e.text() == "!"]

    etiquetas = en_qt(ventana, signos)
    assert len(etiquetas) >= len(esperadas) + len(conf.DESLIZADORES)
    assert all(en_qt(ventana, lambda e=e: e.toolTip()) for e in etiquetas), "todo '!' explica algo"


def test_cada_deslizador_muestra_su_numero_aunque_arranque_en_cero(ventana):
    """El tono empieza en 0: Qt no avisa un cambio que no hubo, y el número quedaba en blanco."""
    from PyQt5 import QtWidgets

    def numeros():
        return [e.text() for e in ventana._panel.ventana.findChildren(QtWidgets.QLabel)
                if e.text().replace(".", "").replace("-", "").isdigit()]

    assert "0.00" in en_qt(ventana, numeros) and "1.00" in en_qt(ventana, numeros)


def test_muestra_lo_que_dice_la_config(ventana, cfg):
    panel = ventana._panel
    assert en_qt(ventana, lambda: panel.controles["stt_vad"].isChecked()) is True
    assert en_qt(ventana, lambda: panel.controles["tts_motor"].currentData()) == "voicevox"
    assert en_qt(ventana, lambda: panel.controles["voz_velocidad"].value()) == 100
    assert en_qt(ventana, lambda: panel._combo_voz.currentData()) == 6, "la voz en uso queda elegida"


def test_sin_tocar_nada_no_hay_cambios(ventana):
    assert en_qt(ventana, ventana._panel.cambios) == {}


def test_guardar_escribe_solo_lo_cambiado_y_lo_aplica(ventana, cfg):
    panel = ventana._panel
    en_qt(ventana, lambda: panel.controles["confirmacion_sonora"].setChecked(False))
    en_qt(ventana, lambda: panel.controles["stt_vad"].setChecked(False))
    guardado = en_qt(ventana, panel.guardar)
    assert guardado == {"confirmacion_sonora": False, "stt_vad": False}
    texto = escritura.ruta_por_defecto().read_text(encoding="utf-8")
    assert "CONFIRMACION_SONORA = False" in texto and "STT_VAD = False" in texto
    assert "LLM_STREAMING" not in texto, "lo que no tocaste no se escribe"
    assert cfg.get("confirmacion_sonora") is False, "vale al instante"
    estado = en_qt(ventana, lambda: panel._estado.text())
    assert "STT_VAD" in estado and "Reiniciá" in estado and "CONFIRMACION_SONORA" in estado
    assert en_qt(ventana, panel.cambios) == {}, "después de guardar ya no hay nada pendiente"


def test_elegir_otra_voz_se_guarda_en_la_del_motor_en_uso(ventana, cfg):
    panel = ventana._panel
    en_qt(ventana, lambda: panel._combo_voz.setCurrentIndex(panel._combo_voz.findData(2)))
    assert en_qt(ventana, panel.guardar) == {"voicevox_speaker_id": 2}


def test_probar_voz_aplica_sin_guardar_y_cancelar_lo_deshace(ventana, cfg):
    panel = ventana._panel
    en_qt(ventana, lambda: panel.controles["voz_velocidad"].setValue(150))
    en_qt(ventana, panel.probar_voz)
    assert _esperar(lambda: ventana.voz.dichas)
    from miku.ui.configuracion import FRASE_PRUEBA
    assert ventana.voz.dichas == [FRASE_PRUEBA] and cfg.get("voz_velocidad") == 1.5
    assert not escritura.ruta_por_defecto().exists(), "probar no guarda"
    en_qt(ventana, panel.cancelar)
    assert cfg.get("voz_velocidad") == 1.0, "cancelar vuelve a como estaba"
    assert en_qt(ventana, lambda: panel.controles["voz_velocidad"].value()) == 100


def test_cambiar_de_motor_avisa_que_hay_que_reiniciar(ventana):
    panel = ventana._panel
    en_qt(ventana, lambda: panel.controles["tts_motor"].setCurrentIndex(
        panel.controles["tts_motor"].findData("aivisspeech")))
    nota = en_qt(ventana, lambda: panel._nota_voz.text())
    assert "AivisSpeech" in nota and "reiniciá" in nota
    assert en_qt(ventana, lambda: panel._combo_voz.isEnabled()) is False, \
        "no se muestran voces de VOICEVOX como si fueran de AivisSpeech"
    assert en_qt(ventana, panel.guardar) == {"tts_motor": "aivisspeech"}
    assert en_qt(ventana, panel._motor_en_uso) == "voicevox", "sigue sonando el de antes hasta reiniciar"


def test_si_el_motor_no_responde_lo_dice(cfg):
    pytest.importorskip("PyQt5")
    from miku.ui.configuracion import VentanaConfiguracion
    v = VentanaConfiguracion(cfg, lambda: VozFalsa(voces=[]))
    v.mostrar()
    assert _esperar(lambda: "no responde" in en_qt(v, lambda: v._panel._combo_voz.currentText()))
    en_qt(v, lambda: v._panel.ventana.hide())


def test_el_menu_de_la_bandeja_tiene_configuracion():
    pytest.importorskip("PyQt5")
    from miku.ui import bandeja
    fuente = Path(bandeja.__file__).read_text(encoding="utf-8")
    assert '_accion("Configuración…", "configuracion")' in fuente


def test_la_app_abre_la_ventana_una_sola_vez(cfg, monkeypatch):
    from miku import app
    from miku.ui import configuracion as conf
    creadas = []

    class Falsa:
        def __init__(self, c, obtener_voz):
            creadas.append(1)
            self.abiertas = 0

        def mostrar(self):
            self.abiertas += 1

    monkeypatch.setattr(conf, "VentanaConfiguracion", Falsa)
    a = app.Asistente(cfg)
    a.abrir_configuracion()
    a.abrir_configuracion()
    assert creadas == [1] and a._configuracion.abiertas == 2


def test_sin_qt_la_app_no_se_cae(cfg, monkeypatch, caplog):
    from miku import app
    from miku.ui import configuracion as conf

    def sin_qt(*a, **k):
        raise RuntimeError("PyQt5 es necesario")

    monkeypatch.setattr(conf, "VentanaConfiguracion", sin_qt)
    app.Asistente(cfg).abrir_configuracion()
    assert "configuración" in caplog.text.lower() or "PyQt5" in caplog.text

