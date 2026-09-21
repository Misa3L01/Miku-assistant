"""Comedor: lectura de la página de inscripciones, trámite completo (navegador simulado) y aviso diario."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from miku.ajustes import carga as config_mod
from miku.plugins.productividad import comedor as mod
from miku.plugins.productividad.comedor import Comedor, decidir, formas_de_fecha, menciona_fecha

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
    assert decidir(pagina("Autogestión Inscripciones. No hay comidas para inscribirse.", [elemento(0, "Salir")]), MANANA).accion == "sin_comidas"


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
        if self.s.get("dos_pasos") and self.s.get("paso") == 1:
            self.s["paso"] = 0                          # recargar la página abandona la confirmación pendiente
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
            indice = int(__import__("re").search(r'data-miku-i="(\d+)"', js).group(1))
            self.eventos.append(("clic_indice", js))
            if self.s.get("dos_pasos"):
                if self.s.get("paso") == 1 and indice == self.s["confirmar"]:
                    self.s["inscripto"], self.s["paso"] = True, 2
                elif self.s.get("paso", 0) == 0 and indice == self.s["boton"]:
                    self.s["paso"] = 1                       # primer clic: aparece "¿Inscribirse?"
            else:
                self.s["inscripto"] = True
            return True
        return None

    def texto(self, limite=6000):
        return ""

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

    reintentable = True

    def puede_reintentar(self, dia):
        return self.reintentable

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


def _ctx_min(cfg, hora, minuto, dia=(2026, 9, 21)):
    from miku.servicios.proactivo import Contexto
    return Contexto(cfg, datetime(*dia, hora, minuto), 0.0, None)


def test_modo_automatico_solo_si_estas_usando_la_pc_y_no_jugando(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00", comedor_auto=True)
    plugin = PluginFalso()
    assert list(_regla(plugin, inactivo=900).evaluar(_ctx(cfg, 20))) == [] and plugin.inscripciones == 0   # ausente
    assert list(_regla(plugin).evaluar(_ctx(cfg, 20, juego="cs2"))) == [] and plugin.inscripciones == 0   # jugando
    (aviso,) = _regla(plugin).evaluar(_ctx(cfg, 20))
    assert aviso.intencion == "comedor.auto" and plugin.inscripciones == 1


def test_modo_automatico_reintenta_hasta_tres_veces_separadas_por_media_hora(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00", comedor_auto=True)
    plugin, regla = PluginFalso(), _regla(PluginFalso())
    regla = _regla(plugin)
    regla.evaluar(_ctx_min(cfg, 19, 0))
    assert plugin.inscripciones == 1
    list(regla.evaluar(_ctx_min(cfg, 19, 10)))            # muy pronto: no
    assert plugin.inscripciones == 1
    list(regla.evaluar(_ctx_min(cfg, 19, 31)))            # ya pasó media hora: reintenta
    list(regla.evaluar(_ctx_min(cfg, 20, 2)))
    list(regla.evaluar(_ctx_min(cfg, 21, 0)))             # cuarto intento: no (el máximo es 3)
    assert plugin.inscripciones == 3


def test_no_reintenta_si_el_problema_es_de_configuracion_ni_si_ya_esta_inscripto(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00", comedor_auto=True)
    fallido = PluginFalso()
    fallido.reintentable = False                          # p. ej. contraseña mal: reintentar no arregla nada
    list(_regla(fallido).evaluar(_ctx(cfg, 20)))
    assert fallido.inscripciones == 0
    listo = PluginFalso(resuelto=True)
    list(_regla(listo).evaluar(_ctx(cfg, 20)))
    assert listo.inscripciones == 0


def test_un_dia_nuevo_empieza_de_cero(cfg):
    cfg.valores.update(comedor_usuario="alumno", comedor_hora="19:00", comedor_auto=True)
    plugin = PluginFalso()
    regla = _regla(plugin)
    for hora, minuto in ((19, 0), (19, 31), (20, 2)):
        list(regla.evaluar(_ctx_min(cfg, hora, minuto)))
    assert plugin.inscripciones == 3
    list(regla.evaluar(_ctx_min(cfg, 19, 5, dia=(2026, 9, 22))))
    assert plugin.inscripciones == 4


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


# --------------------------------------------------------------------------- #
# Con la página REAL del comedor (volcado anonimizado, tests/fixtures/comedor_pagina_real.json)
# --------------------------------------------------------------------------- #
REAL = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "comedor_pagina_real.json")
                  .read_text(encoding="utf-8"))
DIA_REAL = date(2026, 9, 21)
DOMINGO_14_09 = datetime(2026, 9, 20, 14, 9)


def _real(**cambios):
    """Copia de la página real con retoques (para simular otros estados)."""
    import copy
    p = copy.deepcopy(REAL)
    if cambios.get("sin_boton"):
        p["elementos"] = [e for e in p["elementos"] if e.get("texto") != "Inscribirse"]
    if "celda_inscripto" in cambios:
        p["tablas"][-1][1][8] = cambios["celda_inscripto"]
    if cambios.get("sin_filas"):
        p["tablas"][-1] = p["tablas"][-1][:1]
    if cambios.get("deshabilitado"):
        for e in p["elementos"]:
            if e.get("texto") == "Inscribirse":
                e["deshabilitado"] = True
    return p


def test_la_pagina_real_se_lee_como_tabla():
    filas = mod.filas_de_comidas(REAL)
    assert len(filas) == 1
    f = filas[0]
    assert f["tipo de comida"] == "Almuerzo" and f["fecha comida"] == "21/09/2026"
    assert f["habilitado desde"] == "20/09/2026 14:00" and f["cupo"] == "900" and f["inscripto"] == ""


def test_real_hay_almuerzo_habilitado_y_se_aprieta_el_boton_correcto():
    d = decidir(REAL, DIA_REAL, DOMINGO_14_09, ["almuerzo"])
    assert d.accion == "inscribir" and d.indice == 35            # el elemento 35 es el botón "Inscribirse"
    assert REAL["elementos"][35]["id"].endswith("cuadro0_seleccion")


def test_real_antes_de_la_hora_de_habilitacion_no_se_inscribe():
    d = decidir(REAL, DIA_REAL, datetime(2026, 9, 20, 13, 0), ["almuerzo"])
    assert d.accion == "no_habilitado" and "14:00" in d.detalle


def test_real_otro_dia_o_otro_tipo_de_comida_no_hay_nada():
    assert decidir(REAL, date(2026, 9, 22), DOMINGO_14_09).accion == "sin_comidas"
    d = decidir(REAL, DIA_REAL, DOMINGO_14_09, ["cena"])
    assert d.accion == "sin_comidas" and "Almuerzo" in d.detalle
    assert decidir(REAL, DIA_REAL, DOMINGO_14_09, []).accion == "inscribir"        # sin filtro: cualquier tipo


def test_real_tabla_vacia_es_sin_comidas():
    assert decidir(_real(sin_filas=True), DIA_REAL, DOMINGO_14_09).accion == "sin_comidas"


def test_real_sin_boton_no_se_da_por_inscripto_por_el_encabezado():
    """Regresión: el título de columna "Inscripto?" no significa que estés inscripto."""
    d = decidir(_real(sin_boton=True), DIA_REAL, DOMINGO_14_09)
    assert d.accion == "sin_boton"


def test_real_con_la_columna_inscripto_marcada_ya_esta_inscripto():
    assert decidir(_real(celda_inscripto="tilde.png", sin_boton=True), DIA_REAL, DOMINGO_14_09).accion == "ya_inscripto"
    assert decidir(_real(celda_inscripto="Sí"), DIA_REAL, DOMINGO_14_09).accion == "ya_inscripto"
    assert decidir(_real(celda_inscripto="No"), DIA_REAL, DOMINGO_14_09).accion == "inscribir"


def test_real_boton_deshabilitado():
    assert decidir(_real(deshabilitado=True), DIA_REAL, DOMINGO_14_09).accion == "no_habilitado"


def test_una_pagina_de_login_no_tiene_tabla_y_no_se_confunde():
    login = {"url": "x", "titulo": "Autentificación", "texto": "Usuario Clave Ingresar", "elementos": [], "tablas": []}
    assert mod.filas_de_comidas(login) is None
    assert decidir(login, DIA_REAL, DOMINGO_14_09).accion == "desconocido"       # sesión vencida/login: no es "no hay comida"


def test_tramite_completo_con_la_estructura_real(comedor):
    """Sitio simulado con la página real: antes del clic hay botón; después, la fila queda marcada."""
    sitio = {"inscripto": False}

    def instantanea():
        return _real(celda_inscripto="tilde.png", sin_boton=True) if sitio["inscripto"] else REAL

    sitio["instantanea"] = instantanea
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(dia=DIA_REAL, navegador=nav)
    assert r.estado == mod.INSCRIPTO
    assert any(e[0] == "clic_indice" and '"35"' in e[1] for e in nav._pagina.eventos)


def test_tipos_de_comida_desde_la_config(comedor, cfg):
    cfg.valores["comedor_tipos"] = ["cena"]
    sitio = {"inscripto": False, "instantanea": lambda: REAL}
    r = comedor.ejecutar(dia=DIA_REAL, navegador=NavegadorFalso(PaginaFalsa(sitio)))
    assert r.estado == mod.SIN_COMIDAS
    assert mod._lista("almuerzo") == ["almuerzo"] and mod._lista(None) == [] and mod._lista(["a", ""]) == ["a"]


# --------------------------------------------------------------------------- #
# Usuario con comillas sueltas (bug real) y guardado de la contraseña
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("crudo,limpio", [("'12345678", "12345678"), ('"12345678"', "12345678"), ("  ab_cd ", "ab_cd"),
                                          ("`1234", "1234"), ("", ""), (None, "")])
def test_limpiar_usuario(crudo, limpio):
    assert mod.limpiar_usuario(crudo) == limpio


@pytest.mark.parametrize("usuario,valido", [("12345678", True), ("juan_perez1", True), ("'12345678", False),
                                            ("juan perez", False), ("juan@mail.com", False), ("", False)])
def test_usuario_valido_como_lo_exige_la_pagina(usuario, valido):
    assert mod.usuario_valido(usuario) is valido


def test_un_apostrofe_de_mas_en_la_config_se_corrige_solo(comedor, cfg):
    cfg.valores["comedor_usuario"] = "'12345678"
    sitio = _sitio()
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(dia=MANANA, navegador=nav)
    assert r.estado == mod.INSCRIPTO
    assert sitio["escrito"]["#ef_form_103000002_datosusuario"] == "12345678"      # sin el apóstrofe


def test_un_usuario_con_simbolos_no_se_intenta_y_se_explica(comedor, cfg):
    cfg.valores["comedor_usuario"] = "juan@mail.com"
    nav = NavegadorFalso(PaginaFalsa(_sitio()))
    assert comedor.ejecutar(dia=MANANA, navegador=nav).estado == mod.USUARIO_INVALIDO
    assert not nav._pagina.eventos                                                   # ni abrió la página
    assert comedor.inscribir_en_segundo_plano().intencion == "comedor.usuario_invalido"


def test_si_el_login_falla_se_cuenta_lo_que_dijo_la_pagina(comedor):
    sitio = _sitio()
    sitio["login_falla"] = True
    pag = PaginaFalsa(sitio)
    pag.texto = lambda limite=6000: "Se han encontrado los siguientes problemas:\n Usuario no es válido\nversión 3.0.0"
    r = comedor.ejecutar(dia=MANANA, navegador=NavegadorFalso(pag))
    assert r.estado == mod.LOGIN_FALLO and "Usuario no es válido" in r.detalle


def test_guardar_clave_pide_dos_veces_y_confirma(cfg, monkeypatch, capsys):
    import getpass
    cfg.valores["comedor_usuario"] = "12345678"
    monkeypatch.setattr(config_mod, "cargar", lambda: cfg)
    guardado = {}
    monkeypatch.setattr(mod, "guardar_clave", lambda u, c: guardado.update({u: c}) or True)
    monkeypatch.setattr(mod, "leer_clave", lambda u: guardado.get(u))
    respuestas = iter(["secreta1", "secreta1"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(respuestas))
    assert mod._cli_guardar_clave() == 0
    salida = capsys.readouterr().out
    assert guardado == {"12345678": "secreta1"} and "8 caracteres" in salida and "secreta1" not in salida


def test_guardar_clave_no_guarda_si_no_coinciden_o_esta_vacia(cfg, monkeypatch, capsys):
    import getpass
    cfg.valores["comedor_usuario"] = "12345678"
    monkeypatch.setattr(config_mod, "cargar", lambda: cfg)
    guardado = {}
    monkeypatch.setattr(mod, "guardar_clave", lambda u, c: guardado.update({u: c}) or True)
    respuestas = iter(["una", "otra"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(respuestas))
    assert mod._cli_guardar_clave() == 1 and guardado == {} and "no coinciden" in capsys.readouterr().out
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "")
    assert mod._cli_guardar_clave() == 1 and guardado == {}
    cfg.valores["comedor_usuario"] = "12 34"
    assert mod._cli_guardar_clave() == 1 and "no acepta" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Doble confirmación (bug real): "Inscribirse" en la fila y otra vez "Inscribirse" en "¿Inscribirse?"
# --------------------------------------------------------------------------- #
PASO2 = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "comedor_paso2_real.json")
                   .read_text(encoding="utf-8"))


def _sitio_dos_pasos():
    sitio = {"inscripto": False, "dos_pasos": True, "paso": 0, "boton": 35, "confirmar": 9}

    def instantanea():
        if sitio["inscripto"]:
            return _real(celda_inscripto="tilde.png", sin_boton=True)
        return PASO2 if sitio["paso"] == 1 else REAL

    sitio["instantanea"] = instantanea
    return sitio


def test_el_segundo_paso_real_se_reconoce_y_nunca_es_cancelar():
    assert mod.indice_de_confirmacion(PASO2) == 9
    cancelar = PASO2["elementos"][10]
    assert cancelar["texto"] == "Cancelar" and mod.indice_de_confirmacion(PASO2) != cancelar["i"]
    assert mod.indice_de_confirmacion(REAL) is None                 # la tabla del paso 1 no es una confirmación
    assert mod.indice_de_confirmacion({}) is None


def test_tramite_con_doble_confirmacion_aprieta_las_dos_veces_sin_recargar_en_el_medio(comedor):
    sitio = _sitio_dos_pasos()
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(dia=DIA_REAL, navegador=nav)
    assert r.estado == mod.INSCRIPTO
    eventos = nav._pagina.eventos
    clics = [i for i, e in enumerate(eventos) if e[0] == "clic_indice"]
    assert len(clics) == 2 and '"35"' in eventos[clics[0]][1] and '"9"' in eventos[clics[1]][1]
    assert not [e for e in eventos[clics[0]:clics[1]] if e[0] == "ir"]         # ninguna recarga entre los dos clics
    assert eventos[-1][0] != "clic_indice" and any(e[0] == "ir" for e in eventos[clics[1]:])   # y después sí relee


def test_si_la_confirmacion_no_se_pudo_apretar_no_se_da_por_inscripto(comedor):
    sitio = _sitio_dos_pasos()
    sitio["confirmar"] = 999                    # el sitio no reacciona al botón que Miku aprieta en el paso 2
    r = comedor.ejecutar(dia=DIA_REAL, navegador=NavegadorFalso(PaginaFalsa(sitio)))
    assert r.estado == mod.DESCONOCIDO and sitio["inscripto"] is False


def test_se_guarda_la_ultima_lectura_para_ver_como_queda_inscripto(comedor):
    comedor.ejecutar(dia=DIA_REAL, navegador=NavegadorFalso(PaginaFalsa(_sitio_dos_pasos())))
    guardado = json.loads((comedor.tmp / "data" / "comedor" / "ultima_lectura.json").read_text(encoding="utf-8"))
    assert "tablas" in guardado


def test_la_ventana_del_comedor_normal_minimizada_u_oculta(comedor, cfg):
    cfg.valores["brave_ruta_exe"] = "C:/x/brave.exe"
    n = comedor._navegador()
    assert (n.visible, n.minimizada) == (True, False)
    cfg.valores["comedor_ventana"] = "minimizada"
    n = comedor._navegador()
    assert (n.visible, n.minimizada) == (True, True)
    cfg.valores["comedor_ventana"] = "oculta"
    assert comedor._navegador().visible is False
    cfg.valores.update(comedor_ventana="normal", comedor_ver=False)             # compatibilidad con la opción vieja
    assert comedor._navegador().visible is False


# --------------------------------------------------------------------------- #
# La página real YA inscripta y el manejo de resultados
# --------------------------------------------------------------------------- #
INSCRIPTO_REAL = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "comedor_inscripto_real.json")
                            .read_text(encoding="utf-8"))


def test_real_ya_inscripto_se_lee_de_la_columna_inscripto():
    filas = mod.filas_de_comidas(INSCRIPTO_REAL)
    assert filas[0]["inscripto"] == "SI" and filas[0]["ya asistio"] == "NO"
    d = decidir(INSCRIPTO_REAL, DIA_REAL, datetime(2026, 9, 20, 19, 0), ["almuerzo"])
    assert d.accion == "ya_inscripto"
    assert not [e for e in INSCRIPTO_REAL["elementos"] if e["texto"] == "Inscribirse"]        # el botón desaparece


def test_ya_inscripto_no_vuelve_a_apretar_nada(comedor):
    sitio = {"inscripto": True, "instantanea": lambda: INSCRIPTO_REAL}
    nav = NavegadorFalso(PaginaFalsa(sitio))
    r = comedor.ejecutar(dia=DIA_REAL, navegador=nav)
    assert r.estado == mod.YA_INSCRIPTO and not [e for e in nav._pagina.eventos if e[0] == "clic_indice"]


def test_se_recuerda_cada_resultado_y_los_de_configuracion_no_se_reintentan(comedor, monkeypatch):
    from types import SimpleNamespace
    comedor.initialize(SimpleNamespace(voice=None))
    monkeypatch.setattr(mod, "dia_a_inscribirse", lambda hoy=None: MANANA)
    monkeypatch.setattr(comedor, "_avisar", lambda r: None)
    monkeypatch.setattr(comedor, "ejecutar", lambda **k: mod.Resultado(mod.LOGIN_FALLO, "Usuario no es válido", MANANA))
    comedor._tramite(True)
    assert comedor._estado_guardado(MANANA) == "login_fallo" and not comedor.puede_reintentar(MANANA)
    assert comedor.puede_reintentar(MANANA + timedelta(days=1)) and not comedor.ya_resuelto(MANANA)
    monkeypatch.setattr(comedor, "ejecutar", lambda **k: mod.Resultado(mod.SIN_COMIDAS, "", MANANA))
    comedor._tramite(True)
    assert comedor.puede_reintentar(MANANA)                   # "todavía no hay comida" sí se reintenta


def test_el_mismo_todavia_no_hay_comida_no_se_repite_en_los_reintentos(comedor, monkeypatch):
    from types import SimpleNamespace
    comedor.initialize(SimpleNamespace(voice=None))
    monkeypatch.setattr(mod, "dia_a_inscribirse", lambda hoy=None: MANANA)
    avisos = []
    monkeypatch.setattr(comedor, "_avisar", lambda r: avisos.append(r.estado))
    monkeypatch.setattr(comedor, "ejecutar", lambda **k: mod.Resultado(mod.SIN_COMIDAS, "", MANANA))
    comedor._tramite(True)
    comedor._tramite(True)
    assert avisos == [mod.SIN_COMIDAS]                        # el segundo intento automático no lo repite
    comedor._tramite(False)                                   # pedido a mano: siempre responde
    assert avisos == [mod.SIN_COMIDAS, mod.SIN_COMIDAS]
    monkeypatch.setattr(comedor, "ejecutar", lambda **k: mod.Resultado(mod.INSCRIPTO, "", MANANA))
    comedor._tramite(True)
    assert avisos[-1] == mod.INSCRIPTO and comedor.ya_resuelto(MANANA)
