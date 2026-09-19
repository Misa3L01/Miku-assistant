# Informe de auditoría y endurecimiento (Sesión 9)

Revisión completa del código (núcleo, plugins, configuración, lanzadores y empaquetado) y
corrección de los problemas que podían fallar en runtime. Cada hallazgo se verificó leyendo el
código; varios se reprodujeron ejecutando Python. Las correcciones se aplicaron en cambios
chicos, sin cambiar nombres de tools ni firmas públicas.

## Estado antes de la auditoría

- `py_compile` pasaba en todo; sin imports rotos, sin `eval`/`shell=True`; todas las claves de
  config leídas existían en `config.py`.
- Los cambios de las sesiones 4–8 estaban **sin commitear**.
- `miku-assistant.zip` (raíz, sin trackear) contenía `config_local.py` (claves de Groq y token de
  Discord), `data/preferences.json` y la base de memoria, y `.gitignore` no lo excluía.

## Corregido

### Seguridad y confirmaciones
| Problema | Cambio |
|---|---|
| La confirmación usaba *substring*: "no, dejalo **así**" contenía "si" y **confirmaba** (apagar la PC, expulsar de Discord). Además no vencía nunca. | Palabras completas, cancelar con prioridad, vigencia de 60 s (`core/command_parser.py`). |
| Wake word por *substring*: "mi **amigo** dijo…", "**conmigo**", "quí**mica**" activaban a Miku y ejecutaban lo que seguía. | Palabra completa (`\b`), variantes acentuadas (`core/speech_to_text.py`). |
| `cerrar_programa`: la lista blanca era decorativa (quitar una app de `app_whitelist` no impedía cerrarla) y permitía `explorer`. | Lista efectiva = config; el Explorador nunca se cierra; se comprueba el código de `taskkill`. |
| `abrir_programa("")` abría Brave; "Edge of Eternity" abría Edge (coincidencia por substring). | Coincidencia por palabras. |
| `ruta_elegida` (no está en el schema) abría cualquier ruta enviada por el LLM. `buscar_archivo` lanzaba `.exe/.bat/.ps1`. | Solo se aceptan rutas que Miku ofreció; los ejecutables de búsqueda solo se muestran en su carpeta. |
| `interpolar_video`: el `.bat` corre por `cmd.exe` (`AT&T.mp4` ejecutaba `T.mp4`); la ruta podía venir del LLM; dos pipelines en paralelo. | Validación de carpeta/extensión/caracteres y un pipeline a la vez. |
| Telegram autorizaba por id de **chat** (en un grupo pasaba cualquier miembro). | Se autoriza por id de **usuario**. |
| La clave de Gemini iba en la URL y podía quedar en los logs. | Va en el header `x-goog-api-key`. |
| `.gitignore` no cubría `*.zip`, `dist/`, `build/`, `data/capturas/`. | Agregados. |

### Pipeline de voz y núcleo
- **Subtítulos rotos con la bandeja activa** (reproducido): dos hilos, dos `exec_()` de Qt; el segundo devolvía -1. → nuevo `core/qt_hilo.py` (un solo hilo Qt); `subtitles`, `bandeja` y la ventanita lo comparten. Excepciones en slots protegidas (PyQt5 hace `qFatal`).
- Miku **se oía a sí misma** ("¿Sí? Decime." se grababa como comando). → `TextoAVoz.esperar_libre()` (la anterior `esperar_hablando` estaba invertida) y hook `stt.esperar_silencio`.
- `decir()` podía crear dos hilos reproductores. → lock y contador de pendientes.
- El comando dicho junto a "Miku" se cortaba a 4 s. → 8 s. Ruidos <0,4 s ya no gastan una llamada a Whisper; se descartan las "alucinaciones" típicas de Whisper.
- El hilo de escucha moría en silencio si fallaba el micrófono. → `try/except` + aviso por `on_error`.
- Carrera del flag de push-to-talk. → un `Event` por captura.
- Fallback de audio pronunciaba "[audio no reproducido]". → dice la frase original. VOICEVOX caído: se sondea cada 30 s, no en cada frase.
- `cerrar()` se ejecutaba dos veces en push-to-talk; "Salir" de la bandeja no despertaba un `input()`. → idempotente + cierre forzado a los 6 s.
- `bus.voice` nunca se asignaba: los avisos por voz de `asistente_proactivo` y `game_booster` no sonaban.

### Parser / LLM
- La caché devolvía el texto **sin ejecutar la tool** ("abrí Discord" repetido). → solo se cachea charla pura; la clave incluye personalidad y día.
- Tool inventada por el LLM → "¡Listo!". → mensaje honesto.
- Fast-path: "¿Qué hora es?" (acentos/signos) no coincidía; "recordame que X" guardaba "me que X"; el día salía en inglés. → normalización y regex.
- `procesar()` sin lock con estado compartido entre hilos (voz, F22, Telegram). → `RLock`.

