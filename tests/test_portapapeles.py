"""Portapapeles inteligente: traducir/corregir/resumir lo copiado (el LLM y el portapapeles se simulan)."""
from __future__ import annotations

import pytest

from miku.ajustes import carga as config_mod
from miku.cerebro import complemento
from miku.plataforma import portapapeles
from miku.plugins.productividad import portapapeles_inteligente as mod
from miku.plugins.productividad.portapapeles_inteligente import PortapapelesInteligente
from miku.voz.frases.respuesta import hubo_falla


@pytest.fixture
def clip(cfg, monkeypatch):
    cfg.valores.update(groq_api_key="gsk_test", idioma_juego="")
    monkeypatch.setattr(config_mod, "config", cfg)
    estado = {"texto": "hola mundo, esto es una prueva", "tipo": "texto", "pedidos": [], "respuesta": "RESULTADO"}
    monkeypatch.setattr(portapapeles, "leer_texto", lambda: estado["texto"])
    monkeypatch.setattr(portapapeles, "tipo_de_contenido", lambda: estado["tipo"])

    def escribir(t):
        estado["texto"] = t
        return True

    monkeypatch.setattr(portapapeles, "escribir_texto", escribir)

    def pedir(cfg_, instruccion, texto, **kw):
        estado["pedidos"].append((instruccion, texto, kw))
        return estado["respuesta"]

    monkeypatch.setattr(complemento, "pedir_texto", pedir)
    p = PortapapelesInteligente()
    p.estado = estado
    return p


def test_traducir_reemplaza_el_portapapeles_y_se_puede_deshacer(clip):
    r = clip.manejar_tool("procesar_portapapeles", {"accion": "traducir", "idioma": "inglés"}, {})
    assert r.ok and r.intencion == "portapapeles.traducir" and "inglés" in r
    assert clip.estado["texto"] == "RESULTADO"
    instruccion, texto, _ = clip.estado["pedidos"][0]
    assert "inglés" in instruccion and texto == "hola mundo, esto es una prueva"
    assert clip.manejar_tool("deshacer_portapapeles", {}, {}).intencion == "portapapeles.deshecho"
    assert clip.estado["texto"] == "hola mundo, esto es una prueva"
    assert clip.deshacer().intencion == "portapapeles.nada_que_deshacer"


def test_traducir_usa_el_idioma_por_defecto_o_pregunta(clip, cfg):
    assert clip.procesar("traducir").intencion == "portapapeles.sin_idioma" and not clip.estado["pedidos"]
    cfg.valores["idioma_juego"] = "portugués"
    clip.procesar("traducir")
    assert "portugués" in clip.estado["pedidos"][0][0]


def test_corregir_y_reescribir(clip):
    assert clip.procesar("corregir").intencion == "portapapeles.corregir" and clip.estado["texto"] == "RESULTADO"
    clip.estado["texto"] = "hola"
    assert clip.procesar("reescribir").intencion == "portapapeles.sin_instruccion"
    r = clip.procesar("reescribir", instruccion="más formal")
    assert r.intencion == "portapapeles.reescribir" and "más formal" in clip.estado["pedidos"][-1][0]


def test_resumir_y_explicar_se_dicen_sin_tocar_el_portapapeles(clip):
    clip.estado["respuesta"] = "Es un saludo con una falta de ortografía."
    r = clip.procesar("resumir")
    assert r == "Es un saludo con una falta de ortografía." and clip.estado["texto"] == "hola mundo, esto es una prueva"
    r2 = clip.procesar("explicar", copiar=True)
    assert "Además lo dejé copiado" in r2 and clip.estado["texto"] == "Es un saludo con una falta de ortografía."
    assert clip.deshacer().ok and clip.estado["texto"] == "hola mundo, esto es una prueva"


