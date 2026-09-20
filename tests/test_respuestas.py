"""Tests de las respuestas estructuradas (``Respuesta``) y del catálogo de frases de las tools."""
from __future__ import annotations

from types import SimpleNamespace

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
                              "descripcion", "carpeta", "consulta", "idioma", "traduccion", "contenido", "recortado",
                              "dia", "detalle", "hora", "texto", "nivel", "de", "modo", "titulo", "archivo", "mb",
                              "contacto", "motivo", "cantidad", "mas", "nombres", "titulos", "listado",
                              "recuerdo", "total", "restaurado", "fallos", "con_titulo")}
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


# --------------------------------------------------------------------------- #
# Traductor: cualquier idioma, solo portapapeles
# --------------------------------------------------------------------------- #
@pytest.fixture
def traductor(monkeypatch, cfg):
    from miku.ajustes import carga as config_mod
    from miku.plugins.gaming import traductor as mod
    from miku.voz.salida import traduccion

    cfg.valores.update(idioma_juego="", mensajes_juego={"gg": "buena partida"}, groq_api_key="k")
    monkeypatch.setattr(config_mod, "config", cfg)
    pedidos, copiado = [], []
    monkeypatch.setattr(traduccion, "traducir_con_groq",
                        lambda texto, idioma, api_key="", **k: pedidos.append((texto, idioma))
                        or f"[{idioma}] {texto}")
    monkeypatch.setattr(mod, "_copiar_al_portapapeles", lambda t: copiado.append(t) or True)
    plugin = mod.TraductorJuegos()
    plugin.pedidos, plugin.copiado, plugin.cfg = pedidos, copiado, cfg
    return plugin


def test_traduce_texto_libre_a_cualquier_idioma_y_solo_copia(traductor):
    r = traductor.manejar_tool("traducir_mensaje_juego",
                               {"texto": "¿dónde está la biblioteca?", "idioma": "swahili"}, {})
    assert r.ok and r.intencion == "traductor.copiado"
    assert traductor.pedidos == [("¿dónde está la biblioteca?", "swahili")]
    assert traductor.copiado == ["[swahili] ¿dónde está la biblioteca?"]


def test_atajo_exacto_usa_la_frase_asociada(traductor):
    traductor.cfg.valores["idioma_juego"] = "inglés"
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "GG"}, {})
    assert traductor.pedidos == [("buena partida", "inglés")]


def test_un_atajo_parcial_no_se_expande(traductor):
    """'buena' no debe convertirse en 'buena partida': con texto libre traduce lo dicho."""
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "buena suerte", "idioma": "inglés"}, {})
    assert traductor.pedidos == [("buena suerte", "inglés")]


def test_el_idioma_pedido_gana_sobre_el_configurado(traductor):
    traductor.cfg.valores["idioma_juego"] = "inglés"
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "hola", "idioma": "japonés"}, {})
    assert traductor.pedidos == [("hola", "japonés")]


def test_sin_idioma_pregunta_en_vez_de_inventar(traductor):
    r = traductor.manejar_tool("traducir_mensaje_juego", {"texto": "hola"}, {})
    assert hubo_falla(r) and r.intencion == "traductor.sin_idioma"
    assert not traductor.pedidos and not traductor.copiado


def test_parametro_viejo_clave_sigue_andando(traductor):
    traductor.cfg.valores["idioma_juego"] = "inglés"
    traductor.manejar_tool("traducir_mensaje_juego", {"clave": "gg"}, {})
    assert traductor.pedidos == [("buena partida", "inglés")]


def test_fallos_de_traduccion_y_portapapeles(traductor, monkeypatch):
    from miku.plugins.gaming import traductor as mod
    from miku.voz.salida import traduccion

    monkeypatch.setattr(traduccion, "traducir_con_groq", lambda *a, **k: None)
    assert traductor.traducir_mensaje_juego("hola", "inglés").intencion == "traductor.error"
    monkeypatch.setattr(traduccion, "traducir_con_groq", lambda *a, **k: "hello")
    monkeypatch.setattr(mod, "_copiar_al_portapapeles", lambda t: False)
    r = traductor.traducir_mensaje_juego("hola otra vez", "inglés")
    assert r.intencion == "traductor.sin_portapapeles" and "hello" in r
    assert traductor.traducir_mensaje_juego("  ", "inglés").intencion == "traductor.sin_texto"


