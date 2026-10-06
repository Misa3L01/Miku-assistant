"""Control de hábitos: cuándo rezonga, la ruleta de frases, el cierre seguro de la pestaña y el sí / no. Todo simulado."""
from __future__ import annotations

import random
import threading
import types
from datetime import date
from typing import Any, List, Tuple

import pytest

from miku import app
from miku.ajustes import carga as config_mod
from miku.plugins import registro
from miku.plugins.asistente import habitos as plugin_mod
from miku.plugins.asistente.habitos import Habitos
from miku.servicios import habitos
from miku.servicios.habitos import INCOGNITO, Vigilante
from miku.servicios.uso import RegistroUso
from miku.voz.frases import catalogo_habitos
from miku.voz.frases.banco import Banco, frases


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def avanzar(self, segundos: float) -> None:
        self.t += segundos

    def mono(self) -> float:
        return self.t


def ventana(titulo: str, proceso: str = "brave", hwnd: int = 77) -> Tuple[int, str, str]:
    return hwnd, proceso, titulo


# --------------------------------------------------------------------------- #
# La lista
# --------------------------------------------------------------------------- #
def test_la_lista_se_lee_sin_vacios_ni_repetidos():
    reglas = habitos.cargar_reglas(["Facebook", " tiktok ", "", "FACEBOOK", "YouTube Shorts"])
    assert [r.nombre for r in reglas] == ["Facebook", "tiktok", "YouTube Shorts"]
    assert [r.clave for r in reglas] == ["facebook", "tiktok", "youtube shorts"]


def test_la_lista_tambien_se_puede_escribir_con_comas_y_sin_acentos():
    assert [r.clave for r in habitos.cargar_reglas("facebook, Películas ,")] == ["facebook", "peliculas"]
    assert habitos.cargar_reglas(None) == [] and habitos.cargar_reglas([]) == []


def test_una_opcion_de_lista_mal_escrita_usa_la_de_por_defecto():
    assert habitos.lista_normalizada(None, ("a",)) == ("a",)
    assert habitos.lista_normalizada([], ("a",)) == ("a",)
    assert habitos.lista_normalizada(5, ("a",)) == ("a",)
    assert habitos.lista_normalizada(["Brave", " Chrome"], ("a",)) == ("brave", "chrome")


# --------------------------------------------------------------------------- #
# Qué ventana cuenta
# --------------------------------------------------------------------------- #
@pytest.fixture
def vigilante():
    reloj = Reloj()
    v = Vigilante(habitos.cargar_reglas(["facebook", "youtube shorts"]), reloj=reloj.mono)
    v.reloj = reloj
    return v


def test_el_titulo_del_navegador_con_una_palabra_de_la_lista_coincide(vigilante):
    d = vigilante.coincidencia(ventana("(3) Facebook - Brave"))
    assert d is not None and d.nombre == "facebook" and d.proceso == "brave" and d.hwnd == 77 and not d.incognito


@pytest.mark.parametrize("titulo", ["FACEBOOK", "facebook marketplace - Brave", "Inicio | Facebook", "Youtube Shorts - Brave"])
def test_no_importan_mayusculas_ni_el_resto_del_titulo(vigilante, titulo):
    assert vigilante.coincidencia(ventana(titulo)) is not None


def test_sin_acentos_la_palabra_igual_coincide():
    v = Vigilante(habitos.cargar_reglas(["películas"]))
    assert v.coincidencia(ventana("Películas gratis - Brave")) is not None
    assert v.coincidencia(ventana("PELICULAS gratis - Brave")) is not None


@pytest.mark.parametrize("v", [ventana("Facebook - Notepad", "notepad"), ventana("facebook.txt - Visual Studio Code", "code"),
                               ventana("Discord", "discord"), None])
def test_lo_que_no_es_un_navegador_no_cuenta(vigilante, v):
    assert vigilante.coincidencia(v) is None


def test_una_pagina_que_no_esta_en_la_lista_no_cuenta(vigilante):
    assert vigilante.coincidencia(ventana("GitHub - Brave")) is None