def test_leer_dice_el_texto_recortado(clip):
    clip.estado["texto"] = "palabra " * 500
    r = clip.procesar("leer")
    assert r.ok and len(r) <= mod.MAX_HABLADO + 40 and not clip.estado["pedidos"]


def test_portapapeles_vacio_o_con_otra_cosa(clip):
    clip.estado["texto"] = None
    clip.estado["tipo"] = "vacio"
    assert clip.procesar("resumir").intencion == "portapapeles.vacio"
    clip.estado["tipo"] = "imagen"
    r = clip.procesar("resumir")
    assert r.intencion == "portapapeles.no_es_texto" and "imagen" in r
    clip.estado["texto"] = "   "
    assert hubo_falla(clip.procesar("resumir"))


def test_texto_muy_largo_se_recorta_y_se_avisa(clip):
    clip.estado["texto"] = "a" * (mod.MAX_CARACTERES + 500)
    r = clip.procesar("traducir", idioma="francés")
    assert len(clip.estado["pedidos"][0][1]) == mod.MAX_CARACTERES
    assert "muy largo" in r or r.intencion == "portapapeles.traducir"


def test_si_el_llm_falla_el_portapapeles_queda_intacto(clip):
    clip.estado["respuesta"] = None
    r = clip.procesar("corregir")
    assert r.intencion == "portapapeles.sin_respuesta" and clip.estado["texto"] == "hola mundo, esto es una prueva"
    assert clip.deshacer().intencion == "portapapeles.nada_que_deshacer"


def test_accion_desconocida(clip):
    assert clip.procesar("hackear").intencion == "portapapeles.accion_desconocida"


def test_los_argumentos_raros_del_llm_no_rompen(clip):
    assert clip.manejar_tool("procesar_portapapeles", {"accion": "resumir", "copiar": "si"}, {}).ok
    assert clip.estado["texto"] == "hola mundo, esto es una prueva"          # "si" no es bool: no copia


# --------------------------------------------------------------------------- #
# complemento (el pedido al LLM)
# --------------------------------------------------------------------------- #
def test_pedir_texto_usa_groq_y_limpia_el_pensamiento(cfg, monkeypatch):
    from miku.plataforma import red
    cfg.valores["groq_api_key"] = "gsk_x"
    visto = {}

    def post(url, headers=None, json=None, timeout=None):
        visto.update(url=url, headers=headers, json=json)
        from types import SimpleNamespace
        return SimpleNamespace(json=lambda: {"choices": [{"message": {"content": "<think>hmm</think> Hola."}}]})

    monkeypatch.setattr(red, "post", post)
    assert complemento.pedir_texto(cfg, "Traducí", "hola") == "Hola."
    assert visto["headers"]["Authorization"] == "Bearer gsk_x" and visto["json"]["messages"][1]["content"] == "hola"
    cfg.valores["llm_base_url"] = "http://localhost:11434/v1"
    complemento.pedir_texto(cfg, "x", "y")
    assert visto["url"].startswith("http://localhost:11434/v1") and "Authorization" not in visto["headers"]


def test_pedir_texto_sin_clave_o_con_error(cfg, monkeypatch):
    from miku.plataforma import red
    assert complemento.pedir_texto(cfg, "x", "y") is None                      # sin clave de Groq
    cfg.valores["groq_api_key"] = "k"
    monkeypatch.setattr(red, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("red")))
    assert complemento.pedir_texto(cfg, "x", "y") is None


def test_el_plugin_esta_en_el_catalogo(cfg):
    from miku.plugins import registro
    assert "portapapeles_inteligente" in {p.nombre for p in registro.instanciar_plugins(cfg)}


def test_el_traductor_usa_el_modulo_compartido(monkeypatch):
    from miku.plugins.gaming import traductor
    escritos = []
    monkeypatch.setattr(portapapeles, "escribir_texto", lambda t: escritos.append(t) or True)
    assert traductor._copiar_al_portapapeles("hola") is True and escritos == ["hola"]
