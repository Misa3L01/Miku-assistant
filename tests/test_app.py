"""Aplicación: modos en caliente, invocación (F22), hotkey, menú de la bandeja y ventana de depuración."""
from __future__ import annotations

import logging
import sys
import threading
import time
import types

import pytest

from miku import app
from miku.ajustes import carga as config_mod
from miku.servicios import arranque


class STTFalso:
    def __init__(self):
        self.invocaciones = 0
        self.detenido = False

    def invocar(self):
        self.invocaciones += 1

    def detener_escucha(self):
        self.detenido = True


@pytest.fixture
def asistente(cfg, monkeypatch):
    """Asistente con la voz/UI reemplazadas por registros (sin audio, sin Qt)."""
    a = app.Asistente(cfg)
    a.eventos = []
    monkeypatch.setattr(cfg, "guardar_preferencias", lambda d: a.eventos.append(("guardar", d)) or True)

    def iniciar_voz():
        a.stt = a.stt or STTFalso()
        a.stt.detenido = False
        a.eventos.append("iniciar_voz")
        return True

    monkeypatch.setattr(a, "_iniciar_voz", iniciar_voz)
    monkeypatch.setattr(a, "_mostrar_texto", lambda: a.eventos.append("mostrar_texto") or True)
    monkeypatch.setattr(a, "_saludar", lambda: a.eventos.append("saludar"))
    monkeypatch.setattr(a.bandeja, "actualizar", lambda t: None)
    return a


# ---------------------------------------------------------------- modos
def test_arranca_en_voz_y_saluda_una_vez(asistente):
    assert asistente.modo is None
    assert asistente.cambiar_modo("voz", persistir=False) is True
    assert asistente.modo == "voz" and "saludar" in asistente.eventos
    asistente.eventos.clear()
    asistente.cambiar_modo("voz", persistir=False)          # ya estaba en voz
    assert "iniciar_voz" not in asistente.eventos and "saludar" not in asistente.eventos


def test_cambiar_a_texto_detiene_la_escucha_y_vuelve_a_voz_sin_saludar(asistente):
    asistente.cambiar_modo("voz", persistir=False)
    asistente.eventos.clear()
    asistente.cambiar_modo("texto", persistir=False)
    assert asistente.stt.detenido and "mostrar_texto" in asistente.eventos
    asistente.eventos.clear()
    asistente.cambiar_modo("voz", persistir=False)
    assert asistente.eventos == ["iniciar_voz"], "volver a voz no repite el saludo de arranque"


def test_el_modo_elegido_se_guarda(asistente):
    asistente.cambiar_modo("texto")
    assert ("guardar", {"modo_entrada": "texto"}) in asistente.eventos


def test_modo_invalido_se_ignora(asistente):
    assert asistente.cambiar_modo("push") is False
    assert asistente.modo is None


def test_si_el_modo_falla_no_cambia(asistente, monkeypatch):
    monkeypatch.setattr(asistente, "_mostrar_texto", lambda: False)        # sin PyQt5
    assert asistente.cambiar_modo("texto", persistir=False) is False
    assert asistente.modo is None


# ---------------------------------------------------------------- invocación (F22)
def test_invocar_en_voz_llama_al_reconocedor(asistente):
    asistente.cambiar_modo("voz", persistir=False)
    asistente.invocar()
    assert asistente.stt.invocaciones == 1


def test_invocar_en_texto_trae_la_ventana(asistente):
    asistente.consola = types.SimpleNamespace(mostrar=lambda: asistente.eventos.append("mostrar_consola"))
    asistente.cambiar_modo("texto", persistir=False)
    asistente.eventos.clear()
    asistente.invocar()
    assert asistente.eventos == ["mostrar_consola"]


def test_invocar_antes_de_arrancar_queda_pendiente(asistente):
    asistente.invocar()
    assert asistente.stt is None
    asistente.cambiar_modo("voz", persistir=False)
    assert asistente.stt.invocaciones == 1, "la invocación pendiente se atiende al terminar de arrancar"


