"""WhatsApp: números, destinatarios, envío de texto y archivos y vinculación (WhatsApp Web se simula)."""
from __future__ import annotations

import json
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
        self.otra_ventana, self.boton_texto, self.limpiezas, self.pendiente = False, boton_texto, 0, False
        self.enviados, self.texto, self.vista, self.urls, self.trozos, self.pegado = 0, "", False, [], 0, None

    def ir(self, url, espera=20):
        self.urls.append(url)
        return True

    def url(self):
        return self.urls[-1] if self.urls else ""

    def evaluar(self, js):
        if js == mod._JS_ESTADO:
            return self.estado_actual
        if js == mod._JS_ULTIMO_ID:
            return f"m{self.enviados}"
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
        if js.startswith(mod._JS_ULTIMO_ID + " !== "):
            return f"m{self.enviados}" != json.loads(js.split(" !== ", 1)[1])
        if js == mod._JS_SIN_PENDIENTES:
            return not self.pendiente
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


def test_validaciones_antes_de_empezar(wsp, cfg):
    assert wsp.enviar_en_segundo_plano(None, "", "").intencion == "whatsapp.sin_contenido"
    assert wsp.enviar_en_segundo_plano(None, "", "archivo_que_no_existe.zzz").intencion == "whatsapp.archivo_no_encontrado"
    cfg.valores["whatsapp_contacto_default"] = ""
    assert wsp.enviar_en_segundo_plano(None, "hola", "").intencion == "whatsapp.sin_numero"


def test_un_nombre_que_no_esta_en_los_contactos_se_busca_en_los_chats(wsp, monkeypatch):
    """Antes 'mandale a Mati' fallaba si Mati no estaba en WHATSAPP_CONTACTOS con su número."""
    monkeypatch.setattr(wsp, "enviar", lambda *a, **k: ("enviado", ""))
    r = wsp.enviar_en_segundo_plano("Mati", "hola", "")
    assert r.ok and r.intencion == "whatsapp.enviando"
    wsp._hilo.join(5)


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


def test_el_envio_se_confirma_por_el_ultimo_mensaje_y_no_por_la_cantidad():
    """WhatsApp descarta mensajes viejos de la lista: al llegar uno nuevo la cantidad puede no subir."""
    assert "[data-id^" not in mod._JS_ULTIMO_ID and "#main [data-id]" in mod._JS_ULTIMO_ID
    assert not hasattr(mod, "_JS_ENVIADOS")


def test_no_se_cierra_el_navegador_mientras_el_mensaje_sigue_pendiente(wsp):
    """Un archivo aparece enseguida en el chat pero se sube después: cerrar antes lo deja trabado."""
    pag = PaginaWsp()
    pag.pendiente = True
    assert wsp.enviar(None, "hola", navegador=NavegadorWsp(pag)) == ("no_envio", "el texto")


def test_un_archivo_que_no_termina_de_subirse_no_se_da_por_enviado(wsp):
    pag = PaginaWsp()
    pag.pendiente = True
    ruta = _captura(wsp)
    assert wsp.enviar(None, "", archivo=str(ruta), navegador=NavegadorWsp(pag)) == ("no_envio", ruta.name)


def test_el_indicador_de_pendiente_reconoce_los_iconos_reales_de_whatsapp():
    """Iconos vistos en la página real mientras un archivo sube (y el de cancelar la subida)."""
    for icono in ("status-pending", "msg-time", "ic-close"):
        assert icono in mod._JS_SIN_PENDIENTES
    assert "#main [data-id] title" in mod._JS_SIN_PENDIENTES



class HistorialLento(PaginaWsp):
    """Un chat que va cargando mensajes de a poco (el último cambia: a, b, c) y donde ningún envío funciona."""

    def __init__(self):
        super().__init__(enter_envia=False, boton_texto=False)
        self.secuencia, self.pedidos = ["a", "b", "c"], 0

    def evaluar(self, js):
        if js == mod._JS_ULTIMO_ID:
            i = min(self.pedidos, len(self.secuencia) - 1)
            self.pedidos += 1
            return self.secuencia[i]
        return super().evaluar(js)

    def esperar(self, js, segundos=15):
        if js.startswith(mod._JS_ULTIMO_ID + " !== "):
            return self.secuencia[-1] != json.loads(js.split(" !== ", 1)[1])
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


