"""Ideas guardadas: fechas en español, almacén, última captura, plugin, recordatorios y Telegram (todo simulado)."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional

import pytest

from miku.ajustes import carga as config_mod
from miku.plataforma import red
from miku.plugins import registro
from miku.plugins.productividad import ideas as plugin_mod
from miku.plugins.productividad.ideas import Ideas
from miku.plugins.social.telegram_bot import TelegramControl
from miku.servicios import ideas
from miku.servicios.ideas import AlmacenIdeas
from miku.voz.frases import catalogo_ideas
from miku.voz.frases.banco import Banco

MIERCOLES = datetime(2026, 10, 7, 20, 0)                # un miércoles a las 20:00


def cuando(texto: str, ahora: datetime = MIERCOLES, **kw: Any):
    return ideas.parsear_cuando(texto, ahora, **kw)


# --------------------------------------------------------------------------- #
# "¿Cuándo te lo recuerdo?"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("texto,esperado", [
    ("mañana", datetime(2026, 10, 8, 18, 0)),
    ("MAÑANA", datetime(2026, 10, 8, 18, 0)),
    ("pasado mañana", datetime(2026, 10, 9, 18, 0)),
    ("el sábado", datetime(2026, 10, 10, 11, 0)),
    ("este finde", datetime(2026, 10, 10, 11, 0)),
    ("el fin de semana", datetime(2026, 10, 10, 11, 0)),
    ("el domingo", datetime(2026, 10, 11, 11, 0)),
    ("el viernes", datetime(2026, 10, 9, 18, 0)),
    ("el miércoles", datetime(2026, 10, 14, 18, 0)),            # hoy es miércoles: el que viene
    ("en una semana", datetime(2026, 10, 14, 18, 0)),
    ("en 3 días", datetime(2026, 10, 10, 18, 0)),
    ("en dos horas", datetime(2026, 10, 7, 22, 0)),
    ("en 30 minutos", datetime(2026, 10, 7, 20, 30)),
    ("en media hora", datetime(2026, 10, 7, 20, 30)),
    ("más tarde", datetime(2026, 10, 7, 22, 0)),
    ("la semana que viene", datetime(2026, 10, 14, 18, 0)),
    ("la próxima semana", datetime(2026, 10, 14, 18, 0)),
    ("esta noche", datetime(2026, 10, 7, 21, 0)),
    ("mañana a las 9", datetime(2026, 10, 8, 9, 0)),
    ("mañana a las 21:30", datetime(2026, 10, 8, 21, 30)),
    ("mañana a la mañana", datetime(2026, 10, 8, 9, 0)),
    ("mañana a la noche", datetime(2026, 10, 8, 21, 0)),
    ("el sábado a las 15", datetime(2026, 10, 10, 15, 0)),
    ("a las 22", datetime(2026, 10, 7, 22, 0)),
    ("buenísima idea, recordámela el sábado", datetime(2026, 10, 10, 11, 0)),
])
def test_entiende_el_momento_en_español(texto, esperado):
    assert cuando(texto) == (True, esperado)


def test_una_hora_que_ya_paso_se_entiende_para_mañana():
    assert cuando("a las 9") == (True, datetime(2026, 10, 8, 9, 0)), "a las 20, 'a las 9' es mañana"


def test_hoy_con_la_hora_de_aviso_ya_pasada_es_en_una_hora():
    assert cuando("hoy") == (True, datetime(2026, 10, 7, 21, 0))
    assert cuando("esta noche", datetime(2026, 10, 7, 22, 0)) == (True, datetime(2026, 10, 7, 23, 0))


@pytest.mark.parametrize("texto", ["sin recordatorio", "no me lo recuerdes", "no me recuerdes más", "nunca", "no",
                                   "no quiero recordatorio"])
def test_decir_que_no_quiere_recordatorio(texto):
    assert cuando(texto) == (True, None)


@pytest.mark.parametrize("texto", ["", "   ", "buenísima idea para el proyecto", "nunca había visto algo así",
                                   "esto está genial", "en tres perros"])
def test_si_no_hay_un_momento_no_inventa_uno(texto):
    assert cuando(texto) == (False, None)


def test_el_sabado_a_la_mañana_es_hoy_y_a_la_tarde_el_proximo():
    sabado = datetime(2026, 10, 10, 9, 0)
    assert cuando("el finde", sabado) == (True, datetime(2026, 10, 10, 11, 0))
    assert cuando("el finde", datetime(2026, 10, 10, 12, 0)) == (True, datetime(2026, 10, 17, 11, 0))
    assert cuando("el sábado", datetime(2026, 10, 10, 9, 0)) == (True, datetime(2026, 10, 17, 11, 0)), \
        "'el sábado' dicho un sábado es el que viene"


def test_las_horas_de_aviso_se_configuran():
    assert cuando("mañana", hora_aviso="09:30") == (True, datetime(2026, 10, 8, 9, 30))
    assert cuando("el sábado", hora_finde="14:00") == (True, datetime(2026, 10, 10, 14, 0))
    assert cuando("mañana", hora_aviso="raro") == (True, datetime(2026, 10, 8, 18, 0)), "una hora mal escrita no rompe"


@pytest.mark.parametrize("texto", ["mañana", "el sábado", "en una semana", "esta noche", "más tarde", "a las 3"])
def test_lo_que_entiende_siempre_es_en_el_futuro(texto):
    reconocido, fecha = cuando(texto)
    assert reconocido and fecha > MIERCOLES


@pytest.mark.parametrize("fecha,esperado", [
    (datetime(2026, 10, 7, 21, 0), "hoy a las 21"),
    (datetime(2026, 10, 8, 18, 0), "mañana a las 18"),
    (datetime(2026, 10, 10, 11, 0), "el sábado a las 11"),
    (datetime(2026, 10, 9, 9, 30), "el viernes a las 9:30"),
    (datetime(2026, 10, 20, 18, 0), "el 20/10 a las 18"),
    (None, "sin recordatorio"),
])
def test_el_momento_para_decirlo_en_voz_alta(fecha, esperado):
    assert ideas.cuando_en_texto(fecha, MIERCOLES) == esperado


# --------------------------------------------------------------------------- #
# El almacén
# --------------------------------------------------------------------------- #
@pytest.fixture
def almacen(tmp_path):
    reloj = {"t": MIERCOLES}
    a = AlmacenIdeas(tmp_path / "ideas.db", tmp_path / "ideas", ahora=lambda: reloj["t"])
    a.reloj = reloj
    yield a
    a.cerrar()


def test_guardar_y_leer_una_idea(almacen):
    idea = almacen.guardar("Un bot de Discord", "Hacer un bot que traduzca.", "del foro", b"\xff\xd8JPEG",
                           MIERCOLES + timedelta(days=1), "telegram")
    assert idea.id == 1 and idea.titulo == "Un bot de Discord" and idea.estado == "pendiente"
    assert idea.recordar_en == MIERCOLES + timedelta(days=1) and idea.avisada is False and idea.origen == "telegram"
    assert Path(idea.imagen).read_bytes() == b"\xff\xd8JPEG" and Path(idea.imagen).name == "1.jpg"
    assert almacen.obtener(1) == idea and almacen.obtener(99) is None


def test_los_ids_son_distintos_y_la_ultima_es_la_mas_nueva(almacen):
    a, b = almacen.guardar("A"), almacen.guardar("B")
    assert (a.id, b.id) == (1, 2) and almacen.ultima().id == 2
    almacen.cambiar_estado(2, "hecha")
    assert almacen.ultima().id == 1, "la última pendiente"


def test_una_idea_sin_imagen_ni_recordatorio(almacen):
    idea = almacen.guardar("Solo texto")
    assert idea.imagen == "" and idea.recordar_en is None


def test_pendientes_y_estados(almacen):
    for t in ("A", "B", "C"):
        almacen.guardar(t)
    almacen.cambiar_estado(1, "hecha")
    almacen.cambiar_estado(3, "descartada")
    assert [i.titulo for i in almacen.pendientes()] == ["B"]
    almacen.cambiar_estado(1, "pendiente")
    assert [i.titulo for i in almacen.pendientes()] == ["A", "B"]
    assert almacen.cambiar_estado(99, "hecha") is False
    with pytest.raises(ValueError):
        almacen.cambiar_estado(1, "rara")


def test_las_vencidas_son_las_pendientes_con_la_hora_cumplida_y_sin_avisar(almacen):
    pasada = almacen.guardar("pasada", recordar_en=MIERCOLES - timedelta(hours=1))
    almacen.guardar("futura", recordar_en=MIERCOLES + timedelta(hours=1))
    almacen.guardar("sin aviso")
    cerrada = almacen.guardar("cerrada", recordar_en=MIERCOLES - timedelta(hours=2))
    almacen.cambiar_estado(cerrada.id, "descartada")
    assert [i.id for i in almacen.vencidas(MIERCOLES)] == [pasada.id]
    almacen.marcar_avisada(pasada.id)
    assert almacen.vencidas(MIERCOLES) == []


def test_cambiar_el_recordatorio_vuelve_a_dejarla_sin_avisar(almacen):
    idea = almacen.guardar("A", recordar_en=MIERCOLES - timedelta(hours=1))
    almacen.marcar_avisada(idea.id)
    assert almacen.recordar(idea.id, MIERCOLES - timedelta(minutes=1)) is True
    assert [i.id for i in almacen.vencidas(MIERCOLES)] == [idea.id]
    almacen.recordar(idea.id, None)
    assert almacen.obtener(idea.id).recordar_en is None and almacen.vencidas(MIERCOLES) == []
    assert almacen.recordar(99, None) is False


def test_lo_guardado_sobrevive_al_reinicio(tmp_path):
    a = AlmacenIdeas(tmp_path / "ideas.db", tmp_path / "ideas")
    a.guardar("Persistente", "resumen", recordar_en=MIERCOLES)
    a.cerrar()
    otra = AlmacenIdeas(tmp_path / "ideas.db", tmp_path / "ideas")
    assert [i.titulo for i in otra.pendientes()] == ["Persistente"]
    otra.cerrar()


def test_si_no_se_puede_guardar_la_imagen_la_idea_igual_se_guarda(tmp_path):
    bloqueo = tmp_path / "ideas"
    bloqueo.write_text("soy un archivo, no una carpeta")             # mkdir de la carpeta de imágenes falla
    a = AlmacenIdeas(None, bloqueo)
    idea = a.guardar("Sin imagen", imagen_jpeg=b"x")
    assert idea.imagen == "" and idea.titulo == "Sin imagen"
    a.cerrar()


# --------------------------------------------------------------------------- #
# Imágenes
# --------------------------------------------------------------------------- #
def _png(ancho: int, alto: int, modo: str = "RGBA") -> bytes:
    from PIL import Image
    salida = io.BytesIO()
    Image.new(modo, (ancho, alto), (200, 30, 30, 255) if modo == "RGBA" else (200, 30, 30)).save(salida, "PNG")
    return salida.getvalue()


def test_las_imagenes_grandes_se_achican_y_salen_en_jpeg():
    from PIL import Image
    jpeg = ideas.comprimir_imagen(_png(3000, 1500))
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and max(img.size) == ideas.MAX_LADO and img.size == (1280, 640)


def test_una_imagen_chica_no_se_agranda_y_con_transparencia_igual_sirve():
    from PIL import Image
    img = Image.open(io.BytesIO(ideas.comprimir_imagen(_png(200, 100, "RGBA"))))
    assert img.size == (200, 100) and img.mode == "RGB"


@pytest.mark.parametrize("basura", [b"", b"esto no es una imagen", b"\x89PNG\r\n\x1a\nroto"])
def test_lo_que_no_es_una_imagen_devuelve_none(basura):
    assert ideas.comprimir_imagen(basura) is None


# --------------------------------------------------------------------------- #
# "La última captura"
# --------------------------------------------------------------------------- #
def _archivo(carpeta: Path, nombre: str, hace_min: float, ahora: float, contenido: bytes = b"img") -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    p = carpeta / nombre
    p.write_bytes(contenido)
    os.utime(p, (ahora - hace_min * 60, ahora - hace_min * 60))
    return p


AHORA = 1_800_000_000.0


def test_un_archivo_reciente_es_la_ultima_captura(tmp_path):
    _archivo(tmp_path / "a", "Captura 1.png", 3, AHORA, b"reciente")
    c = ideas.buscar_ultima_captura([tmp_path / "a"], ahora=lambda: AHORA)
    assert c.datos == b"reciente" and c.minutos == pytest.approx(3) and "Captura 1.png" in c.origen


def test_gana_el_archivo_mas_nuevo_de_todas_las_carpetas(tmp_path):
    _archivo(tmp_path / "a", "vieja.png", 10, AHORA, b"vieja")
    _archivo(tmp_path / "b", "nueva.jpg", 2, AHORA, b"nueva")
    assert ideas.buscar_ultima_captura([tmp_path / "a", tmp_path / "b"], ahora=lambda: AHORA).datos == b"nueva"


def test_si_el_archivo_es_viejo_se_prefiere_lo_copiado_en_el_portapapeles(tmp_path):
    from PIL import Image
    _archivo(tmp_path, "ayer.png", 600, AHORA)
    copiada = Image.new("RGB", (50, 50), (0, 255, 0))
    c = ideas.buscar_ultima_captura([tmp_path], lambda: copiada, ahora=lambda: AHORA)
    assert c.minutos is None and "copiada" in c.origen and c.datos[:2] == b"\xff\xd8"


def test_sin_archivos_se_usa_el_portapapeles(tmp_path):
    from PIL import Image
    c = ideas.buscar_ultima_captura([tmp_path / "no_existe"], lambda: Image.new("RGB", (10, 10)), ahora=lambda: AHORA)
    assert c is not None and "copiada" in c.origen


def test_un_archivo_viejo_sin_portapapeles_se_usa_diciendo_de_hace_cuanto(tmp_path):
    _archivo(tmp_path, "ayer.png", 600, AHORA, b"vieja")
    c = ideas.buscar_ultima_captura([tmp_path], ahora=lambda: AHORA)
    assert c.datos == b"vieja" and c.minutos == pytest.approx(600)


def test_sin_nada_no_hay_captura(tmp_path):
    assert ideas.buscar_ultima_captura([tmp_path], ahora=lambda: AHORA) is None
    assert ideas.buscar_ultima_captura([], ahora=lambda: AHORA) is None


def test_solo_cuentan_las_imagenes(tmp_path):
    _archivo(tmp_path, "notas.txt", 1, AHORA)
    _archivo(tmp_path, ".oculta.png", 1, AHORA)
    _archivo(tmp_path, "descarga.crdownload", 1, AHORA)
    assert ideas.buscar_ultima_captura([tmp_path], ahora=lambda: AHORA) is None


def test_un_portapapeles_con_algo_roto_no_rompe(tmp_path):
    assert ideas.buscar_ultima_captura([tmp_path], lambda: object(), ahora=lambda: AHORA) is None


# --------------------------------------------------------------------------- #
# Lo que contesta la visión
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("respuesta,titulo,resumen", [
    ("TITULO: Bot de Discord | RESUMEN: Hacer un bot que traduzca mensajes.", "Bot de Discord", "Hacer un bot que traduzca mensajes."),
    ("TITULO: Bot\nRESUMEN: Hacerlo esta semana.", "Bot", "Hacerlo esta semana."),
    ("**TITULO:** Bot | **RESUMEN:** Algo.", "Bot", "Algo."),
    ("titulo: minúsculas | resumen: igual", "minúsculas", "igual"),
    ("Una idea de proyecto.\nHabría que armarlo con Python.", "Una idea de proyecto.", "Habría que armarlo con Python."),
    ("Solo una frase. Y otra más.", "Solo una frase.", "Y otra más."),
])
def test_separa_titulo_y_resumen(respuesta, titulo, resumen):
    assert ideas.separar_titulo_resumen(respuesta) == (titulo, resumen)


@pytest.mark.parametrize("vacio", ["", None, "   ", "***"])
def test_una_respuesta_vacia_no_da_titulo(vacio):
    assert ideas.separar_titulo_resumen(vacio) == ("", "")


def test_un_titulo_larguisimo_se_recorta():
    t, r = ideas.separar_titulo_resumen("TITULO: " + "palabra " * 50 + "| RESUMEN: " + "x" * 900)
    assert len(t) <= 90 and len(r) <= 400


# --------------------------------------------------------------------------- #
# La ruleta de frases
# --------------------------------------------------------------------------- #
def test_el_catalogo_de_ideas_es_coherente():
    assert set(catalogo_ideas.CATALOGO) == set(catalogo_ideas.TITULOS)
    b = Banco(random.Random(0))
    catalogo_ideas.registrar(b)
    for clave, variantes in catalogo_ideas.CATALOGO.items():
        assert len(variantes) >= 2, clave
        for _ in range(len(variantes) * 2):
            texto = b.elegir(clave, titulo="El proyecto", cuando="mañana a las 18")
            assert "{" not in texto and texto.strip(), clave
    assert len(catalogo_ideas.CATALOGO["idea.recordatorio"]) >= 5 and len(catalogo_ideas.CATALOGO["idea.guardada"]) >= 5


# --------------------------------------------------------------------------- #
# El plugin
# --------------------------------------------------------------------------- #
class VisionFalsa:
    nombre = "vision"

    def __init__(self) -> None:
        self.respuesta: Optional[str] = "TITULO: Bot traductor | RESUMEN: Un bot que traduce mensajes de Discord."
        self.pedidos: List[tuple] = []

    def analizar_imagen(self, img_b64: str, pregunta: str):
        self.pedidos.append((img_b64, pregunta))
        return (self.respuesta, "") if self.respuesta else (None, "Gemini no responde.")


class TelegramFalso:
    nombre = "telegram_control"

    def __init__(self) -> None:
        self.enviados: List[tuple] = []
        self.falla = False

    def enviar(self, texto, foto=None, botones=None):
        if self.falla:
            raise OSError("sin red")
        self.enviados.append((texto, foto, botones))
        return True


class VozFalsa:
    def __init__(self) -> None:
        self.dichas: List[str] = []

    def decir(self, texto: str) -> None:
        self.dichas.append(texto)


@pytest.fixture
def mundo(cfg, tmp_path, monkeypatch):
    cfg.valores.update(proactivo_silencio_desde="23:00", proactivo_silencio_hasta="08:00",
                       carpeta_capturas=str(tmp_path / "capturas"))
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(plugin_mod, "RUTA_DB", tmp_path / "ideas.db")
    monkeypatch.setattr(plugin_mod, "CARPETA_IMAGENES", tmp_path / "ideas")
    monkeypatch.setattr(Ideas, "_bucle", lambda self: None)                    # sin hilo de verdad
    toasts: List[tuple] = []
    from miku.servicios import notificaciones
    monkeypatch.setattr(notificaciones, "notificar", lambda t, m: toasts.append((t, m)))
    # que "la última captura" sea siempre una carpeta de la prueba (no la de Windows de quien corre los tests)
    monkeypatch.setattr(Ideas, "_carpetas_de_capturas", staticmethod(lambda: [tmp_path / "capturas"]))
    monkeypatch.setattr(plugin_mod.portapapeles, "leer_imagen", lambda: None)
    bus = types.SimpleNamespace(plugins=[], voice=VozFalsa())
    m = types.SimpleNamespace(vision=VisionFalsa(), telegram=TelegramFalso(), bus=bus, toasts=toasts, tmp=tmp_path)
    bus.plugins = [m.vision, m.telegram]
    p = Ideas()
    p.initialize(bus)
    p._hilo.join(1)
    p.reloj_actual = {"t": MIERCOLES}
    p._reloj = lambda: p.reloj_actual["t"]
    m.plugin = p
    yield m
    p.cerrar()


def test_guardar_una_imagen_le_pone_titulo_resumen_y_recordatorio_para_mañana(mundo):
    idea, texto = mundo.plugin.guardar_desde_imagen(_png(400, 300), "del foro de Facebook", "telegram")
    assert idea.titulo == "Bot traductor" and idea.resumen.startswith("Un bot que traduce")
    assert idea.nota == "del foro de Facebook" and idea.origen == "telegram"
    assert idea.recordar_en == datetime(2026, 10, 8, 18, 0), "sin decir cuándo: mañana a las 18"
    assert "Bot traductor" in texto and "mañana a las 18" in texto
    assert Path(idea.imagen).is_file()


def test_la_vision_recibe_la_imagen_y_la_nota(mundo):
    mundo.plugin.guardar_desde_imagen(_png(400, 300), "para mi proyecto", "telegram")
    b64, pedido = mundo.vision.pedidos[0]
    assert len(b64) > 50 and "para mi proyecto" in pedido and "TITULO" in pedido
    mundo.plugin.guardar_desde_imagen(_png(400, 300), "", "telegram")
    assert "nota" not in mundo.vision.pedidos[1][1]


def test_el_pie_de_la_foto_puede_decir_cuando(mundo):
    idea, texto = mundo.plugin.guardar_desde_imagen(_png(400, 300), "buenísima idea, recordámela el sábado", "telegram")
    assert idea.recordar_en == datetime(2026, 10, 10, 11, 0) and "el sábado a las 11" in texto


def test_lo_que_dijiste_en_voz_gana_sobre_el_pie(mundo):
    idea, _ = mundo.plugin.guardar_desde_imagen(_png(400, 300), "recordámela el sábado", "captura", "en una semana")
    assert idea.recordar_en == datetime(2026, 10, 14, 18, 0)


def test_se_puede_guardar_sin_recordatorio(mundo):
    idea, texto = mundo.plugin.guardar_desde_imagen(_png(400, 300), "", "captura", "sin recordatorio")
    assert idea.recordar_en is None and "Bot traductor" in texto and "mañana" not in texto


def test_las_horas_de_aviso_son_las_de_tu_config(mundo, cfg):
    cfg.valores.update(ideas_hora_aviso="09:15", ideas_hora_finde="14:00")
    idea, _ = mundo.plugin.guardar_desde_imagen(_png(400, 300), "", "captura")
    assert idea.recordar_en == datetime(2026, 10, 8, 9, 15)
    idea, _ = mundo.plugin.guardar_desde_imagen(_png(400, 300), "", "captura", "el sábado")
    assert idea.recordar_en == datetime(2026, 10, 10, 14, 0)


def test_sin_visionla_idea_se_guarda_igual_con_la_nota_de_titulo(mundo):
    mundo.vision.respuesta = None
    idea, texto = mundo.plugin.guardar_desde_imagen(_png(400, 300), "Revisar el foro de bots", "telegram")
    assert idea.titulo == "Revisar el foro de bots" and idea.resumen == ""
    assert any(f in texto for f in catalogo_ideas.CATALOGO["idea.sin_vision"])


def test_sin_vision_ni_nota_queda_idea_sin_titulo(mundo):
    mundo.bus.plugins.remove(mundo.vision)
    idea, texto = mundo.plugin.guardar_desde_imagen(_png(400, 300), "", "telegram")
    assert idea.titulo == "Idea sin título" and idea.recordar_en is not None


def test_si_la_vision_explota_la_idea_se_guarda(mundo):
    def explota(*a):
        raise RuntimeError("boom")

    mundo.vision.analizar_imagen = explota
    idea, _ = mundo.plugin.guardar_desde_imagen(_png(400, 300), "nota", "telegram")
    assert idea is not None and idea.titulo == "nota"


def test_algo_que_no_es_una_imagen_no_se_guarda(mundo):
    idea, texto = mundo.plugin.guardar_desde_imagen(b"hola", "", "telegram")
    assert idea is None and "imagen" in texto and mundo.plugin._almacen.pendientes() == []


# ---------------------------------------------------------------- guardar desde la PC
def test_guardar_la_ultima_captura_por_voz(mundo):
    _archivo(mundo.tmp / "capturas", "Captura.png", 2, os.path.getmtime(mundo.tmp) + 0, _png(300, 200))
    # el reloj de los archivos es el real: se fuerza con utime al "ahora" de verdad
    os.utime(mundo.tmp / "capturas" / "Captura.png", None)
    texto = mundo.plugin.manejar_tool("guardar_idea", {"cuando": "el sábado"}, {})
    assert "Bot traductor" in texto and "el sábado a las 11" in texto
    assert mundo.plugin._almacen.ultima().origen == "captura"


def test_si_no_hay_captura_lo_explica(mundo):
    assert mundo.plugin.manejar_tool("guardar_idea", {}, {}) in catalogo_ideas.CATALOGO["idea.sin_captura"]


def test_una_captura_vieja_se_guarda_diciendo_de_hace_cuanto(mundo):
    carpeta = mundo.tmp / "capturas"
    carpeta.mkdir()
    archivo = carpeta / "Ayer.png"
    archivo.write_bytes(_png(300, 200))
    hace = (datetime.now() - timedelta(hours=3)).timestamp()
    os.utime(archivo, (hace, hace))
    texto = mundo.plugin.manejar_tool("guardar_idea", {}, {})
    assert "Ayer.png" in texto and "de hace 3 horas" in texto


def test_guardar_lo_que_se_ve_ahora(mundo, monkeypatch):
    monkeypatch.setattr(Ideas, "_capturar_pantalla",
                        staticmethod(lambda: ideas.Captura(ideas.comprimir_imagen(_png(300, 200)), "la pantalla", None)))
    texto = mundo.plugin.manejar_tool("guardar_idea", {"fuente": "pantalla", "nota": "esto"}, {})
    assert "Bot traductor" in texto and mundo.plugin._almacen.ultima().origen == "pantalla"
    monkeypatch.setattr(Ideas, "_capturar_pantalla", staticmethod(lambda: None))
    assert mundo.plugin.manejar_tool("guardar_idea", {"fuente": "pantalla"}, {}) in catalogo_ideas.CATALOGO["idea.sin_captura"]


# ---------------------------------------------------------------- listar, cambiar, cerrar
def test_listar_ideas(mundo):
    p = mundo.plugin
    assert p.manejar_tool("listar_ideas", {}, {}) in catalogo_ideas.CATALOGO["idea.no_hay"]
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    uno = p.manejar_tool("listar_ideas", {}, {})
    assert uno == "Tenés 1 idea pendiente: 1: Bot traductor, te la recuerdo mañana a las 18."
    for _ in range(6):
        p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    varias = p.manejar_tool("listar_ideas", {}, {})
    assert varias.startswith("Tenés 7 ideas pendientes (las últimas 5): 3:") and varias.count(";") == 4


def test_recordar_otra_vez(mundo):
    p = mundo.plugin
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    texto = p.manejar_tool("recordar_idea", {"cuando": "el sábado"}, {})
    assert "Bot traductor" in texto and "el sábado a las 11" in texto
    assert p._almacen.obtener(1).recordar_en == datetime(2026, 10, 10, 11, 0)
    assert p.manejar_tool("recordar_idea", {"cuando": "sin recordatorio"}, {}) in [
        f.format(titulo="Bot traductor") for f in catalogo_ideas.CATALOGO["idea.sin_aviso"]]
    assert p._almacen.obtener(1).recordar_en is None


def test_recordar_una_idea_por_su_numero_y_cuando_no_se_entiende(mundo):
    p = mundo.plugin
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    p.manejar_tool("recordar_idea", {"cuando": "en una semana", "numero": 1}, {})
    assert p._almacen.obtener(1).recordar_en == datetime(2026, 10, 14, 18, 0)
    assert p._almacen.obtener(2).recordar_en == datetime(2026, 10, 8, 18, 0), "la otra no se toca"
    assert p.manejar_tool("recordar_idea", {"cuando": "cuando se me dé la gana"}, {}) in catalogo_ideas.CATALOGO["idea.no_entendi_cuando"]
    assert p.manejar_tool("recordar_idea", {"cuando": "mañana", "numero": 99}, {}) in catalogo_ideas.CATALOGO["idea.no_existe"]
    assert p.manejar_tool("recordar_idea", {"cuando": "mañana", "numero": "raro"}, {}) is not None, "un número raro usa la última"


def test_cerrar_una_idea(mundo):
    p = mundo.plugin
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    assert "Bot traductor" in p.manejar_tool("cerrar_idea", {"accion": "vista"}, {})
    assert [i.id for i in p._almacen.pendientes()] == [1], "cerró la última"
    p.manejar_tool("cerrar_idea", {"accion": "descartar", "numero": 1}, {})
    assert p._almacen.pendientes() == [] and p._almacen.obtener(1).estado == "descartada"
    assert p.manejar_tool("cerrar_idea", {"accion": "vista"}, {}) in catalogo_ideas.CATALOGO["idea.no_existe"]


def test_las_tools_ajenas_se_ignoran(mundo):
    assert mundo.plugin.manejar_tool("otra", {}, {}) is None


# ---------------------------------------------------------------- botones
def test_los_botones_de_fecha_llevan_el_id_de_la_idea(mundo):
    filas = mundo.plugin.botones_de_fecha(7)
    assert [d for fila in filas for _, d in fila] == ["idea:7:man", "idea:7:finde", "idea:7:sem", "idea:7:no"]
    assert all(len(f) <= 2 for f in filas) and all(len(d) < 64 for f in filas for _, d in f)


@pytest.mark.parametrize("accion,fecha", [("man", datetime(2026, 10, 8, 18, 0)), ("finde", datetime(2026, 10, 10, 11, 0)),
                                           ("sem", datetime(2026, 10, 14, 18, 0)), ("no", None)])
def test_tocar_un_boton_de_fecha_cambia_el_recordatorio(mundo, accion, fecha):
    mundo.plugin.guardar_desde_imagen(_png(100, 100), "", "telegram", "en 3 días")
    texto = mundo.plugin.responder_boton(1, accion)
    assert mundo.plugin._almacen.obtener(1).recordar_en == fecha and "Bot traductor" in texto


def test_tocar_ya_la_vi_o_descartar(mundo):
    p = mundo.plugin
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    p.guardar_desde_imagen(_png(100, 100), "", "telegram")
    dicho = p.responder_boton(1, "vista")                    # una sola vez: cada llamada elige otra frase al azar
    assert dicho in [f.format(titulo="Bot traductor") for f in catalogo_ideas.CATALOGO["idea.hecha"]]
    p.responder_boton(2, "desc")
    assert p._almacen.pendientes() == []


def test_un_boton_de_una_idea_que_no_existe_o_una_accion_rara(mundo):
    assert mundo.plugin.responder_boton(99, "man") in catalogo_ideas.CATALOGO["idea.no_existe"]
    mundo.plugin.guardar_desde_imagen(_png(100, 100), "", "telegram")
    assert mundo.plugin.responder_boton(1, "rara") == "No entendí ese botón."


# ---------------------------------------------------------------- recordatorios
def _con_idea_vencida(mundo, silencio=False):
    mundo.plugin.guardar_desde_imagen(_png(100, 100), "del foro", "telegram", "mañana")
    mundo.plugin.reloj_actual["t"] = datetime(2026, 10, 8, 3, 0 if silencio else 0) if silencio else datetime(2026, 10, 8, 18, 5)


def test_a_la_hora_te_la_recuerda_por_voz_toast_y_telegram(mundo):
    _con_idea_vencida(mundo)
    assert mundo.plugin.revisar_recordatorios() == 1
    assert mundo.bus.voice.dichas[0] in [f.format(titulo="Bot traductor") for f in catalogo_ideas.CATALOGO["idea.recordatorio"]]
    assert mundo.toasts[0][0] == "Idea guardada"
    texto, foto, botones = mundo.telegram.enviados[0]
    assert "Bot traductor" in texto and "Un bot que traduce" in texto and "del foro" in texto
    assert foto and Path(foto).is_file()
    assert [d for fila in botones for _, d in fila] == ["idea:1:vista", "idea:1:man", "idea:1:finde", "idea:1:desc"]


def test_el_recordatorio_se_avisa_una_sola_vez(mundo):
    _con_idea_vencida(mundo)
    mundo.plugin.revisar_recordatorios()
    mundo.plugin.reloj_actual["t"] += timedelta(minutes=30)
    assert mundo.plugin.revisar_recordatorios() == 0 and len(mundo.telegram.enviados) == 1


def test_antes_de_la_hora_no_avisa(mundo):
    mundo.plugin.guardar_desde_imagen(_png(100, 100), "", "telegram", "mañana")
    assert mundo.plugin.revisar_recordatorios() == 0 and mundo.bus.voice.dichas == []


def test_una_idea_cerrada_antes_de_la_hora_no_se_recuerda(mundo):
    _con_idea_vencida(mundo)
    mundo.plugin.responder_boton(1, "desc")
    assert mundo.plugin.revisar_recordatorios() == 0


def test_de_noche_espera_y_avisa_cuando_termina_el_silencio(mundo):
    mundo.plugin.guardar_desde_imagen(_png(100, 100), "", "telegram", "a las 23:30")
    mundo.plugin.reloj_actual["t"] = datetime(2026, 10, 7, 23, 45)
    assert mundo.plugin.revisar_recordatorios() == 0 and mundo.bus.voice.dichas == [] and mundo.telegram.enviados == []
    mundo.plugin.reloj_actual["t"] = datetime(2026, 10, 8, 8, 5)
    assert mundo.plugin.revisar_recordatorios() == 1, "no se pierde: sale apenas termina el silencio"


def test_si_telegram_falla_igual_te_lo_dice_y_no_se_repite(mundo):
    _con_idea_vencida(mundo)
    mundo.telegram.falla = True
    assert mundo.plugin.revisar_recordatorios() == 1 and len(mundo.bus.voice.dichas) == 1
    assert mundo.plugin.revisar_recordatorios() == 0


def test_sin_telegram_ni_voz_no_rompe(mundo):
    mundo.bus.plugins.remove(mundo.telegram)
    mundo.bus.voice = None
    _con_idea_vencida(mundo)
    assert mundo.plugin.revisar_recordatorios() == 1


def test_si_la_imagen_ya_no_esta_el_recordatorio_sale_sin_foto(mundo):
    _con_idea_vencida(mundo)
    Path(mundo.plugin._almacen.obtener(1).imagen).unlink()
    mundo.plugin.revisar_recordatorios()
    assert mundo.telegram.enviados[0][1] is None


# ---------------------------------------------------------------- registro, opciones y router
def test_el_plugin_se_carga_por_defecto_y_se_apaga_con_la_opcion(cfg):
    entrada = next(e for e in registro._CATALOGO if e.modulo == "productividad.ideas")
    assert entrada.condicion(cfg) is True
    cfg.valores["ideas_activo"] = False
    assert entrada.condicion(cfg) is False


def test_las_opciones_de_ideas_tienen_sus_valores_por_defecto(cfg):
    assert cfg.get("ideas_activo") is True and cfg.get("ideas_hora_aviso") == ideas.HORA_AVISO
    assert cfg.get("ideas_hora_finde") == ideas.HORA_FINDE


def test_las_tools_se_eligen_con_frases_del_dia_a_dia():
    from miku.cerebro import enrutador
    from miku.plugins.pantalla.captura import Captura
    from miku.plugins.social.telegram_envio import TelegramEnvio
    from miku.plugins.sistema.estado_pc import SystemStatus
    todas = Ideas.tools + Captura.tools + TelegramEnvio.tools + SystemStatus.tools
    casos = {"guardá la última captura para después": "guardar_idea", "qué ideas tengo guardadas": "listar_ideas",
             "recordame la idea el sábado": "recordar_idea", "ya vi la idea, descartala": "cerrar_idea"}
    for frase, tool in casos.items():
        elegidas = [t["function"]["name"] for t in enrutador.seleccionar(frase, todas, maximo=3)]
        assert tool in elegidas, (frase, elegidas)


# --------------------------------------------------------------------------- #
# Telegram: recibir la foto, los botones y mandar el recordatorio
# --------------------------------------------------------------------------- #
class Archivo:
    def __init__(self, contenido: bytes = b"foto") -> None:
        self.contenido = contenido

    async def download_as_bytearray(self):
        return bytearray(self.contenido)


class Bot:
    def __init__(self, falla: bool = False) -> None:
        self.falla, self.pedidos = falla, []

    async def get_file(self, file_id):
        self.pedidos.append(file_id)
        if self.falla:
            raise OSError("sin red")
        return Archivo()


class Mensaje:
    def __init__(self, fotos=None, documento=None, caption: str = "") -> None:
        self.photo, self.document, self.caption = fotos or [], documento, caption
        self.respuestas: List[tuple] = []

    async def reply_text(self, texto, reply_markup=None):
        self.respuestas.append((texto, reply_markup))


class IdeasFalso:
    nombre = "ideas"

    def __init__(self) -> None:
        self.guardadas: List[tuple] = []
        self.idea: Optional[Any] = types.SimpleNamespace(id=7)
        self.botones: List[tuple] = []

    def guardar_desde_imagen(self, datos, nota, origen):
        self.guardadas.append((datos, nota, origen))
        return self.idea, "Guardada: Bot. Te la recuerdo mañana a las 18."

    def botones_de_fecha(self, identificador):
        return [[("Mañana", f"idea:{identificador}:man")]]

    def responder_boton(self, identificador, accion):
        self.botones.append((identificador, accion))
        return "Listo, te la recuerdo mañana."


@pytest.fixture
def telegram(cfg, monkeypatch):
    cfg.valores.update(telegram_bot_token="123:ABC", telegram_chat_id="777")
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(TelegramControl, "_teclado", staticmethod(lambda filas: ("teclado", filas)))
    enviados: List[dict] = []
    estado = {"respuesta": types.SimpleNamespace(status_code=200, json=lambda: {"ok": True})}

    def post(url, **kw):
        enviados.append({"url": url, **kw})
        r = estado["respuesta"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(red, "post", post)
    t = TelegramControl()
    t.ideas = IdeasFalso()
    t._event_bus = types.SimpleNamespace(plugins=[t.ideas])
    t.enviados, t.estado = enviados, estado
    return t


def _foto(telegram, mensaje, usuario=777, bot=None):
    update = types.SimpleNamespace(message=mensaje, effective_user=types.SimpleNamespace(id=usuario))
    contexto = types.SimpleNamespace(bot=bot or Bot())
    asyncio.run(telegram._on_foto(update, contexto))
    return contexto


def test_una_foto_se_guarda_y_pregunta_cuando_recordarla(telegram):
    m = Mensaje(fotos=[types.SimpleNamespace(file_id="chica", file_size=10), types.SimpleNamespace(file_id="grande", file_size=99)],
                caption="para el finde")
    contexto = _foto(telegram, m)
    assert contexto.bot.pedidos == ["grande"], "se baja la de mayor resolución"
    assert telegram.ideas.guardadas == [(b"foto", "para el finde", "telegram")]
    assert m.respuestas[0] == ("Mirando la imagen…", None)
    texto, teclado = m.respuestas[1]
    assert texto.startswith("Guardada: Bot.") and texto.endswith("¿La recordamos en otro momento?")
    assert teclado == ("teclado", [[("Mañana", "idea:7:man")]])


def test_una_imagen_mandada_como_archivo_tambien_vale(telegram):
    doc = types.SimpleNamespace(file_id="doc", file_size=10, mime_type="image/png")
    m = Mensaje(documento=doc)
    _foto(telegram, m)
    assert telegram.ideas.guardadas and telegram.ideas.guardadas[0][1] == ""


def test_un_archivo_que_no_es_imagen_se_ignora(telegram):
    m = Mensaje(documento=types.SimpleNamespace(file_id="pdf", file_size=10, mime_type="application/pdf"))
    _foto(telegram, m)
    assert m.respuestas == [] and telegram.ideas.guardadas == []


def test_solo_el_usuario_autorizado_puede_guardar_ideas(telegram):
    m = Mensaje(fotos=[types.SimpleNamespace(file_id="x", file_size=1)])
    _foto(telegram, m, usuario=999)
    assert m.respuestas == [] and telegram.ideas.guardadas == []


def test_sin_el_plugin_de_ideas_lo_avisa(telegram):
    telegram._event_bus = types.SimpleNamespace(plugins=[])
    m = Mensaje(fotos=[types.SimpleNamespace(file_id="x", file_size=1)])
    _foto(telegram, m)
    assert m.respuestas == [("No tengo activado el guardado de ideas.", None)]


def test_una_imagen_enorme_no_se_baja(telegram):
    m = Mensaje(fotos=[types.SimpleNamespace(file_id="x", file_size=20 * 1024 * 1024)])
    contexto = _foto(telegram, m)
    assert contexto.bot.pedidos == [] and "demasiado grande" in m.respuestas[0][0]


def test_si_no_se_puede_bajar_la_imagen_lo_dice(telegram):
    m = Mensaje(fotos=[types.SimpleNamespace(file_id="x", file_size=1)])
    _foto(telegram, m, bot=Bot(falla=True))
    assert "No pude bajar" in m.respuestas[0][0] and telegram.ideas.guardadas == []


def test_si_no_era_una_imagen_valida_dice_por_que_sin_botones(telegram):
    telegram.ideas.idea = None
    m = Mensaje(fotos=[types.SimpleNamespace(file_id="x", file_size=1)])
    _foto(telegram, m)
    assert m.respuestas[-1] == ("Guardada: Bot. Te la recuerdo mañana a las 18.", None)


class Consulta:
    def __init__(self, datos: str, con_foto: bool = False) -> None:
        self.data = datos
        self.message = types.SimpleNamespace(photo=[1] if con_foto else [], caption="💡 Bot traductor", text="💡 Bot traductor")
        self.respondida: Optional[str] = "sin responder"
        self.editado: Optional[tuple] = None

    async def answer(self, texto=None):
        self.respondida = texto

    async def edit_message_text(self, texto):
        self.editado = ("texto", texto)

    async def edit_message_caption(self, texto):
        self.editado = ("pie", texto)


def _tocar(telegram, datos, usuario=777, con_foto=False):
    consulta = Consulta(datos, con_foto)
    update = types.SimpleNamespace(callback_query=consulta, effective_user=types.SimpleNamespace(id=usuario))
    asyncio.run(telegram._on_boton(update, None))
    return consulta


def test_tocar_un_boton_de_idea_la_resuelve_y_actualiza_el_mensaje(telegram):
    c = _tocar(telegram, "idea:7:man")
    assert telegram.ideas.botones == [(7, "man")] and c.respondida == "Listo"
    assert c.editado == ("texto", "💡 Bot traductor\n\nListo, te la recuerdo mañana.")


def test_en_un_recordatorio_con_foto_se_actualiza_el_pie(telegram):
    c = _tocar(telegram, "idea:7:vista", con_foto=True)
    assert c.editado[0] == "pie" and c.editado[1].endswith("Listo, te la recuerdo mañana.")


@pytest.mark.parametrize("datos", ["idea:", "idea:abc:man", "idea:7", "idea:7:man:extra"])
def test_un_boton_de_idea_mal_formado_no_hace_nada(telegram, datos):
    c = _tocar(telegram, datos)
    assert telegram.ideas.botones == [] and c.respondida == "Eso ya no está disponible." and c.editado is None


def test_solo_el_usuario_autorizado_toca_los_botones_de_ideas(telegram):
    c = _tocar(telegram, "idea:7:man", usuario=999)
    assert telegram.ideas.botones == [] and c.editado is None


def test_los_botones_de_ideas_no_pisan_los_de_las_preguntas(telegram):
    class ParserFalso:
        def resolver_confirmacion(self, numero, acepta, contexto):
            return "Listo."

    telegram._parser = ParserFalso()
    c = _tocar(telegram, "ok:3")
    assert telegram.ideas.botones == [] and c.editado[1].endswith("✅ Sí: Listo.")


def test_enviar_un_mensaje_con_botones(telegram):
    assert telegram.enviar("💡 Idea", None, [[("Sí", "a:1"), ("No", "a:2")]]) is True
    envio = telegram.enviados[0]
    assert envio["url"].endswith("/sendMessage") and envio["json"]["chat_id"] == "777"
    assert envio["json"]["reply_markup"]["inline_keyboard"] == [[{"text": "Sí", "callback_data": "a:1"},
                                                                 {"text": "No", "callback_data": "a:2"}]]


def test_enviar_con_foto_sube_la_imagen(telegram, tmp_path):
    foto = tmp_path / "1.jpg"
    foto.write_bytes(b"jpeg")
    assert telegram.enviar("x" * 2000, str(foto), [[("Ok", "a:1")]]) is True
    envio = telegram.enviados[0]
    assert envio["url"].endswith("/sendPhoto") and envio["files"]["photo"][0] == "1.jpg"
    assert len(envio["data"]["caption"]) == 1000, "Telegram limita el pie a 1024"
    assert '"callback_data": "a:1"' in envio["data"]["reply_markup"]


def test_enviar_sin_token_o_si_telegram_rechaza_devuelve_false(telegram, cfg):
    telegram.estado["respuesta"] = types.SimpleNamespace(status_code=400, json=lambda: {"ok": False})
    assert telegram.enviar("x") is False
    cfg.valores["telegram_bot_token"] = ""
    telegram.enviados.clear()
    assert telegram.enviar("x") is False and telegram.enviados == []


def test_un_error_de_red_al_enviar_no_filtra_el_token(telegram, caplog):
    import requests
    telegram.estado["respuesta"] = requests.ConnectionError("Max retries with url: /bot123:ABC/sendMessage")
    with caplog.at_level(logging.DEBUG):
        assert telegram.enviar("x") is False
    assert "123:ABC" not in caplog.text and "ConnectionError" in caplog.text
