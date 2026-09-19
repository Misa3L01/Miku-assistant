# 📋 CONTEXTO DEL PROYECTO - MIKU ASSISTANT

## 🎯 Objetivo del Proyecto
Asistente virtual personal para PC con IA, orientado a:
- Programación
- Gaming (CS:GO, Fortnite, Genshin Impact)
- Uso diario
- Comunicación en Discord

## 📅 Última actualización: Sesión 10 (fases A a E, G, H e I de la reestructuración — ver `docs/plan_reestructuracion.md`)

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
- **Tanda fácil (F1–F7):** acoplar/dividir ventanas (hoy con la tool `organizar_ventanas`; `acoplar_ventanas`/`dividir_pantalla` quedaron internas), mute por app (`mutear_app`), estado del sistema (`system_status`/`estado_pc`), carpetas favoritas (`favoritos`), calculadora local (`core/calculadora.py`), captura de pantalla (`captura`) y comandos encadenados (varias `tool_calls` por turno)
- **Tanda intermedia (I1–I10):** Brave por CDP (`browser`), TIDAL (`tidal`), salir de modos (`modos`/`core/modos.py`), recordatorios a hora exacta (`programar_accion` con `hora`), clima Open-Meteo (`clima`), personalidad configurable (`personalidad`/`core/personalidad.py`), toasts (`core/notificaciones.py`), Auto-Game Booster (`game_booster`), OCR de pantalla (`ocr`) y briefing de arranque (`core/briefing.py`)
- **Tanda final (Z1–Z6):** memoria/búsqueda **semántica** (`core/embeddings.py` fastembed opcional + `core/memoria.py` + re-rank en `buscar_archivo`), **visión de pantalla con Gemini** (`vision`), **Todoist** (`todoist`), **control remoto por Telegram** (`telegram_control`), **asistente proactivo** (`asistente_proactivo`). Pendiente gated: Z7 (lanzador/F22/bandeja) y Z8 (.exe), que van paso a paso con confirmación.
- **Ciclo de vida, apertura de juegos y ventanas (tanda A–E):** `Plugin` declara `cerrar()` (no-op) y `main` cierra cada plugin **aislado** (un plugin roto no impide cerrar el resto); `game_booster` **revierte su snapshot** (`core.modos.salir_modo`) si cerrás el asistente en medio de un juego y **reusa el `SystemControl` del bus** (no instancia pycaw/win32 por ciclo); `abrir_programa` suma **fallback por Everything** (`.exe`/`.lnk` por nombre, scoring, descarte de ruido y **desambiguación** si hay varios) para juegos fuera de AppOpener/Steam/Epic; y el split de pantalla se unificó en la tool **`organizar_ventanas`** (1 o 2 ventanas, 50/50, con aviso si es la misma ventana de los dos lados).

### 🚧 En Progreso:
- (ninguno crítico abierto — los de Sesión 1/2 quedaron resueltos en Sesión 3)

### 📝 Pendiente:
- Decisiones abiertas de la Sesión 9 (ver `docs/informe_auditoria.md`): push-to-talk inalcanzable desde la ventanita (solo Voz/Texto), recordatorios que piden confirmación y no sobreviven a reiniciar, `vision` sin confirmación, verificar si Gemini `gemini-2.0-flash` y Todoist REST v2 siguen vigentes.
- Ideas: `traducir_a_canal` de Discord, modo `always_on`, ventanita desde la bandeja, temperatura/Wallpaper Engine en el Game Booster, OCR de regiones, motores de voz alternativos.
- Pruebas automáticas: hoy solo hay pruebas de humo manuales (no versionadas).

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

