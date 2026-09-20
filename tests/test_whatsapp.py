"""WhatsApp: números, destinatarios, envío de texto y archivos y vinculación (WhatsApp Web se simula)."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from miku.ajustes import carga as config_mod
from miku.plugins.social import whatsapp as mod
from miku.plugins.social.whatsapp import WhatsApp, formato_legible, normalizar_numero

MAIN = "5493751123456"


# --------------------------------------------------------------------------- #
# Números
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("crudo,esperado", [
    ("+54 3751 123456", MAIN), ("3751 123456", MAIN), ("5493751123456", MAIN), ("+54 9 3751 123456", MAIN),
    ("03751123456", MAIN), ("+54 3755 654321", "5493755654321"), ("+1 415 555 0100", "14155550100"),
    ("123", None), ("", None), (None, None), ("abc", None)])
def test_normalizar_numero(crudo, esperado):
    assert normalizar_numero(crudo) == esperado


def test_formato_legible():
    assert formato_legible(MAIN) == "+54 9 3751 123456" and formato_legible("14155550100") == "+14155550100"


# --------------------------------------------------------------------------- #
# WhatsApp Web simulado
# --------------------------------------------------------------------------- #
class PaginaWsp:
    """Simula lo justo de WhatsApp Web: estado, caja de texto, contador de enviados y vista previa."""

    def __init__(self, estado=mod.CHAT, enter_envia=True, vista_previa=True, boton_texto=True):
        self.estado_actual, self.enter_envia, self.hay_vista_previa = estado, enter_envia, vista_previa
        self.otra_ventana, self.boton_texto, self.limpiezas = False, boton_texto, 0
        self.enviados, self.texto, self.vista, self.urls, self.trozos, self.pegado = 0, "", False, [], 0, None

    def ir(self, url, espera=20):
        self.urls.append(url)
        return True

    def url(self):
        return self.urls[-1] if self.urls else ""

    def evaluar(self, js):
        if js == mod._JS_ESTADO:
            return self.estado_actual
        if js == mod._JS_ENVIADOS:
            return self.enviados
        if js == mod._JS_FOCO_CAJA:
            return True
        if js.startswith("window.__mk = []"):
            self.trozos = 0
            return True
        if js.startswith("window.__mk.push"):
            self.trozos += 1
            return True
        if "new ClipboardEvent" in js:
            self.pegado = js
            self.vista = self.hay_vista_previa
            return True
        if js == mod._JS_USAR_AQUI:
            if self.estado_actual == mod.CARGANDO and self.otra_ventana:
                self.estado_actual, self.otra_ventana = mod.CHAT, False       # "Usar aquí" y carga
                return True
            return False
        if js == mod._JS_LIMPIAR_CAJA:
            self.limpiezas, self.texto = self.limpiezas + 1, ""
            return True
        if js == mod._JS_APRETAR_ENVIAR:
            if self.vista:
                self.vista, self.enviados = False, self.enviados + 1
                return True
            if self.texto and self.boton_texto:                       # el botón verde manda el texto escrito
                self.enviados, self.texto = self.enviados + 1, ""
                return True
            return False
        return None

    def esperar(self, js, segundos=15):
        if js == mod._JS_HAY_VISTA_PREVIA:
            return self.vista
        if js.startswith(mod._JS_ENVIADOS + " > "):
            return self.enviados > int(js.rsplit(">", 1)[1])
        return True

    def llamar(self, metodo, params=None):
        if metodo == "Input.insertText":
            self.texto = params["text"]
        elif metodo == "Input.dispatchKeyEvent" and params["type"] == "rawKeyDown" and params["key"] == "Enter":
            if self.enter_envia and self.texto:
                self.enviados, self.texto = self.enviados + 1, ""
        return {}

    def cerrar(self):
        pass


class NavegadorWsp:
    def __init__(self, pagina, abre=True):
        self._pagina, self._abre, self.cerrado = pagina, abre, False

    def abrir(self, espera=25):
        return self._abre

    def pagina(self):
        return self._pagina

    def cerrar(self):
        self.cerrado = True


@pytest.fixture
def wsp(cfg, tmp_path, monkeypatch):
    cfg.valores.update(whatsapp_contacto_default="+54 3751 123456", brave_ruta_exe="C:/x/brave.exe",
                       whatsapp_contactos={"Mamá": "+54 3755 111111"}, carga_capturas="")
    cfg.valores["carpeta_capturas"] = str(tmp_path / "capturas")
    (tmp_path / "capturas").mkdir()
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(mod, "_QUIETO", 0.0)                    # sin esperas en los tests
    monkeypatch.setattr(config_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    p = WhatsApp()
    p.tmp = tmp_path
    return p


def _captura(wsp, nombre="captura.png", tamano=20):
    ruta = wsp.tmp / "capturas" / nombre
    ruta.write_bytes(b"x" * tamano)
    return ruta


# --------------------------------------------------------------------------- #
# Destinatario
# --------------------------------------------------------------------------- #
def test_destino_por_defecto_y_por_nombre(wsp):
    assert wsp.destino(None) == MAIN
    assert wsp.destino("mama") == "5493755111111" and wsp.destino("MAMÁ") == "5493755111111"
    assert wsp.destino("pedro") is None


def test_sin_numero_por_defecto(wsp, cfg):
    cfg.valores["whatsapp_contacto_default"] = ""
    assert wsp.destino(None) is None
    assert wsp.enviar_en_segundo_plano(None, "hola", "").intencion == "whatsapp.sin_numero"


# --------------------------------------------------------------------------- #
# Envío de texto
# --------------------------------------------------------------------------- #
def test_envia_un_texto_al_numero_normalizado(wsp):
    pag = PaginaWsp()
    nav = NavegadorWsp(pag)
    assert wsp.enviar(None, "hola desde Miku", navegador=nav) == ("enviado", "")
    assert pag.urls == [f"https://web.whatsapp.com/send?phone={MAIN}"] and pag.enviados == 1 and nav.cerrado


def test_texto_a_un_contacto_usa_su_numero(wsp):
    pag = PaginaWsp()
    wsp.enviar("mamá", "llego tarde", navegador=NavegadorWsp(pag))
    assert pag.urls[0].endswith("5493755111111")


def test_sin_sesion_pide_vincular_y_no_manda_nada(wsp):
    pag = PaginaWsp(estado=mod.QR)
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag)) == ("sin_sesion", "") and pag.enviados == 0


def test_numero_sin_whatsapp(wsp):
    estado, detalle = wsp.enviar(None, "hola", navegador=NavegadorWsp(PaginaWsp(estado=mod.INVALIDO)))
    assert estado == "numero_invalido" and "3751" in detalle


def test_si_la_pagina_no_termina_de_cargar(wsp, monkeypatch):
    reloj = iter(range(0, 100000, 20))
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(reloj))
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(PaginaWsp(estado=mod.CARGANDO)))[0] == "no_cargo"


def test_si_el_mensaje_no_aparece_como_enviado_no_se_da_por_hecho(wsp, monkeypatch):
    reloj = iter(range(0, 100000, 10))
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(reloj))
    pag = PaginaWsp(enter_envia=False, boton_texto=False)
    pag.esperar = lambda js, segundos=15: False               # el contador nunca sube
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag)) == ("no_envio", "el texto")


def test_sin_navegador_o_que_no_abre(wsp, cfg):
    assert wsp.enviar(None, "x", navegador=NavegadorWsp(PaginaWsp(), abre=False))[0] == "sin_navegador"
    cfg.valores["brave_ruta_exe"] = ""
    assert wsp.enviar_en_segundo_plano(None, "x", "").intencion == "whatsapp.sin_navegador"


def test_un_error_inesperado_no_rompe_y_libera_el_candado(wsp):
    class Explota(PaginaWsp):
        def ir(self, url, espera=20):
            raise RuntimeError("boom")

    assert wsp.enviar(None, "x", navegador=NavegadorWsp(Explota()))[0] == "error"
    assert wsp.enviar(None, "x", navegador=NavegadorWsp(PaginaWsp()))[0] == "enviado"


def test_dos_envios_a_la_vez_no_se_pisan(wsp):
    wsp._lock.acquire()
    try:
        assert wsp.enviar(None, "x", navegador=NavegadorWsp(PaginaWsp()))[0] == "en_curso"
    finally:
        wsp._lock.release()


# --------------------------------------------------------------------------- #
# Archivos
# --------------------------------------------------------------------------- #
def test_manda_la_ultima_captura_pegandola_y_enviando_la_vista_previa(wsp):
    _captura(wsp, tamano=mod._TROZO * 2)                      # varios trozos base64
    pag = PaginaWsp()
    assert wsp.enviar(None, "", "la ultima captura", NavegadorWsp(pag)) == ("enviado", "captura.png")
    assert pag.enviados == 1 and pag.trozos >= 3 and '"captura.png"' in pag.pegado and '"image/png"' in pag.pegado


def test_texto_y_archivo_van_como_dos_mensajes(wsp):
    _captura(wsp)
    pag = PaginaWsp()
    assert wsp.enviar(None, "mirá", "captura", NavegadorWsp(pag))[0] == "enviado" and pag.enviados == 2


def test_si_no_aparece_la_vista_previa_se_avisa(wsp, monkeypatch):
    _captura(wsp)
    reloj = iter(range(0, 100000, 10))
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(reloj))
    pag = PaginaWsp(vista_previa=False)
    assert wsp.enviar(None, "", "captura", NavegadorWsp(pag)) == ("no_envio", "captura.png")


def test_archivo_inexistente_o_demasiado_grande(wsp, monkeypatch):
    assert wsp.enviar(None, "", "no_existe_xyz.bin", NavegadorWsp(PaginaWsp()))[0] == "archivo_no_encontrado"
    _captura(wsp, tamano=50)
    monkeypatch.setattr(mod, "MAX_BYTES", 10)
    estado, detalle = wsp.enviar(None, "", "captura", NavegadorWsp(PaginaWsp()))
    assert estado == "archivo_grande" and "captura.png" in detalle


# --------------------------------------------------------------------------- #
# Tool, aviso y segundo plano
# --------------------------------------------------------------------------- #
def test_la_tool_responde_al_toque_y_avisa_al_terminar(wsp, monkeypatch):
    dichas, listo = [], threading.Event()
    wsp.initialize(SimpleNamespace(voice=SimpleNamespace(decir=lambda t: (dichas.append(t), listo.set()))))
    monkeypatch.setattr(wsp, "enviar", lambda *a, **k: ("enviado", ""))
    r = wsp.manejar_tool("enviar_a_whatsapp", {"mensaje": "hola"}, {})
    assert r.ok and r.intencion == "whatsapp.enviando"
    assert listo.wait(5) and "WhatsApp" in dichas[0]


def test_validaciones_antes_de_empezar(wsp):
    assert wsp.enviar_en_segundo_plano(None, "", "").intencion == "whatsapp.sin_contenido"
    assert wsp.enviar_en_segundo_plano("pedro", "hola", "").intencion == "whatsapp.sin_contacto"
    assert wsp.enviar_en_segundo_plano(None, "", "archivo_que_no_existe.zzz").intencion == "whatsapp.archivo_no_encontrado"


def test_enviar_a_otra_persona_es_peligroso_y_pregunta(wsp, parser):
    assert "enviar_whatsapp_a_contacto" in wsp.peligrosas and "enviar_a_whatsapp" not in wsp.peligrosas
    pregunta = parser._encolar_confirmacion("enviar_whatsapp_a_contacto", {"contacto": "mamá", "mensaje": "llego tarde"})
    assert "mamá" in pregunta and "llego tarde" in pregunta and "WhatsApp" in pregunta


# --------------------------------------------------------------------------- #
# Vinculación (QR)
# --------------------------------------------------------------------------- #
def test_conectar_espera_al_qr_y_cierra_al_vincular(wsp):
    estados = iter([mod.CARGANDO, mod.QR, mod.QR, mod.LISTA])
    pag = PaginaWsp()
    pag.evaluar = lambda js: next(estados) if js == mod._JS_ESTADO else None
    nav = NavegadorWsp(pag)
    assert wsp.conectar(espera=60, navegador=nav) is True and nav.cerrado


def test_conectar_que_vence(wsp, monkeypatch):
    reloj = iter(range(0, 100000, 50))
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(reloj))
    nav = NavegadorWsp(PaginaWsp(estado=mod.QR))
    assert wsp.conectar(espera=100, navegador=nav) is False and nav.cerrado


def test_la_ventana_de_conectar_siempre_se_ve_y_la_de_enviar_es_configurable(wsp, cfg):
    cfg.valores["whatsapp_ventana"] = "oculta"
    assert wsp._navegador().visible is False
    n = wsp._navegador("normal")
    assert n.visible is True and n.minimizada is False and "Chrome" in n.user_agent and "Headless" not in n.user_agent
    cfg.valores["whatsapp_ventana"] = "minimizada"
    assert wsp._navegador().minimizada is True


def test_el_plugin_solo_se_carga_con_un_contacto(cfg):
    from miku.plugins import registro
    assert "whatsapp" not in {p.nombre for p in registro.instanciar_plugins(cfg)}
    cfg.valores["whatsapp_contacto_default"] = "+54 3751 123456"
    assert "whatsapp" in {p.nombre for p in registro.instanciar_plugins(cfg)}


def test_los_selectores_de_estado_no_confunden_login_con_chat():
    """El JS de estado revisa primero número inválido, luego chat, lista de chats y por último el QR."""
    js = mod._JS_ESTADO
    assert js.index("invalido") < js.index("'chat'") < js.index("'lista'") < js.index("'qr'")


def test_si_whatsapp_estaba_abierto_en_otra_ventana_aprieta_usar_aqui(wsp):
    pag = PaginaWsp(estado=mod.CARGANDO)
    pag.otra_ventana = True
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag))[0] == "enviado"
    assert pag.otra_ventana is False and pag.enviados == 1


def test_el_texto_se_manda_con_el_boton_verde_aunque_el_enter_no_funcione(wsp):
    pag = PaginaWsp(enter_envia=False)
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag))[0] == "enviado" and pag.enviados == 1


def test_si_no_hay_boton_verde_se_prueba_con_enter(wsp):
    pag = PaginaWsp(boton_texto=False)
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag))[0] == "enviado" and pag.enviados == 1


def test_antes_de_escribir_se_vacia_el_borrador_que_haya_quedado(wsp):
    pag = PaginaWsp()
    pag.texto = "borrador viejo sin enviar"
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag))[0] == "enviado"
    assert pag.limpiezas == 1


def test_el_contador_de_enviados_cuenta_los_mensajes_del_chat_sin_depender_del_prefijo_del_id():
    assert "[data-id^" not in mod._JS_ENVIADOS and "#main [data-id]" in mod._JS_ENVIADOS


class HistorialLento(PaginaWsp):
    """Un chat que va cargando mensajes de a poco (1, 2, 3) y donde ningún envío funciona."""

    def __init__(self):
        super().__init__(enter_envia=False, boton_texto=False)
        self.secuencia, self.pedidos = [1, 2, 3], 0

    def evaluar(self, js):
        if js == mod._JS_ENVIADOS:
            i = min(self.pedidos, len(self.secuencia) - 1)
            self.pedidos += 1
            return self.secuencia[i]
        return super().evaluar(js)

    def esperar(self, js, segundos=15):
        if js.startswith(mod._JS_ENVIADOS + " > "):
            return self.secuencia[-1] > int(js.rsplit(">", 1)[1])
        return super().esperar(js, segundos)


def test_la_carga_del_historial_no_se_confunde_con_un_mensaje_enviado(wsp, monkeypatch):
    """Al abrir el chat los mensajes aparecen de a poco: contarlos enseguida daba un falso 'enviado'."""
    reloj = {"t": 0.0}

    def tic():
        reloj["t"] += 0.2
        return reloj["t"]

    monkeypatch.setattr(mod, "_QUIETO", 1.0)
    monkeypatch.setattr(mod.time, "monotonic", tic)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    pag = HistorialLento()
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag)) == ("no_envio", "el texto")
    assert pag.pedidos >= 3                                       # esperó a que terminara de cargar