def test_el_incognito_solo_cuenta_si_se_pide():
    sin = Vigilante([])
    con = Vigilante([], vigilar_incognito=True)
    privada = ventana("Nueva pestaña - Brave (Private)")
    assert sin.coincidencia(privada) is None
    d = con.coincidencia(privada)
    assert d is not None and d.clave == INCOGNITO and d.incognito and d.nombre == "incógnito"
    assert con.coincidencia(ventana("GitHub - Brave")) is None, "una ventana normal no es incógnito"


@pytest.mark.parametrize("titulo", ["Nueva pestaña - Google Chrome (Incógnito)", "Inicio - Microsoft Edge InPrivate",
                                    "Nueva pestaña - Brave (Private)", "Mozilla Firefox Private Browsing",
                                    "Inicio - Firefox (Navegación privada)"])
def test_marcas_de_incognito_de_cada_navegador(titulo):
    assert habitos.es_incognito(titulo) is True


def test_una_pagina_de_la_lista_dentro_de_incognito_salta_por_la_pagina():
    v = Vigilante(habitos.cargar_reglas(["facebook"]), vigilar_incognito=True)
    d = v.coincidencia(ventana("Facebook - Brave (Private)"))
    assert d.clave == "facebook" and d.incognito is True


def test_las_marcas_de_incognito_se_pueden_cambiar():
    v = Vigilante([], vigilar_incognito=True, marcas_incognito=("modo secreto",))
    assert v.coincidencia(ventana("Inicio - Brave (Private)")) is None
    assert v.coincidencia(ventana("Inicio - Brave modo secreto")) is not None


# --------------------------------------------------------------------------- #
# Cuándo rezonga
# --------------------------------------------------------------------------- #
FB = ventana("Facebook - Brave")


def _pasar(v, ventana_, segundos, paso=3.0, inactivo=0.0):
    """Avanza el reloj de a ``paso`` s con esa ventana a la vista; devuelve el primer disparo."""
    for _ in range(int(segundos / paso)):
        v.reloj.avanzar(paso)
        d = v.revisar(ventana_, inactivo)
        if d is not None:
            return d
    return None


def test_no_rezonga_por_un_vistazo(vigilante):
    assert vigilante.revisar(FB) is None
    assert _pasar(vigilante, FB, 9) is None, "aún no pasaron los 10 s"


def test_rezonga_cuando_lleva_diez_segundos_seguidos(vigilante):
    vigilante.revisar(FB)
    d = _pasar(vigilante, FB, 12)
    assert d is not None and d.nombre == "facebook"


def test_un_alt_tab_reinicia_la_cuenta(vigilante):
    vigilante.revisar(FB)
    _pasar(vigilante, FB, 9)
    vigilante.reloj.avanzar(3)
    vigilante.revisar(ventana("GitHub - Brave"))                   # cambió de pestaña
    assert _pasar(vigilante, FB, 9) is None, "tiene que volver a juntar 10 s seguidos"


def test_cambiar_de_pagina_vigilada_tambien_reinicia(vigilante):
    vigilante.revisar(FB)
    _pasar(vigilante, FB, 9)
    vigilante.revisar(ventana("Youtube Shorts - Brave"))
    assert _pasar(vigilante, ventana("Youtube Shorts - Brave"), 9) is None


def test_no_vuelve_a_rezongar_dentro_del_cooldown(vigilante):
    vigilante.revisar(FB)
    assert _pasar(vigilante, FB, 12) is not None
    assert _pasar(vigilante, FB, 29 * 60) is None, "pasaron 29 minutos"
    assert _pasar(vigilante, FB, 3 * 60) is not None, "pasados los 30 minutos sí"


def test_cada_pagina_tiene_su_propio_cooldown(vigilante):
    vigilante.revisar(FB)
    assert _pasar(vigilante, FB, 12) is not None
    yt = ventana("Youtube Shorts - Brave")
    vigilante.revisar(yt)
    d = _pasar(vigilante, yt, 12)
    assert d is not None and d.nombre == "youtube shorts"


