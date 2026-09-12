# 📋 CONTEXTO DEL PROYECTO - MIKU ASSISTANT

## 🎯 Objetivo del Proyecto
Asistente virtual personal para PC con IA, orientado a:
- Programación
- Gaming (CS:GO, Fortnite, Genshin Impact)
- Uso diario
- Comunicación en Discord

## 📅 Última actualización: Sesión 4

## 🏗️ Estado Actual del Proyecto

### ✅ Completado:
- Estructura base del proyecto (core/, plugins/, data/, docs/)
- Configuración de Git/GitHub
- Setup de DeepSeek API (vía Continue.dev en VS Code)
- Refactor de config.py con sistema de prioridades (defaults → preferences.json → config_local.py → entorno)
- Bus de eventos (event_bus.py) con despacho a plugins y modo async opcional
- Parser de comandos (command_parser.py) con fast path local + tools vía LLM (Groq)
- STT en hilos separados (speech_to_text.py) con wake word "Miku" y transcripción vía Whisper de Groq
- TTS con VOICEVOX (text_to_speech.py): traducción ES→JA con **API de Groq** (cuenta STT) + cache en memoria + síntesis local + reproducción con pygame, con fallback a pyttsx3
- Subtítulos en pantalla estilo overlay (subtitles.py) con PyQt5, **sincronizados por frase**
- Plugin de control de sistema (system_control.py): abrir/cerrar programas, brillo, ventanas, multimedia, volumen, energía, búsqueda, **acciones diferidas**
- Plugin de búsqueda web (web_search.py): abrir búsquedas/sitios en el navegador
- **Scheduler genérico (core/scheduler.py)** para acciones diferidas + cancelación
- **Tono anti-repetición (core/tono.py)** para respuestas cortas
- Confirmación **genérica** de acciones peligrosas (persistente entre turnos)
- **Memoria persistente (core/memoria.py)** reactivada con backend **SQLite** (stdlib), con degradación elegante y activable con `memoria_activa`
- **Traducción compartida (core/traduccion.py)**: cliente único de Groq que usan el TTS (ES→JA) y el traductor de juegos
- Plugin `macros.py` (lee `data/macros_config.json`: `listar_macros` / `ejecutar_macro`)
- Plugin `video_interpolador.py` (`interpolar_video`): dispara el `.bat` de interpolación en segundo plano + aviso por voz al terminar + desambiguación
- Plugin `traductor_juegos.py` (`traducir_mensaje_juego`): traduce mensajes predefinidos y los copia al portapapeles (`win32clipboard`)
- **Volumen por app** en `ajustar_volumen` (parámetro `app`, ajusta todas las sesiones de audio de la app vía pycaw)
- requirements.txt depurado (solo libs realmente usadas, imports lazy documentados)

### 🚧 En Progreso:
- (ninguno crítico abierto — los de Sesión 1/2 quedaron resueltos en Sesión 3)

### 📝 Pendiente:
- Plugins "grandes" heredados: Discord, Game Booster, traductor de juegos, macros
- Navegador (Brave) por CDP (más profundo que `buscar_en_web`, que solo abre la búsqueda)
- Sistema de macros (ya existe `macros_config.json`, pero ningún plugin lo lee todavía)
- Decidir si se reactiva `memoria.py` (hoy es un stub desactivado a propósito, sin backend)
- Ideas de lanzador/experiencia de escritorio: bandeja del sistema (QSystemTrayIcon), modo `always_on`, hotkey F22 como toggle, ventanita flotante de modo, empaquetado a `.exe` con PyInstaller (ver `docs/informe_sesion_3.md`)

## 🔧 Decisiones de Arquitectura

### Estructura de Módulos:
```
core/    -> Núcleo del sistema (voz, parsing, eventos)
plugins/ -> Funcionalidades específicas (discord, gaming, etc)

data/    -> Preferencias y configuraciones (preferences.json, macros_config.json, miku_memoria.db)
docs/    -> Documentación
```

### Stack Tecnológico:
- **Lenguaje**: Python 3.10+ (Windows)
- **Cerebro / LLM**: Groq API (modelo configurable, function calling/tools)
- **STT**: SpeechRecognition + Whisper de Groq (API), en hilos separados
- **TTS**: VOICEVOX (motor local en `localhost:50021`) + traducción ES→JA con **API de Groq** (cuenta STT, con cache en memoria) + reproducción con pygame; fallback a pyttsx3 si VOICEVOX no responde. Voz genérica de VOICEVOX (no clonada), configurable por `voicevox_speaker_id`
- **Subtítulos**: overlay transparente con PyQt5, siempre encima, en hilo dedicado, **sincronizados por frase** (prefetch de la siguiente)
- **Scheduler**: `threading.Timer` (`core/scheduler.py`) para acciones diferidas/cancelables
- **Discord**: discord.py *(pendiente de integrar)*
- **Config privada**: `config_local.py` (no versionado) para API keys y rutas específicas de la máquina (interpolación de video, Brave, Tidal, etc.)

