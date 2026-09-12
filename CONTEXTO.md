# 📋 CONTEXTO DEL PROYECTO - MIKU ASSISTANT

## 🎯 Objetivo del Proyecto
Asistente virtual personal para PC con IA, orientado a:
- Programación
- Gaming (CS:GO, Fortnite, Genshin Impact)
- Uso diario
- Comunicación en Discord

## 📅 Última actualización: Sesión 3

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
data/    -> Preferencias y configuraciones (preferences.json, macros_config.json)
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
- **Control de ventanas**: pywin32 (win32gui/con/api)
- **Control de sistema**: AppOpener (abrir programas), screen-brightness-control (brillo), keyboard (push-to-talk)
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
1. Abordar los plugins "grandes" pendientes (Discord, Game Booster, traductor de juegos, macros).
2. Decidir si se reactiva el sistema de memoria con un backend liviano (SQLite).
3. Evaluar las ideas de lanzador/experiencia de escritorio (bandeja, `always_on`, hotkey F22 toggle, ventanita de modo, `.exe`) en una sesión dedicada — ver `docs/informe_sesion_3.md`.

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