### Sesión 5 (tanda fácil F1–F7):
- **F1 — Acoplar/dividir ventanas (fix):** en `plugins/system_control.py` se sumó la tool `acoplar_ventanas(ventana_a, ventana_b, layout, monitor)` y los helpers `_traer_al_frente()` (SW_RESTORE + SetWindowPos(HWND_TOP) + SetForegroundWindow con fallback de tecla Alt) y `_acoplar_par()` (restaura AMBAS ventanas y las sube al frente). `dividir_pantalla` reusa el acoplado cuando encuentra las dos ventanas. Arregla el bug de "la segunda ventana queda atrás/escondida". (Nota posterior: esas dos dejaron de exponerse al LLM; la tool de cara al usuario es `organizar_ventanas`, que delega en ellas.)
- **F2 — Mute por app:** nueva tool `mutear_app(nombre, accion)` (alias `silenciar_app`) en `system_control.py`; usa las sesiones de audio (pycaw) y hace `SetMute` por proceso sin tocar el master; mensaje claro si no hay sesión sonando.
- **F3 — Estado del sistema:** nuevo `plugins/system_status.py` con la tool `estado_pc` (alias `como_esta_la_pc`): CPU %, RAM usada/total, disco libre y batería si hay. Sin temperatura. Usa `psutil` (lazy).
- **F4 — Carpetas favoritas:** nuevo `plugins/favoritos.py` con `abrir_carpeta_favorita` y `guardar_carpeta_favorita`. El mapa sale de `CARPETAS_FAVORITAS` (config_local) y de las "enseñadas" por voz, persistidas en `data/preferences.json` (merge que preserva el resto del JSON).
- **F5 — Calculadora rápida (sin LLM):** nuevo `core/calculadora.py` (parser de descenso recursivo: `+ - * / %`, paréntesis y palabras en español; porcentaje contextual "X más 10%" y módulo). Fast-path en `command_parser._comandos_inmediatos` que nunca llama a la API.
- **F6 — Captura de pantalla:** nuevo `plugins/captura.py` con `capturar_pantalla(monitor, formato)`; guarda PNG/JPG con fecha-hora en `CARPETA_CAPTURAS` (config_local) o `data/capturas/`. Usa `PIL.ImageGrab` (lazy).
- **F7 — Comandos encadenados:** `command_parser.procesar` ahora ejecuta TODAS las `tool_calls` del turno en orden (no solo la primera); las peligrosas se siguen gateando con UNA confirmación sin perder las seguras del mismo turno.
- **Config/integ:** `config.py` suma `carpetas_favoritas` y `carpeta_capturas`; `config_local.py.example` documenta `CARPETAS_FAVORITAS` y `CARPETA_CAPTURAS`; `main.py` registra `SystemStatus`, `Favoritos` y `Captura`; `requirements.txt` suma `psutil` y `Pillow`. `py_compile` OK.

### Sesión 8 (lanzador Z7 — pasos 1–5, incremental):
- **Z7.1 — Bandeja sola:** nuevo `core/bandeja.py` (`QSystemTrayIcon` en hilo propio con event loop Qt; reutiliza la `QApplication` si existe; icono + menú "Miku está activa"/"Salir"; degrada si falta PyQt5). `main.py` la arranca tras el núcleo y la cierra al salir; "Salir" interrumpe el hilo principal (`_thread.interrupt_main`) para un cierre ordenado.
- **Z7.2 — Ventanita Voz/Texto:** nuevo `core/ventanita.py` (diálogo "Modo Voz"/"Modo Texto"); se muestra DESDE el hilo Qt de la bandeja (`Bandeja.pedir_modo`). `main.py` la usa para elegir modo y cae a la consola si no hay bandeja.
- **Z7.3 — Texto siempre con voz:** se quitó el "texto sin voz"; el modo texto llama a `run_modo_texto(con_voz=True)`.
- **Z7.4 — F22 global → ventanita:** `keyboard.add_hotkey("f22", ...)` abre la ventanita y **persiste** el modo en `data/preferences.json`. **No se registra en modo push** (ahí F22 sigue siendo push-to-talk).
- **Z7.5 — Modo Voz → bandeja:** en modo voz, el tooltip de la bandeja pasa a "Miku - Modo Voz (escuchando)".
- **Verificado:** `py_compile` OK; `import main` OK; `bandeja`/`ventanita` OK.
- **Nota:** el aviso Qt "Timers cannot be stopped from another thread" al cerrar es cosmético (mismo patrón que `subtitles.py`).