### Convenciones de Código:
- Clases en PascalCase
- Métodos en snake_case
- Comentarios en español
- Logging en lugar de print
- Imports pesados/opcionales (PyQt5, pyttsx3, pygame, pywin32, speech_recognition, pycaw/comtypes) SIEMPRE lazy (dentro de funciones/métodos), para no penalizar el arranque en modo texto
- Ningún dato privado (API keys, rutas específicas de la PC) hardcodeado en archivos versionados: todo vive en `config_local.py` o variables de entorno

## 🐛 Bugs Conocidos
- (Ninguno crítico abierto.) Los bugs de Sesión 1/2 (contexto de confirmación que se reseteaba, `control_energia` sin implementar, push-to-talk que no cortaba, rutas privadas en `config.py`) quedaron **resueltos** en Sesión 2/3.

## 📝 Próximos Pasos Inmediatos



1. Abordar los plugins "grandes" pendientes (Discord, Game Booster, navegador por CDP).
2. Evaluar las ideas de lanzador/experiencia de escritorio (bandeja, `always_on`, hotkey F22 toggle, ventanita de modo, `.exe`) en una sesión dedicada — ver `docs/informe_sesion_3.md`.

## 💡 Ideas Futuras
- OCR para lectura de pantalla
- Visión por computador
- Integración con más juegos
- Sistema de macros conectado a `macros_config.json`

## 🔑 Recursos Importantes
- API DeepSeek: Configurada en Continue.dev (VS Code), plan de pago
- API Groq: usada para el cerebro (chat + tools) y para STT (Whisper), claves separadas opcionales (`GROQ_API_KEY` / `GROQ_API_KEY_STT`)
- VOICEVOX: motor de síntesis de voz corriendo local en `http://localhost:50021`
- Repositorio: [URL del repo]

## 📊 Historial de Sesiones

### Sesión 1 ([Fecha]):
- Crear estructura base
- Configurar entorno
- Refactor inicial de config.py, main.py, event_bus.py, command_parser.py, speech_to_text.py, text_to_speech.py, subtitles.py, system_control.py, memoria.py (desactivada)
- Agregar guards de API key faltante
- Depurar requirements.txt

### Sesión 2 (09/09/2026):
- Revisión completa del código generado en la Sesión 1
- Detectado bug crítico: el contexto de confirmación (`espera_confirmacion`/`pendiente_energia`) se resetea en cada turno y nunca persiste, dejando muerto el flujo de confirmación de acciones peligrosas
- Detectado que la tool `control_energia` es referenciada por el parser pero nunca fue implementada en `system_control.py`
- Detectado que el push-to-talk no corta realmente la grabación al soltar la tecla (`detener_captura_activa()` no existe)
- Decidido: crear `config_local.py` como lugar único para datos privados (API keys, rutas de interpolación, rutas de Brave/Tidal) y sacar cualquier resto de esos datos de `config.py`, agregando `config_local.py.example` como plantilla versionada
- Marcado para revisión: el bloque de exportación a `globals()` al final de `config.py`, por posible duplicación de lógica
- Marcado para revisión: comparación por substring en la whitelist de `cerrar_programa`
- Pendiente de decisión: si se reactiva `memoria.py` o se sigue sin memoria persistente