def test_la_traduccion_se_recuerda_con_tope(traductor):
    traductor.traducir_mensaje_juego("hola", "inglés")
    traductor.traducir_mensaje_juego("HOLA", "inglés")
    assert len(traductor.pedidos) == 1


# --------------------------------------------------------------------------- #
# TIDAL: "poné X" (búsqueda y sesión simuladas: nunca toca TIDAL ni la red)
# --------------------------------------------------------------------------- #
class _Elemento:
    def __init__(self, id_, name, artista=None):
        self.id, self.name = id_, name
        self.artist = type("A", (), {"name": artista})() if artista else None


class _SesionFalsa:
    def __init__(self, resultados=None, valida=True):
        self.resultados, self.valida, self.consultas = resultados or {}, valida, []

    def load_session_from_file(self, ruta):
        return True

    def check_login(self):
        return self.valida

    def search(self, consulta, models=None, limit=5):
        self.consultas.append((consulta, models))
        return self.resultados

    def save_session_to_file(self, ruta):
        ruta.write_text("{}", encoding="utf-8")


class _ControlFalso:
    """ControlTidal falso: registra qué se le pidió y devuelve lo que se configure."""

    def __init__(self):
        from miku.plugins.multimedia.tidal_control import LISTO
        self.estado, self.suena, self.pedidos, self.botones = LISTO, True, [], []
        self.aleatorios = []
        self.en_vivo = True
        self.ahora_datos = {"titulo": "Show", "artista": "Ado", "reproduciendo": True}

    def asegurar(self, espera=30.0):
        return self.estado

    def reproducir(self, tipo, id_, titulo="", artista="", aleatorio=None):
        self.pedidos.append((tipo, id_, titulo, artista))
        self.aleatorios.append(aleatorio)
        return self.suena

    def aleatorio(self, activar):
        self.aleatorios.append(("cambio", activar))
        return True

    def vivo(self):
        return self.en_vivo

    def boton(self, nombre):
        self.botones.append(nombre)
        return True

    def ahora(self):
        return self.ahora_datos


@pytest.fixture
def tidal(tmp_path, monkeypatch):
    from miku.plugins.multimedia import tidal as mod
    from miku.plugins.multimedia.tidal_busqueda import BuscadorTidal

    sesion = _SesionFalsa({"tracks": [_Elemento(77, "Bohemian Rhapsody", "Queen")],
                           "artists": [_Elemento(5, "Soda Stereo")]})
    ruta = tmp_path / "sesion.json"
    ruta.write_text("{}", encoding="utf-8")
    plugin = mod.Tidal()
    plugin._buscador = BuscadorTidal(ruta, fabrica_sesion=lambda: sesion)
    abiertos = []
    monkeypatch.setattr(mod.Tidal, "_abrir_enlace", staticmethod(lambda e: abiertos.append(e) or True))
    plugin._control = _ControlFalso()
    plugin.abiertos, plugin.sesion = abiertos, sesion
    return plugin


def test_pone_una_cancion_abriendo_el_enlace_de_tidal(tidal):
    r = tidal.manejar_tool("reproducir_en_tidal", {"consulta": "bohemian rhapsody queen"}, {})
    assert r.ok and r.intencion == "tidal.reproduciendo"
    assert "Bohemian Rhapsody" in r and "Queen" in r
    assert tidal._control.pedidos == [("track", "77", "Bohemian Rhapsody", "Queen")]
    assert tidal.abiertos == []            # con control de TIDAL no hace falta abrir el enlace
    assert tidal.sesion.consultas[0][0] == "bohemian rhapsody queen"


def test_pone_un_artista(tidal):
    r = tidal.reproducir_en_tidal("Soda Stereo", "banda")
    assert tidal._control.pedidos == [("artist", "5", "Soda Stereo", "")] and "Soda Stereo" in r