### Sesión 7 (tanda final Z1–Z6):
- **Z1 — Memoria semántica:** nuevo `core/embeddings.py` (motor liviano **fastembed**, opcional; degrada a LIKE si falta) y `core/memoria.py` con la MISMA API pero búsqueda por similitud coseno (columna `embedding` por migración + umbral en config). "cuándo es mi cumpleaños" → "nací el 15 de julio".
- **Z2 — Búsqueda semántica de archivos:** `plugins/system_control._rerank_semantico` re-rankea los resultados de Everything por significado (bonus 0-20 puntos). NO reemplaza Everything; sin motor, todo igual.
- **Z3 — Visión de pantalla (Gemini):** nuevo `plugins/vision.py` (`ver_pantalla`/`que_error_me_tira`/`que_hay_en_pantalla`). Captura → comprime (1280px, JPEG q70) → REST `generateContent`. Avisa privacidad (se envía a Google). `GEMINI_API_KEY` en config_local.
- **Z4 — Todoist:** nuevo `plugins/todoist.py` (`tareas_hoy`, `agregar_tarea`, `completar_tarea`, REST v2 Bearer). Plan FREE alcanza. `TODOIST_API_TOKEN` en config_local.
- **Z5 — Telegram:** nuevo `plugins/telegram_control.py` (bot en hilo daemon + loop propio; texto del celu → MISMO parser vía `bus.parser`; solo responde al `TELEGRAM_CHAT_ID`; NO prende la PC). Comandos `/start`, `/estado`, `/pendientes`.
- **Z6 — Asistente proactivo:** nuevo `plugins/asistente_proactivo.py` (hilo daemon; batería baja / disco casi lleno; avisa por toast + voz con cooldown; sin temperatura; config `PROACTIVO_*`).
- **Config/integ:** `config.py` y `config_local.py.example` suman embeddings/Gemini/Todoist/Telegram/proactivo; `main.py` registra 4 plugins nuevos (total **20**) y expone `bus.parser`; `requirements.txt` con `fastembed` y `python-telegram-bot` opcionales (comentados). `py_compile` OK.
- **Pendiente gated:** Z7 (lanzador/F22/bandeja, incremental con confirmación) y Z8 (empaquetado .exe).

### Sesión 6 (tanda intermedia I1–I10):
- **I1 — Brave por CDP:** nuevo `plugins/browser.py` (`abrir_pestana`, `cerrar_pestana`, `buscar_en_pestana_actual`). Usa el HTTP JSON API del puerto de depuración (`brave_debug_port`, default 9222); si no está, LANZA Brave con `brave_ruta_exe` + `--remote-debugging-port`. Mensaje claro si no puede conectar/lanzar. No se "arregla" la restauración de pestañas (es de Brave).
- **I2 — TIDAL:** nuevo `plugins/tidal.py` (`controlar_tidal`, `que_esta_sonando`). Controles por teclas multimedia del sistema (TIDAL no tiene API pública); "qué suena" vía Windows SMTC (PowerShell/WinRT). Límite documentado.
- **I3 — Salir de modos:** nuevo `core/modos.py` (snapshot de volumen/resolución/brillo + reversión) y `plugins/modos.py` (`salir_modo`). `plugins/macros.py` captura el snapshot ANTES de aplicar una macro; si no hay snapshot, avisa sin romper.
- **I4 — Recordatorios a hora exacta:** `programar_accion` acepta `hora` (HH:MM/18h/18); `_segundos_hasta_hora` calcula el delta y, si la hora ya pasó, asume mañana. Cancelar sigue igual.
- **I5 — Clima (Open-Meteo):** nuevo `plugins/clima.py` (sin API key). Geocodifica `CIUDAD_CLIMA` (o usa `CLIMA_LAT/LON`) y arma frase natural con recomendación ("hace frío, abrigate").
- **I6 — Personalidad configurable:** nuevo `core/personalidad.py` (perfiles neutral/tsundere/formal/entusiasta; `prompt_extra()` + `adorno_corto()`) y `plugins/personalidad.py` (`cambiar_personalidad`, `listar_personalidades`). `BrainGroq` inyecta el prompt del perfil en cada consulta; `main.responder()` aplica el adorno a respuestas cortas. Persistible en preferences.json.
- **I7 — Toasts:** nuevo `core/notificaciones.py` (win10toast → PowerShell+WinRT → log; no bloqueante, con escape XML).
- **I8 — Auto-Game Booster:** nuevo `plugins/game_booster.py` (hilo daemon; detecta juego en primer plano por proceso vía win32+psutil; snapshot + baja volumen de `BOOSTER_APPS_VOLUMEN`; avisa por toast/voz; revierte al salir; sin spam).
- **I9 — OCR de pantalla:** nuevo `plugins/ocr.py` (`leer_pantalla`/`que_dice_esta_ventana`). Motor elegido: **Tesseract** (pytesseract, primario, paquete `spa`) con **fallback a Windows.Media.Ocr** (WinRT) sin instalar nada. Todo lazy/best-effort.
- **I10 — Briefing:** nuevo `core/briefing.py`; `run_modo_voz` reemplaza el "Ya estoy lista" seco por hora + clima (si hay) + recordatorios pendientes (del Scheduler) en una frase.
- **Config/integ:** `config.py` suma clima, personalidad, booster y OCR; `config_local.py.example` documenta `CIUDAD_CLIMA`, `PERSONALIDAD`, `JUEGOS_BOOSTER`, `BOOSTER_*`, `TESSERACT_RUTA`, `OCR_IDIOMA`; `main.py` registra `PersonalidadPlugin`, `Modos`, `Clima`, `Browser`, `Tidal`, `Ocr`, `GameBooster` (16 plugins). requirements.txt: `pytesseract` opcional comentado. `py_compile` OK.