def test_el_reconocedor_atiende_la_invocacion_sin_wake_word(cfg):
    from miku.voz.entrada.escucha import SpeechToText
    stt = SpeechToText(cfg)
    stt._sr_mod = types.SimpleNamespace(WaitTimeoutError=TimeoutError)
    stt.transcribir_audio = lambda audio: "abrí brave"

    class Audio:
        frame_data, sample_rate, sample_width = b"\0" * 32000, 16000, 2

    class Reconocedor:
        def listen(self, source=None, timeout=None, phrase_time_limit=None):
            if not stt._stop.is_set() and timeout == 6:
                return Audio()                       # la captura del comando tras el saludo
            time.sleep(0.05)
            raise TimeoutError()                     # nadie habla: el bucle sigue revisando

    stt._reconocedor = Reconocedor()
    comandos, saludos = [], []
    stt.on_comando, stt.on_wake = comandos.append, saludos.append
    stt.invocar()
    hilo = threading.Thread(target=stt._escuchar, args=(object(),), daemon=True)
    hilo.start()
    for _ in range(60):
        if comandos:
            break
        time.sleep(0.05)
    stt._stop.set()
    hilo.join(2)
    assert comandos == ["abrí brave"] and saludos == ["invocacion"]


# ---------------------------------------------------------------- modo inicial
def test_silencioso_usa_el_modo_guardado_sin_ventanita(asistente):
    asistente.cfg.valores["modo_entrada"] = "texto"
    llamadas = []
    asistente.bandeja.pedir_modo = lambda *a, **k: llamadas.append(1) or "voz"
    args = app._analizar_argumentos(["--silencioso"])
    assert app._elegir_modo_inicial(asistente, args) == ("texto", False)
    assert llamadas == []


def test_sin_silencioso_manda_la_ventanita(asistente):
    asistente.bandeja.pedir_modo = lambda *a, **k: "texto"
    assert app._elegir_modo_inicial(asistente, app._analizar_argumentos([])) == ("texto", True)


def test_si_cierra_la_ventanita_sin_elegir_se_usa_el_guardado(asistente):
    asistente.bandeja.pedir_modo = lambda *a, **k: None
    asistente.bandeja._hilo = object()                     # hay bandeja
    assert app._elegir_modo_inicial(asistente, app._analizar_argumentos([])) == ("voz", False)


def test_el_modo_push_viejo_cuenta_como_voz(cfg):
    cfg.valores["modo_entrada"] = "push"
    assert cfg.modo_entrada == "voz"


def test_argumentos():
    a = app._analizar_argumentos(["--silencioso", "--invocar", "--cosa-rara"])
    assert a.silencioso and a.invocar


# ---------------------------------------------------------------- hotkey F22
class TecladoFalso:
    def __init__(self):
        self.altas, self.bajas = [], []

    def add_hotkey(self, tecla, callback):
        self.altas.append(tecla)
        return f"hk-{len(self.altas)}"

    def remove_hotkey(self, manejador):
        self.bajas.append(manejador)


@pytest.fixture
def teclado(monkeypatch):
    falso = TecladoFalso()
    monkeypatch.setitem(sys.modules, "keyboard", falso)
    return falso


def test_f22_dentro_de_miku_si_no_hay_atajo_de_windows(asistente, teclado, monkeypatch):
    monkeypatch.setattr(arranque, "atajo_f22_activo", lambda ruta=None: False)
    asistente.actualizar_hotkey_f22()
    asistente.actualizar_hotkey_f22()                       # idempotente
    assert teclado.altas == ["f22"]