# --------------------------------------------------------------------------- #
# Mandar a una persona o a un grupo buscándolo por nombre (la lupa de WhatsApp Web)
# --------------------------------------------------------------------------- #
from miku.plataforma.texto import normalizar as _norm  # noqa: E402

CHATS = [("Chats", "Mati Rojas"), ("Contactos", "Matias"), ("Contactos", "Ana ( Mati )"),
         ("Contactos", "Mati Rojas"), ("Chats", "Familia"), ("Chats", "Mamá"), ("Mensajes", "Mati")]


class PaginaBusqueda(PaginaWsp):
    """WhatsApp Web con la lupa: buscar filtra la lista por sección, un clic de mouse abre el chat."""

    def __init__(self, chats=CHATS, vacias=0, abre_otro=False, estado=mod.LISTA, **kw):
        super().__init__(estado=estado, **kw)
        self.chats, self.consulta, self.foco, self.titulo_chat = chats, "", "caja", ""
        self.vacias, self.sin_resultados, self.abre_otro = vacias, False, abre_otro
        self.borrados, self.clics, self.filas_pedidas = 0, [], []

    def _filas(self):
        q = _norm(self.consulta)
        if not q or self.sin_resultados:
            return []
        return [{"i": 2 * k + 1, "seccion": sec, "titulo": t} for k, (sec, t) in enumerate(self.chats) if q in _norm(t)]

    def evaluar(self, js):
        if js == mod._JS_CERRAR_AVISOS:
            return False
        if js == mod._JS_FOCO_BUSQUEDA:
            self.foco = "busqueda"
            return True
        if js in (mod._JS_FOCO_CAJA, mod._JS_LIMPIAR_CAJA):
            self.foco = "caja"
        if js == mod._JS_BORRAR_BUSQUEDA:
            self.consulta, self.borrados = "", self.borrados + 1
            return True
        if js == mod._JS_RESULTADOS:
            return self._filas()
        if js == mod._JS_TITULO_CHAT:
            return self.titulo_chat
        for f in self._filas():
            if js == mod._JS_CENTRO_RESULTADO % f["i"]:
                return {"x": 5, "y": f["i"]}
        return super().evaluar(js)

    def llamar(self, metodo, params=None):
        if metodo == "Input.insertText" and self.foco == "busqueda":
            self.consulta = params["text"]
            self.filas_pedidas.append(params["text"])
            self.sin_resultados, self.vacias = self.vacias > 0, max(0, self.vacias - 1)
            return {}
        if metodo == "Input.dispatchMouseEvent" and params["type"] == "mousePressed":
            self.clics.append((params["x"], params["y"]))
            fila = next((f for f in self._filas() if f["i"] == params["y"]), None)
            if fila is not None:
                self.titulo_chat = "Otro chat" if self.abre_otro else fila["titulo"]
                self.estado_actual, self.foco = mod.CHAT, "caja"
            return {}
        return super().llamar(metodo, params)


@pytest.fixture
def reloj(monkeypatch):
    """Un reloj que avanza solo (medio segundo por consulta): las esperas de los tests no tardan de verdad."""
    t = {"v": 0.0}

    def monotonic():
        t["v"] += 0.5
        return t["v"]

    monkeypatch.setattr(mod.time, "monotonic", monotonic)


def test_mandale_a_mati_busca_el_chat_y_le_manda(wsp, reloj):
    pag = PaginaBusqueda()
    assert wsp.enviar("Mati", "llego tarde", navegador=NavegadorWsp(pag)) == ("enviado", "")
    assert wsp.ultimo_chat == "Mati Rojas"                    # el nombre real: Matias y Ana (Mati) NO
    assert pag.urls == [mod.URL] and pag.enviados == 1          # no se usó el enlace por número
    assert len(pag.clics) == 1


def test_a_un_grupo_por_el_nombre_aunque_diga_el_grupo(wsp, reloj):
    pag = PaginaBusqueda()
    assert wsp.enviar("el grupo Familia", "confirmo", navegador=NavegadorWsp(pag))[0] == "enviado"
    assert wsp.ultimo_chat == "Familia" and pag.filas_pedidas == ["el grupo Familia", "Familia"]