### Sesión 4 (Memoria SQLite, traducción compartida, volumen por app, video, traductor de juegos, wake word):
- **Bloque 13 — Interpolación de video:** nuevo `plugins/video_interpolador.py` con la tool `interpolar_video`. Lista videos de `CARPETA_VIDEOS`, elige por nombre o "el último", y dispara `RUTA_BAT_INTERPOLAR` **en segundo plano** (sin ventana), en un hilo aparte, pasando la ruta completa del video como argumento. Al terminar **avisa por voz** (o consola) según el código de salida. Desambigua si hay varios. Respuestas honestas si falta config.
- **Bloque 13 (fix del .bat):** comparado contra el `interpolar_miku.bat` real del usuario, se corrigió el **desacople de formato de argumento** (el `.bat` ahora recibe la **RUTA COMPLETA** y se eliminó `VIDEOS_DIR` hardcodeado; `TARGET_VIDEO=%%~fV` directo) y la **extensión de 5 caracteres** (`.webm`): se usa `%%~nV` (nombre sin extensión) en vez del offset fijo `~0,-4`. Además se sacaron los `::` dentro del bloque `for` (pasados a `rem`) y los emojis (ASCII puro, sin BOM). Python **no** se tocó (ya pasaba la ruta completa). Verificado con simulación `echo` (mkv/webm/espacios/varios).
- **Bloque 14 — Traductor de juegos + traducción compartida:** nuevo `core/traduccion.py` (cliente Groq único: `traducir_con_groq` + `normalizar_para_traducir`). `text_to_speech.py` refactorizado para delegar (re-exporta `normalizar_para_traducir`). Nuevo `plugins/traductor_juegos.py` (`traducir_mensaje_juego`): resuelve la frase desde `MENSAJES_JUEGO`, la traduce a `IDIOMA_JUEGO` (cuenta principal) y la copia al **portapapeles** (`win32clipboard`). Config nueva `idioma_juego`/`mensajes_juego`.
- **Bloque 15 — Volumen por app:** `ajustar_volumen` acepta el parámetro opcional `app`: ajusta **todas las sesiones de audio** de esa app (pycaw `GetAllSessions`, matching tolerante a acentos y `.exe`); sin `app` se comporta como antes (volumen general). Base para el futuro Game Booster.
- **Bloque 11 — Wake word en una sola frase:** `core/speech_to_text.py` ahora soporta AMBOS flujos. Nuevo helper `_extraer_comando_en_linea()`: tras detectar la wake word, toma lo que vino DESPUÉS (primera variante de `_VARIANTES_MIKU`), limpia separadores sueltos y, si queda contenido significativo (>2 chars), lo usa como **comando directo** en `_bucle_escucha_permanente()` vía `on_comando(...)` **sin** segunda escucha. Si el usuario dijo solo "Miku" (o "Miku."/"Miku,"), sigue el flujo de dos pasos sin cambios. Se mantiene `on_wake()` ("¿Sí? Decime.") en ambos flujos por consistencia. Probado con 17 casos de parsing + 4 escenarios de flujo completo con STT simulado (audio real no disponible en el entorno de desarrollo).
- **Nota:** plugins `video_interpolador` y `traductor_juegos` registrados en `main.instalar_core()`. requirements.txt: pywin32 ya cubría `win32clipboard`; sin deps nuevas.

