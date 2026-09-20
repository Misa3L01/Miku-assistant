"""Juegos con lanzador, esperar ventanas de apps recién abiertas, listar ventanas reales y pestañas."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from miku.plugins.sistema import lanzadores
from miku.plugins.utiles import _AperturasRecientes
from miku.voz.frases.respuesta import hubo_falla

CFG = {"genshin": {"juego": r"D:\HoYoPlay\games\Genshin Impact game\GenshinImpact.exe",
                   "lanzador": r"D:\HoYoPlay\launcher.exe", "proceso": "HYP", "espera": "2"},
       "zenless": {"juego": r"D:\HoYoPlay\games\ZenlessZoneZero Game\ZenlessZoneZero.exe",
                   "lanzador": r"D:\HoYoPlay\launcher.exe"}}


def test_buscar_juego_por_parte_del_nombre():
    assert lanzadores.buscar_juego("Genshin Impact", CFG).alias == "genshin"
    assert lanzadores.buscar_juego("zenless zone zero", CFG).proceso_juego == "zenlesszonezero"
    assert lanzadores.buscar_juego("genshin", CFG).espera == 2.0
    assert lanzadores.buscar_juego("fortnite", CFG) is None
    assert lanzadores.buscar_juego("", CFG) is None
    assert lanzadores.buscar_juego("genshin", None) is None
    assert lanzadores.buscar_juego("x", {"x": "no es un dict"}) is None
    assert lanzadores.buscar_juego("x", {"x": {"lanzador": "sin juego"}}) is None


def test_procesos_del_juego():
    j = lanzadores.buscar_juego("zenless", CFG)
    assert j.proceso_lanzador == "launcher"            # sin "proceso": el del exe del lanzador
    assert lanzadores.buscar_juego("genshin", CFG).proceso_lanzador == "hyp"


class Escenario:
    """Simula procesos que aparecen con el tiempo."""

    def __init__(self, corriendo=()):
        self.vivos = set(corriendo)
        self.t = 0.0
        self.abiertos, self.juegos, self.avisos = [], [], []
        self.aparece_en = {}
        self.juego_ok = True

    def corriendo(self, nombre):
        if nombre in self.aparece_en and self.t >= self.aparece_en[nombre]:
            self.vivos.add(nombre)
        return nombre in self.vivos

    def dormir(self, s):
        self.t += s

    def ejecutar(self, juego, **kw):
        return lanzadores.iniciar(
            juego, lambda estado, ok: self.avisos.append((estado, ok)), corriendo=self.corriendo,
            abrir_lanzador=self.abiertos.append, abrir_juego=lambda r: self.juegos.append(r) or self.juego_ok,
            dormir=self.dormir, reloj=lambda: self.t, **kw)


def test_abre_el_lanzador_espera_y_despues_el_juego():
    e = Escenario()
    e.aparece_en["hyp"] = 25            # tarda en aparecer (el usuario acepta el UAC)
    j = lanzadores.buscar_juego("genshin", CFG)
    assert e.ejecutar(j) is True
    assert e.abiertos == [j.lanzador] and e.juegos == [j.juego]
    assert e.avisos == [("abriendo_juego", True)]
    assert e.t >= 25 + 2                # esperó al lanzador y el respiro


def test_si_el_lanzador_ya_esta_abierto_va_directo_al_juego():
    e = Escenario(corriendo=["hyp"])
    assert e.ejecutar(lanzadores.buscar_juego("genshin", CFG)) is True
    assert e.abiertos == [] and len(e.juegos) == 1


def test_si_el_juego_ya_corre_no_hace_nada():
    e = Escenario(corriendo=["genshinimpact"])
    assert e.ejecutar(lanzadores.buscar_juego("genshin", CFG)) is True
    assert e.abiertos == [] and e.juegos == [] and e.avisos == [("ya_abierto", True)]


def test_lanzador_que_nunca_aparece_avisa_y_no_abre_el_juego():
    e = Escenario()
    assert e.ejecutar(lanzadores.buscar_juego("genshin", CFG), espera_lanzador=30) is False
    assert e.juegos == [] and e.avisos == [("lanzador_no_aparece", False)]


def test_lanzador_que_no_abre_y_juego_que_no_abre():
    e = Escenario()

    def falla(_):
        raise OSError("no existe")

    j = lanzadores.buscar_juego("genshin", CFG)
    assert lanzadores.iniciar(j, lambda s, ok: e.avisos.append((s, ok)), corriendo=e.corriendo,
                              abrir_lanzador=falla, dormir=e.dormir, reloj=lambda: e.t) is False
    assert e.avisos == [("lanzador_no_abre", False)]
    e2 = Escenario(corriendo=["hyp"])
    e2.juego_ok = False
    assert e2.ejecutar(j) is False and e2.avisos == [("juego_no_abre", False)]


def test_abrir_programa_con_lanzador_responde_al_toque_y_avisa_despues(cfg, monkeypatch):
    from miku.ajustes import carga as config_mod
    from miku.plugins.sistema.programas import Programas

    cfg.valores["juegos_lanzador"] = CFG
    monkeypatch.setattr(config_mod, "config", cfg)
    lanzados = []
    monkeypatch.setattr(lanzadores, "iniciar_en_hilo", lambda j, avisar: lanzados.append((j, avisar)))
    p = Programas()
    dichas = []
    p.initialize(SimpleNamespace(voice=SimpleNamespace(decir=dichas.append)))
    r = p.abrir_programa("Genshin Impact")
    assert r.ok and r.intencion == "juego.lanzador_iniciado" and len(lanzados) == 1
    lanzados[0][1]("abriendo_juego", True)                 # el hilo avisa al terminar
    assert dichas and "genshin" in dichas[0].lower()


# ------------------------------------------------------------- orden en cadena
def test_aperturas_recientes():
    a = _AperturasRecientes()
    assert not a.reciente("discord")
    a.marcar("Discord")
    assert a.reciente("discord") and a.reciente("Discord app") and not a.reciente("brave")
    assert not a.reciente("discord", segundos=-1)
    assert not a.reciente("")


def test_una_ventana_de_algo_recien_abierto_se_espera(monkeypatch):
    from miku.plugins.sistema import ventanas as mod
    from miku.plugins.utiles import aperturas

    v = mod.Ventanas()
    llamadas = {"n": 0}

    def enumerar():
        llamadas["n"] += 1
        return [(1, "Discord")] if llamadas["n"] >= 4 else []

    monkeypatch.setattr(v, "_enumerar_ventanas", enumerar)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    aperturas.marcar("discord")
    assert v._hwnd_de("discord") == 1 and llamadas["n"] == 4


def test_una_ventana_que_no_se_abrio_recien_no_se_espera(monkeypatch):
    from miku.plugins.sistema import ventanas as mod

    v = mod.Ventanas()
    llamadas = {"n": 0}
    monkeypatch.setattr(v, "_enumerar_ventanas", lambda: llamadas.update(n=llamadas["n"] + 1) or [])
    monkeypatch.setattr(mod.time, "sleep", lambda s: pytest.fail("no debe esperar"))
    assert v._hwnd_de("app que nadie abrio xyz") is None and llamadas["n"] == 1


# ------------------------------------------------------- listar ventanas y pestañas
def test_nombres_hablables_de_ventanas():
    from miku.plugins.sistema.ventanas import _nombre_corto
    assert _nombre_corto("archivo.py - miku - Visual Studio Code") == "Visual Studio Code (archivo.py)"
    assert _nombre_corto("HoYoPlay") == "HoYoPlay"
    assert _nombre_corto("Brave - Brave") == "Brave"


def test_el_listado_de_ventanas_es_corto_y_natural(monkeypatch):
    from miku.plugins.sistema import ventanas as mod
    v = mod.Ventanas()
    monkeypatch.setattr(v, "_enumerar_ventanas", lambda: [(i, f"Doc {i} - App{i}") for i in range(12)])
    r = v.listar_ventanas_abiertas()
    assert r.ok and "y 4 más" in r and r.count(";") == 7
    monkeypatch.setattr(v, "_enumerar_ventanas", lambda: [])
    assert hubo_falla(v.listar_ventanas_abiertas())


class GuiFalso:
    def __init__(self, ventanas):
        self.v = ventanas

    def GetWindowText(self, h):
        return self.v[h]["titulo"]

    def GetWindowLong(self, h, i):
        return self.v[h].get("estilo", 0)

    def GetWindow(self, h, c):
        return self.v[h].get("dueno", 0)

    def GetWindowRect(self, h):
        return self.v[h].get("rect", (0, 0, 800, 600))

    def IsIconic(self, h):
        return self.v[h].get("minimizada", False)


def test_filtra_overlays_ventanas_de_herramientas_y_del_sistema(monkeypatch):
    from miku.plugins.sistema import ventanas as mod
    datos = {
        1: {"titulo": "Brave"},
        2: {"titulo": "NVIDIA GeForce Overlay", "estilo": 0x08000000 | 0x20},      # overlay transparente
        3: {"titulo": "Paleta", "estilo": 0x80},                                     # ventana de herramientas
        4: {"titulo": "Program Manager"},
        5: {"titulo": "Cuadro de dialogo", "dueno": 99},                             # pertenece a otra
        6: {"titulo": "Chiquita", "rect": (0, 0, 10, 10)},
        7: {"titulo": "Minimizada pero real", "rect": (-32000, -32000, -31900, -31900), "minimizada": True},
        8: {"titulo": "Oculta por Windows"},
    }
    v = mod.Ventanas()
    v._w = {"gui": GuiFalso(datos)}
    monkeypatch.setattr(mod, "_cloaked", lambda h: h == 8)
    reales = [h for h in datos if v._es_ventana_de_usuario(h)]
    assert reales == [1, 7]


def test_listar_pestanas_de_brave(monkeypatch):
    from miku.plugins.navegacion.brave import Browser
    b = Browser()
    monkeypatch.setattr(b, "_asegurar_cdp", lambda: True)
    monkeypatch.setattr(b, "_pestanas", lambda: [
        {"type": "page", "title": "YouTube", "url": "https://youtube.com"},
        {"type": "page", "title": "GitHub", "url": "https://github.com"},
        {"type": "page", "title": "DevTools", "url": "devtools://devtools/x"},
        {"type": "page", "title": "", "url": "https://ejemplo.com"}])
    r = b.manejar_tool("listar_pestanas", {}, {})
    assert r.ok and "3 pestañas" in r and "YouTube" in r and "DevTools" not in r
    monkeypatch.setattr(b, "_asegurar_cdp", lambda: False)
    assert b.listar_pestanas().intencion == "brave.sin_cdp"