def test_si_la_pc_esta_sola_no_rezonga(vigilante):
    vigilante.revisar(FB, 0)
    assert _pasar(vigilante, FB, 60, inactivo=habitos.INACTIVO_MAX_SEG + 1) is None
    assert vigilante._actual is None, "y la cuenta se reinicia: al volver tiene que juntar 10 s de nuevo"


def test_olvidar_permite_reintentar_enseguida(vigilante):
    vigilante.revisar(FB)
    d = _pasar(vigilante, FB, 12)
    vigilante.olvidar(d.clave)
    assert _pasar(vigilante, FB, 3) is not None


def test_tras_cerrarla_se_vuelve_a_rezongar_a_los_dos_minutos_no_a_los_treinta(vigilante):
    vigilante.revisar(FB)
    d = _pasar(vigilante, FB, 12)
    vigilante.tras_cerrar(d.clave)
    vigilante.revisar(FB)                                          # la reabrió
    assert _pasar(vigilante, FB, 60) is None
    assert _pasar(vigilante, FB, 90) is not None


def test_la_espera_y_el_cooldown_se_configuran():
    reloj = Reloj()
    v = Vigilante(habitos.cargar_reglas(["facebook"]), espera_seg=3, cooldown_min=1, reloj=reloj.mono)
    v.reloj = reloj
    v.revisar(FB)
    assert _pasar(v, FB, 6) is not None
    assert _pasar(v, FB, 54) is None and _pasar(v, FB, 9) is not None


# --------------------------------------------------------------------------- #
# La ruleta de frases
# --------------------------------------------------------------------------- #
DATOS = {"pagina": "facebook", "veces": 3}


def test_hay_muchas_frases_para_cada_situacion():
    minimos = {"habito.regano": 10, "habito.otra_vez": 6, "habito.incognito": 6, "habito.cerrada": 6,
               "habito.perdon": 6, "habito.no_pude": 3, "habito.ya_no": 3}
    assert set(catalogo_habitos.CATALOGO) == set(minimos) == set(catalogo_habitos.TITULOS)
    for clave, minimo in minimos.items():
        assert len(set(catalogo_habitos.CATALOGO[clave])) >= minimo, clave


def test_todas_las_frases_se_completan_sin_dejar_llaves():
    b = Banco(random.Random(0))
    catalogo_habitos.registrar(b)
    for clave, variantes in catalogo_habitos.CATALOGO.items():
        for _ in range(len(variantes) * 2):
            texto = b.elegir(clave, **DATOS)
            assert "{" not in texto and "}" not in texto and texto.strip(), clave


@pytest.mark.parametrize("clave", ["habito.regano", "habito.otra_vez", "habito.incognito"])
def test_las_frases_de_rezongo_terminan_preguntando_si_se_cierra(clave):
    """Cada una tiene que poder contestarse con un "sí" o un "no"."""
    for frase in catalogo_habitos.CATALOGO[clave]:
        assert "?" in frase and ("cierr" in frase.lower() or "cerrar" in frase.lower()), frase


def test_varias_de_reincidencia_dicen_cuantas_veces():
    frases_con_numero = [f for f in catalogo_habitos.CATALOGO["habito.otra_vez"] if "{veces}" in f]
    assert len(frases_con_numero) >= 4


def test_la_ruleta_rota_sin_repetir_la_anterior_y_con_variedad():
    b = Banco(random.Random(3))
    catalogo_habitos.registrar(b)
    salidas = [b.elegir("habito.regano", **DATOS) for _ in range(60)]
    assert all(a != c for a, c in zip(salidas, salidas[1:]))
    assert len(set(salidas)) >= 10


def test_las_frases_se_registran_en_el_banco_global():
    for clave in catalogo_habitos.CATALOGO:
        assert frases.existe(clave)


# --------------------------------------------------------------------------- #
# El hab
# --------------------------------------------------------------------------- #
class WinFalsa:
    """Windows simulado: una ventana con título, foco y los efectos que se le piden."""

    def __init__(self) -> None:
        self.titulos = {77: "Facebook - Brave"}
        self.foco = 77
        self.puede_traer = True
        self.efectos: List[str] = []
        self.falla = False

    def titulo(self, hwnd):
        return self.titulos.get(hwnd)

    def es_primer_plano(self, hwnd):
        return self.foco == hwnd

    def traer_al_frente(self, hwnd):
        self.efectos.append("al_frente")
        if self.puede_traer:
            self.foco = hwnd

    def cerrar_pestana(self):
        if self.falla:
            raise OSError("sin teclado")
        self.efectos.append("ctrl+w")

    def cerrar_ventana(self, hwnd):
        self.efectos.append(f"WM_CLOSE {hwnd}")