def test_con_varios_que_empatan_no_manda_y_dice_cuales(wsp, reloj):
    chats = [("Chats", "Mati Rojas"), ("Chats", "Mati Gómez"), ("Contactos", "Matias")]
    pag = PaginaBusqueda(chats)
    estado, detalle = wsp.enviar("Mati", "hola", navegador=NavegadorWsp(pag))
    assert estado == "ambiguo" and detalle == "Mati Rojas, Mati Gómez"
    assert pag.enviados == 0 and pag.clics == []                # ante la duda no se manda nada


def test_si_no_existe_ese_chat_no_manda(wsp, reloj):
    pag = PaginaBusqueda()
    assert wsp.enviar("Zzqx", "hola", navegador=NavegadorWsp(pag)) == ("no_encontrado", "Zzqx")
    assert pag.enviados == 0 and pag.clics == []


def test_no_se_confunde_una_parte_de_una_palabra(wsp, reloj):
    """'Mat' aparece dentro de 'Matias' pero no es esa persona: no se elige."""
    pag = PaginaBusqueda()
    assert wsp.enviar("Mat", "hola", navegador=NavegadorWsp(pag))[0] == "no_encontrado"


def test_la_seccion_de_mensajes_no_cuenta(wsp, reloj):
    """Un mensaje que dice 'Mati' no es un chat al que mandar."""
    pag = PaginaBusqueda([("Mensajes", "Mati")])
    assert wsp.enviar("Mati", "hola", navegador=NavegadorWsp(pag))[0] == "no_encontrado"


def test_si_la_primera_busqueda_sale_vacia_se_borra_y_se_reintenta(wsp, reloj):
    pag = PaginaBusqueda(vacias=1)
    assert wsp.enviar("Mati", "hola", navegador=NavegadorWsp(pag))[0] == "enviado"
    assert pag.borrados >= 1 and pag.filas_pedidas == ["Mati", "Mati"]


def test_si_se_abre_otro_chat_distinto_al_elegido_no_manda(wsp, reloj):
    pag = PaginaBusqueda(abre_otro=True)
    assert wsp.enviar("Mati", "hola", navegador=NavegadorWsp(pag)) == ("no_cargo", "")
    assert pag.enviados == 0


def test_sin_vincular_avisa_en_vez_de_buscar(wsp, reloj):
    pag = PaginaBusqueda(estado=mod.QR)
    assert wsp.enviar("Mati", "hola", navegador=NavegadorWsp(pag)) == ("sin_sesion", "")
    assert pag.filas_pedidas == []


def test_por_nombre_el_navegador_va_sin_ventana_y_por_numero_usa_lo_configurado(wsp, reloj, monkeypatch):
    """Con la ventana minimizada la página queda oculta y WhatsApp no llena la lista de la búsqueda."""
    pedidos = []

    def navegador(ventana=None):
        pedidos.append(ventana)
        return NavegadorWsp(PaginaBusqueda() if ventana == "oculta" else PaginaWsp())

    monkeypatch.setattr(wsp, "_navegador", navegador)
    assert wsp.enviar("Mati", "hola")[0] == "enviado"
    assert wsp.enviar(None, "hola")[0] == "enviado"
    assert wsp.enviar("Mamá", "hola")[0] == "enviado"          # está en WHATSAPP_CONTACTOS: va por su número
    assert pedidos == ["oculta", None, None]


def test_un_contacto_configurado_sigue_yendo_por_su_numero(wsp, reloj):
    pag = PaginaWsp()
    assert wsp.enviar("Mamá", "hola", navegador=NavegadorWsp(pag))[0] == "enviado"
    assert pag.urls == [f"{mod.URL}send?phone=5493755111111"] and wsp.ultimo_chat == ""


def _avisos(wsp):
    dichas, listo = [], threading.Event()
    wsp.initialize(SimpleNamespace(voice=SimpleNamespace(decir=lambda t: (dichas.append(t), listo.set()))))
    return dichas, listo


def test_el_aviso_dice_a_quien_se_lo_mando_de_verdad(wsp, monkeypatch):
    dichas, listo = _avisos(wsp)

    def enviar(*a, **k):
        wsp.ultimo_chat = "Mati Rojas"
        return "enviado", ""

    monkeypatch.setattr(wsp, "enviar", enviar)
    wsp.manejar_tool("enviar_whatsapp_a_contacto", {"contacto": "Mati", "mensaje": "hola"}, {})
    assert listo.wait(5) and "Mati Rojas" in dichas[0]


