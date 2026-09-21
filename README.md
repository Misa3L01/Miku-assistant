# Miku Assistant

Asistente de voz personal para **Windows** (Python 3.10+), pensado para programar,
jugar (CS, Fortnite, Genshin Impact), el uso diario y Discord. Lo controlás hablando
("Miku, abrí Brave y poné Discord a la derecha"), escribiendo o desde el celular
(Telegram). Arquitectura modular: un **núcleo** (`miku/cerebro`, `voz`, `ui`, `servicios`) y
**plugins** enchufables (`miku/plugins/`) que le publican *tools* al cerebro (un LLM en Groq).

- **Voz de entrada:** wake word "Miku" (escucha continua) o la tecla de invocación (F13). El fin de tu frase lo detecta un **VAD** (silero) y la palabra clave se puede resolver **en tu PC** (sin mandar a la nube todo lo que se habla).
- **Voz de salida:** VOICEVOX local (traduce ES→JA con Groq) con subtítulos estilo anime; cae a la voz del sistema si VOICEVOX no está.
- **Cerebro:** Groq con *function calling*, más un *fast-path* local (hora, saludos, calculadora, memoria) que no gasta API. Miku **empieza a hablar con la primera oración** mientras el modelo escribe el resto.
- **Seguridad:** las acciones peligrosas (apagar la PC, expulsar a alguien de Discord) piden un "sí" explícito.
- **Degradación elegante:** casi todo lo pesado o de terceros es opcional; si falta algo, esa función avisa y el resto sigue.

---

## Contenido