@pytest.fixture
def bus():
    b = types.SimpleNamespace(preguntas=[], acepta=True)

    def preguntar(texto, tool, args, al_cancelar=None):
        b.preguntas.append({"texto": texto, "tool": tool, "args": args, "al_cancelar": al_cancelar})
        return b.acepta

    b.preguntar = preguntar
    return b


@pytest.fixture
def hab(cfg, bus, tmp_path, monkeypatch):
    cfg.valores.update(habitos_vigilar=["facebook"], habitos_incognito=False)
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(plugin_mod, "RUTA_REGISTRO", tmp_path / "habitos.json")
    monkeypatch.setattr(Habitos, "_bucle", lambda self: None)            # sin hilo de verdad
    p = Habitos()
    p.initialize(bus)
    p._hilo.join(1)
    p.reloj = Reloj()
    p._vigilante._reloj = p.reloj.mono
    p.estado = {"ventana": ventana("Facebook - Brave"), "inactivo": 0.0}
    p._leer_ventana = lambda: p.estado["ventana"]
    p._leer_inactivo = lambda: p.estado["inactivo"]
    p._win = WinFalsa()
    p._dormir = lambda s: None
    return p


def _vueltas(p, segundos, paso=3.0):
    for _ in range(int(segundos / paso)):
        p.reloj.avanzar(paso)
        p.revisar()


def test_sin_nada_que_vigilar_el_plugin_queda_inactivo(cfg, bus, monkeypatch):
    cfg.valores.update(habitos_vigilar=[], habitos_incognito=False)
    monkeypatch.setattr(config_mod, "config", cfg)
    p = Habitos()
    p.initialize(bus)
    assert p._hilo is None and p._vigilante is None
    p.revisar()                                                    # no hace nada ni falla


def test_a_los_diez_segundos_pregunta_con_una_frase_de_la_ruleta(hab, bus):
    hab.revisar()
    _vueltas(hab, 12)
    (q,) = bus.preguntas
    assert q["texto"] in [f.format(pagina="facebook", veces=1) for f in catalogo_habitos.CATALOGO["habito.regano"]]
    assert q["tool"] == "cerrar_pagina_vigilada"
    assert q["args"] == {"hwnd": 77, "proceso": "brave", "regla": "facebook", "nombre": "facebook", "modo": "pestana"}


def test_no_pregunta_dos_veces_seguidas(hab, bus):
    hab.revisar()
    _vueltas(hab, 60)
    assert len(bus.preguntas) == 1


def test_al_decir_que_no_contesta_con_otra_frase_de_la_ruleta(hab, bus):
    hab.revisar()
    _vueltas(hab, 12)
    assert bus.preguntas[0]["al_cancelar"]() in catalogo_habitos.CATALOGO["habito.perdon"]


def test_la_segunda_vez_del_dia_usa_las_frases_de_reincidencia(hab, bus):
    hab.revisar()
    _vueltas(hab, 12)
    hab.p = None
    _vueltas(hab, 31 * 60)                                      # pasa el cooldown, sigue en la página
    assert len(bus.preguntas) == 2
    asi = [f.format(pagina="facebook", veces=2) for f in catalogo_habitos.CATALOGO["habito.otra_vez"]]
    assert bus.preguntas[1]["texto"] in asi


def test_el_incognito_pregunta_por_la_ventana_entera(hab, bus, cfg):
    hab._vigilante.vigilar_incognito = True
    hab.estado["ventana"] = ventana("Nueva pestaña - Brave (Private)")
    hab.revisar()
    _vueltas(hab, 12)
    q = bus.preguntas[0]
    assert q["texto"] in catalogo_habitos.CATALOGO["habito.incognito"]
    assert q["args"]["modo"] == "ventana" and q["args"]["regla"] == INCOGNITO