def test_el_aviso_de_varios_posibles_nombra_las_opciones(wsp, monkeypatch):
    dichas, listo = _avisos(wsp)
    monkeypatch.setattr(wsp, "enviar", lambda *a, **k: ("ambiguo", "Mati Rojas, Mati Gómez"))
    wsp.manejar_tool("enviar_whatsapp_a_contacto", {"contacto": "Mati", "mensaje": "hola"}, {})
    assert listo.wait(5) and "Mati Rojas, Mati Gómez" in dichas[0] and "Mati" in dichas[0]


def test_el_aviso_de_no_encontrado_dice_el_nombre(wsp, monkeypatch):
    dichas, listo = _avisos(wsp)
    monkeypatch.setattr(wsp, "enviar", lambda *a, **k: ("no_encontrado", "Zzqx"))
    wsp.manejar_tool("enviar_whatsapp_a_contacto", {"contacto": "Zzqx", "mensaje": "hola"}, {})
    assert listo.wait(5) and "Zzqx" in dichas[0]


# ------------------------------ elegir_chat / puntaje_titulo (funciones puras) ------------------------------
def _f(seccion, titulo, i=1):
    return {"i": i, "seccion": seccion, "titulo": titulo}


@pytest.mark.parametrize("consulta,titulo,puntaje", [
    ("Mati", "Mati", 3), ("mati", "MATI", 3), ("Mama", "Mamá", 3),
    ("Mati", "Mati Rojas", 2), ("mati rojas", "Mati Rojas", 3),
    ("Mati", "Ana ( Mati )", 1), ("Mati", "Matias", 0), ("Mat", "Mati Rojas", 0),
    ("", "Mati", 0), ("Mati", "", 0)])
def test_puntaje_titulo(consulta, titulo, puntaje):
    assert mod.puntaje_titulo(consulta, titulo) == puntaje


def test_elegir_chat_prefiere_el_que_empieza_con_lo_pedido():
    filas = [_f("Chats", "Mati Rojas", 1), _f("Contactos", "Matias", 3), _f("Contactos", "Ana ( Mati )", 4)]
    veredicto, fila = mod.elegir_chat("Mati", filas)
    assert veredicto == "ok" and fila["titulo"] == "Mati Rojas"


def test_elegir_chat_junta_al_mismo_como_chat_y_como_contacto():
    filas = [_f("Chats", "Mati Rojas", 1), _f("Contactos", "Mati Rojas", 3)]
    assert mod.elegir_chat("Mati", filas)[0] == "ok"


def test_elegir_chat_con_empate_es_ambiguo_y_ninguno_si_nada_coincide():
    filas = [_f("Chats", "Mati Rojas", 1), _f("Chats", "Mati Gómez", 2)]
    veredicto, opciones = mod.elegir_chat("Mati", filas)
    assert veredicto == "ambiguo" and [o["titulo"] for o in opciones] == ["Mati Rojas", "Mati Gómez"]
    assert mod.elegir_chat("Pedro", filas) == ("ninguno", [])
    assert mod.elegir_chat("Mati", [_f("Mensajes", "Mati")]) == ("ninguno", [])


def test_elegir_chat_reconoce_las_secciones_en_ingles_y_los_grupos():
    assert mod.elegir_chat("Familia", [_f("Groups in common", "Familia")])[0] == "ok"
    assert mod.elegir_chat("Familia", [_f("Grupos en común", "Familia")])[0] == "ok"
    assert mod.elegir_chat("Familia", [_f("Contacts", "Familia")])[0] == "ok"


@pytest.mark.parametrize("dicho,queda", [
    ("el grupo Familia", "Familia"), ("grupo Familia", "Familia"), ("al grupo de la facultad", "facultad"),
    ("el chat de mamá", "mamá"), ("Mati", "Mati"), ("Grupo Familia", "Familia")])
def test_se_saca_grupo_o_chat_del_nombre(dicho, queda):
    assert mod._PREFIJO_CHAT.sub("", dicho).strip() == queda


def test_los_textos_de_javascript_de_una_linea_no_tienen_saltos_de_linea_reales():
    """Un salto real dentro de una cadena de JS lo rompe (pasó con el título del chat): las de una línea
    se escriben crudas o sin saltos, y las de varias líneas abren con la llave de la función."""
    for nombre in dir(mod):
        valor = getattr(mod, nombre)
        if nombre.startswith("_JS_") and isinstance(valor, str) and "\n" in valor:
            assert valor.startswith(("(() => {\n", "(async () => {\n")), nombre