1. [Requisitos](#requisitos)
2. [Instalación](#instalación)
3. [Cómo correr](#cómo-correr)
4. [Configuración](#configuración)
5. [Arquitectura](#arquitectura)
6. [Plugins disponibles](#plugins-disponibles)
7. [Crear un plugin nuevo](#crear-un-plugin-nuevo)
8. [Seguridad y privacidad](#seguridad-y-privacidad)
9. [Empaquetado (.exe)](#empaquetado-exe)
10. [Solución de problemas](#solución-de-problemas)
11. [Límites conocidos y roadmap](#límites-conocidos-y-roadmap)

---

## Requisitos

| Necesario | Para qué |
|---|---|
| Windows 10/11 y Python 3.10+ | Todo (usa Win32, COM/pycaw, WinRT) |
| Clave de [Groq](https://console.groq.com) (`GROQ_API_KEY`) | El cerebro (LLM) y la transcripción de voz (Whisper) |
| Un micrófono | Modos de voz |

Opcionales (cada uno habilita una función; ver [Plugins](#plugins-disponibles)):
[VOICEVOX](https://voicevox.hiroshiba.jp) (voz), Everything + `es.exe` (búsqueda de archivos; ya viene en `bin/`),
Tesseract (OCR), `fastembed` (memoria semántica), `python-telegram-bot`, un bot de Discord,
clave de Gemini, token de Todoist.

---

## Instalación

1. **Entorno virtual y dependencias**
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```
   `requirements.txt` incluye lo necesario para el uso normal. Los paquetes pesados
   (PyQt5, pygame, pyaudio, pycaw, discord.py...) se importan **recién cuando se usan**:
   si falta uno, esa función avisa y el asistente sigue arrancando.
   Los opcionales (`pytesseract`, `fastembed`, `python-telegram-bot`) están comentados
   en el archivo: descomentalos si los querés.

2. **Configuración privada:** copiá `config_local.py.example` → `config_local.py` y completá
   tus datos (claves, rutas). Ese archivo **nunca se versiona** (está en `.gitignore`).
   Sin `GROQ_API_KEY` el asistente igual arranca: el fast-path local funciona y el resto avisa
   que falta la clave. Para ver qué tenés configurado y qué falta: `python -m miku.ajustes estado`
   (ver [Configuración](#configuración)).

3. **VOICEVOX (opcional):** si dejás el motor en `extern/VOICEVOX/vv-engine/run.exe` (o indicás
   `VOICEVOX_RUN_EXE`), Miku lo **arranca sola y oculto** la primera vez que necesita hablar,
   y lo cierra al salir si lo lanzó ella. Sin VOICEVOX habla con la voz del sistema (pyttsx3).

4. **Búsqueda de archivos:** `bin/es.exe` (Everything CLI) ya está en el repo. Requiere
   [Everything](https://www.voidtools.com) corriendo en segundo plano.

---

## Cómo correr

```bat
run.bat
```
o `python main.py`. `run.bat` / `run.ps1` (atajos a `scripts/`) crean el `venv` si falta, reinstalan las dependencias
cuando `requirements.txt` cambia y arrancan VOICEVOX si no está corriendo
(`run.bat sinvoicevox` / `.\run.ps1 -SinVoicevox` para saltearlo).

Miku vive **en segundo plano, en la bandeja del sistema** (el icono es su "cara"). Al abrirla a mano
aparece una **ventanita** para elegir el modo; con `--silencioso` (o al arrancar con Windows) usa el
modo guardado sin preguntar. Hay **una sola Miku a la vez**: si ya está abierta, abrirla de nuevo solo
la invoca.

| Modo | Cómo se usa |
|---|---|
| **Modo Voz** (el normal) | Escucha continua, como "OK Google": decí **"Miku"** y esperá el "¿Sí? Decime."; o todo junto: **"Miku, qué hora es"**. También podés **apretar F13** (en este equipo es la tecla Insert remapeada, ver abajo): saluda y escucha un comando sin decir "Miku". Mientras Miku habla, el micrófono espera para no oírla. Al iniciar hace un saludo corto (`SALUDO_AL_INICIAR = False` lo apaga; la hora, el clima y los pendientes te los da al volver de una ausencia, ver más abajo). |
| **Modo Texto** (depuración) | Una ventana para escribirle a Miku y ver las respuestas (habla igual). Sirve para probar sin micrófono. |

**Menú del icono de la bandeja** (clic derecho): *Invocar ahora* · *Modo voz* · *Modo texto (depuración)* ·
*Iniciar con Windows* · *Atajo de teclado para abrir Miku* · *Elegir tecla de invocación…* · *Salir*. El modo se cambia en caliente y se recuerda para la
próxima vez.

**Tecla de invocación e inicio con Windows** (los dos son opcionales y los activás desde ese menú):

- **Iniciar con Windows:** registra a Miku en el inicio del usuario. Aparece en *Administrador de tareas →
  Inicio*, donde también la podés desactivar. Arranca en segundo plano y sin ventanita.
- **F13 con Miku abierta:** la invoca (saluda y escucha).
- **Atajo de teclado para abrir Miku:** crea un acceso directo en el Menú Inicio con **la tecla de invocación (F13) como tecla de método
  abreviado**. Así esa tecla abre a Miku **aunque esté cerrada** (y no necesita el inicio con Windows). Si ya estaba abierta,
  la nueva ejecución le avisa a la primera y se cierra. Si no activás el atajo, la tecla funciona igual pero solo mientras Miku corre.
- **Insert → F13 (este equipo):** como el teclado no tiene F13, la tecla **Insert** se remapeó a F13 a nivel de Windows (registro `Scancode Map`, con `scripts/remapear_insert_a_f13.ps1`; pide administrador y **hay que reiniciar la PC**). Así el acceso directo del Menú Inicio (F13) abre a Miku aunque esté cerrada. Para volver a la Insert normal: `powershell -ExecutionPolicy Bypass -File scripts\remapear_insert_a_f13.ps1 -Restaurar` y reiniciar.
- **Otra tecla en vez de F13:** un teclado de notebook no tiene F13-F24. Con *Elegir tecla de invocación…* (menú de la bandeja) apretás la tecla que querés y Miku la detecta y la guarda (`TECLA_INVOCAR`; una tecla sin nombre queda como `sc:NN`). Funciona **solo con Miku abierta** (el atajo de Windows que la abre cerrada solo admite teclas F1-F24, con o sin modificadores). Para ver qué manda una tecla: `python -m miku.servicios.tecla`. Algunas teclas de fabricante (p. ej. la de **OMEN** en HP Victus/Omen) las consume el software del fabricante (OMEN Hub) antes que los programas: si al apretarla no aparece nada, esa tecla no se puede usar y conviene elegir otra (o reasignarla en OMEN Hub).
- Argumentos: `--silencioso` (no muestra la ventanita) y `--invocar` (al arrancar, saluda y escucha).
- Sin consola (con `pythonw`) el log queda en `data/miku.log`.
- Para salir: menú de la bandeja → *Salir* (o `Ctrl+C` si la abriste desde una consola).

---

## Configuración

`miku/ajustes/carga.py` arma la configuración con estas capas, de menor a mayor prioridad:

1. **Defaults** (los del esquema `miku/ajustes/esquema.py`, sin datos privados).
2. `data/preferences.json` — preferencias que Miku guarda sola (modo, personalidad, carpetas favoritas).
3. `config_local.py` — tus secretos y rutas (claves en MAYÚSCULAS, se pasan a minúsculas).
4. **Variables de entorno** con el mismo nombre (`GROQ_API_KEY=...`), para secretos.

**Asistente de configuración** (`python -m miku.ajustes ...`). Todas las opciones están
declaradas una sola vez, con su descripción, en `miku/ajustes/esquema.py`, y de ahí salen los
valores por defecto, el `config_local.py.example` y la validación:

| Comando | Qué hace |
|---|---|
| `estado` | Lista cada opción con su valor (las claves y tokens **se ocultan**), de dónde viene, y qué funciones quedan sin configurar |
| `validar` | Avisa de opciones mal escritas (con sugerencia: "¿quisiste decir `CARPETA_CAPTURAS`?"), obsoletas o con tipo incorrecto |
| `completar` | Agrega a **tu** `config_local.py` las opciones que no tenés, **comentadas** y con su explicación. No toca ninguno de tus valores y guarda una copia `config_local.py.bak` |
| `ejemplo` | Regenera `config_local.py.example` |

Al arrancar, Miku ejecuta la misma validación y deja los avisos en el log. En `config_local.py`,
una línea que empieza con `#` está desactivada (usa el valor por defecto): para cambiar una opción,
sacale el `#`. Las opciones principales:

| Clave | Para qué | Plugin/módulo |
|---|---|---|
| `GROQ_API_KEY`, `GROQ_API_KEY_STT` | LLM y Whisper/traducción TTS (la de STT cae a la principal si está vacía) | parser, STT, TTS |
| `VOICEVOX_URL`, `VOICEVOX_SPEAKER_ID`, `VOICEVOX_RUN_EXE` | Motor de voz y voz elegida | TTS |
| `TTS_MOTOR`, `TTS_IDIOMA`, `TTS_COMANDO`, `SUBTITULOS` | Motor de voz alternativo (voces propias / español) y subtítulos | TTS |
| `TTS_CACHE`, `TTS_CACHE_MAX` | Caché en `data/tts_cache/` con el audio de las frases ya dichas (las repetidas suenan al instante) | TTS |
| `STT_PAUSA_FIN` | Segundos de silencio que cierran tu frase (0.6 por defecto; más bajo responde antes pero puede cortarte) | STT |
| `STT_VAD`, `VAD_PROVEEDOR`, `VAD_MODELO`, `VAD_UMBRAL` | Detección de voz para saber cuándo terminaste de hablar (silero / energía) | STT |
| `WAKE_PROVEEDOR`, `WAKE_MODELO`, `WAKE_MODELO_LOCAL`, `WAKE_UMBRAL` | Dónde se detecta "Miku": en tu PC o en la nube | STT |
| `LLM_STREAMING` | Hablar con la primera oración mientras el LLM sigue escribiendo | parser |
| `CONFIRMACION_SONORA` | Un "mmm" corto apenas te escucha, mientras piensa | app |
| `VOICEVOX_GPU` | Sintetizar por GPU (necesita el paquete GPU del motor) | TTS |
| `METRICAS_LATENCIA` | Una línea por orden en el log con lo que tardó cada etapa | cerebro |
| `LLM_BASE_URL`, `LLM_MODELO`, `LLM_API_KEY`, `LLM_SOPORTA_TOOLS` | LLM local o de otro proveedor (compatible con OpenAI) | parser |
| `STT_PROVEEDOR`, `STT_MODELO_LOCAL` | Transcripción en la nube (Groq) o local (faster-whisper) | STT |
| `MICROFONO_INDEX` | Micrófono fijo (`None` = el del sistema) | STT |
| `TECLA_INVOCAR` | Tecla que te invoca (F13 por defecto) | app |
| `MODO_ENTRADA`, `LOG_LEVEL` | Modo por defecto de la consola; nivel de log | `main` |
| `MEMORIA_ACTIVA`, `EMBEDDINGS_ACTIVOS`, `EMBEDDINGS_UMBRAL` | Memoria persistente y búsqueda semántica | memoria |
| `HISTORIAL_TURNOS`, `HISTORIAL_MINUTOS`, `ENRUTAR_TOOLS`, `ENRUTAR_MAX_TOOLS` | Historial de la charla y enrutado de tools | parser |
| `APP_WHITELIST` | Apps que se pueden cerrar por voz (`explorer` **nunca** se cierra) | `programas` |
| `STEAM_RUTA`, `JUEGOS_EPIC` | Abrir juegos | `programas` |
| `CARPETA_VIDEOS`, `RUTA_BAT_INTERPOLAR` | Interpolación de video | `video_interpolador` |
| `IDIOMA_JUEGO`, `PERFILES_JUEGO`, `MENSAJES_JUEGO` | Traductor: idioma por defecto, idioma por juego y atajos (todo opcional) | `traductor_juegos` |
| `CARPETAS_FAVORITAS`, `CARPETA_CAPTURAS` | Carpetas rápidas y destino de capturas | `favoritos`, `captura` |
| `CIUDAD_CLIMA` (o `CLIMA_LAT`/`CLIMA_LON`) | Clima y briefing | `clima` |
| `PERSONALIDAD` | Estilo por defecto | `personalidad` |
| `JUEGOS_BOOSTER`, `BOOSTER_*` | Modo gaming automático | `game_booster` |
| `BRAVE_RUTA_EXE`, `BRAVE_DEBUG_PORT`, `TIDAL_RUTA_EXE` | Brave por CDP y TIDAL | `browser`, `tidal` |
| `TESSERACT_RUTA`, `OCR_IDIOMA` | OCR | `ocr` |
| `GEMINI_API_KEY`, `GEMINI_MODELO` | Visión de pantalla | `vision` |
| `TODOIST_API_TOKEN` | Tareas | `todoist` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Control remoto (el ID es **tu id de usuario**) | `telegram_control` |
| `PROACTIVO_*` | Avisos proactivos: clima, estado de la PC, batería, disco, horario de silencio | `asistente_proactivo` |
| `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID` | Bot de Discord | `discord_control` |

Macros y alias: `data/macros_config.json` (lo lee el plugin `macros`).

---

## Arquitectura

```
miku-assistant/
├── main.py                       # Punto de entrada (3 líneas): arranca miku.app
├── config_local.py               # (no versionado) tus secretos y rutas
├── config_local.py.example       # GENERADO desde el esquema (python -m miku.ajustes ejemplo)
├── requirements.txt / requirements-dev.txt
├── run.bat / run.ps1             # Atajos a scripts/ (para no romper accesos directos)
├── scripts/                      # run.bat, run.ps1 (lanzadores) y miku.spec (PyInstaller)
├── tests/                        # pytest (no tocan tus datos ni tus claves)
├── data/                         # (gitignored) preferences.json, macros_config.json, miku_memoria.db, capturas/
├── bin/es.exe                    # Everything CLI
├── extern/VOICEVOX/              # (gitignored) motor de voz local
├── docs/                         # Empaquetado (.exe) e interpolación de video
└── miku/
    ├── plataforma/               # Helpers compartidos: red (sesión HTTP con conexiones persistentes) · cdp (navegador por depuración) · texto · subprocesos · pantalla · audio · everything · openmeteo · hardware · procesos
    ├── app.py                    # Ensambla todo: Asistente, modos (voz/texto) en caliente, tecla de invocación, cierre ordenado
    ├── ajustes/                  # Configuración
    │   ├── esquema.py            #   Esquema ÚNICO de opciones (tipo, default, descripción)
    │   ├── carga.py              #   Capas defaults → preferences → config_local → entorno + guardado atómico
    │   ├── validacion.py         #   Typos con sugerencia, tipos, plugins sin configurar
    │   ├── ejemplo.py            #   Genera el .example y completa tu config_local.py
    │   └── __main__.py           #   python -m miku.ajustes estado|validar|completar|ejemplo
    ├── cerebro/
    │   ├── parser.py             #   Fast-path local, LLM (Groq) + tools, confirmaciones, desambiguación
    │   ├── calculadora.py        #   Calculadora local (sin LLM ni eval)
    │   └── memoria/              #   almacen.py (SQLite) · embeddings.py (fastembed, opcional)
    ├── voz/
    │   ├── entrada/              #   escucha.py (captura y flujo) · vad.py (cuándo terminás de hablar) · wake.py (palabra clave) · transcriptores.py
    │   ├── salida/               #   tts.py (VOICEVOX + fallback pyttsx3) · cache_audio.py (frases ya sintetizadas) · traduccion.py (Groq, compartida)
    │   └── frases/               #   tono.py (variantes de tono) · banco.py (frases con variantes que rotan) · catalogo_proactivo.py
    ├── ui/                       #   qt_hilo.py (UN hilo de Qt) · bandeja.py · subtitulos.py · selector_modo.py · consola.py (modo texto)
    ├── servicios/                #   instancia.py (una sola Miku) · arranque.py (inicio con Windows, atajo de teclado) · metricas.py (latencia por etapa) · eventos (registro de plugins y contexto compartido) · scheduler · notificaciones · modos · briefing · personalidad · proactivo (motor de avisos) · reglas_proactivas
    └── plugins/
        ├── base.py               #   Clase base Plugin (contrato documentado en el módulo)
        ├── registro.py           #   Catálogo de plugins con carga perezosa y aislada
        ├── sistema/              #   programas · archivos · audio · energia · ventanas · estado_pc (+ biblioteca_juegos)
        ├── navegacion/           #   web (búsqueda) · brave (pestañas por CDP)
        ├── multimedia/           #   tidal
        ├── pantalla/             #   captura · ocr · vision
        ├── productividad/        #   todoist · clima · favoritos · macros · video (interpolación)
        ├── gaming/               #   game_booster · traductor
        ├── social/               #   discord_bot · telegram_bot
        └── asistente/            #   personalidad · modos · proactivo
```

### Flujo de un comando

1. **Entrada:** el STT (wake word o tecla de invocación), la consola o Telegram producen un texto y llaman a `Asistente.responder()`.
2. **`CommandParser.procesar()`** (serializado con un lock: lo llaman hilos distintos) decide, en orden:
   1. ¿Hay una **confirmación** pendiente? → se resuelve (ver abajo).
   2. ¿Hay una **desambiguación** pendiente ("¿cuál de estos 3?")? → se resuelve con "el segundo", "2" o parte del nombre.
   3. **Fast-path local** (sin API): calculadora, hora, saludos, "plugins", "acordate que…".
   4. **LLM (Groq)** con las *tools* que publican los plugins. Se ejecutan **todas** las `tool_calls` del turno, en orden. Una tool inventada por el LLM no se ejecuta ni responde "Listo".
3. **Salida:** el texto pasa por `tono` (variantes) y `personalidad` (coletilla) y se habla (VOICEVOX + subtítulo por frase) o se imprime.

### Confirmaciones

Cada plugin declara en `peligrosas` qué tools exigen confirmación (`energia`: `control_energia` y `programar_accion`; `discord_control`: las tres). El parser guarda `{tool, args}`, pregunta y espera:

- **Confirma** solo con palabras completas ("sí", "dale", "ok", "confirmo"…). "No, dejalo así" **no** confirma.
- **Cancelar tiene prioridad** ("no, dale" cancela).
- **Vence a los 60 s**: un "sí" perdido más tarde no dispara nada.

### Pipeline de voz e hilos

- **Escucha** (hilo `escucha_voz`): el **VAD** (`miku/voz/entrada/vad.py`) marca dónde empieza y termina tu frase (silero distingue voz de ruido, así que la pausa puede ser corta sin que el ventilador la dispare); el **detector de palabra clave** (`wake.py`) decide si te dirigiste a Miku, en tu PC o en la nube. Antes de escuchar espera a que Miku termine de hablar.
- **Habla** (hilo `tts_player`): cola de textos; cada frase se traduce/sintetiza (con *prefetch* de la siguiente) y se subtitula en sincronía. Las frases cortas ya dichas salen de una **caché en disco** (sin traducir ni sintetizar), y el saludo del wake se precalienta al arrancar. `decir()` es seguro desde cualquier hilo.
- **Red:** las llamadas a Groq (Whisper, LLM, traducción) y a VOICEVOX van por una **sesión HTTP compartida** (`miku/plataforma/red.py`): la conexión TLS se reutiliza en vez de abrirse en cada llamada. Ante un 429/5xx pasajero del LLM se reintenta una vez.
- **Latencia:** cada orden deja una línea en el log con el reparto (`miku/servicios/metricas.py`): `latencia | escuchar 1.2 s · stt 812 ms · llm 534 ms · tts 208 ms | hasta la voz 2.8 s`. Se apaga con `METRICAS_LATENCIA = False`.
- **Qt** (hilo `miku_qt`): un único event loop para bandeja, subtítulos y ventanita.
- **Otros hilos daemon:** Discord (su propio `asyncio`), Telegram, Game Booster, asistente proactivo, timers del scheduler. Cada plugin los cierra en `cerrar()`; `Asistente.cerrar()` es idempotente.

---

### Qué hace Miku para responder rápido

| Truco | Qué evita |
|---|---|
| **Streaming del LLM** (`LLM_STREAMING`) | Esperar la respuesta entera: habla con la primera oración terminada mientras el modelo sigue. Medido contra Groq: la primera palabra pasó de 1,05 s a 0,34 s. Si el modelo pide una *tool*, no habla nada por adelantado. |
| **Caché de audio** (`TTS_CACHE`) | Traducir y sintetizar de nuevo una frase ya dicha: de 1-3,7 s a ~8 ms. |
| **Confirmación sonora** (`CONFIRMACION_SONORA`) | El silencio mientras piensa: suelta un "mmm" corto (de la caché) apenas te escuchó. Solo en órdenes habladas, y solo cuando va a consultar al LLM. |
| **Sesión HTTP compartida** | El *handshake* TLS en cada llamada: ~79 ms menos por llamada a Groq. |
| **Palabra clave local** (`WAKE_PROVEEDOR`) | Mandar a la nube **cada frase** que se oye en la habitación, solo para ver si dijiste "Miku". |
| **VAD** (`STT_VAD`) | Esperar una pausa fija larga: silero corta apenas dejás de hablar, sin confundirse con el ruido. |
| **VOICEVOX por GPU** (`VOICEVOX_GPU`) | Sintetizar en CPU (0,5-1,2 s por frase nueva). |

Para ver el reparto real de tu equipo, mirá las líneas `latencia |` en `data/miku.log`.

## Plugins disponibles

Se cargan de forma perezosa desde `miku/plugins/registro.py`; uno roto o sin dependencias se omite sin afectar al resto. ⚠️ = pide confirmación.

| Plugin | Tools | Requiere |
|---|---|---|
| `programas` | `abrir_programa`, `cerrar_programa`, `actualizar_biblioteca_juegos` | AppOpener, Steam/Epic, Everything (opcional) |
| `archivos` | `buscar_archivo` | Everything |
| `audio` | `control_multimedia`, `ajustar_volumen` (general o por `app`), `mutear_app` | pycaw |
| `energia` | `control_energia` ⚠️, `programar_accion` ⚠️, `cancelar_accion_programada`, `controlar_brillo` | — |
| `ventanas` | `listar_ventanas`, `mover_ventana`, `posicionar_ventana`, `organizar_ventanas`, `minimizar_ventana` | pywin32 |
| `web_search` | `buscar_en_web` (MercadoLibre, YouTube, Google, Wikipedia, GitHub o dominio) | — |
| `browser` | `abrir_pestana`, `listar_pestanas` (qué pestañas hay abiertas), `cerrar_pestana` (la actual o por título), `buscar_en_pestana_actual` (navega la pestaña activa; con `websocket-client`) | Brave (`BRAVE_RUTA_EXE`), CDP |
| `tidal` | `controlar_tidal`, `que_esta_sonando`, `reproducir_en_tidal` ("poné X de Y"), `volumen_tidal`, `conectar_tidal` | TIDAL / Windows SMTC; `tidalapi` (opcional) para buscar por nombre |
| `system_status` | `estado_pc` (CPU, RAM, disco, batería) | psutil |
| `favoritos` | `abrir_carpeta_favorita`, `guardar_carpeta_favorita` | — |
| `captura` | `capturar_pantalla` | Pillow |
| `ocr` | `leer_pantalla` | Tesseract (opcional; si no, OCR de Windows) |
| `vision` | `ver_pantalla` (todos los monitores o uno puntual; **envía la captura a Google o a Groq**) | `GEMINI_API_KEY` y/o `GROQ_API_KEY` |
| `portapapeles_inteligente` | `procesar_portapapeles` (traducir, corregir, reescribir, resumir, explicar o leer lo copiado), `deshacer_portapapeles` | Groq (o `LLM_BASE_URL`) |
| `comedor` | `inscribir_comedor` ("Miku, comedor": inicia sesión y te inscribe para mañana) | `COMEDOR_USUARIO` + contraseña en el Administrador de credenciales, `keyring`, `BRAVE_RUTA_EXE` |
| `whatsapp` | `enviar_a_whatsapp` (a tu contacto por defecto), `enviar_whatsapp_a_contacto` ⚠️ (a otra persona: pide confirmación), `conectar_whatsapp` | `WHATSAPP_CONTACTO_DEFAULT`, `BRAVE_RUTA_EXE` |
| `telegram_envio` | `enviar_a_telegram` (a tu chat), `enviar_a_contacto_telegram` ⚠️ (a otra persona: pide confirmación) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_CONTACTOS` |
| `clima` | `clima` (Open-Meteo, sin clave) | `CIUDAD_CLIMA` |
| `todoist` | `tareas_hoy`, `agregar_tarea`, `completar_tarea` | `TODOIST_API_TOKEN` |
| `personalidad` | `cambiar_personalidad`, `listar_personalidades` | — |
| `modos` | `salir_modo` (revierte resolución/volumen/brillo; **sobrevive a reiniciar**), `volver_resolucion_nativa` | — |
| `recuerdos` | `guardar_recuerdo`, `olvidar_recuerdo`, `listar_recuerdos`, `olvidar_conversacion` | — |
| `macros` | `listar_macros` (dice cuántas hay y nombra 5), `ejecutar_macro` | `data/macros_config.json` |
| `video_interpolador` | `interpolar_video` (lanza el `.bat` en segundo plano y avisa al terminar) | `CARPETA_VIDEOS`, `RUTA_BAT_INTERPOLAR` |
| `traductor_juegos` | `traducir_mensaje_juego` (cualquier texto a **cualquier idioma**; deja el resultado **solo en el portapapeles**) | Groq; opcional `IDIOMA_JUEGO`, `PERFILES_JUEGO`, `MENSAJES_JUEGO` |
| `discord_control` | `silenciar_usuario_discord` ⚠️, `volumen_usuario_discord` ⚠️, `expulsar_usuario_discord` ⚠️ | `DISCORD_BOT_TOKEN`, intent *Server Members* |
| `game_booster` | *(automático)* baja el volumen de apps al detectar un juego y lo restaura al salir | `JUEGOS_BOOSTER` |
| `asistente_proactivo` | *(automático)* avisos proactivos (ver abajo) | psutil, `CIUDAD_CLIMA` para el clima |
| `telegram_control` | *(automático)* comandos remotos: texto libre, `/estado`, `/pendientes` | `python-telegram-bot`, token + tu user ID |

### Proveedores intercambiables (nube / local)

Cada pieza pesada de Miku se puede cambiar desde `config_local.py`, sin tocar código:

| Pieza | Por defecto | Alternativa |
|---|---|---|
| **Cerebro (LLM)** | Groq | `LLM_BASE_URL = "http://localhost:11434/v1"` (Ollama) o `http://localhost:1234/v1` (LM Studio) + `LLM_MODELO = "qwen2.5:7b"`. Sin clave. Si el modelo no entiende *function calling*: `LLM_SOPORTA_TOOLS = False` (conversa pero no ejecuta acciones). Sirve cualquier servidor con el formato de OpenAI. |
| **Oído (STT)** | Whisper en Groq | `STT_PROVEEDOR = "local"` → faster-whisper en tu PC (`pip install faster-whisper`, modelo `STT_MODELO_LOCAL = "small"`). Sin nube; el primer uso descarga el modelo. |
| **Palabra clave** | Whisper en Groq (`auto` usa lo mejor disponible) | `WAKE_PROVEEDOR = "local"` → faster-whisper `tiny` en tu PC. `"openwakeword"` → modelo chico que detecta "Miku" **sin transcribir** (`pip install openwakeword` + `WAKE_MODELO` con un `.onnx` entrenado para "Miku": openWakeWord no trae uno). Con openWakeWord no hay texto, así que "Miku, qué hora es" pasa a ser dos pasos. |
| **Fin de frase** | silero-vad si está el modelo, si no energía | `python -m miku.voz.entrada.vad descargar` baja el modelo (2 MB, corre sobre onnxruntime, **no** necesita torch). `VAD_PROVEEDOR = "energia"` vuelve al detector simple. |
| **Voz (TTS)** | VOICEVOX (japonés; Miku traduce lo que dice) | `TTS_MOTOR = "sistema"` (voz de Windows en español) o `"comando"`: tu propio motor. |

**Voz propia / otro idioma.** Con `TTS_MOTOR = "comando"` Miku ejecuta *tu* comando, que tiene que escribir un WAV en `{salida}` (el texto entra por stdin, o usá `{texto}`). Sirve para Piper, XTTS, GPT-SoVITS, RVC… Ejemplo con Piper en español: `TTS_COMANDO = r"piper --model C:\voces\es_AR.onnx --output_file {salida}"`, `TTS_IDIOMA = "es"`. Si `TTS_IDIOMA` no es `es` (por ejemplo `pt`), Miku traduce antes de hablar. Se ejecuta **sin shell**, con tiempo máximo, y si falla habla con la voz de Windows.

**Subtítulos.** `SUBTITULOS = "auto"` (por defecto) los muestra **solo cuando Miku no habla en español** (con VOICEVOX, para que leas lo que dice en japonés); `"siempre"` o `"nunca"` para forzarlos.

> Ninguna de las alternativas locales se probó con el motor real desde el desarrollo (no hay modelos instalados en el entorno de trabajo): están cubiertas con tests que simulan cada proveedor, y el comando externo sí se probó con un programa de verdad.

### Avisos proactivos

Un motor (`miku/servicios/proactivo.py`) revisa en segundo plano y Miku avisa **sin que se lo pidas**, con toast y voz. Cada aviso tiene varias frases que rotan (no repite la anterior) y pasa por una política para no molestar:

| Aviso | Cuándo |
|---|---|
| **Clima** | Va a llover en las próximas 6 h (≥60 %), está lloviendo, sensación térmica ≤12 °C (frío) o ≥32 °C (calor). **Como mucho una vez por día por condición**, y se recuerda aunque reinicies Miku. Necesita `CIUDAD_CLIMA` (o `CLIMA_LAT`/`CLIMA_LON`). |
| **Estado al jugar** | ~45 s después de que arranca un juego de `JUEGOS_BOOSTER`: "todo tranquilo, CPU al 35 %, RAM…, GPU al 70 % a 65 grados", o una advertencia si va exigida. |
| **Carga sostenida** | CPU o RAM ≥92 % durante ~5 minutos (no por picos). |
| **GPU caliente** | GPU NVIDIA por encima de 85 °C (avisa incluso jugando; usa `nvidia-smi`). |
| **Batería / disco** | Batería baja sin cargador; poco espacio libre en el disco del sistema. |

Política: **horario de silencio** (23:00–08:00 por defecto), **no molestar mientras jugás** (salvo estado al empezar y GPU caliente), cooldown por aviso y **máximo 4 por hora**. Todo se ajusta con las opciones `PROACTIVO_*` (`python -m miku.ajustes estado` las lista) y se apaga con `PROACTIVO_ACTIVO = False`.

Detalles que conviene saber:

- **`abrir_programa`** busca en: alias/AppOpener → biblioteca de **Steam** → **Epic** (`JUEGOS_EPIC`) → ejecutable por nombre con **Everything**; si hay varios candidatos, **pregunta cuál**.
- **`buscar_archivo`** nunca lanza ejecutables (`.exe`, `.bat`, `.ps1`…): los muestra en su carpeta.
- **`cerrar_programa`** solo cierra apps de `APP_WHITELIST` (coincidencia exacta) y nunca el Explorador.
- **`programar_accion`** acepta minutos o una hora (`HH:MM`); los recordatorios se hablan por voz. Los recordatorios sobreviven a cerrar Miku; los apagados y suspensiones programados no (ver *Recordatorios* más abajo).
- **Discord:** un bot **no** puede cambiar el volumen de otro usuario (límite de la API), por eso `volumen_usuario_discord` es mute/deafen. El rol del bot debe estar por encima del usuario objetivo.
- **TIDAL:** no tiene API pública de reproducción, y el enlace `tidal://track/<id>` **solo abre la ficha del tema, no lo reproduce**. Por eso, para **"poné X de Y"** Miku maneja la app de escritorio (que es Electron) por su **puerto de depuración**, como hace con Brave: busca el tema en tu cuenta (`pip install tidalapi`; decí *"conectá TIDAL"* una sola vez, aprobás en el navegador y la sesión queda guardada en `data/tidal_sesion.json`, que no se sube a git y **se mantiene entre reinicios**), abre `https://desktop.tidal.com/track/<id>` y aprieta el play. Necesita `TIDAL_RUTA_EXE` y `websocket-client`. Si TIDAL estaba abierto sin el puerto (`TIDAL_PUERTO_CONTROL`, 9223), **la primera vez lo cierra y lo abre de nuevo con el puerto** (~15 s); si no estaba abierto, lo abre ella. Con el puerto activo, pausa/siguiente/anterior, aleatorio y "qué suena" también van directo a TIDAL (las teclas multimedia van al último reproductor que sonó, que puede ser el navegador). Si TIDAL cambia su interfaz web y el botón deja de encontrarse, Miku lo dice en vez de fingir. Los selectores son los de TIDAL 2.43.
- **TIDAL: qué se le puede pedir.** *"Poné Show de Ado"* (un tema); *"poné mi playlist de anime en aleatorio"* (busca primero **tus** playlists por nombre y después las públicas; "animes" encuentra "Anime"); *"poné canciones de Soda Stereo"* (una mezcla en **aleatorio** de sus temas, no un álbum entero; con *"en orden"* no se mezcla); *"poné el álbum Thriller"*; *"poné X y que siga aleatorio"* (pone el tema y deja activado el aleatorio del reproductor, así lo que sigue es al azar); *"activá / sacá el aleatorio"*.
- **TIDAL: volumen.** *"Subí el volumen de la música"* / *"poné la música al 40"* mueve el **volumen interno de TIDAL** (Ctrl+flechas, de a 10 puntos; lee el nivel del log del reproductor), sin tocar el volumen general de la PC.
- **Visión de pantalla.** `ver_pantalla` mira todos los monitores o uno (*"qué estoy viendo en el monitor 2"*). Prueba Gemini y, si falla por clave, créditos o límite, sigue con un modelo de Groq que también ve imágenes (`VISION_PROVEEDOR = auto`). Google retiró `gemini-2.0-flash`: el modelo por defecto ahora es el alias `gemini-flash-latest`, que siempre apunta al vigente. La captura sale de tu PC hacia Google o Groq.
- **Enviar archivos por Telegram.** *"Mandame la última captura al Telegram"*, *"pasame mi última descarga"*, *"enviame informe.pdf"* → llegan a tu chat (imágenes como foto, el resto como archivo, hasta 50 MB). A otra persona (`TELEGRAM_CONTACTOS = {"juan": "123456789"}`, quien tiene que haber iniciado tu bot) **pide confirmación**.
- **WhatsApp.** *"Mandame la última captura al WhatsApp"*, *"mandame por WhatsApp que compre leche"*, *"mandale a Mati que llego tarde"*, *"mandá al grupo Familia que confirmo"* (a otra persona o grupo: pide confirmación). Miku **busca el chat por nombre** en la lupa de tu WhatsApp, igual que vos: si el nombre coincide con uno solo lo abre; si hay varios que empatan ("Mati" -> Mati Rojas y Mati Gómez) **no manda nada** y te dice cuáles son para que repitas con el nombre completo; si no existe, lo avisa. `WHATSAPP_CONTACTOS` (nombre -> número) es opcional y sirve para fijar un número exacto. Cuando lo que dice el aviso es "ya salió por WhatsApp a Mati Rojas", ese es el chat real al que llegó. Usa **WhatsApp Web en un Brave aparte** (perfil propio en `data/navegador_whatsapp/`; tu Brave de siempre no se toca, y por eso **no hace falta** tener sesión en tu perfil normal). El QR se escanea **una sola vez** (`venv\Scripts\python.exe -m miku.plugins.social.whatsapp conectar`): la cuenta que queda vinculada es la del teléfono que escanea, y de ese número salen los mensajes. `WHATSAPP_CONTACTO_DEFAULT` es el destinatario cuando no decís a quién. Manda texto y archivos (los archivos se "pegan" en el chat y se envían desde la vista previa; hasta 16 MB; Miku espera a que termine de subirse antes de cerrar el navegador). Para elegir el chat por nombre el navegador va **sin ventana** (con la ventana minimizada o tapada WhatsApp no llena la lista de resultados); `venv\Scripts\python.exe -m miku.plugins.social.whatsapp buscar <nombre>` abre el chat y dice cuál encontró, sin mandar nada. **Estos Brave aparte (WhatsApp y comedor) nunca restauran pestañas:** antes de abrirlos se borran las pestañas guardadas de su perfil y, al empezar, se cierran las sobrantes (con dos WhatsApp Web abiertos a la vez ninguno carga); el resto del perfil, o sea la sesión de WhatsApp, no se toca. WhatsApp no ofrece API para cuentas personales: esto maneja su página web y, si WhatsApp la cambia, puede dejar de encontrar un botón (Miku lo dice; `... whatsapp explorar` ayuda a ajustarlo).
- **Portapapeles inteligente.** Copiás un texto y le pedís a Miku: *"traducí lo que copié al inglés"*, *"corregí lo que copié"* o *"reescribilo más formal"* (el resultado **queda en el portapapeles**: pegás con Ctrl+V), *"resumí lo que copié"* / *"explicame lo que copié"* (te lo dice en voz alta y no toca el portapapeles, salvo que pidas copiarlo) o *"leeme lo que copié"*. Antes de reemplazar guarda el original: *"deshacé lo del portapapeles"* lo devuelve. Solo texto (si hay una imagen o archivos copiados, te lo dice); se mandan hasta 12 mil caracteres al modelo, así que no copies claves ni datos que no quieras enviar.
- **Comedor de la facultad.** *"Miku, comedor"* abre una ventana de Brave **aparte** (perfil propio en `data/navegador_miku/`; tu Brave normal no se toca), inicia sesión (la página no guarda sesiones, así que se hace cada vez), entra a *Autogestión > Inscripciones*, mira si hay comida para **mañana** y, si la inscripción está habilitada, te inscribe; después te cuenta cómo salió y te manda la captura por Telegram. Puesta en marcha: (1) `COMEDOR_USUARIO = "tu_usuario"` en `config_local.py`; (2) `python -m miku.plugins.productividad.comedor guardar-clave` (la contraseña queda en el Administrador de credenciales de Windows, **no** en un archivo); (3) `python -m miku.plugins.productividad.comedor explorar` la primera vez, con comida disponible para mañana: vuelca la estructura de la página a `data/comedor_exploracion.json` para ajustar la lectura; (4) `... comedor probar`: hace todo menos apretar "inscribirse". La página habilita la inscripción a las 14:00 del día anterior y Miku lo respeta (antes de esa hora te dice cuándo se abre). Se inscribe a los tipos de `COMEDOR_TIPOS` (por defecto `["almuerzo"]`). Con `COMEDOR_HORA = "19:00"` te avisa cada tarde (de domingo a jueves, cuando mañana es día de semana) y con `COMEDOR_AUTO = True` te inscribe sola a esa hora, **solo si estás usando la PC y no jugando**. Con `COMEDOR_AUTO = True` reintenta hasta 3 veces por día, con media hora de por medio (por si la comida se carga tarde), y no reintenta si el problema es tu usuario o contraseña. Si la página muestra un paro, un botón deshabilitado o algo que no reconoce, no inscribe a ciegas: te lo dice. La página pide **confirmar** (apretás "Inscribirse" en la fila y otra vez "Inscribirse" en "¿Inscribirse? [Inscribirse] [Cancelar]"): Miku aprieta las dos y recién después relee la página para confirmar. La ventana del trámite se elige con `COMEDOR_VENTANA = "normal" | "minimizada" | "oculta"`; en los tres casos es un Brave **aparte** que se cierra solo al terminar (no se puede usar tu Brave de siempre: desde Chromium 136 el puerto de control se ignora en el perfil por defecto, y la página no guarda sesiones así que tu perfil no aportaría nada).
- **Juegos con lanzador (Genshin, Zenless…).** Con `JUEGOS_LANZADOR` Miku abre el lanzador (HoYoPlay), espera a que esté abierto —tenés tiempo de aceptar el aviso de administrador— y después abre el juego (otro aviso de administrador). Windows no permite saltear ese aviso.
- **Órdenes en cadena.** *"Llevá Brave al monitor 1, abrí Discord y llevalo al monitor 2"* funciona: todas las herramientas de una frase se ejecutan en orden, y si una ventana es de algo que Miku acaba de abrir, **la espera** (hasta 12 s) en vez de decir que no la ve.
- **Saludo y resumen.** Al arrancar Miku hace un saludo corto (sin hora ni clima; `BRIEFING_AL_INICIAR = True` lo restaura). Cuando **volvés de una ausencia** (`BRIEFING_AFK_MIN`, 30 min sin teclado ni mouse) te da hora, clima y pendientes, como mucho cada `BRIEFING_COOLDOWN_H` (4 h).
- **Apps Electron y VS Code:** si abrís Miku desde una terminal de VS Code, hereda `ELECTRON_RUN_AS_NODE=1`, que hace que apps como TIDAL o Discord mueran al instante sin abrir ventana. Miku quita esa variable al arrancar.
- **Traductor:** *"traducí 'buena suerte' al portugués"* / *"decí gracias en japonés"* / *"mandá gg"* → traduce con Groq y lo deja en el portapapeles para pegar; no escribe ni envía nada. Idioma: el que digas > el perfil del juego en primer plano (`PERFILES_JUEGO = {"cs2": "portugués"}`) > `IDIOMA_JUEGO`. Un atajo (`MENSAJES_JUEGO`) solo cuenta si lo decís exacto.
- **Historial y enrutado (cerebro):** Miku recuerda los últimos `HISTORIAL_TURNOS` (4) turnos de los últimos `HISTORIAL_MINUTOS` (10) para entender *"y mañana?"*; *"empecemos de nuevo"* lo borra (los recuerdos guardados no se tocan). Al LLM no se le mandan las ~45 tools en cada consulta sino las **relacionadas** con lo que dijiste (`miku/cerebro/enrutador.py`: raíces de palabras + sinónimos; si nada se relaciona claramente, manda todas). Se apaga con `ENRUTAR_TOOLS = False`.
- **Recordatorios:** los que programás con *"recordame en 10 minutos…"* **sobreviven a cerrar Miku** (`data/recordatorios.json`); si vencen con Miku cerrada, te los avisa al abrir (hasta 24 h después). Apagados y suspensiones programados **nunca** se guardan: no se disparan en otra sesión.
- **Telegram:** solo responde al **usuario** autorizado (no al chat/grupo) y no puede prender la PC.

---

## Crear un plugin nuevo

1. Creá `miku/plugins/<tema>/mi_plugin.py` (p. ej. `miku/plugins/productividad/mi_plugin.py`):

   ```python
   from typing import Any, Dict
   from miku.plugins.base import Plugin

   class MiPlugin(Plugin):
       nombre = "mi_plugin"
       descripcion = "Qué hace, en una línea."
       peligrosas = frozenset()          # nombres de tools que piden confirmación

       tools = [{
           "type": "function",
           "function": {
               "name": "saludar",
               "description": "Saluda a alguien por su nombre.",
               "parameters": {
                   "type": "object",
                   "properties": {"nombre": {"type": "string"}},
                   "required": ["nombre"],
               },
           },
       }]

       def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
           if nombre_tool == "saludar":
               return f"Hola {args.get('nombre', '')}."
           return None                    # None = "esta tool no es mía"

       def cerrar(self) -> None:          # opcional: liberar hilos/sockets
           ...
   ```

2. Sumá una línea a `_CATALOGO` en `miku/plugins/registro.py` (`_Entrada("productividad.mi_plugin", "MiPlugin")`) (con una condición de config si no tiene sentido sin credenciales).

Reglas del contrato (detalle en el docstring de `miku/plugins/base.py`):

- `manejar_tool` devuelve un **texto** (lo que Miku dice), `None` si la tool no es suya, o un dict `{"desambiguar": True, "tool_origen": ..., "args_origen": ..., "opciones": [{"indice", "etiqueta", "valor"}]}` para preguntarle al usuario cuál elegir.
- **Respuestas naturales:** en vez de texto fijo, devolvé `falla("ventana.no_encontrada", app=nombre)` / `exito("audio.volumen", pct=30)` (`miku/voz/frases/respuesta.py`). La frase sale de un **banco con varias variantes** (`catalogo_respuestas.py`) que rota sin repetir la anterior. Es un `str` normal, pero además lleva `ok` / `intencion` / `datos`: **nunca hagas `res.startswith("No encontré")`**, usá `hubo_falla(res)`. Devolver un `str` común sigue siendo válido. Regla de honestidad: "no lo encuentro" ≠ "no está instalado" (un test lo vigila).
- `contexto` trae `cfg`, `voice` (puede ser `None`) y `scheduler`. Los plugins que avisan por su cuenta usan `event_bus.voice`.
- Imports pesados **dentro** de funciones. Leé la config con `config.config` (no recargues: `config.cargar()` es idempotente) y guardá preferencias con `config.config.guardar_preferencias({...})` (escritura atómica).
- Los nombres de tool deben ser únicos; si se repiten, el parser ignora la segunda y lo avisa.

---

## Seguridad y privacidad

- **Secretos:** claves y tokens viven solo en `config_local.py` o variables de entorno (ambos fuera del repo). ⚠️ No subas archivos `.zip` del proyecto: pueden incluir `config_local.py` y `data/` (`.gitignore` ya los excluye).
- Ningún comando usa `shell=True`; no hay `eval`/`exec` (la calculadora es un parser propio).
- `cerrar_programa` con lista blanca exacta; acciones peligrosas con confirmación vigente 60 s.
- `interpolar_video` valida que el video esté dentro de `CARPETA_VIDEOS` y rechaza nombres con `& % ^ ! ( ) | < > "` (el `.bat` corre por `cmd.exe`).
- **Privacidad:** el audio y los textos van a Groq (transcripción, LLM, traducción); `ver_pantalla` envía la captura a Google (Gemini); Open-Meteo recibe la ciudad. La clave de Gemini viaja en un *header*, no en la URL.

---

## Desarrollo y tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                      # más de 750 tests, ~40 segundos
python -m ruff check .                # lint: imports/variables sin usar, nombres indefinidos, trampas comunes
```

Los tests **no leen** tu `config_local.py` ni tu carpeta `data/`: usan una configuración de fábrica
y directorios temporales (y Qt en modo *offscreen*), así que corren en cualquier PC. Cubren
confirmaciones, wake word, caché del LLM, memoria, calculadora, contrato de plugins, validaciones de
seguridad y la configuración. Si cambiás `miku/ajustes/esquema.py`, regenerá el ejemplo con
`python -m miku.ajustes ejemplo` (un test lo comprueba).

---

## Empaquetado (.exe)

`scripts/miku.spec` + `requirements-dev.txt` arman un ejecutable con PyInstaller (`pyinstaller --clean scripts/miku.spec` → `dist/Miku/Miku.exe`; sin probar desde la reestructuración). En el ejecutable, `config_local.py`, `data/` y `bin/` se leen **junto al `.exe`**. VOICEVOX no se incluye. Detalles y hooks en [docs/empaquetado.md](docs/empaquetado.md).

---

## Solución de problemas

| Síntoma | Causa probable |
|---|---|
| "No tengo mi clave de acceso configurada" | Falta `GROQ_API_KEY` en `config_local.py` o el entorno |
| Habla con voz robótica | VOICEVOX no responde (mirá los logs `[VOICEVOX]`); se reintenta a los 30 s |
| No oye / "no se pudo abrir el micrófono" | Probá `MICROFONO_INDEX` (la lista aparece al iniciar el modo voz) |
| Se activa sola con conversaciones | Bajá el ruido ambiente; la wake word ya exige la palabra "Miku" completa |
| Sin bandeja ni subtítulos | Falta PyQt5 (`pip install PyQt5`) |
| "El bot de Discord todavía no está conectado" | Tarda unos segundos en conectar; revisá el token y el intent *Server Members* |
| El volumen por app no anda | `pycaw>=20240210` y que la app esté reproduciendo audio |
| Sube la búsqueda pero no encuentra archivos | Everything tiene que estar abierto (`es.exe` le consulta) |

Subí el nivel de log con `LOG_LEVEL = "DEBUG"` en `config_local.py`.

---

## Límites conocidos y roadmap

**Límites actuales (por diseño o pendientes):**

- Miku siempre habla, también en modo texto (necesita VOICEVOX o la voz del sistema). Sin PyQt5 no hay bandeja ni ventana: el modo texto cae a una consola.
- La wake word usa Whisper por API **salvo que la pongas en local** (`WAKE_PROVEEDOR`): por defecto, cada frase de la escucha continua es una llamada (se filtran ruidos cortos, pero consume cuota).
- La síntesis de VOICEVOX en CPU tarda ~0,5-1 s por frase nueva (las repetidas salen de la caché de audio).
- Telegram/Discord/Todoist/Tidal/Brave dependen de servicios externos y no tienen pruebas automáticas.

**Ideas pendientes:** `traducir_a_canal` de Discord, pausar Wallpaper Engine y temperatura en el Game Booster, OCR de regiones/ventanas puntuales, motores de voz alternativos y auto-actualización del `.exe`.

---

## Documentación adicional

- [docs/empaquetado.md](docs/empaquetado.md): construir el `.exe` con PyInstaller.
- [docs/roadmap.md](docs/roadmap.md): ideas evaluadas y todavía no implementadas, con el porqué.
- [docs/interpolacion/LEEME.md](docs/interpolacion/LEEME.md): el `.bat` de interpolación de video.
- `python -m miku.ajustes estado`: qué tenés configurado y qué falta (las opciones están documentadas en `miku/ajustes/esquema.py`).