def test_si_ya_habia_otra_pregunta_se_reintenta_enseguida(hab, bus):
    bus.acepta = False
    hab.revisar()
    _vueltas(hab, 12)
    assert len(bus.preguntas) == 1
    bus.acepta = True
    _vueltas(hab, 3)
    assert len(bus.preguntas) == 2, "no se esperó media hora: la pregunta anterior no llegó a hacerse"
    assert hab._registro.totales(1) == {"facebook": 1.0}, "y solo cuenta la que se hizo"


def test_sin_la_funcion_de_preguntar_no_rompe(cfg, tmp_path, monkeypatch):
    cfg.valores.update(habitos_vigilar=["facebook"])
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(plugin_mod, "RUTA_REGISTRO", tmp_path / "habitos.json")
    monkeypatch.setattr(Habitos, "_bucle", lambda self: None)
    p = Habitos()
    p.initialize(types.SimpleNamespace())
    p._hilo.join(1)
    reloj = Reloj()
    p._vigilante._reloj = reloj.mono
    p._leer_ventana, p._leer_inactivo = lambda: ventana("Facebook - Brave"), lambda: 0.0
    for _ in range(6):
        reloj.avanzar(3)
        p.revisar()


def test_solo_cuenta_cuantas_veces_saltaron_nunca_que_mirabas(hab, tmp_path):
    hab.revisar()
    _vueltas(hab, 12)
    hab.cerrar()
    texto = (tmp_path / "habitos.json").read_text(encoding="utf-8")
    assert "facebook" in texto and "Brave" not in texto and "Marketplace" not in texto


# ---------------------------------------------------------------- cerrar la pestaña
ARGS = {"hwnd": 77, "proceso": "brave", "regla": "facebook", "nombre": "facebook", "modo": "pestana"}


def test_con_tu_si_cierra_la_pestana(hab):
    texto = hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {})
    assert texto in [f.format(pagina="facebook") for f in catalogo_habitos.CATALOGO["habito.cerrada"]]
    assert hab._win.efectos == ["ctrl+w"]


def test_una_ventana_de_incognito_se_cierra_entera(hab):
    hab._win.titulos[77] = "Nueva pestaña - Brave (Private)"
    hab._vigilante.vigilar_incognito = True
    args = {**ARGS, "regla": INCOGNITO, "nombre": "incógnito", "modo": "ventana"}
    hab.manejar_tool("cerrar_pagina_vigilada", args, {})
    assert hab._win.efectos == ["WM_CLOSE 77"]


def test_si_ya_cambiaste_de_pestana_no_cierra_nada(hab):
    """Lo más importante: un 'sí' tardío nunca cierra una pestaña que no es."""
    hab._win.titulos[77] = "GitHub - Brave"
    texto = hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {})
    assert texto in catalogo_habitos.CATALOGO["habito.ya_no"] and hab._win.efectos == []


def test_si_la_ventana_ya_no_existe_no_hace_nada(hab):
    hab._win.titulos.clear()
    assert hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {}) in catalogo_habitos.CATALOGO["habito.ya_no"]
    assert hab._win.efectos == []


def test_si_la_ventana_no_esta_en_primer_plano_la_trae_antes_de_cerrar(hab):
    hab._win.foco = 5
    hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {})
    assert hab._win.efectos == ["al_frente", "ctrl+w"]


def test_si_no_puede_traerla_al_frente_no_manda_ctrl_w_a_otra_ventana(hab):
    """Ctrl+W va a la ventana con el foco: si no es la del navegador, cerraría otra cosa."""
    hab._win.foco, hab._win.puede_traer = 5, False
    texto = hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {})
    assert texto in catalogo_habitos.CATALOGO["habito.no_pude"]
    assert hab._win.efectos == ["al_frente"], "intentó traerla pero no mandó ninguna tecla"


