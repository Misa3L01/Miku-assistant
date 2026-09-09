# 📋 CONTEXTO DEL PROYECTO - MIKU ASSISTANT

## 🎯 Objetivo del Proyecto
Asistente virtual personal para PC con IA, orientado a:
- Programación
- Gaming (CS:GO, Fortnite, Genshin Impact)
- Uso diario
- Comunicación en Discord

## 📅 Última actualización: 09/09/2026

## 🏗️ Estado Actual del Proyecto

### ✅ Completado:
- Estructura base del proyecto (core/, plugins/, data/, docs/)
- Configuración de Git/GitHub
- Setup de DeepSeek API (vía Continue.dev en VS Code)
- Refactor de config.py con sistema de prioridades (defaults → preferences.json → config_local.py → entorno)
- Bus de eventos (event_bus.py) con despacho a plugins y modo async opcional
- Parser de comandos (command_parser.py) con fast path local + tools vía LLM (Groq)
- STT en hilos separados (speech_to_text.py) con wake word "Miku" y transcripción vía Whisper de Groq
- TTS con VOICEVOX (text_to_speech.py): traducción ES→JA con deep-translator + síntesis local + reproducción con pygame, con fallback a pyttsx3
- Subtítulos en pantalla estilo overlay (subtitles.py) con PyQt5, corriendo en hilo dedicado
- Plugin de control de sistema (system_control.py): abrir/cerrar programas, brillo, listar/mover/minimizar ventanas
- Guards de API key faltante (Groq texto y Groq STT) para no romper el arranque sin claves configuradas
- requirements.txt depurado (solo libs realmente usadas, imports lazy documentados)

### 🚧 En Progreso:
- Separar TODO dato privado (rutas de interpolación, rutas de Brave/Tidal, API keys) del código versionado hacia `config_local.py` (no versionado) + plantilla `config_local.py.example`
- Arreglar el flujo de confirmación de acciones peligrosas (hoy roto: el contexto se resetea en cada turno y nunca persiste `espera_confirmacion` / `pendiente_energia`)
- Agregar la tool `control_energia` (apagar/reiniciar/suspender) que el parser ya espera pero que no existe en `system_control.py`
- Corregir push-to-talk: `detener_captura_activa()` no existe en `speech_to_text.py`, así que soltar F22 no corta la grabación en curso

### 📝 Pendiente:
- Refactorizar código monolítico a modular *(ya bastante avanzado con esta sesión)*
- Implementar sistema de plugins *(base ya lista en `plugins/__init__.py`, faltan plugins concretos nuevos)*
- Integrar Discord API
- Crear game booster
- Implementar traductor (además del interno ES→JA para TTS)
- Sistema de macros (ya existe `macros_config.json` con formato definido, pero ningún plugin lo lee todavía)
- Decidir si se reactiva `memoria.py` (hoy es un stub desactivado a propósito, sin backend) o se sigue sin memoria persistente por ahora
- Endurecer el chequeo de whitelist en `cerrar_programa` (hoy usa coincidencia por substring, debería ser exacta)
- Revisar si el bloque de exportación a `globals()` en `config.py` sigue teniendo sentido una vez que `config_local.py` sea la forma canónica de manejar secretos, o si conviene eliminarlo por duplicar lógica con las propiedades de `Config`

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
- **TTS**: VOICEVOX (motor local en `localhost:50021`) + traducción ES→JA con deep-translator + reproducción con pygame; fallback a pyttsx3 si VOICEVOX no responde. Voz genérica de VOICEVOX (no clonada), configurable por `voicevox_speaker_id`
- **Subtítulos**: overlay transparente con PyQt5, siempre encima, en hilo dedicado
- **Discord**: discord.py *(pendiente de integrar)*
- **Control de ventanas**: pywin32 (win32gui/con/api)
- **Control de sistema**: AppOpener (abrir programas), screen-brightness-control (brillo), keyboard (push-to-talk)
- **Config privada**: `config_local.py` (no versionado) para API keys y rutas específicas de la máquina (interpolación de video, Brave, Tidal, etc.)

### Convenciones de Código:
- Clases en PascalCase
- Métodos en snake_case
- Comentarios en español
- Logging en lugar de print
- Imports pesados/opcionales (PyQt5, pyttsx3, deep-translator, pygame, pywin32, speech_recognition) SIEMPRE lazy (dentro de funciones/métodos), para no penalizar el arranque en modo texto
- Ningún dato privado (API keys, rutas específicas de la PC) hardcodeado en archivos versionados: todo vive en `config_local.py` o variables de entorno

## 🐛 Bugs Conocidos
- **Crítico**: `Asistente._contexto_base()` en `main.py` resetea `espera_confirmacion`/`pendiente_energia` en cada turno → el flujo de confirmación de acciones peligrosas nunca se dispara
- La tool `control_energia` es referenciada por `command_parser.py` pero no está implementada en ningún plugin
- Push-to-talk: `finalizar_grabacion()` llama a un método (`detener_captura_activa`) que no existe en `SpeechToText`, así que soltar F22 no corta la captura activa (funciona "por accidente" gracias al timeout/silencio de SpeechRecognition)
- Verificar que `preferences.json` esté efectivamente dentro de `data/` y no en la raíz del proyecto (si no, se carga en silencio con los defaults)
- Rutas privadas (interpolación de video, Brave, Tidal) todavía figuran como default real dentro de `config.py` en vez de vivir únicamente en `config_local.py`

## 📝 Próximos Pasos Inmediatos
1. Crear `config_local.py` + `config_local.py.example` y mover ahí todos los datos privados (API keys, rutas de interpolación, rutas de Brave/Tidal), agregando `config_local.py` a `.gitignore`
2. Arreglar la persistencia del contexto de confirmación entre turnos en `main.py`
3. Implementar la tool `control_energia` en `system_control.py` y conectarla al flujo de confirmación
4. Corregir el corte real de la grabación en push-to-talk

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
