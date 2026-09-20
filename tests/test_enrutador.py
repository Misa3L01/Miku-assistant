"""Enrutador de tools: manda pocas al LLM sin perder la que hace falta (corpus con las tools reales)."""
from __future__ import annotations

import pytest

from miku.cerebro import enrutador
from miku.plugins import registro

# (lo que dice el usuario, tool que TIENE que llegar al LLM)
CASOS = [
    ("abrí discord", "abrir_programa"), ("abrime brave", "abrir_programa"), ("lanzá fortnite", "abrir_programa"),
    ("ejecutá steam", "abrir_programa"), ("cerrá el bloc de notas", "cerrar_programa"),
    ("matá el proceso de chrome", "cerrar_programa"), ("subí el volumen", "ajustar_volumen"),
    ("bajá el volumen", "ajustar_volumen"), ("más fuerte", "ajustar_volumen"),
    ("poné el volumen al 30", "ajustar_volumen"), ("silenciá chrome", "mutear_app"),
    ("bajá el brillo", "controlar_brillo"), ("más brillo", "controlar_brillo"),
    ("pausá la música", "control_multimedia"), ("pausá", "control_multimedia"),
    ("siguiente canción", "control_multimedia"), ("pasá al próximo tema", "control_multimedia"),
    ("pausá tidal", "controlar_tidal"), ("qué está sonando", "que_esta_sonando"),
    ("poné bohemian rhapsody de queen", "reproducir_en_tidal"), ("reproducí thriller", "reproducir_en_tidal"),
    ("apagá la pc en 10 minutos", "programar_accion"),
    ("recordame en 5 minutos sacar la pizza", "programar_accion"),
    ("cancelá lo programado", "cancelar_accion_programada"), ("apagá la compu", "control_energia"),
    ("reiniciá", "control_energia"), ("suspendé la pc", "control_energia"),
    ("bloqueá la pantalla", "control_energia"), ("minimizá spotify", "minimizar_ventana"),
    ("movelo al segundo monitor", "mover_ventana"),
    ("poné chrome a la izquierda y discord a la derecha", "organizar_ventanas"),
    ("qué ventanas tengo abiertas", "listar_ventanas"), ("buscame en youtube gatos", "buscar_en_web"),
    ("buscá en mercadolibre auriculares", "buscar_en_web"), ("googleá recetas", "buscar_en_web"),
    ("buscame el archivo factura", "buscar_archivo"), ("dónde está mi cv", "buscar_archivo"),
    ("modo fortnite", "ejecutar_macro"), ("qué macros tenés", "listar_macros"),
    ("interpolá el video", "interpolar_video"), ("traducí buena suerte al portugués", "traducir_mensaje_juego"),
    ("decí gracias en japonés", "traducir_mensaje_juego"), ("cómo está la pc", "estado_pc"),
    ("cómo anda la compu", "estado_pc"), ("cómo está el clima", "clima"), ("va a llover hoy", "clima"),
    ("qué temperatura hace", "clima"), ("sacá una captura", "capturar_pantalla"),
    ("qué dice la pantalla", "leer_pantalla"), ("qué ves en mi pantalla", "ver_pantalla"),
    ("mis tareas de hoy", "tareas_hoy"), ("qué tengo pendiente", "tareas_hoy"),
    ("agregá una tarea comprar leche", "agregar_tarea"), ("completá la tarea de leche", "completar_tarea"),
    ("cambiá tu personalidad a tsundere", "cambiar_personalidad"), ("salí del modo", "salir_modo"),
    ("volvé a la resolución normal", "volver_resolucion_nativa"),
    ("abrí mi carpeta de descargas", "abrir_carpeta_favorita"),
    ("guardá esta carpeta como trabajo", "guardar_carpeta_favorita"),
    ("silenciá a pepe en discord", "silenciar_usuario_discord"),
    ("expulsá a juan del servidor", "expulsar_usuario_discord"),
    ("abrí una pestaña con github", "abrir_pestana"), ("cerrá la pestaña", "cerrar_pestana"),
    ("actualizá la biblioteca de juegos", "actualizar_biblioteca_juegos"), ("conectá tidal", "conectar_tidal"),
    ("mové la ventana de chrome al monitor 2", "mover_ventana"),
    ("poné en pantalla completa el navegador", "posicionar_ventana"),
    ("qué personalidades tenés", "listar_personalidades"), ("ponete tsundere", "cambiar_personalidad"),
    ("bajale el volumen a spotify", "ajustar_volumen"), ("prendé el modo gaming", "ejecutar_macro"),
    ("acordate que mi cumple es el 3 de mayo", "guardar_recuerdo"), ("olvidá lo de mi cumple", "olvidar_recuerdo"),
    ("qué recordás de mí", "listar_recuerdos"), ("empecemos de nuevo", "olvidar_conversacion"),
    ("traducí lo que copié al inglés", "procesar_portapapeles"), ("resumí lo que copié", "procesar_portapapeles"),
    ("corregí lo que copié", "procesar_portapapeles"), ("deshacé lo del portapapeles", "deshacer_portapapeles"),
    ("comedor", "inscribir_comedor"), ("inscribime al comedor", "inscribir_comedor"),
    ("mandame la última captura al whatsapp", "enviar_a_whatsapp"), ("mandame por wasap que compre leche", "enviar_a_whatsapp"),
    ("conectá whatsapp", "conectar_whatsapp"),
    ("qué pestañas tengo abiertas", "listar_pestanas"), ("subí el volumen de la música", "volumen_tidal"),
    ("mandame la última captura al telegram", "enviar_a_telegram"),
    ("qué estoy viendo en el monitor 2", "ver_pantalla"), ("abrí genshin impact", "abrir_programa"),
]


@pytest.fixture(scope="module")
def tools():
    from miku.ajustes import carga
    cfg = carga.Config()
    cfg.valores.update(telegram_bot_token="x", juegos_booster=["cs2"], todoist_api_token="x", gemini_api_key="x", comedor_usuario="x", whatsapp_contacto_default="+54 3751 123456")
    return [t for p in registro.instanciar_plugins(cfg) for t in p.tools]


@pytest.mark.parametrize("frase,esperada", CASOS)
def test_la_tool_necesaria_siempre_llega(tools, frase, esperada):
    nombres = {t["function"]["name"] for t in enrutador.seleccionar(frase, tools)}
    assert esperada in nombres


def test_manda_bastantes_menos_tools_en_promedio(tools):
    enviadas = [len(enrutador.seleccionar(f, tools)) for f, _ in CASOS]
    assert sum(enviadas) / len(enviadas) < len(tools) / 2
    assert max(enviadas) <= 14 or max(enviadas) == len(tools)


@pytest.mark.parametrize("frase", ["contame un chiste", "cuál es la capital de Francia", "y en chrome?",
                                   "hola cómo estás", "gracias", "blablabla"])
def test_sin_relacion_clara_manda_todas(tools, frase):
    assert enrutador.seleccionar(frase, tools) == tools


def test_desactivado_o_pocas_tools_no_filtra(tools):
    assert enrutador.seleccionar("abrí discord", tools, maximo=0) == tools
    assert enrutador.seleccionar("abrí discord", tools[:5], maximo=14) == tools[:5]


def test_conserva_el_orden_original(tools):
    sel = enrutador.seleccionar("abrí discord y subí el volumen", tools)
    indices = [tools.index(t) for t in sel]
    assert indices == sorted(indices)