def test_si_al_traerla_la_pagina_ya_era_otra_no_cierra(hab):
    hab._win.foco = 5

    def traer(hwnd):
        hab._win.foco = hwnd
        hab._win.titulos[hwnd] = "GitHub - Brave"            # al restaurarla muestra otra pestaña

    hab._win.traer_al_frente = traer
    assert hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {}) in catalogo_habitos.CATALOGO["habito.ya_no"]
    assert hab._win.efectos == []


def test_si_el_teclado_falla_lo_dice_sin_romper(hab):
    hab._win.falla = True
    assert hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {}) in catalogo_habitos.CATALOGO["habito.no_pude"]


def test_un_hwnd_invalido_no_rompe(hab):
    texto = hab.manejar_tool("cerrar_pagina_vigilada", {**ARGS, "hwnd": "raro"}, {})
    assert texto in catalogo_habitos.CATALOGO["habito.ya_no"] and hab._win.efectos == []


def test_tras_cerrarla_si_la_reabris_se_rezonga_pronto(hab, bus):
    hab.revisar()
    _vueltas(hab, 12)
    hab.manejar_tool("cerrar_pagina_vigilada", ARGS, {})
    hab.revisar()
    _vueltas(hab, 60)
    assert len(bus.preguntas) == 1
    _vueltas(hab, 90)
    assert len(bus.preguntas) == 2


def test_la_tool_de_cerrar_es_interna_el_llm_no_la_ve(hab):
    assert [t["function"]["name"] for t in hab.tools] == ["resumen_habitos"]
    assert hab.manejar_tool("otra", {}, {}) is None


# ---------------------------------------------------------------- resumen
def test_resumen_de_habitos(hab):
    assert "no te agarré" in hab.manejar_tool("resumen_habitos", {}, {})
    hab._registro.sumar("facebook", 1)
    hab._registro.sumar("facebook", 1)
    hab._registro.sumar("tiktok", 1)
    assert hab.manejar_tool("resumen_habitos", {"periodo": "hoy"}, {}) == "Hoy te agarré en: facebook, 2 veces; tiktok, 1 vez."
    assert hab.manejar_tool("resumen_habitos", {"periodo": "semana"}, {}).startswith("En los últimos 7 días te agarré en:")


def test_resumen_de_ayer_y_sin_registro():
    hoy = {"d": date(2026, 5, 2)}
    r = RegistroUso(hoy=lambda: hoy["d"])
    r.sumar("facebook", 3)
    assert "no te agarré" in habitos.resumen(r, "ayer")
    hoy["d"] = date(2026, 5, 3)
    assert habitos.resumen(r, "ayer") == "Ayer te agarré en: facebook, 3 veces."


def test_la_tool_se_elige_al_preguntar_por_los_habitos():
    from miku.cerebro import enrutador
    from miku.plugins.sistema.estado_pc import SystemStatus
    from miku.plugins.sistema.audio import Audio
    for frase in ("¿cuántas veces entré a facebook?", "cómo voy con mis hábitos"):
        elegidas = [t["function"]["name"] for t in enrutador.seleccionar(frase, Habitos.tools + SystemStatus.tools + Audio.tools, maximo=1)]
        assert elegidas == ["resumen_habitos"], frase


# ---------------------------------------------------------------- registro y opciones
def test_el_plugin_solo_se_carga_si_hay_algo_que_vigilar(cfg):
    entrada = next(e for e in registro._CATALOGO if e.modulo == "asistente.habitos")
    assert entrada.condicion(cfg) is False, "por defecto no vigila nada"
    cfg.valores["habitos_vigilar"] = ["facebook"]
    assert entrada.condicion(cfg) is True
    cfg.valores.update(habitos_vigilar=[], habitos_incognito=True)
    assert entrada.condicion(cfg) is True


def test_las_opciones_tienen_sus_valores_por_defecto(cfg):
    assert cfg.get("habitos_vigilar") == [] and cfg.get("habitos_incognito") is False
    assert cfg.get("habitos_espera_seg") == 10.0 and cfg.get("habitos_cooldown_min") == 30.0
    assert "brave" in cfg.get("habitos_navegadores") and cfg.get("habitos_marcas_incognito")
    assert tuple(cfg.get("habitos_navegadores")) == habitos.NAVEGADORES
    assert tuple(cfg.get("habitos_marcas_incognito")) == habitos.MARCAS_INCOGNITO, "el esquema y el módulo dicen lo mismo"