### Sesión 9 (auditoría completa y endurecimiento):
- **Diagnóstico:** revisión de núcleo, plugins, config, lanzadores y empaquetado; hallazgos verificados leyendo el código (varios reproducidos). Detalle completo en `docs/informe_auditoria.md`.
- **Seguridad:** la confirmación de acciones peligrosas usaba *substring* ("no, dejalo así" confirmaba) y no vencía → palabras completas, cancelar primero, TTL 60 s. Wake word por *substring* ("amigo", "química") → palabra completa. Lista blanca de `cerrar_programa` real (el Explorador nunca), `ruta_elegida` solo si fue ofrecida, ejecutables de `buscar_archivo` solo se muestran, validación de videos (`.bat` por cmd.exe), Telegram por id de usuario, clave de Gemini en header. `.gitignore` cubre `*.zip`/`dist`/`build`/`data/capturas`; había un `miku-assistant.zip` sin trackear con `config_local.py` adentro.
- **Núcleo:** nuevo `core/qt_hilo.py` (un solo hilo Qt: los subtítulos no funcionaban con la bandeja activa), Miku ya no se escucha a sí misma (`esperar_libre`), lock en `decir()` y `procesar()`, caché del LLM solo para charla pura, tools inventadas por el LLM ya no responden "Listo", fast-path con acentos, `cerrar()` idempotente, `bus.voice` asignado.
- **Plugins:** contrato `Plugin.peligrosas` (una sola fuente de verdad) y carga perezosa en `plugins/registro.py` (un plugin roto no tumba el arranque; `miku.spec` usa `collect_submodules`). Arreglos en tidal (SMTC), game_booster (volumen por app, snapshot con dueño), discord (estado por eventos, timeouts), telegram (`to_thread`, cierre limpio), video, browser, capturas/OCR, macros, calculadora, memoria (lock, backfill, stopwords).
- **Config:** `config.cargar()` idempotente + `recargar()`, `guardar_preferencias()` atómico con lock (favoritos, personalidad y modo), `BASE_DIR` respeta `sys.frozen`.
- **Docs/lanzadores:** README reescrito contra el código real; `run.bat`/`run.ps1` reinstalan si cambia `requirements.txt`; `pycaw>=20240210`.
- **No verificado en este entorno:** micrófono, VOICEVOX/audio, Telegram (`python-telegram-bot` no instalado), Brave por CDP, Todoist, Gemini, Game Booster con un juego real.