def test_sin_resultados_es_honesto(tidal):
    tidal.sesion.resultados = {"tracks": []}
    r = tidal.reproducir_en_tidal("asdfgh")
    assert hubo_falla(r) and r.intencion == "tidal.sin_resultados" and not tidal.abiertos


def test_sin_sesion_o_sin_consulta_explica_que_falta(tidal, tmp_path):
    from miku.plugins.multimedia.tidal_busqueda import BuscadorTidal
    assert tidal.reproducir_en_tidal("  ").intencion == "tidal.sin_consulta"
    tidal._buscador = BuscadorTidal(tmp_path / "no_existe.json", fabrica_sesion=lambda: _SesionFalsa())
    r = tidal.reproducir_en_tidal("algo")
    assert hubo_falla(r) and r.intencion in ("tidal.sin_sesion", "tidal.sin_libreria")
    assert not tidal.abiertos


def test_sesion_vencida_no_se_usa(tidal):
    tidal.buscador()._sesion = None
    tidal.sesion.valida = False
    assert tidal.buscador().sesion() is None


def test_tipos_de_busqueda():
    from miku.plugins.multimedia.tidal_busqueda import Resultado, normalizar_tipo
    assert normalizar_tipo("Canción") == "cancion" and normalizar_tipo("disco") == "album"
    assert normalizar_tipo("cualquier cosa") == "cancion"
    assert Resultado("playlist", "abc", "x").enlace == "tidal://playlist/abc"


def test_conectar_guarda_la_sesion_al_aprobar(tmp_path):
    import concurrent.futures
    import threading
    from miku.plugins.multimedia.tidal_busqueda import BuscadorTidal

    futuro = concurrent.futures.Future()
    sesion = _SesionFalsa()
    sesion.login_oauth = lambda: (type("L", (), {"verification_uri_complete": "link.tidal.com/AB12"})(),
                                  futuro)
    ruta = tmp_path / "data" / "sesion.json"
    buscador = BuscadorTidal(ruta, fabrica_sesion=lambda: sesion)
    urls, terminado, listo = [], [], threading.Event()
    url = buscador.conectar(lambda ok: (terminado.append(ok), listo.set()), urls.append)
    assert url == "https://link.tidal.com/AB12" and urls == [url]
    assert buscador.conectar(lambda ok: None, urls.append) is None      # ya hay una en curso
    futuro.set_result(True)
    assert listo.wait(3) and terminado == [True] and ruta.exists()
    assert buscador.sesion() is sesion


def test_perfil_del_juego_en_primer_plano_define_el_idioma(traductor, monkeypatch):
    from miku.plataforma import procesos
    traductor.cfg.valores.update(idioma_juego="inglés",
                                 perfiles_juego={"cs2": "portugués", "GenshinImpact.exe": "japonés"})
    monkeypatch.setattr(procesos, "proceso_primer_plano", lambda: "cs2")
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "corran"}, {})
    monkeypatch.setattr(procesos, "proceso_primer_plano", lambda: "genshinimpact")
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "vamos"}, {})
    monkeypatch.setattr(procesos, "proceso_primer_plano", lambda: "chrome")      # sin perfil: el por defecto
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "hola"}, {})
    # Un idioma dicho explícitamente le gana al perfil.
    monkeypatch.setattr(procesos, "proceso_primer_plano", lambda: "cs2")
    traductor.manejar_tool("traducir_mensaje_juego", {"texto": "chau", "idioma": "francés"}, {})
    assert [i for _, i in traductor.pedidos] == ["portugués", "japonés", "inglés", "francés"]


# --------------------------------------------------------------------------- #
# Macros: respuestas cortas
# --------------------------------------------------------------------------- #
def _macros(n):
    from miku.plugins.productividad.macros import Macros
    m = Macros()
    m._macros = {f"macro_{i}": {"descripcion": f"hace {i}", "comando": "x"} for i in range(n)}
    return m


