"""Comedor: lectura de la página de inscripciones, trámite completo (navegador simulado) y aviso diario."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from miku.ajustes import carga as config_mod
from miku.plugins.productividad import comedor as mod
from miku.plugins.productividad.comedor import Comedor, Decision, decidir, formas_de_fecha, menciona_fecha

MANANA = date(2026, 9, 22)                          # martes


def elemento(i, texto="", contexto="", deshabilitado=False, **kw):
    base = {"i": i, "tag": "button", "id": "", "texto": texto, "valor": "", "titulo": "", "href": "",
            "deshabilitado": deshabilitado, "contexto": contexto}
    base.update(kw)
    return base


def pagina(texto="", elementos=()):
    return {"url": "https://x/comedorfi", "titulo": "Inscripciones", "texto": texto,
            "elementos": list(elementos), "tablas": []}


# --------------------------------------------------------------------------- #
# Fechas
# --------------------------------------------------------------------------- #
def test_formas_de_fecha_y_mencion():
    formas = formas_de_fecha(MANANA)
    assert "22/09/2026" in formas and "22 de septiembre" in formas and "martes 22" in formas
    assert menciona_fecha("Martes 22/09/2026 - Menú: milanesa", MANANA)
    assert menciona_fecha("MARTES 22 (almuerzo)", MANANA)
    assert menciona_fecha("Comida del 22 de Septiembre", MANANA)
    assert not menciona_fecha("Miércoles 23/09/2026", MANANA)
    assert not menciona_fecha("", MANANA)


# --------------------------------------------------------------------------- #
# Decisión sobre la página
# --------------------------------------------------------------------------- #
def test_hay_comida_y_boton_habilitado_se_inscribe():
    inst = pagina("Comidas disponibles", [
        elemento(0, "Inscribirse", "Lunes 21/09/2026 Milanesa Inscribirse"),
        elemento(1, "Inscribirse", "Martes 22/09/2026 Pastas Inscribirse"),
        elemento(2, "Salir", "")])
    d = decidir(inst, MANANA)
    assert d.accion == "inscribir" and d.indice == 1          # el de MAÑANA, no el primero


def test_ya_inscripto_por_boton_de_anular_o_por_texto():
    con_boton = pagina("Martes 22/09/2026", [elemento(0, "Anular inscripción", "Martes 22/09/2026 Pastas")])
    assert decidir(con_boton, MANANA).accion == "ya_inscripto"
    con_texto = pagina("Martes 22/09/2026: ya estás inscripto", [])
    assert decidir(con_texto, MANANA).accion == "ya_inscripto"


def test_boton_deshabilitado_o_aviso_de_paro_no_habilita():
    deshabilitado = pagina("Martes 22/09/2026", [elemento(0, "Inscribirse", "Martes 22/09/2026", deshabilitado=True)])
    assert decidir(deshabilitado, MANANA).accion == "no_habilitado"
    paro = pagina("Martes 22/09/2026: sin servicio por paro docente", [])
    d = decidir(paro, MANANA)
    assert d.accion == "no_habilitado" and "servicio" in d.detalle


def test_pagina_vacia_es_que_no_hay_comidas():
    assert decidir(pagina("No hay comidas para inscribirse.", [elemento(0, "Salir")]), MANANA).accion == "sin_comidas"


def test_una_pagina_que_no_se_reconoce_no_adivina():
    # Menciona el día pero no tiene nada que se pueda apretar ni un estado claro.
    assert decidir(pagina("Martes 22/09/2026 Menú del día", []), MANANA).accion == "desconocido"
    # Botón de inscribirse de OTRO día: no se aprieta a ciegas.
    otro = pagina("Miércoles 23/09/2026", [elemento(0, "Inscribirse", "Miércoles 23/09/2026 Pastas")])
    assert decidir(otro, MANANA).accion == "desconocido"


def test_no_confunde_inscribirse_con_anular_en_la_misma_fila():
    inst = pagina("", [elemento(0, "Inscribirse", "Martes 22/09/2026"), elemento(1, "Anular", "Martes 22/09/2026")])
    assert decidir(inst, MANANA).accion == "ya_inscripto"


# --------------------------------------------------------------------------- #
# Trámite completo con un navegador simulado
# --------------------------------------------------------------------------- #
class PaginaFalsa:
    """Simula el sitio: login -> inscripciones -> clic -> el sitio recuerda la inscripción."""

    def __init__(self, sitio):
        self.s = sitio
        self.url_actual = ""
        self.eventos = []

    def ir(self, url, espera=20):
        self.url_actual = url
        self.eventos.append(("ir", url))
        return True

    def esperar(self, js, segundos=15):
        if "ef_form_103000002_datosusuario" in js:
            return self.s.get("hay_formulario", True)
        return True

    def escribir(self, selector, valor):
        self.eventos.append(("escribir", selector, valor))
        self.s.setdefault("escrito", {})[selector] = valor
        return True

    def clic(self, selector):
        self.eventos.append(("clic", selector))
        return True

    def captura(self, ruta):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(b"png")
        return True

    def evaluar(self, js):
        if "ef_form_103000002_datosclave" in js:                 # ¿sigue el formulario de acceso?
            return self.s.get("login_falla", False)
        if js == mod._JS_INSTANTANEA:
            return self.s["instantanea"]()
        if "confirm" in js:
            return True
        if "data-miku-i" in js:                                  # clic en un elemento de la página
            self.s["inscripto"] = True
            self.eventos.append(("clic_indice", js))
            return True
        return None

    def cerrar(self):
        pass


class NavegadorFalso:
    def __init__(self, pagina, abre=True):
        self._pagina, self._abre, self.cerrado = pagina, abre, False

    def abrir(self, espera=25):
        return self._abre

    def pagina(self):
        return self._pagina

    def cerrar(self):
        self.cerrado = True


@pytest.fixture
def comedor(cfg, tmp_path, monkeypatch):
    cfg.valores.update(comedor_usuario="alumno", telegram_bot_token="")
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(config_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(mod, "leer_clave", lambda u: "secreto")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    c = Comedor()
    c.tmp = tmp_path
    return c


def _sitio(inscripto=False, deshabilitado=False):
    sitio = {"inscripto": inscripto}

    def instantanea():
        if sitio["inscripto"]:
            return pagina("Martes 22/09/2026", [elemento(0, "Anular inscripción", "Martes 22/09/2026 Pastas")])
        return pagina("Comidas", [elemento(3, "Inscribirse", "Martes 22/09/2026 Pastas", deshabilitado=deshabilitado)])

    sitio["instantanea"] = instantanea
    return sitio


def test_tramite_completo_inscribe_y_verifica(comedor):
    sitio = _sitio()
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(dia=MANANA, navegador=nav)
    assert r.estado == mod.INSCRIPTO and r.captura and r.captura.exists()
    escritos = sitio["escrito"]
    assert escritos["#ef_form_103000002_datosusuario"] == "alumno" and escritos["#ef_form_103000002_datosclave"] == "secreto"
    assert any(e[0] == "clic_indice" and '"3"' in e[1] for e in nav._pagina.eventos)     # apretó el elemento 3
    assert nav.cerrado


def test_ya_inscripto_no_aprieta_nada(comedor):
    sitio = _sitio(inscripto=True)
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(dia=MANANA, navegador=nav)
    assert r.estado == mod.YA_INSCRIPTO
    assert not [e for e in nav._pagina.eventos if e[0] == "clic_indice"]


def test_simulacro_hace_todo_menos_inscribirse(comedor):
    sitio = _sitio()
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(simulacro=True, dia=MANANA, navegador=nav)
    assert r.estado == "listo_para_inscribir" and sitio["inscripto"] is False


def test_boton_deshabilitado(comedor):
    nav = NavegadorFalso(PaginaFalsa(_sitio(deshabilitado=True)))
    assert comedor.ejecutar(dia=MANANA, navegador=nav).estado == mod.NO_HABILITADO


def test_login_que_falla(comedor):
    sitio = _sitio()
    sitio["login_falla"] = True
    assert comedor.ejecutar(dia=MANANA, navegador=NavegadorFalso(PaginaFalsa(sitio))).estado == mod.LOGIN_FALLO


def test_clic_que_no_confirma_no_se_da_por_inscripto(comedor):
    sitio = _sitio()
    pag = PaginaFalsa(sitio)
    original = pag.evaluar
    pag.evaluar = lambda js: True if ("data-miku-i" in js and js != mod._JS_INSTANTANEA) else original(js)       # el clic no cambia nada
    assert comedor.ejecutar(dia=MANANA, navegador=NavegadorFalso(pag)).estado == mod.DESCONOCIDO


def test_sin_usuario_sin_clave_sin_navegador(comedor, cfg, monkeypatch):
    nav = NavegadorFalso(PaginaFalsa(_sitio()), abre=False)
    assert comedor.ejecutar(dia=MANANA, navegador=nav).estado == mod.SIN_NAVEGADOR and nav.cerrado
    monkeypatch.setattr(mod, "leer_clave", lambda u: None)
    assert comedor.ejecutar(dia=MANANA, navegador=nav).estado == mod.SIN_CLAVE
    cfg.valores["comedor_usuario"] = ""
    assert comedor.ejecutar(dia=MANANA, navegador=nav).estado == mod.SIN_USUARIO


def test_un_error_inesperado_no_rompe_y_libera_el_candado(comedor):
    class Explota(PaginaFalsa):
        def ir(self, url, espera=20):
            raise RuntimeError("boom")

    r = comedor.ejecutar(dia=MANANA, navegador=NavegadorFalso(Explota(_sitio())))
    assert r.estado == mod.ERROR
    assert comedor.ejecutar(dia=MANANA, navegador=NavegadorFalso(PaginaFalsa(_sitio()))).estado == mod.INSCRIPTO


def test_dos_tramites_a_la_vez_no_se_pisan(comedor):
    comedor._lock.acquire()
    try:
        assert comedor.ejecutar(dia=MANANA, navegador=NavegadorFalso(PaginaFalsa(_sitio()))).estado == mod.ERROR
    finally:
        comedor._lock.release()


# --------------------------------------------------------------------------- #
# Herramienta, avisos y estado del día
# --------------------------------------------------------------------------- #
def test_la_tool_responde_al_toque_y_avisa_al_terminar(comedor, monkeypatch):
    dichas, listo = [], __import__("threading").Event()
    from types import SimpleNamespace
    comedor.initialize(SimpleNamespace(voice=SimpleNamespace(decir=lambda t: (dichas.append(t), listo.set()))))
    monkeypatch.setattr(comedor, "ejecutar", lambda: mod.Resultado(mod.INSCRIPTO, dia=date.today() + timedelta(days=1)))
    r = comedor.manejar_tool("inscribir_comedor", {}, {})
    assert r.ok and r.intencion == "comedor.iniciando"
    assert listo.wait(5) and "mañana" in dichas[0]
    comedor._hilo.join(5)
    assert comedor.ya_resuelto(date.today() + timedelta(days=1))


def test_sin_usuario_la_tool_lo_dice(comedor, cfg):
    cfg.valores["comedor_usuario"] = ""
    assert comedor.inscribir_en_segundo_plano().intencion == "comedor.sin_usuario"


def test_el_estado_del_dia(comedor):
    assert not comedor.ya_resuelto(MANANA)
    comedor._recordar(mod.Resultado(mod.INSCRIPTO, dia=MANANA))
    assert comedor.ya_resuelto(MANANA) and not comedor.ya_resuelto(MANANA + timedelta(days=1))
    comedor._recordar(mod.Resultado(mod.LOGIN_FALLO, dia=MANANA + timedelta(days=1)))
    assert not comedor.ya_resuelto(MANANA + timedelta(days=1))


def test_dia_hablado():
    assert mod._dia_hablado(date.today() + timedelta(days=1)) == "mañana"
    lejano = date.today() + timedelta(days=3)
    assert mod._dia_hablado(None) == "mañana"
    assert mod._dia_hablado(lejano) == f"el {mod._DIAS[lejano.weekday()]} {lejano.day}"


def test_la_macro_comedor_delega_en_el_plugin(cfg, monkeypatch):
    from types import SimpleNamespace
    from miku.plugins.productividad.macros import Macros
    llamado = []
    plugin = SimpleNamespace(nombre="comedor", inscribir_en_segundo_plano=lambda: llamado.append(1) or "ok")
    m = Macros()
    m._bus = SimpleNamespace(plugins=[plugin])
    assert m._resolver_manejador("abrir_comedor")("abrir_comedor", {}) == "ok" and llamado == [1]
    m._bus = SimpleNamespace(plugins=[])
    assert m._resolver_manejador("abrir_comedor")("abrir_comedor", {}).intencion == "comedor.sin_usuario"


def test_el_plugin_solo_se_carga_con_usuario(cfg):
    from miku.plugins import registro
    assert "comedor" not in {p.nombre for p in registro.instanciar_plugins(cfg)}
    cfg.valores["comedor_usuario"] = "alumno"
    assert "comedor" in {p.nombre for p in registro.instanciar_plugins(cfg)}


# --------------------------------------------------------------------------- #
# Regla proactiva
# --------------------------------------------------------------------------- #
class PluginFalso:
    def __init__(self, resuelto=False):
        self.resuelto, self.inscripciones = resuelto, 0

    def ya_resuelto(self, dia):
        return self.resuelto

    def inscribir_en_segundo_plano(self, automatico=False):
        self.inscripciones += 1


def _ctx(cfg, hora, dia=(2026, 9, 21), juego=None):
    from miku.servicios.proactivo import Contexto
    return Contexto(cfg, datetime(*dia, hora, 0), 0.0, juego)


def _regla(plugin, inactivo=10):
    from miku.servicios.reglas_proactivas import ComedorDiario
    return ComedorDiario(lambda: plugin, inactivo=lambda: inactivo)


def test_avisa_desde_la_hora_configurada(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00")
    regla = _regla(PluginFalso())
    assert list(regla.evaluar(_ctx(cfg, 18))) == []
    (aviso,) = regla.evaluar(_ctx(cfg, 19))
    assert aviso.intencion == "comedor.es_hora" and aviso.una_vez_por_dia


def test_no_avisa_si_ya_esta_resuelto_sin_usuario_o_sin_hora(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00")
    assert list(_regla(PluginFalso(resuelto=True)).evaluar(_ctx(cfg, 20))) == []
    assert list(_regla(None).evaluar(_ctx(cfg, 20))) == []
    cfg.valores["comedor_hora"] = ""
    assert list(_regla(PluginFalso()).evaluar(_ctx(cfg, 20))) == []
    cfg.valores.update(comedor_hora="19:00", comedor_usuario="")
    assert list(_regla(PluginFalso()).evaluar(_ctx(cfg, 20))) == []


def test_viernes_y_sabado_no_hay_comedor_al_dia_siguiente(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00")
    regla = _regla(PluginFalso())
    assert list(regla.evaluar(_ctx(cfg, 20, dia=(2026, 9, 25)))) == []      # viernes: mañana es sábado
    assert list(regla.evaluar(_ctx(cfg, 20, dia=(2026, 9, 26)))) == []      # sábado
    assert len(list(regla.evaluar(_ctx(cfg, 20, dia=(2026, 9, 27))))) == 1  # domingo: mañana es lunes


def test_modo_automatico_inscribe_una_vez_y_solo_si_estas_usando_la_pc(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00", comedor_auto=True)
    plugin = PluginFalso()
    assert list(_regla(plugin, inactivo=900).evaluar(_ctx(cfg, 20))) == [] and plugin.inscripciones == 0   # ausente
    assert list(_regla(plugin).evaluar(_ctx(cfg, 20, juego="cs2"))) == [] and plugin.inscripciones == 0   # jugando
    regla = _regla(plugin)
    (aviso,) = regla.evaluar(_ctx(cfg, 20))
    assert aviso.intencion == "comedor.auto" and plugin.inscripciones == 1
    assert list(regla.evaluar(_ctx(cfg, 20))) == [] and plugin.inscripciones == 1                          # un intento por día


def test_la_regla_esta_en_el_motor(cfg):
    from miku.servicios.reglas_proactivas import reglas_por_defecto
    assert "comedor" in {r.nombre for r in reglas_por_defecto(cfg)}


def test_clave_en_el_administrador_de_credenciales(monkeypatch):
    import sys
    from types import SimpleNamespace
    guardado = {}
    falso = SimpleNamespace(set_password=lambda s, u, c: guardado.update({(s, u): c}),
                            get_password=lambda s, u: guardado.get((s, u)))
    monkeypatch.setitem(sys.modules, "keyring", falso)
    assert mod.guardar_clave("alumno", "x") and mod.leer_clave("alumno") == "x" and mod.leer_clave("otro") is None
    assert (mod.SERVICIO_CLAVE, "alumno") in guardado