def test_con_atajo_de_windows_no_se_duplica_la_invocacion(asistente, teclado, monkeypatch):
    activo = {"si": False}
    monkeypatch.setattr(arranque, "atajo_f22_activo", lambda ruta=None: activo["si"])
    asistente.actualizar_hotkey_f22()
    assert teclado.altas == ["f22"]
    activo["si"] = True                                     # el usuario activa el atajo desde la bandeja
    asistente.actualizar_hotkey_f22()
    assert teclado.bajas == ["hk-1"], "se quita el hotkey propio para no invocar dos veces"
    activo["si"] = False
    asistente.actualizar_hotkey_f22()
    assert teclado.altas == ["f22", "f22"]


# ---------------------------------------------------------------- menú de la bandeja
def test_acciones_del_menu(asistente, monkeypatch):
    llamadas = []
    monkeypatch.setattr(arranque, "activar_inicio_windows", lambda *a: llamadas.append("inicio_on"))
    monkeypatch.setattr(arranque, "desactivar_inicio_windows", lambda *a: llamadas.append("inicio_off"))
    monkeypatch.setattr(arranque, "activar_atajo_f22", lambda *a: llamadas.append("atajo_on"))
    monkeypatch.setattr(arranque, "desactivar_atajo_f22", lambda *a: llamadas.append("atajo_off"))
    monkeypatch.setattr(asistente, "actualizar_hotkey_f22", lambda: llamadas.append("hotkey"))
    acciones = asistente.acciones_menu()
    assert set(acciones) == {"invocar", "modo_voz", "modo_texto", "inicio_windows", "atajo_f22", "elegir_tecla"}
    acciones["inicio_windows"](True)
    acciones["inicio_windows"](False)
    acciones["atajo_f22"](True)
    assert llamadas == ["inicio_on", "inicio_off", "atajo_on", "hotkey"]


def test_estado_del_menu(asistente, monkeypatch):
    monkeypatch.setattr(arranque, "inicio_windows_activo", lambda *a: True)
    monkeypatch.setattr(arranque, "atajo_f22_activo", lambda *a: False)
    asistente.modo = "voz"
    assert asistente.estado_menu() == {"modo": "voz", "inicio_windows": True, "atajo_f22": False}


# ---------------------------------------------------------------- salida y logging
def test_pedir_salir_despierta_la_espera_y_cerrar_es_idempotente(asistente, monkeypatch):
    monkeypatch.setattr(threading, "Timer", lambda *a, **k: types.SimpleNamespace(
        start=lambda: None, daemon=False))
    hilo = threading.Thread(target=asistente.esperar_salida)
    hilo.start()
    asistente.pedir_salir()
    hilo.join(3)
    assert not hilo.is_alive()
    asistente.cerrar()
    asistente.cerrar()


def test_el_log_va_a_un_archivo_rotativo(tmp_path, monkeypatch):
    raiz = logging.getLogger()
    antes = (raiz.handlers[:], raiz.level)
    monkeypatch.setattr(config_mod, "BASE_DIR", tmp_path)
    try:
        app.configurar_logging("INFO")
        logging.getLogger("miku.prueba").info("hola log")
        for h in raiz.handlers:
            h.flush()
        assert "hola log" in (tmp_path / "data" / "miku.log").read_text(encoding="utf-8")
    finally:
        for h in raiz.handlers[:]:
            raiz.removeHandler(h)
            h.close()
        for h in antes[0]:
            raiz.addHandler(h)
        raiz.setLevel(antes[1])


# ---------------------------------------------------------------- Qt: ventana de depuración y menú
def test_consola_de_depuracion_envia_y_muestra():
    pytest.importorskip("PyQt5")
    from miku.ui.consola import ConsolaDebug

    recibidos = []
    consola = ConsolaDebug(recibidos.append)
    consola.mostrar()
    time.sleep(0.4)
    assert consola.visible() is True
    consola._hilo.ejecutar_y_esperar(lambda: (consola._panel._entrada.setText("hola <b>"),
                                              consola._panel._entrada.returnPressed.emit()))
    for _ in range(40):
        if recibidos:
            break
        time.sleep(0.05)
    assert recibidos == ["hola <b>"]
    consola.agregar("Miku", "respuesta")
    time.sleep(0.3)
    texto = consola._hilo.ejecutar_y_esperar(lambda: consola._panel._registro.toPlainText())
    assert "Vos: hola <b>" in texto and "Miku: respuesta" in texto, "el HTML se escapa, no se interpreta"
    consola.ocultar()
    time.sleep(0.3)
    assert consola.visible() is False