def test_listar_macros_no_recita_todo():
    r = _macros(8).listar_macros()
    assert r.ok and "8" in r and "macro 0" in r and "macro 4" in r and "macro 5" not in r
    corto = _macros(3).listar_macros()
    assert "3" in corto and "macro 2" in corto
    assert hubo_falla(_macros(0).listar_macros())


def test_macro_desconocida_sugiere_pocas():
    r = _macros(8).ejecutar_macro("inexistente")
    assert hubo_falla(r) and r.intencion == "macros.desconocida" and "macro 6" not in r


# --------------------------------------------------------------------------- #
# Brave: cerrar pestaña por título y buscar en la pestaña actual
# --------------------------------------------------------------------------- #
@pytest.fixture
def brave(monkeypatch):
    from miku.plugins.navegacion.brave import Browser
    b = Browser()
    b.cerradas, b.navegadas, b.nuevas = [], [], []
    b.tabs = [
        {"id": "1", "type": "page", "title": "YouTube - gatos", "url": "https://youtube.com/watch?v=1",
         "webSocketDebuggerUrl": "ws://x/1"},
        {"id": "2", "type": "page", "title": "GitHub", "url": "https://github.com/miku"},
        {"id": "3", "type": "page", "title": "Docs de Python", "url": "https://docs.python.org"},
        {"id": "4", "type": "page", "title": "Python Tutorial", "url": "https://python.org/tut"},
    ]
    monkeypatch.setattr(b, "_asegurar_cdp", lambda: True)
    monkeypatch.setattr(b, "_pestanas", lambda: b.tabs)
    monkeypatch.setattr(b, "_cerrar_por_id", lambda pid: b.cerradas.append(pid) or True)
    monkeypatch.setattr(b, "_navegar", lambda tab, url: b.navegadas.append((tab["id"], url)) or True)
    monkeypatch.setattr(b, "_nueva_pestana", lambda url: b.nuevas.append(url) or "id")
    return b


def test_cerrar_pestana_actual_sin_titulo(brave):
    r = brave.manejar_tool("cerrar_pestana", {}, {})
    assert r.ok and brave.cerradas == ["1"]


def test_cerrar_pestana_por_titulo_o_sitio(brave):
    assert brave.cerrar_pestana("github").ok and brave.cerradas == ["2"]
    assert brave.cerrar_pestana("YouTube").ok and brave.cerradas == ["2", "1"]


def test_cerrar_por_titulo_ambiguo_no_cierra_nada(brave):
    r = brave.cerrar_pestana("python")
    assert hubo_falla(r) and r.intencion == "brave.varias_pestanas" and brave.cerradas == []
    assert "Docs de Python" in r and "Python Tutorial" in r


def test_cerrar_por_titulo_inexistente(brave):
    r = brave.cerrar_pestana("netflix")
    assert r.intencion == "brave.pestana_no_encontrada" and not brave.cerradas


def test_cerrar_sin_pestanas_o_sin_cdp(brave, monkeypatch):
    monkeypatch.setattr(brave, "_pestanas", lambda: [])
    assert brave.cerrar_pestana().intencion == "brave.sin_pestanas"
    monkeypatch.setattr(brave, "_asegurar_cdp", lambda: False)
    assert brave.cerrar_pestana().intencion == "brave.sin_cdp"


def test_buscar_en_la_pestana_actual_navega_esa_pestana(brave):
    r = brave.buscar_en_pestana_actual("recetas de pizza")
    assert r.intencion == "brave.buscado_en_actual" and not brave.nuevas
    assert brave.navegadas[0][0] == "1" and "recetas+de+pizza" in brave.navegadas[0][1]


def test_buscar_sin_poder_navegar_abre_nueva_y_lo_dice(brave, monkeypatch):
    monkeypatch.setattr(brave, "_navegar", lambda tab, url: False)
    r = brave.buscar_en_pestana_actual("recetas")
    assert r.intencion == "brave.buscado_en_nueva" and len(brave.nuevas) == 1
    assert brave.buscar_en_pestana_actual("  ").intencion == "brave.buscar_sin_consulta"