### Plugins
- `tidal.que_esta_sonando` roto en PowerShell 5.1 (`GetAwaiter`) y respondía "no suena nada". → patrón AsTask; probado contra una sesión real.
- `game_booster`: no restauraba el volumen por app, consumía el snapshot de una macro y tenía una carrera en `cerrar()`. → snapshot con dueño, volúmenes por app guardados/restaurados, lock.
- `discord_control`: `initialize` bloqueaba 12 s y una conexión tardía quedaba "no conectada" para siempre; el `except asyncio.TimeoutError` era código muerto en Python 3.10 (reintento duplicaba un kick); pedía el intent `message_content` sin usarlo. → estado por eventos, timeouts correctos con mensaje honesto, intent quitado.
- `telegram_control`: el parser bloqueante corría dentro del event loop; `cerrar()` frenaba el loop a la fuerza; sin scheduler en el contexto remoto. → `to_thread`, `stop_running` + `join`, contexto compartido.
- `browser.cerrar_pestana` cerraba la pestaña más antigua; capturas/OCR de monitor secundario salían negras; macros sensibles a mayúsculas; `es.exe -s` recortaba por ruta en vez de por nombre; toasts "exitosos" aunque PowerShell fallara.
- Calculadora: "5 por ciento de 200" daba 1000 (descartaba palabras); "1.500" se leía 1,5. → rechaza lo que no entiende; miles a la argentina.
- Memoria: conexión SQLite sin lock; recuerdos sin vector invisibles; `olvidar` casi siempre respondía "varios"; `_` como comodín de LIKE. → lock, backfill, stopwords, `ESCAPE`.
- Otros: `tono` (clave "¿…?" nunca coincidía), scheduler (`inf` dejaba una tarea zombi), caché de traducciones sin tope, `int()` de argumentos del LLM sin proteger.

### Consistencia de plugins, configuración y rendimiento
- **Contrato `Plugin`** documentado y con `peligrosas` (el parser ya no tiene listas duplicadas de tools peligrosas); nombres de tool repetidos se detectan.
- **Carga perezosa** (`plugins/registro.py`): un plugin roto ya no tumba el arranque; Telegram, Game Booster y asistente proactivo no se importan si no están configurados. `miku.spec` incluye los plugins con `collect_submodules`.
- `config.cargar()` es idempotente (8 sitios releían `preferences.json` y pisaban cambios en runtime); `config.guardar_preferencias()` escribe de forma atómica y con lock (favoritos, personalidad y modo antes escribían sin coordinación); `BASE_DIR` respeta `sys.frozen`.
- `run.bat`/`run.ps1` reinstalan dependencias si cambia `requirements.txt`; `pycaw>=20240210`.

### Limpieza
Código muerto eliminado (`preguntar_submodo_texto`, ramas `con_voz`, `capturar_comando`/`capturar_una_vez_sync`, `_mostrar_subtitulos`, `_balloon_ctypes`, `_EXT_NATIVAS_ES`, `_DESACTIVADA`, ramas de `dividir_pantalla`/`acoplar_ventanas` en `manejar_tool`, config sin uso `modelo_miku`/`tidal_debug_port`, imports sin usar). Docstrings agregados en subtítulos, bandeja, discord, STT/TTS y el contrato de plugins.

## No se cambió (decisiones abiertas)

| Tema | Detalle |
|---|---|
| Push-to-talk inalcanzable con PyQt5 | La ventanita solo ofrece Voz/Texto; push queda en el menú de consola. Sugerencia: tercer botón o `MIKU_MODO`. |
| Recordatorios | `programar_accion` pide confirmación también para un recordatorio simple; no sobreviven a reiniciar. |
| `web_search` con dominio | Descarta el término (decisión documentada en el código). |
| `tidal` "pausá"/"play" | Ambos son el mismo toggle multimedia. |
| `vision` | Envía la captura a Google sin confirmación cuando el LLM lo decide. |
| Discord | Coincidencia por substring de un solo miembro ejecuta el kick tras confirmar; el cache de miembros puede estar incompleto justo tras `on_ready`. |
| COM/pycaw en hilos | Los agentes discreparon; se comprobó que funciona con pycaw 20251023/comtypes 1.4.16, no se agregó `CoInitialize`. |
| APIs externas | Gemini `gemini-2.0-flash` y Todoist REST v2 pueden estar deprecados: **no verificado** (revisar). |

## Verificación

- `python -m compileall` de todo el proyecto; `import main`; arranque del núcleo con el registro perezoso (18 plugins, 42 tools, sin duplicadas, 5 peligrosas); `cerrar()` dos veces sin error.
- Pruebas de humo en scratch (no versionadas): confirmaciones (incluye "no, dejalo así", TTL y prioridad de cancelar), caché del LLM con `requests` simulado, `procesar()` concurrente, wake word (8 frases), bucle STT simulado, TTS concurrente, memoria bajo 4 hilos, calculadora (14 casos), validación de rutas de video, alias y lista blanca, bandeja + subtítulos juntos con Qt *offscreen*, SMTC real de TIDAL, OCR de Windows.
- **No probado** (sin hardware/servicios en el entorno de revisión): micrófono real, VOICEVOX/audio, Telegram (`python-telegram-bot` no está instalado), Brave por CDP, Todoist, Gemini y Game Booster con un juego real.