def test_menu_de_la_bandeja_marca_el_estado_y_ejecuta_acciones():
    pytest.importorskip("PyQt5")
    from miku.ui.bandeja import Bandeja

    llamadas = []
    estado = {"modo": "texto", "inicio_windows": True, "atajo_f22": False}
    acciones = {"invocar": lambda: llamadas.append("invocar"),
                "modo_voz": lambda m=True: llamadas.append("voz"),
                "modo_texto": lambda m=True: llamadas.append("texto"),
                "inicio_windows": lambda m: llamadas.append(("inicio", m)),
                "atajo_f22": lambda m: llamadas.append(("atajo", m))}
    b = Bandeja()
    assert b.iniciar(on_salir=lambda: None, acciones=acciones, estado=lambda: estado)
    panel = b._panel

    def marcas():
        panel._refrescar_checks()
        return {k: a.isChecked() for k, a in panel._checks.items()}

    assert b._hilo.ejecutar_y_esperar(marcas) == {
        "modo_voz": False, "modo_texto": True, "inicio_windows": True, "atajo_f22": False}
    b._hilo.ejecutar_y_esperar(lambda: panel._ejecutar("invocar", False, False))
    b._hilo.ejecutar_y_esperar(lambda: panel._ejecutar("inicio_windows", False, True))
    for _ in range(40):
        if len(llamadas) >= 2:
            break
        time.sleep(0.05)
    assert sorted(map(str, llamadas)) == sorted(["invocar", str(("inicio", False))])
    b.detener()


# ---------------------------------------------------------------- reinicio de la escucha (bug real hallado en la prueba E2E)
def test_reiniciar_la_escucha_espera_al_hilo_que_se_esta_deteniendo(cfg, monkeypatch):
    """Antes: stop + start rápidos dejaban el hilo viejo muriendo y NINGUNO escuchando."""
    from miku.voz.entrada import escucha
    monkeypatch.setattr(escucha, "_ESPERA_CIERRE_HILO", 5.0)
    stt = escucha.SpeechToText(cfg)
    arranques = []

    def bucle_lento():
        arranques.append(threading.current_thread().name)
        while not stt._stop.is_set():
            time.sleep(0.05)
        time.sleep(0.6)                     # tarda en terminar (p. ej. una captura en curso)

    monkeypatch.setattr(stt, "_bucle_escucha_permanente", bucle_lento)
    stt.iniciar_escucha_continua()
    time.sleep(0.2)
    stt._stop.set()                         # se pide parar (sin esperar) ...
    stt.iniciar_escucha_continua()          # ... e inmediatamente volver a arrancar
    time.sleep(0.3)
    assert stt._hilo_escucha.is_alive() and len(arranques) == 2, "debe haber un hilo NUEVO escuchando"
    stt.detener_escucha()
    assert not stt._hilo_escucha.is_alive()


def test_la_espera_de_silencio_reacciona_al_pedido_de_parar(cfg):
    from miku.voz.entrada.escucha import SpeechToText
    stt = SpeechToText(cfg)
    stt.esperar_silencio = lambda timeout=0.5: (time.sleep(timeout), False)[1]      # Miku "habla" siempre
    hilo = threading.Thread(target=stt._esperar_silencio)
    hilo.start()
    time.sleep(0.3)
    stt._stop.set()
    hilo.join(2)
    assert not hilo.is_alive(), "no debe quedar bloqueada esperando a que Miku calle"