def test_si_tidal_no_reproduce_lo_dice_y_no_finge(tidal):
    tidal._control.suena = False
    r = tidal.reproducir_en_tidal("bohemian rhapsody")
    assert hubo_falla(r) and r.intencion == "tidal.no_reprodujo"


def test_sin_ruta_de_tidal_abre_la_ficha_pero_avisa_que_no_reprodujo(tidal):
    from miku.plugins.multimedia.tidal_control import SIN_EXE
    tidal._control.estado = SIN_EXE
    r = tidal.reproducir_en_tidal("bohemian rhapsody")
    assert r.intencion == "tidal.sin_control" and tidal.abiertos == ["tidal://track/77"]


def test_controles_y_que_suena_usan_tidal_si_esta_disponible(tidal):
    tidal.controlar_tidal("pausa")
    assert tidal._control.botones == ["play_pausa"]
    r = tidal.que_esta_sonando()
    assert "Show" in r and "Ado" in r and "sonando" in r
    tidal._control.ahora_datos = {"titulo": "Show", "artista": "Ado", "reproduciendo": False}
    assert "pausa" in tidal.que_esta_sonando()


def test_un_artista_queda_en_aleatorio_por_defecto(tidal):
    r = tidal.reproducir_en_tidal("Soda Stereo", "artista")
    assert tidal._control.aleatorios == [True] and "aleatorio" in r
    tidal._control.aleatorios.clear()
    tidal.reproducir_en_tidal("Soda Stereo", "artista", aleatorio=False)
    assert tidal._control.aleatorios == [False]


def test_un_tema_solo_es_aleatorio_si_se_pide(tidal):
    tidal.manejar_tool("reproducir_en_tidal", {"consulta": "bohemian"}, {})
    tidal.manejar_tool("reproducir_en_tidal", {"consulta": "bohemian", "aleatorio": True}, {})
    tidal.manejar_tool("reproducir_en_tidal", {"consulta": "bohemian", "aleatorio": "si"}, {})   # no es bool: se ignora
    assert tidal._control.aleatorios == [None, True, None]


def test_controlar_aleatorio(tidal):
    assert tidal.controlar_tidal("aleatorio_on").intencion == "tidal.aleatorio_on"
    assert tidal.controlar_tidal("aleatorio_off").intencion == "tidal.aleatorio_off"
    assert tidal._control.aleatorios == [("cambio", True), ("cambio", False)]
    tidal._control.en_vivo = False
    assert tidal.controlar_tidal("aleatorio_on").intencion == "tidal.aleatorio_error"


def test_elegir_playlist_propia_por_nombre():
    from miku.plugins.multimedia.tidal_busqueda import elegir_playlist
    listas = [("1", "Anime"), ("2", "Rock Nacional"), ("3", "Gym")]
    assert elegir_playlist("mi playlist de animes", listas).id == "1"
    assert elegir_playlist("reproducí mi lista de rock nacional en aleatorio", listas).titulo == "Rock Nacional"
    assert elegir_playlist("rock", listas) is None              # falta 'nacional': no es esa
    assert elegir_playlist("música para dormir", listas) is None
    especifica = listas + [("4", "Anime Opening")]
    assert elegir_playlist("mi playlist de anime opening", especifica).id == "4"
    assert elegir_playlist("mi playlist de anime", especifica).id == "1"


def test_buscar_playlist_mira_primero_las_propias():
    from miku.plugins.multimedia.tidal_busqueda import BuscadorTidal
    sesion = _SesionFalsa({"playlists": [_Elemento("pub", "Anime Hits")]})
    sesion.user = SimpleNamespace(playlists=lambda: [_Elemento("mia", "Anime")],
                                  favorites=SimpleNamespace(playlists=lambda: []))
    import tempfile, pathlib
    ruta = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    ruta.write_text("{}", encoding="utf-8")
    b = BuscadorTidal(ruta, fabrica_sesion=lambda: sesion)
    assert b.buscar("mi playlist de animes", "playlist").id == "mia"
    assert not sesion.consultas                                    # ni siquiera buscó en el catálogo
    assert b.buscar("hits de moda", "playlist").id == "pub"        # no es suya: cae a la búsqueda pública