### Sesión 10 (reestructuración: fases A y B):
- **Decisiones del dueño:** estructura completa `miku/` (opción A); se ELIMINA push-to-talk; el modo texto queda como depuración (elegible en bandeja/ventanita); inicio con Windows opcional y F22 = invocar a Miku (si está residente, saluda y escucha; si no, hace falta un lanzador: a probar el atajo de `.lnk` o un proceso residente mínimo); traductor de juegos para CUALQUIER idioma, resultado solo al portapapeles; macro `modo_fortnite` = 1920x1440 (aplicado en `data/macros_config.json`, el driver lo acepta según `CDS_TEST`).
- **Fase A (tests):** carpeta `tests/` con pytest (119 tests, ~5 s): confirmaciones, fast-path, caché del LLM, wake word, TTS, memoria, calculadora, scheduler, config, contrato de plugins, seguridad de system_control/video y Qt offscreen. No leen `config_local.py` ni `data/`.
- **Fase B (configuración):** nuevo paquete `miku/ajustes/`: `esquema.py` (cada opción declarada UNA vez: tipo, default, descripción, plugin), `validacion.py` (typos con sugerencia, obsoletas, tipos, plugins sin configurar), `ejemplo.py` (genera `config_local.py.example` y agrega opciones faltantes a `config_local.py` comentadas, con `.bak`) y `python -m miku.ajustes estado|validar|completar|ejemplo` (los secretos nunca se imprimen). `config.py` toma sus defaults del esquema (idénticos a los anteriores; se quitaron 4 claves muertas y se sumó `voicevox_run_exe`, que VOICEVOX leía saltándose la config). `Config` registra `origenes` y `claves_locales`; `main` valida al arrancar (solo log).
- **Tu `config_local.py`:** se le agregaron 38 opciones COMENTADAS (mismas 13 variables y valores, verificado por hash); falta completar lo que uses: `GEMINI_API_KEY`, `CARPETA_CAPTURAS`, `BRAVE_RUTA_EXE`, `TIDAL_RUTA_EXE`, `CIUDAD_CLIMA`, etc.
- **Interpolación de video (prueba real):** funciona (1080p -> 47,95 fps, audio y subtítulos intactos, sin temporales). Hallazgos: el `.bat` devolvía siempre 0 aunque fallara (versión revisada en `docs/interpolacion/`, más `LEEME.md`) y el `.vpy` recorta 8 px de más (`Crop(bottom=16)` debe ser `8`; salida 1920x1072). El plugin ahora verifica que exista `<nombre>-2x.mkv`.
- **Pendiente (orden acordado con el dueño):** solo F, al final (interfaces LLM/STT/TTS, proveedor local/nube, idioma de voz y subtítulos automáticos, voces custom).
- **Fase C (mudanza a `miku/`):** `core/` y `plugins/` desaparecen; todo vive en el paquete `miku/`: `ajustes/`, `cerebro/` (parser, calculadora, `memoria/`), `voz/` (`entrada`, `salida`, `frases`), `ui/` (qt_hilo, bandeja, subtítulos, selector de modo), `servicios/` (eventos, scheduler, notificaciones, modos, briefing, personalidad) y `plugins/` por tema (`sistema`, `navegacion`, `multimedia`, `pantalla`, `productividad`, `gaming`, `social`, `asistente`). `config.py` -> `miku/ajustes/carga.py` (alias `config_mod` en los imports), `main.py` queda como shim de 3 líneas, `run.bat`/`run.ps1`/`miku.spec` -> `scripts/` (con atajos en la raíz). Sin cambios de comportamiento. Verificado: 119 tests, 135 imports `miku.*` resueltos (incluidos los perezosos), arranque del núcleo con 18 plugins / 42 tools. **Sin probar:** `scripts/miku.spec` (PyInstaller no está instalado) y la ejecución completa de `run.bat` con la ventanita.
- `data/preferences.json` se limpió de claves sin uso (`juegos`, `notas`, `programas_favoritos`; copia en `preferences.json.bak`). La clave `juegos: {"zzz": ["tidal", "discord"]}` parece una idea de perfil de juego (abrir esas apps al lanzarlo).
- **Fase D (system_control + plataforma):** nueva capa `miku/plataforma/` (`texto`, `subprocesos`, `pantalla`, `audio`, `everything`) y `miku/plugins/utiles.py` (`a_entero`, `RutasOfrecidas`, `abrir_resultado`). `system_control.py` se dividió en los plugins `programas`, `archivos`, `audio`, `energia` (incluye brillo) y `ventanas`, más `biblioteca_juegos` (Steam/Epic); las 42 tools y sus nombres no cambiaron (comprobado). El Game Booster ahora busca el plugin `audio`. Discord se apaga de forma ordenada (loop con `run_forever` + cancelación de tareas). Tests: 150+; pyflakes limpio salvo comprobaciones de disponibilidad. Commits en la rama `reestructuracion`.
- **Fase G (modo único, F22, inicio con Windows):** se eliminó el push-to-talk. Miku vive en la bandeja; modos `voz`/`texto` cambiables en caliente (`Asistente.cambiar_modo`), texto = ventana de depuración (`ui/consola.py`). `main.py` comprueba primero si ya hay una Miku (`servicios/instancia.py`, mutex + evento con nombre): si la hay, la invoca y termina. `Asistente.invocar()` (F22) hace saludar y tomar un comando sin la palabra "Miku" (`SpeechToText.invocar`). `servicios/arranque.py`: inicio con Windows (clave `Run`, argumento `--silencioso`) y atajo F22 (`.lnk` en el Menú Inicio con `Hotkey=F22`; se comprobó en este equipo que Windows lo acepta y ejecuta). El hotkey F22 interno se registra solo si NO existe el atajo (evita invocar dos veces). Menú de la bandeja con casillas reales. Opción `saludo_al_iniciar`; `modo_entrada` solo `voz`/`texto` (el viejo `push` cuenta como `voz`). Log rotativo `data/miku.log`. Prueba E2E real: arranque, invocación, cambio de modo en caliente y cierre ordenado; destapó y corrigió un bug (al reiniciar la escucha quedaba sin hilo) y precalienta VOICEVOX en segundo plano. **Ninguno de los dos mecanismos (inicio con Windows / atajo F22) está activado por defecto:** los activa el usuario desde el menú de la bandeja.
- **Fase H (motor proactivo):** `servicios/proactivo.py` (`MotorProactivo`: hilo daemon cada 10 s; reglas con intervalo propio; política = horario de silencio, no molestar en juego con 60 s de gracia por alt-tab, cooldown por clave, "una vez por día" persistido en `data/proactivo_estado.json`, presupuesto `proactivo_max_por_hora`, urgentes y `ignora_juego`; como mucho un aviso por regla y vuelta) + `servicios/reglas_proactivas.py` (`ClimaAvisos` lluvia/frío/calor, `EstadoAlJugar`, `CargaSostenida`, `GpuCaliente`, `BateriaBaja`, `DiscoLleno`). Frases: `voz/frases/banco.py` (`Banco`, rota sin repetir la anterior, datos faltantes no rompen) y `catalogo_proactivo.py` (base para la fase E). Plataforma: `openmeteo.py` (geocodificación + pronóstico con `proxima_lluvia`; lo usa también el plugin `clima`), `hardware.py`, `procesos.py` (primer plano; lo usa `game_booster`). 12 opciones nuevas `PROACTIVO_*` (esquema, `.example` regenerado, `config_local.py` del usuario completado con comentarios; copia en `config_local.py.bak`). Sin ciudad configurada la regla de clima no hace nada. 47 tests nuevos (234 en total).
- **Fase E (respuestas naturales):** `voz/frases/respuesta.py`: `Respuesta(str)` con `ok`, `intencion`, `datos`; helpers `exito()`, `falla()`, `hubo_falla()`. Las frases viven en `voz/frases/catalogo_respuestas.py` (banco con variantes que rotan, `banco.py`). Compatibilidad total: consumidores de texto (parser, LLM, TTS) no cambian; un `str` común cuenta como éxito. `tono.variar()` sigue para las frases cortas fijas ("Listo"). Tests: `tests/test_respuestas.py` (12) y dos tests viejos ahora comprueban `intencion` en vez del texto. Para pasar más mensajes al sistema: agregar la intención al catálogo y devolver `exito/falla` desde el plugin.
- **Fase I (plugins y cerebro):** traductor (`gaming/traductor.py`: `texto`+`idioma?`, atajo solo por coincidencia exacta, perfil de juego vía `plataforma/procesos`, LRU); TIDAL (`multimedia/tidal_busqueda.py` con `BuscadorTidal`, sesión OAuth en `data/tidal_sesion.json`; `tidal.py` abre `tidal://<tipo>/<id>` y usa SMTC `status` para forzar play; **sin probar contra cuenta real**); `servicios/modos.py` guarda `data/modo_snapshot.json` (vigencia 12 h) y `pantalla.resolucion_nativa()`; macros cortas; `plugins/asistente/recuerdos.py` + `Memoria.listar_recuerdos`; parser: `_historial` (deque, TTL) inyectado como `contexto["historial"]` (con historial no se usa la caché del LLM) y `enrutador.seleccionar` (la validación de tools inventadas usa el catálogo completo); Brave: cerrar por título (ambigüedad => no cierra) y navegación de la pestaña activa por websocket opcional; `servicios/scheduler.py` con `persistente=` + `recuperar()` (los perdidos se avisan en `_saludar`; el cierre usa `cancelar_todos(olvidar=False)`) y `servicios/recordatorios.py`. Opciones nuevas: `PERFILES_JUEGO`, `HISTORIAL_TURNOS`, `HISTORIAL_MINUTOS`, `ENRUTAR_TOOLS`, `ENRUTAR_MAX_TOOLS`. Tests: 393.
