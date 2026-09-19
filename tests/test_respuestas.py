"""Tests de las respuestas estructuradas (``Respuesta``) y del catálogo de frases de las tools."""
from __future__ import annotations

import pytest

from miku.plugins.sistema.ventanas import Ventanas
from miku.voz.frases import catalogo_respuestas as cat
from miku.voz.frases.banco import Banco
from miku.voz.frases.respuesta import Respuesta, exito, falla, hubo_falla, responder


def test_respuesta_es_un_str_con_metadatos():
    r = falla("ventana.no_encontrada", app="Discord")
    assert isinstance(r, str) and "Discord" in r
    assert (r.ok, r.intencion, r.datos) == (False, "ventana.no_encontrada", {"app": "Discord"})
    assert r.startswith("No") or "Discord" in r        # sigue usándose como texto
    assert exito("audio.volumen", pct=30).ok


def test_hubo_falla_solo_mira_respuestas_fallidas():
    assert hubo_falla(falla("ventana.monitor_invalido"))
    assert not hubo_falla(exito("audio.volumen", pct=5))
    assert not hubo_falla("No encontré nada")      # un str común cuenta como éxito
    assert not hubo_falla(None)


def test_respuesta_sobrevive_a_json_y_a_slicing():
    import json
    r = exito("audio.volumen", pct=42)
    assert json.loads(json.dumps({"r": r}))["r"] == str(r)
    assert isinstance(r[:3], str)


def test_todas_las_frases_se_pueden_armar_sin_dejar_llaves():
    """Con cualquier variante y datos genéricos no queda ningún ``{campo}`` sin resolver."""
    b = Banco()
    cat.registrar(b)
    datos = {k: "x" for k in ("nombre", "app", "a", "b", "falta", "puesta", "izq", "der", "lado",
                              "lugar", "accion", "orientacion", "pct", "etiqueta", "monitor",
                              "descripcion", "carpeta", "consulta")}
    for clave, variantes in cat.CATALOGO.items():
        assert variantes, clave
        for _ in range(len(variantes) * 2):
            texto = b.elegir(clave, **datos)
            assert texto and "{" not in texto, (clave, texto)


def test_las_frases_de_no_encontrado_no_afirman_lo_que_no_saben():
    """Honestidad: 'no lo encuentro' no puede decir 'no está instalado' / 'no existe'."""
    assert cat.INTENCIONES_NO_ENCONTRADO      # el filtro no quedó vacío
    for clave in cat.INTENCIONES_NO_ENCONTRADO:
        for frase in cat.CATALOGO[clave]:
            bajo = frase.lower()
            assert not any(p in bajo for p in cat.AFIRMACIONES_PROHIBIDAS), (clave, frase)


def test_las_variantes_rotan_sin_repetir():
    vistas = [str(falla("audio.app_no_suena", app="x")) for _ in range(30)]
    assert all(a != b for a, b in zip(vistas, vistas[1:]))
    assert len(set(vistas)) == len(cat.CATALOGO["audio.app_no_suena"])


def test_intencion_inexistente_es_un_error_de_programacion():
    with pytest.raises(KeyError):
        responder("no.existe")


def test_registrar_es_idempotente_y_no_reinicia_la_rotacion():
    b = Banco()
    cat.registrar(b)
    b.elegir("ventana.monitor_invalido")
    ultima = dict(b._ultima)
    cat.registrar(b)
    assert b._ultima == ultima


# --------------------------------------------------------------------------- #
# Las tools ya no dependen del texto de otras tools
# --------------------------------------------------------------------------- #
class _VentanasSinWindows(Ventanas):
    """Ventanas con las llamadas a Windows reemplazadas: solo prueba la lógica de organizar."""

    def __init__(self, existentes):
        super().__init__()
        self._existentes = set(existentes)
        self.posicionadas = []

    def _hwnd_de(self, nombre_app):
        return 1 if nombre_app in self._existentes else None

    def posicionar_ventana(self, nombre_app, posicion, monitor=1):
        if nombre_app not in self._existentes:
            return falla("ventana.no_encontrada", app=nombre_app)
        self.posicionadas.append((nombre_app, posicion))
        return exito("ventana.posicionada", app=nombre_app, lugar=f"a la {posicion}")


def test_dividir_pantalla_con_una_sola_ventana_dice_cual_falto():
    v = _VentanasSinWindows({"chrome"})
    r = v.dividir_pantalla("chrome", "spotify")
    assert hubo_falla(r) and r.intencion == "ventana.organizada_parcial"
    assert "spotify" in r and "chrome" in r
    assert v.posicionadas == [("chrome", "izquierda")]


def test_dividir_pantalla_sin_ninguna_ventana():
    r = _VentanasSinWindows(set()).dividir_pantalla("a", "b")
    assert hubo_falla(r) and r.intencion == "ventana.ninguna_de_dos"


def test_dividir_pantalla_lado_derecho_falta():
    v = _VentanasSinWindows({"spotify"})
    r = v.dividir_pantalla("chrome", "spotify")
    assert r.intencion == "ventana.organizada_parcial" and r.datos["puesta"] == "spotify"
    assert r.datos["lado"] == "a la derecha"


def test_briefing_ignora_el_clima_fallido(monkeypatch):
    from miku.plugins.productividad import clima
    from miku.servicios import briefing

    monkeypatch.setattr(clima.Clima, "consultar_clima", lambda self, ciudad="": falla("clima.sin_datos"))
    assert briefing._clima_resumen() == ""
    monkeypatch.setattr(clima.Clima, "consultar_clima",
                        lambda self, ciudad="": "En Casa ahora está despejado con 20 grados. Llevate campera.")
    assert briefing._clima_resumen() == "En Casa ahora está despejado con 20 grados"
