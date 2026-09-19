"""
esquema.py - Declaración ÚNICA de todas las opciones de configuración de Miku.

Cada opción se declara una sola vez, con su tipo, su valor por defecto y una descripción
en español. De este esquema salen:

    - los valores por defecto de ``config.py`` (``valores_por_defecto()``),
    - el archivo ``config_local.py.example`` (``ejemplo.generar_ejemplo()``),
    - la validación al arrancar (claves mal escritas, tipos incorrectos, plugins a los que
      les falta configuración: ``validacion.py``),
    - el asistente ``python -m miku.ajustes`` y, más adelante, la ventana de ajustes.

Para agregar una opción: sumar una línea a ``_OPCIONES`` (y, si un plugin la necesita para
funcionar, ``REQUISITOS``). No hace falta tocar nada más.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

# Orden en que aparecen los grupos en el .example y en el asistente.
GRUPOS: Tuple[Tuple[str, str], ...] = (
    ("claves", "Claves de API y cuentas"),
    ("voz", "Voz, micrófono y modo de arranque"),
    ("memoria", "Memoria"),
    ("sistema", "Sistema, programas y juegos"),
    ("carpetas", "Carpetas personales"),
    ("pantalla", "Capturas, OCR y visión"),
    ("navegador", "Navegador y música"),
    ("video", "Interpolación de video"),
    ("juegos", "Traductor de juegos y Game Booster"),
    ("clima", "Clima y personalidad"),
    ("avisos", "Tareas, control remoto y avisos automáticos"),
    ("discord", "Discord"),
)

# Tipos: texto | secreto | ruta | entero | decimal | booleano | lista | mapa
TIPOS = ("texto", "secreto", "ruta", "entero", "decimal", "booleano", "lista", "mapa")


@dataclass(frozen=True)
class Opcion:
    """Una opción de configuración.

    Attributes:
        clave: Nombre en minúsculas (``config.get("clave")``); en ``config_local.py`` va en
            MAYÚSCULAS.
        default: Valor por defecto (nunca datos privados).
        grupo: Clave de ``GRUPOS``.
        descripcion: Qué hace, en una o dos frases.
        tipo: Uno de ``TIPOS`` (para validar y para ocultar los secretos).
        ejemplo: Valor de ejemplo, ya escrito como código Python (``r"D:\\Capturas"``).
        usado_por: Módulo o plugin que la usa.
        permitidos: Si no está vacío, los únicos valores válidos.
    """

    clave: str
    default: Any
    grupo: str
    descripcion: str
    tipo: str = "texto"
    ejemplo: str = ""
    usado_por: str = ""
    permitidos: Tuple[Any, ...] = ()

    @property
    def nombre_local(self) -> str:
        """Nombre de la variable en ``config_local.py``."""
        return self.clave.upper()


def _o(clave, default, grupo, descripcion, tipo="texto", ejemplo="", usado_por="", permitidos=()):
    return Opcion(clave, default, grupo, descripcion, tipo, ejemplo, usado_por, tuple(permitidos))


_OPCIONES: List[Opcion] = [
    # ------------------------------------------------------------------ claves
    _o("groq_api_key", "", "claves",
       "Clave de Groq (https://console.groq.com): es el cerebro (LLM) de Miku. Sin ella solo "
       "funcionan los comandos locales (hora, calculadora, sistema).",
       "secreto", 'GROQ_API_KEY = "gsk_..."', "cerebro"),
    _o("groq_api_key_stt", "", "claves",
       "Clave de Groq para la transcripción de voz (Whisper) y la traducción de la voz. Si la "
       "dejás vacía se usa la principal.", "secreto", usado_por="voz"),
    _o("modelo_api_externa", "openai/gpt-oss-20b", "claves",
       "Modelo de Groq que usa el cerebro (debe soportar herramientas/tools).",
       usado_por="cerebro"),
    _o("gemini_api_key", "", "claves",
       "Clave de Google AI Studio (https://aistudio.google.com) para que Miku pueda MIRAR la "
       "pantalla (`ver_pantalla`). OJO: la captura se envía a Google. No hace falta para leer "
       "texto de la pantalla (eso es OCR local).", "secreto", usado_por="vision"),
    _o("gemini_modelo", "gemini-2.0-flash", "claves",
       "Modelo de visión de Gemini.", usado_por="vision"),
    _o("todoist_api_token", "", "claves",
       "Token personal de Todoist (Ajustes > Integraciones > Desarrollador). El plan gratis "
       "alcanza.", "secreto", usado_por="todoist"),
    _o("telegram_bot_token", "", "claves",
       "Token del bot de Telegram (lo crea @BotFather) para controlar Miku desde el celular.",
       "secreto", usado_por="telegram_control"),
    _o("telegram_chat_id", "", "claves",
       "Tu ID NUMÉRICO de usuario de Telegram (p. ej. con @userinfobot). Miku solo obedece a "
       "ese usuario.", "texto", usado_por="telegram_control"),
    _o("discord_bot_token", "", "claves",
       "Token del bot de Discord (https://discord.com/developers/applications). Necesita el "
       "intent privilegiado 'Server Members' activado.", "secreto", usado_por="discord_control"),

    # --------------------------------------------------------------------- voz
    _o("voicevox_url", "http://localhost:50021", "voz",
       "Dirección del motor de voz VOICEVOX.", usado_por="voz"),
    _o("voicevox_speaker_id", 6, "voz",
       "Número de la voz de VOICEVOX que usa Miku.", "entero", usado_por="voz"),
    _o("voicevox_run_exe", "", "voz",
       "Ruta a run.exe de VOICEVOX si no está en extern/VOICEVOX/vv-engine/. Miku lo "
       "arranca sola (oculto) si no está corriendo.", "ruta",
       r'r"D:\Programas\VOICEVOX\vv-engine\run.exe"', "voz"),
    _o("microfono_index", None, "voz",
       "Número del micrófono a usar. None = el predeterminado de Windows. La lista de "
       "micrófonos aparece en la consola al iniciar el modo voz.", "entero", "MICROFONO_INDEX = 2",
       "voz"),
    _o("modelo_stt_wake", "whisper-large-v3", "voz",
       "Modelo de Whisper para detectar la palabra 'Miku'.", usado_por="voz"),
    _o("modelo_stt_comando", "whisper-large-v3", "voz",
       "Modelo de Whisper para transcribir lo que le pedís.", usado_por="voz"),
    _o("llm_base_url", "", "voz",
       "Servidor del LLM compatible con OpenAI. Vacío = Groq. Para un modelo LOCAL: Ollama "
       "'http://localhost:11434/v1' o LM Studio 'http://localhost:1234/v1'.", "texto",
       'LLM_BASE_URL = "http://localhost:11434/v1"', "parser"),
    _o("llm_modelo", "", "voz",
       "Modelo a usar con ese servidor (vacío = MODELO_API_EXTERNA). Ej. Ollama: 'qwen2.5:7b'.",
       usado_por="parser"),
    _o("llm_api_key", "", "claves",
       "Clave del servidor LLM propio (los servidores locales no la necesitan).", "secreto",
       usado_por="parser"),
    _o("llm_soporta_tools", True, "voz",
       "False si tu modelo local no entiende herramientas (function calling): Miku conversa pero "
       "no ejecuta acciones con él.", "booleano", usado_por="parser"),
    _o("stt_proveedor", "groq", "voz",
       "Quién transcribe lo que decís: 'groq' (Whisper en la nube) o 'local' (faster-whisper en tu "
       "PC: sin nube, más lento; pip install faster-whisper).", "texto", usado_por="voz",
       permitidos=("groq", "local")),
    _o("stt_modelo_local", "small", "voz",
       "Tamaño del modelo de faster-whisper cuando STT_PROVEEDOR = 'local' (tiny, base, small, "
       "medium, large-v3).", "texto", usado_por="voz"),
    _o("tts_motor", "voicevox", "voz",
       "Motor de voz: 'voicevox' (voz japonesa, Miku traduce lo que dice), 'sistema' (voz de "
       "Windows, español) o 'comando' (tu propio motor: ver TTS_COMANDO).", "texto", usado_por="voz",
       permitidos=("voicevox", "sistema", "comando")),
    _o("tts_idioma", "es", "voz",
       "Idioma que HABLA el motor 'comando' (es, en, ja, pt…). Si no es 'es', Miku traduce antes "
       "de hablar.", "texto", usado_por="voz"),
    _o("tts_comando", "", "voz",
       "Comando de tu motor de voz (Piper, XTTS, GPT-SoVITS…). Debe escribir un WAV en {salida}; el "
       "texto entra por stdin (o usá {texto}).", "texto",
       r'TTS_COMANDO = r"piper --model C:\voces\es.onnx --output_file {salida}"', "voz"),
    _o("subtitulos", "auto", "voz",
       "Subtítulos en pantalla: 'auto' (solo cuando Miku NO habla en español), 'siempre' o "
       "'nunca'.", "texto", usado_por="voz", permitidos=("auto", "siempre", "nunca")),
    _o("modo_entrada", "voz", "voz",
       "Modo con el que arranca cuando no se elige uno en la ventanita (o con --silencioso): "
       "'voz' (escucha continua, decís \"Miku\") o 'texto' (ventana de depuración). Se puede "
       "cambiar desde el icono de la bandeja.", "texto", usado_por="app",
       permitidos=("voz", "texto")),
    _o("saludo_al_iniciar", True, "voz",
       "Al iniciar el modo voz, Miku saluda con la hora, el clima y tus pendientes. Ponelo en "
       "False si no querés que hable al arrancar con Windows.", "booleano", usado_por="app"),
    _o("log_level", "INFO", "voz",
       "Detalle de los mensajes de diagnóstico: DEBUG, INFO, WARNING o ERROR.",
       usado_por="app", permitidos=("DEBUG", "INFO", "WARNING", "ERROR")),

    # ----------------------------------------------------------------- memoria
    _o("memoria_activa", True, "memoria",
       "Si Miku guarda y recuerda cosas que le pedís ('acordate que…').", "booleano",
       usado_por="memoria"),
    _o("embeddings_activos", True, "memoria",
       "Búsqueda por SIGNIFICADO en la memoria y en los archivos (requiere `pip install "
       "fastembed`; si no está instalado se busca por palabras).", "booleano",
       usado_por="memoria"),
    _o("embeddings_umbral", 0.35, "memoria",
       "Qué tan parecido (0 a 1) debe ser un recuerdo para que Miku lo use. Más alto = más "
       "estricto.", "decimal", usado_por="memoria"),

    # ----------------------------------------------------------------- sistema
    _o("app_whitelist", ["brave", "discord", "tidal", "vscode", "code", "chrome", "edge",
                         "spotify", "steam", "obs", "notepad", "explorer", "calculadora"],
       "sistema",
       "Apps que Miku puede CERRAR por voz (nombre exacto). El Explorador de Windows nunca se "
       "cierra aunque esté acá.", "lista", usado_por="programas"),
    _o("ruta_everything_es", "bin/es.exe", "sistema",
       "Ruta a es.exe (Everything CLI) para buscar archivos. Everything tiene que estar "
       "abierto en segundo plano.", "ruta", usado_por="archivos"),
    _o("steam_ruta", "", "sistema",
       "Carpeta de instalación de Steam si no está en la ubicación habitual.", "ruta",
       r'r"D:\Steam"', "programas"),
    _o("juegos_epic", {}, "sistema",
       "Juegos de Epic Games a mano: nombre -> id del ítem (Epic no se detecta solo).", "mapa",
       'JUEGOS_EPIC = {"fortnite": "fn"}', "programas"),

    # --------------------------------------------------------------- carpetas
    _o("carpetas_favoritas", {}, "carpetas",
       "Carpetas de acceso rápido: 'abrí la carpeta de animes'. También podés enseñarlas por "
       "voz y se guardan solas.", "mapa",
       'CARPETAS_FAVORITAS = {"animes": r"R:\\Animes", "descargas": r"C:\\Users\\vos\\Downloads"}',
       "favoritos"),
    _o("carpeta_capturas", "", "carpetas",
       "Dónde se guardan las capturas de pantalla. Vacío = data/capturas/ dentro del "
       "proyecto.", "ruta", r'r"D:\Capturas"', "captura"),

    # ---------------------------------------------------------------- pantalla
    _o("tesseract_ruta", "", "pantalla",
       "Ruta a tesseract.exe para leer texto de la pantalla con mejor precisión. Vacío = se "
       "busca en el PATH y, si no está, se usa el OCR incluido en Windows.", "ruta",
       r'r"C:\Program Files\Tesseract-OCR\tesseract.exe"', "ocr"),
    _o("ocr_idioma", "spa", "pantalla",
       "Idioma del OCR de Tesseract: spa, eng o spa+eng.", usado_por="ocr"),

    # --------------------------------------------------------------- navegador
    _o("brave_ruta_exe", "", "navegador",
       "Ruta a brave.exe. Miku lo lanza con el puerto de depuración para controlar las "
       "pestañas.", "ruta", r'r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"',
       "browser"),
    _o("brave_perfil_dir", "", "navegador",
       "Carpeta del perfil de Brave a usar (para abrir con tu sesión).", "ruta",
       usado_por="browser"),
    _o("brave_debug_port", 9222, "navegador",
       "Puerto de depuración remota de Brave.", "entero", usado_por="browser"),
    _o("tidal_ruta_exe", "", "navegador",
       "Ruta a TIDAL.exe.", "ruta", usado_por="tidal"),

    # ------------------------------------------------------------------- video
    _o("carpeta_videos", "", "video",
       "Carpeta con los videos a interpolar. Miku solo procesa videos de esta carpeta.", "ruta",
       r'r"R:\VapourSynth-Env\videos"', "video_interpolador"),
    _o("ruta_bat_interpolar", "", "video",
       "Ruta al .bat que interpola (recibe la ruta completa del video como argumento).", "ruta",
       r'r"R:\VapourSynth-Env\Python\interpolar_miku.bat"', "video_interpolador"),

    # --------------------------------------------------------------- conversación
    _o("historial_turnos", 4, "memoria",
       "Cuántos turnos previos de la charla recuerda Miku para entender 'y mañana?' o 'ahora en "
       "Chrome' (0 = sin historial).", "entero", usado_por="parser"),
    _o("historial_minutos", 10, "memoria",
       "Minutos que un turno sigue contando como contexto de la charla.", "entero", usado_por="parser"),
    _o("enrutar_tools", True, "memoria",
       "Mandarle al LLM solo las herramientas relacionadas con lo que dijiste (menos costo y "
       "latencia). Si ninguna se relaciona claramente, manda todas.", "booleano", usado_por="parser"),
    _o("enrutar_max_tools", 14, "memoria",
       "Máximo de herramientas que se mandan al LLM por consulta cuando el enrutado está activo.",
       "entero", usado_por="parser"),

    # ------------------------------------------------------------------ juegos
    _o("idioma_juego", "", "juegos",
       "Idioma por defecto del traductor cuando no decís uno ('traducí X al portugués' siempre "
       "gana): 'inglés', 'portugués', 'japonés'… Opcional.", usado_por="traductor_juegos"),
    _o("perfiles_juego", {}, "juegos",
       "Idioma del traductor por juego: si estás jugando uno de estos y no decís idioma, se usa el suyo "
       "(tiene prioridad sobre IDIOMA_JUEGO). Clave: proceso sin .exe.", "mapa",
       'PERFILES_JUEGO = {"cs2": "portugués", "genshinimpact": "inglés"}', "traductor_juegos"),
    _o("mensajes_juego", {}, "juegos",
       "Atajos del traductor: clave -> frase en español ('gg': 'buena partida'). Opcional.",
       "mapa", 'MENSAJES_JUEGO = {"gg": "buena partida"}', "traductor_juegos"),
    _o("juegos_booster", [], "juegos",
       "Procesos de juegos (sin .exe) que activan el modo gaming automático.", "lista",
       'JUEGOS_BOOSTER = ["cs2", "fortniteclient-win64-shipping", "genshinimpact"]',
       "game_booster"),
    _o("booster_apps_volumen", ["brave", "chrome", "edge", "firefox"], "juegos",
       "Apps a las que baja el volumen el modo gaming.", "lista", usado_por="game_booster"),
    _o("booster_volumen_objetivo", 20, "juegos",
       "Volumen (0-100) al que se bajan esas apps mientras jugás.", "entero",
       usado_por="game_booster"),
    _o("booster_aviso", True, "juegos",
       "Avisar (notificación + voz) al entrar y salir del modo gaming.", "booleano",
       usado_por="game_booster"),

    # ------------------------------------------------------------------- clima
    _o("ciudad_clima", "", "clima",
       "Tu ciudad para el clima (Open-Meteo, sin clave).", usado_por="clima"),
    _o("clima_lat", "", "clima",
       "Latitud, si preferís coordenadas exactas en vez de la ciudad.", usado_por="clima"),
    _o("clima_lon", "", "clima",
       "Longitud (junto con la latitud).", usado_por="clima"),
    _o("personalidad", "neutral", "clima",
       "Estilo de Miku: neutral, tsundere, formal o entusiasta.", usado_por="personalidad",
       permitidos=("neutral", "tsundere", "formal", "entusiasta")),

    # ------------------------------------------------------------------ avisos
    _o("proactivo_activo", True, "avisos",
       "Avisos automáticos de Miku (clima, estado de la PC, batería, disco). Es el interruptor general.",
       "booleano",
       usado_por="asistente_proactivo"),
    _o("proactivo_bateria_min", 20, "avisos",
       "Avisar cuando la batería baje de este porcentaje.", "entero",
       usado_por="asistente_proactivo"),
    _o("proactivo_disco_gb", 5.0, "avisos",
       "Avisar cuando queden menos de estos GB libres.", "decimal",
       usado_por="asistente_proactivo"),
    _o("proactivo_intervalo_min", 5, "avisos",
       "Cada cuántos minutos revisa el estado de la PC.", "entero",
       usado_por="asistente_proactivo"),
    _o("proactivo_cooldown_min", 30, "avisos",
       "Minutos mínimos entre dos avisos iguales.", "entero", usado_por="asistente_proactivo"),
    _o("proactivo_max_por_hora", 4, "avisos",
       "Máximo de avisos por hora (para que no moleste). 0 = sin límite.", "entero",
       usado_por="asistente_proactivo"),
    _o("proactivo_silencio_desde", "23:00", "avisos",
       "Desde qué hora no avisa nada (HH:MM). Vacío junto con la de abajo = sin horario de silencio.",
       usado_por="asistente_proactivo"),
    _o("proactivo_silencio_hasta", "08:00", "avisos",
       "Hasta qué hora no avisa nada (HH:MM).", usado_por="asistente_proactivo"),
    _o("proactivo_no_molestar_en_juego", True, "avisos",
       "Mientras jugás solo avisa lo urgente (temperatura de la GPU) y el estado al empezar.",
       "booleano", usado_por="asistente_proactivo"),
    _o("proactivo_clima", True, "avisos",
       "Avisar del clima cuando importa (lluvia, frío o calor fuertes). Necesita CIUDAD_CLIMA "
       "o CLIMA_LAT/CLIMA_LON. Avisa como mucho una vez por día por cada condición.",
       "booleano", usado_por="asistente_proactivo"),
    _o("proactivo_clima_intervalo_min", 120, "avisos",
       "Cada cuántos minutos consulta el pronóstico para los avisos.", "entero",
       usado_por="asistente_proactivo"),
    _o("proactivo_lluvia_prob", 60, "avisos",
       "Avisar de lluvia cuando la probabilidad en las próximas horas sea de este % o más.",
       "entero", usado_por="asistente_proactivo"),
    _o("proactivo_frio_c", 12, "avisos",
       "Avisar de frío cuando la sensación térmica sea de estos grados o menos.", "decimal",
       usado_por="asistente_proactivo"),
    _o("proactivo_calor_c", 32, "avisos",
       "Avisar de calor cuando la sensación térmica sea de estos grados o más.", "decimal",
       usado_por="asistente_proactivo"),
    _o("proactivo_estado_juego", True, "avisos",
       "Contar cómo está la PC (CPU, RAM, GPU) un rato después de que empieza un juego de "
       "JUEGOS_BOOSTER.", "booleano", usado_por="asistente_proactivo"),
    _o("proactivo_carga_pct", 92, "avisos",
       "Avisar cuando CPU o RAM se mantengan por encima de este % durante unos minutos.",
       "entero", usado_por="asistente_proactivo"),
    _o("proactivo_gpu_temp_c", 85, "avisos",
       "Avisar (aunque estés jugando) cuando la GPU NVIDIA pase de estos grados.", "decimal",
       usado_por="asistente_proactivo"),

    # ----------------------------------------------------------------- discord
    _o("discord_guild_id", "", "discord",
       "ID del servidor de Discord por defecto (clic derecho en el servidor > Copiar ID, con "
       "el Modo desarrollador activado).", usado_por="discord_control"),
    _o("discord_canal_default", "", "discord",
       "Reservado: canal por defecto para futuras funciones.", usado_por="discord_control"),
]

#: Opciones por clave.
OPCIONES: Dict[str, Opcion] = {o.clave: o for o in _OPCIONES}

#: Claves de versiones anteriores que ya no hacen nada (se avisa si están en config_local).
OBSOLETAS: Dict[str, str] = {
    "dolar_actual": "ya no se usa (cálculo de precios eliminado)",
    "recargo_tarjeta": "ya no se usa (cálculo de precios eliminado)",
    "ganancia": "ya no se usa (cálculo de precios eliminado)",
    "discos_buscar": "ya no se usa (Everything busca en todos los discos)",
    "tidal_debug_port": "ya no se usa",
    "modelo_miku": "ya no se usa (la voz es VOICEVOX)",
}

#: Qué necesita cada plugin para funcionar. Cada requisito es un grupo de claves donde
#: basta con que UNA esté configurada; deben cumplirse todos los grupos.
REQUISITOS: Dict[str, Tuple[str, Sequence[Sequence[str]]]] = {
    "cerebro (Groq)": ("responder a lo que le pedís", [("groq_api_key",)]),
    "vision": ("mirar la pantalla con Gemini", [("gemini_api_key",)]),
    "todoist": ("tus tareas de Todoist", [("todoist_api_token",)]),
    "telegram_control": ("control remoto por Telegram",
                         [("telegram_bot_token",), ("telegram_chat_id",)]),
    "discord_control": ("moderar Discord", [("discord_bot_token",)]),
    "clima": ("el clima", [("ciudad_clima", "clima_lat")]),
    "video_interpolador": ("interpolar videos",
                           [("carpeta_videos",), ("ruta_bat_interpolar",)]),
    "game_booster": ("el modo gaming automático", [("juegos_booster",)]),
    "browser": ("controlar Brave", [("brave_ruta_exe",)]),
}


def valores_por_defecto() -> Dict[str, Any]:
    """Diccionario ``clave -> default`` (copias profundas: nadie comparte listas/dicts)."""
    return {o.clave: copy.deepcopy(o.default) for o in _OPCIONES}


def opciones_del_grupo(grupo: str) -> List[Opcion]:
    """Opciones de un grupo, en el orden en que se declararon."""
    return [o for o in _OPCIONES if o.grupo == grupo]