# ---------------------------------------------------------------- el recorrido completo con el parser
def test_con_tu_si_o_tu_no_por_el_parser(parser, hab, bus):
    parser.bus.plugins.insert(0, hab)       # antes que el plugin falso del conftest, que responde a cualquier tool
    hab.revisar()
    _vueltas(hab, 12)
    q = bus.preguntas[0]
    assert parser.preguntar(q["tool"], q["args"], q["texto"], q["al_cancelar"]) == q["texto"]
    assert parser.pregunta_pendiente() is not None
    assert parser.procesar("sí", {}) in [f.format(pagina="facebook") for f in catalogo_habitos.CATALOGO["habito.cerrada"]]
    assert hab._win.efectos == ["ctrl+w"] and parser.pregunta_pendiente() is None

    assert parser.preguntar(q["tool"], q["args"], q["texto"], q["al_cancelar"]) == q["texto"]
    assert parser.procesar("no", {}) in catalogo_habitos.CATALOGO["habito.perdon"]
    assert hab._win.efectos == ["ctrl+w"], "con el no no se cerró nada más"


def test_el_parser_no_pisa_una_pregunta_que_esta_esperando(parser):
    assert parser.preguntar("cerrar_pagina_vigilada", {}, "¿La cierro?") == "¿La cierro?"
    assert parser.preguntar("cerrar_pagina_vigilada", {}, "¿Otra?") is None
    assert parser.pregunta_pendiente()[1] == "¿La cierro?"


def test_pasada_la_espera_el_parser_acepta_una_pregunta_nueva(parser):
    parser.preguntar("cerrar_pagina_vigilada", {}, "¿La cierro?")
    parser._confirmacion_desde -= 120
    assert parser.preguntar("cerrar_pagina_vigilada", {}, "¿Otra?") == "¿Otra?"


def test_una_pregunta_con_botones_vigentes_tampoco_se_pisa(parser):
    parser.preguntar("cerrar_pagina_vigilada", {}, "¿La cierro?")
    parser.habilitar_boton(1, 300)
    parser._confirmacion_desde -= 120
    assert parser.preguntar("cerrar_pagina_vigilada", {}, "¿Otra?") is None


def test_una_respuesta_al_no_que_falla_no_rompe(parser):
    def falla():
        raise RuntimeError("boom")

    parser.preguntar("cerrar_pagina_vigilada", {}, "¿La cierro?", falla)
    assert parser.procesar("no", {}) == "Cancelado, no hice nada."


def test_la_confirmacion_normal_no_hereda_el_no_de_la_pregunta_anterior(parser, cerebro):
    parser.preguntar("cerrar_pagina_vigilada", {}, "¿La cierro?", lambda: "frase de hábitos")
    parser.procesar("no", {})
    cerebro.resp = {"respuesta": "", "tools_call": [{"nombre": "control_energia", "args": {"accion": "apagar"}}]}
    parser.procesar("apagá la pc", {})
    assert parser.procesar("no", {}) == "Cancelado, no hice nada."


# ---------------------------------------------------------------- la app
class ParserDePregunta:
    def __init__(self) -> None:
        self.preguntas: List[tuple] = []
        self.acepta = True

    def preguntar(self, tool, args, texto, al_cancelar=None):
        self.preguntas.append((tool, args, texto, al_cancelar))
        return texto if self.acepta else None

    def pregunta_pendiente(self):
        return (1, "¿La cierro?") if self.acepta else None


class Temporizador:
    creados: List[Any] = []

    def __init__(self, espera, funcion, args=()) -> None:
        self.espera, self.funcion, self.args = espera, funcion, args
        self.daemon, self.name = False, ""
        Temporizador.creados.append(self)

    def start(self) -> None:
        pass