### Sesión 3 (Scheduler, web, tono, VOICEVOX oculto):
- **Bloque 4.3 — Tono anti-repetición:** nuevo `core/tono.py` con catálogo de variantes para respuestas cortas ("Listo", "Ya está", "Dale", etc.), evitando repetir la última; conectado en `Asistente.responder()`/`decir()`.
- **Bloque 6 — Scheduler genérico:** nuevo `core/scheduler.py` (`threading.Timer`); instanciado en `Asistente` y pasado por `contexto["scheduler"]`; se cancela todo al cerrar. Tools nuevas `programar_accion` y `cancelar_accion_programada`. **Estado de confirmación generalizado** en el parser: `_pendiente_peligroso {tool, args}` sirve para cualquier tool peligrosa (`_TOOLS_PELIGROSAS`). Fix: "recordame X en N minutos" ya no lo intercepta el fast-path de memoria.
- **Bloque 7 — Búsqueda web:** nuevo `plugins/web_search.py` con la tool `buscar_en_web` (MercadoLibre/YouTube/Google/Wikipedia/GitHub o dominio directo); registrado en `main.instalar_core()`. System prompt actualizado para que "buscá en internet" use `buscar_en_web`.
- **Bloque 8 (ya resuelto previamente en 4.2d):** traducción ES→JA con Groq (cuenta STT) + cache en memoria; **no** se usó `deep-translator` ni se cambió la arquitectura de generación de respuestas. Se hizo el cierre documental (README/CONTEXTO/informe) reflejando esto.
- **Bloque 9 — VOICEVOX oculto + saludo de arranque:** `run.bat` ahora usa `start "" /B`; `_asegurar_voicevox()` reforzado con `STARTUPINFO/SW_HIDE` + stdout/stderr a `DEVNULL`; `run.ps1` con `-WindowStyle Hidden`. Miku dice "Ya estoy lista" una vez tras arrancar la escucha continua (modo voz).
- **Bloque 10 — Documentación de cierre:** README (tabla de tools + secciones de traducción/subtítulos/tono/scheduler), CONTEXTO (esta sesión), `docs/informe_sesion_3.md`. requirements.txt confirmado (pycaw/comtypes ya estaban; sin deps nuevas).
- Ideas de lanzador/escritorio dejadas **explícitamente** para una sesión aparte (bandeja, `always_on`, hotkey F22 toggle, ventanita de modo, `.exe` con PyInstaller).

### Sesión 4 (Memoria SQLite, traducción compartida, volumen por app, video, traductor de juegos, wake word):
- **Bloque 13 — Interpolación de video:** nuevo `plugins/video_interpolador.py` con la tool `interpolar_video`. Lista videos de `CARPETA_VIDEOS`, elige por nombre o "el último", y dispara `RUTA_BAT_INTERPOLAR` **en segundo plano** (sin ventana), en un hilo aparte, pasando la ruta completa del video como argumento. Al terminar **avisa por voz** (o consola) según el código de salida. Desambigua si hay varios. Respuestas honestas si falta config.
- **Bloque 13 (fix del .bat):** comparado contra el `interpolar_miku.bat` real del usuario, se corrigió el **desacople de formato de argumento** (el `.bat` ahora recibe la **RUTA COMPLETA** y se eliminó `VIDEOS_DIR` hardcodeado; `TARGET_VIDEO=%%~fV` directo) y la **extensión de 5 caracteres** (`.webm`): se usa `%%~nV` (nombre sin extensión) en vez del offset fijo `~0,-4`. Además se sacaron los `::` dentro del bloque `for` (pasados a `rem`) y los emojis (ASCII puro, sin BOM). Python **no** se tocó (ya pasaba la ruta completa). Verificado con simulación `echo` (mkv/webm/espacios/varios).
- **Bloque 14 — Traductor de juegos + traducción compartida:** nuevo `core/traduccion.py` (cliente Groq único: `traducir_con_groq` + `normalizar_para_traducir`). `text_to_speech.py` refactorizado para delegar (re-exporta `normalizar_para_traducir`). Nuevo `plugins/traductor_juegos.py` (`traducir_mensaje_juego`): resuelve la frase desde `MENSAJES_JUEGO`, la traduce a `IDIOMA_JUEGO` (cuenta principal) y la copia al **portapapeles** (`win32clipboard`). Config nueva `idioma_juego`/`mensajes_juego`.
- **Bloque 15 — Volumen por app:** `ajustar_volumen` acepta el parámetro opcional `app`: ajusta **todas las sesiones de audio** de esa app (pycaw `GetAllSessions`, matching tolerante a acentos y `.exe`); sin `app` se comporta como antes (volumen general). Base para el futuro Game Booster.
- **Bloque 11 — Wake word en una sola frase:** `core/speech_to_text.py` ahora soporta AMBOS flujos. Nuevo helper `_extraer_comando_en_linea()`: tras detectar la wake word, toma lo que vino DESPUÉS (primera variante de `_VARIANTES_MIKU`), limpia separadores sueltos y, si queda contenido significativo (>2 chars), lo usa como **comando directo** en `_bucle_escucha_permanente()` vía `on_comando(...)` **sin** segunda escucha. Si el usuario dijo solo "Miku" (o "Miku."/"Miku,"), sigue el flujo de dos pasos sin cambios. Se mantiene `on_wake()` ("¿Sí? Decime.") en ambos flujos por consistencia. Probado con 17 casos de parsing + 4 escenarios de flujo completo con STT simulado (audio real no disponible en el entorno de desarrollo).
- **Nota:** plugins `video_interpolador` y `traductor_juegos` registrados en `main.instalar_core()`. requirements.txt: pywin32 ya cubría `win32clipboard`; sin deps nuevas.