@pytest.fixture
def asistente(cfg, monkeypatch):
    Temporizador.creados = []
    monkeypatch.setattr(app.threading, "Timer", Temporizador)
    a = app.Asistente(cfg)
    a.parser = ParserDePregunta()
    a.dichas, a.escuchas = [], []
    a.decir = a.dichas.append
    a.stt = types.SimpleNamespace(esperar_respuesta=lambda: a.escuchas.append(1))
    a.consola = types.SimpleNamespace(agregar=lambda quien, texto: a.dichas.append((quien, texto)))
    a.modo = "voz"
    return a


def test_el_bus_ofrece_la_pregunta_a_los_plugins(asistente):
    assert asistente.bus.preguntar == asistente.preguntar_proactivo


def test_miku_pregunta_por_su_cuenta_la_dice_y_abre_el_microfono(asistente):
    assert asistente.preguntar_proactivo("¿Qué hacés viendo facebook? ¿La cierro?", "cerrar_pagina_vigilada", {"hwnd": 1}) is True
    assert asistente.parser.preguntas[0][:3] == ("cerrar_pagina_vigilada", {"hwnd": 1}, "¿Qué hacés viendo facebook? ¿La cierro?")
    assert "¿Qué hacés viendo facebook? ¿La cierro?" in asistente.dichas
    assert asistente.escuchas == [1], "el micrófono se abre para contestar sin decir Miku"
    assert len(Temporizador.creados) == 1, "y si no contestás, a los 20 s va al celular"


def test_en_modo_texto_no_abre_el_microfono_pero_si_manda_al_celular(asistente):
    asistente.modo = "texto"
    asistente.preguntar_proactivo("¿La cierro?", "cerrar_pagina_vigilada", {})
    assert asistente.escuchas == [] and len(Temporizador.creados) == 1
    assert ("Miku", "¿La cierro?") in asistente.dichas, "aparece en la ventana de texto"


def test_si_ya_habia_una_pregunta_no_la_dice(asistente):
    asistente.parser.acepta = False
    assert asistente.preguntar_proactivo("¿La cierro?", "cerrar_pagina_vigilada", {}) is False
    assert asistente.dichas == [] and asistente.escuchas == [] and Temporizador.creados == []


def test_el_no_se_pasa_al_parser(asistente):
    llamada = lambda: "bueno"  # noqa: E731
    asistente.preguntar_proactivo("¿La cierro?", "cerrar_pagina_vigilada", {}, llamada)
    assert asistente.parser.preguntas[0][3] is llamada


# ---------------------------------------------------------------- el diagnóstico
def test_el_diagnostico_muestra_que_ve_y_que_saltaria(cfg, monkeypatch):
    from miku.plataforma import procesos
    cfg.valores.update(habitos_vigilar=["facebook"], habitos_incognito=True)
    monkeypatch.setattr(config_mod, "cargar", lambda: cfg)
    monkeypatch.setattr(config_mod, "config", cfg)
    vistas = iter([ventana("Facebook - Brave"), ventana("Nueva pestaña - Brave (Private)"), ventana("GitHub - Brave"),
                   ventana("notas.txt - Notepad", "notepad"), None])
    monkeypatch.setattr(procesos, "ventana_primer_plano", lambda: next(vistas))
    monkeypatch.setattr(habitos.time, "sleep", lambda s: None)
    lineas: List[str] = []
    assert habitos.diagnostico(5, lineas.append) == 0
    texto = "\n".join(lineas)
    assert "Lista vigilada: facebook | incógnito: sí" in texto
    assert "SALTARÍA: facebook" in texto and "SALTARÍA: incógnito" in texto
    assert texto.count("no coincide") == 2 and "no pude leer" in texto
    assert "[notepad | no es navegador | normal]" in texto and "[brave | navegador | incógnito]" in texto


def test_el_hilo_del_plugin_se_detiene_al_cerrar(cfg, bus, tmp_path, monkeypatch):
    cfg.valores["habitos_vigilar"] = ["facebook"]
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(plugin_mod, "RUTA_REGISTRO", tmp_path / "habitos.json")
    p = Habitos()
    p.initialize(bus)
    assert p._hilo is not None and p._hilo.is_alive()
    p.cerrar()
    assert p._hilo is None
    assert not any(t.name == "miku_habitos" and t.is_alive() for t in threading.enumerate())
