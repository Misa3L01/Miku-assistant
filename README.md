# Miku Assistant

Asistente de voz personal para Windows (Python 3.10+), pensado para programación,
gaming (CS:GO, Fortnite, Genshin Impact), uso diario y comunicación en Discord.
Arquitectura modular: un núcleo (`core/`) + plugins enchufables (`plugins/`).

> **Estado actual:** base funcional operativa — control de sistema, voz (VOICEVOX),
> confirmación de acciones peligrosas, push-to-talk real, búsqueda de archivos y
> control multimedia/volumen (**general y por app**), **acciones diferidas programadas
> (scheduler)**, **búsqueda web**, **variantes de tono**, **subtítulos sincronizados por
> frase**, **memoria persistente (SQLite)**, **macros personalizadas**, **interpolación
> de video**, **traductor para juegos** y **control de Discord (bot)**. Quedan pendientes
> los plugins "grandes" heredados del sistema anterior (Game Booster, navegador por CDP)
> — el framework de plugins (`Plugin` + `tools`) ya está listo para sumarlos.

---

## Estructura de carpetas

```
miku-assistant/
├── main.py                     # Punto de entrada (orquesta todo el asistente)
├── config.py                   # Carga de configuración (defaults + prefs + config_local + entorno)
├── config_local.py             # (NO versionado) secretos y rutas privadas de tu PC
├── config_local.py.example     # Plantilla versionada de config_local.py, sin datos reales
├── requirements.txt            # Dependencias agrupadas por función
│
├── core/                        # Núcleo reutilizable
│   ├── __init__.py
│   ├── event_bus.py             # Bus de eventos + registro de plugins
│   ├── speech_to_text.py        # STT: wake word "Miku" + push-to-talk real, vía Groq/Whisper
│   ├── text_to_speech.py        # TTS: VOICEVOX (auto-arranque) + traducción ES→JA (Groq) + pygame, fallback pyttsx3
│   ├── command_parser.py        # "Cerebro": fast path local + LLM (tools) + confirmaciones persistentes
│   ├── subtitles.py             # Overlay de subtítulos estilo anime (PyQt5)
│   ├── scheduler.py             # Programador de acciones diferidas (timers) + cancelación
│   ├── tono.py                  # Variantes de tono para respuestas cortas (anti-repetición)
│   ├── traduccion.py            # Traducción por API de Groq, COMPARTIDA (TTS ES→JA y traductor de juegos)
│   └── memoria.py               # Memoria persistente de recuerdos (SQLite, stdlib)
│
├── plugins/                     # Capacidades enchufables
│   ├── __init__.py               # Base Plugin + registro
│   ├── system_control.py         # Programas, brillo, ventanas, multimedia, volumen (general y por app), energía, búsqueda
│   ├── web_search.py             # Búsqueda/apertura de sitios web en el navegador
│   ├── video_interpolador.py     # Dispara el pipeline de interpolación de video (.bat) y avisa al terminar
│   ├── traductor_juegos.py       # Traduce mensajes de juego predefinidos y los copia al portapapeles
│   ├── discord_control.py        # Bot de Discord (mute/deafen de VOZ y expulsar) en su propio hilo/loop async
│   └── macros.py                 # Ejecuta macros/alias definidos en data/macros_config.json
│
├── data/                         # Datos persistentes del usuario (gitignored)
│   ├── preferences.json          # Preferencias (juegos, programas favoritos, notas)
│   ├── macros_config.json        # Macros/alias configurables (los consume plugins/macros.py)
│   └── miku_memoria.db           # Base SQLite de la memoria persistente (recuerdos)
│
├── bin/                          # Binarios externos
│   └── es.exe                    # Everything CLI (búsqueda instantánea de archivos)
│
├── extern/                       # Motores externos
│   └── VOICEVOX/vv-engine/       # Motor local de síntesis de voz (run.exe)
│
├── docs/
│   ├── informe_implementacion_base.md   # Bitácora técnica de la implementación
│   └── informe_sesion_3.md              # Bitácora de la Sesión 3 (scheduler, web, tono, VOICEVOX)
│
├── CONTEXTO.md                  # (raíz) Bitácora de decisiones / sesiones de trabajo
│
├── .gitignore                    # Ignora config_local.py, data/, __pycache__, etc.
└── README.md
```

---

## Instalación

Requiere **Python 3.10+** sobre **Windows**.

1. Crear y activar un entorno virtual:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```

2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
   Todas las dependencias pesadas/opcionales (PyQt5, pygame, pyttsx3,
   SpeechRecognition, pyaudio, keyboard, AppOpener, pywin32, screen-brightness-control,
   pycaw, comtypes, discord.py)
   se importan de forma **lazy**: si falta alguna, el asistente sigue arrancando y
   simplemente esa función particular avisa que no está disponible.

3. Copiar `config_local.py.example` → `config_local.py` y completar tus datos reales:
   - `GROQ_API_KEY` / `GROQ_API_KEY_STT` — claves de [console.groq.com](https://console.groq.com) (STT vacío = usa la principal).
   - `VOICEVOX_URL` / `VOICEVOX_SPEAKER_ID` — si tu VOICEVOX corre en otro puerto o preferís otra voz.
   - `MICROFONO_INDEX` — si tenés varios micrófonos y querés fijar uno.
   - `BRAVE_RUTA_EXE`, `BRAVE_PERFIL_DIR`, `TIDAL_RUTA_EXE` — rutas de instalación (para cuando se implementen esos plugins).
   - `CARPETA_VIDEOS`, `RUTA_BAT_INTERPOLAR` — rutas del pipeline de interpolación de video.
   - `MEMORIA_ACTIVA` — `False` para desactivar la memoria persistente (default `True`).
   - `IDIOMA_JUEGO`, `MENSAJES_JUEGO` — idioma destino y diccionario de mensajes del traductor de juegos.
   - `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_CANAL_DEFAULT` — token del bot de Discord y (opcional) servidor/canal por defecto. Sin token, el plugin de Discord queda inactivo (no rompe el arranque).

   **`config_local.py` nunca se sube al repo** (está en `.gitignore`). `config.py`
   trae todos los defaults públicos vacíos o genéricos, sin ningún dato real.

   Si todavía no tenés `GROQ_API_KEY`, el asistente igual arranca: los comandos
   fast-path (hora, saludo, listar plugins) funcionan sin API, y cualquier otra
   consulta devuelve un aviso claro en vez de romperse.

4. (Opcional) Asegurate de que `bin/es.exe` (Everything CLI) esté presente para
   habilitar la búsqueda de archivos por voz/texto — ya viene incluido en este repo.

5. (Opcional) Si querés voz VOICEVOX, el motor puede estar en
   `extern/VOICEVOX/vv-engine/run.exe`; el asistente lo detecta y lo **arranca
   automáticamente** la primera vez que necesita hablar, si no está corriendo.

---

## Cómo correr

```bash
python main.py
```

Al arrancar pregunta el **modo de entrada** (Enter usa el default de `config.py`):

1. **Hablando** — escucha continua con wake word. Podés decir **"Miku"** y
   esperar a que responda "¿Sí? Decime." y recién ahí dar el comando (flujo en
   dos pasos), **o** decir **"Miku, qué hora es"** todo junto en una sola frase
   (en ese caso Miku usa directo el contenido posterior a "Miku" y salta la
   segunda escucha).
2. **Push-to-talk** — mantenés apretada la tecla F22: graba mientras la tenés
   presionada y corta al soltarla (implementación real, no depende solo del
   timeout de silencio).
3. **Escribiendo** — modo texto por consola. Preguntá si querés que además
   hable (para probar el pipeline de TTS) o que sea 100% silencioso.

Para salir: `Ctrl+C` (o escribí "salir" en modo texto).

---

## Cómo funciona (flujo de un comando)

1. **`main.py`** prepara logging, carga la config y ensambla el `Asistente`.
2. Crea el `EventBus` y registra los plugins activos (`SystemControl`, `WebSearch`,
   `Macros`, `VideoInterpolador`, `TraductorJuegos`, `DiscordControl`).
3. El `CommandParser` recibe el texto y:
   - atiende **fast path local** (hora, saludos, "acordate que...") sin gastar API;
   - deja responder a un **plugin** si conoce el comando directamente;
   - si hay una **confirmación pendiente** (p. ej. apagar la PC, o programar un
     apagado diferido), la resuelve antes de seguir — este estado vive en el
     propio parser y persiste entre turnos, no se resetea en cada mensaje. La
     confirmación es **genérica**: sirve para cualquier tool peligrosa
     (`control_energia`, `programar_accion`, y futuras);
   - si nada de lo anterior aplica, consulta al **LLM** (Groq, vía `BrainGroq`)
     con las `tools` que publicaron los plugins. Si el LLM llama una tool
     **peligrosa** (`control_energia`, `programar_accion`, o las de **Discord**
     que afectan a otro usuario), se pide confirmación explícita antes de
     ejecutarla.
4. La respuesta se habla (VOICEVOX + subtítulos) o se imprime, según el modo.
   Antes de hablar/imprimir, una respuesta **corta** puede pasar por
   `core/tono.variar()` para no sonar repetitiva (ver más abajo).

---

## Plugins (cómo agregar capacidades en el futuro)

Cada plugin es una clase en `plugins/` que:
- hereda de la base `Plugin`,
- define `nombre` y `descripcion`,
- opcionalmente publica `tools` (schemas tipo OpenAI function-calling) y
  el método `manejar_tool(nombre, args, contexto)`.

Se registra en `main.instalar_core()` dentro de `candidatos`. El framework
(`core.event_bus.EventBus` y `plugins.registrar_plugins`) hace el resto: el
cerebro "ve" esas tools y puede invocarlas por voz o texto.

**Disponible hoy:** `system_control`, `web_search`, `video_interpolador`,
`traductor_juegos`, `discord_control` y `macros`, con las siguientes tools:

### `system_control` (control del sistema)

| Tool | Qué hace |
|---|---|
| `abrir_programa` | Abre un programa por nombre/alias (AppOpener). Si no es un programa instalado, busca en la **biblioteca de Steam** (`steam://rungameid/<appid>`) o en los juegos de Epic configurados (`JUEGOS_EPIC`) |
| `cerrar_programa` | Cierra un proceso vía `taskkill`, solo si está en la whitelist (coincidencia exacta) |
| `controlar_brillo` | Sube, baja o fija el brillo de la pantalla |
| `listar_ventanas` | Lista las ventanas visibles actualmente |
| `mover_ventana` | Mueve una ventana a otro monitor (maximizada) |
| `posicionar_ventana` | Coloca una ventana en una mitad del monitor (izquierda/derecha/arriba/abajo) o completa |
| `dividir_pantalla` | Parte la pantalla en dos: una app a la izquierda, otra a la derecha (estilo Snap 11) |
| `actualizar_biblioteca_juegos` | Re-escanea la biblioteca de Steam a demanda (juego nuevo sin reiniciar) |
| `minimizar_ventana` | Minimiza la ventana de un programa |
| `control_multimedia` | Play / pausa / siguiente / anterior (teclas virtuales) |
| `ajustar_volumen` | Sube, baja, **fija** un nivel exacto (0-100), o silencia/desmutea (mute real, no toggle). Con `app` ajusta el volumen de **una app puntual** (ej. "bajá el volumen de Brave") |
| `buscar_archivo` | Búsqueda instantánea con Everything (`bin/es.exe`), con filtro por extensión, apertura directa y desambiguación cuando hay varias coincidencias |
| `control_energia` | Apagar / reiniciar / suspender — **siempre pide confirmación** antes de ejecutar |
| `programar_accion` | Programa una acción **diferida**: apagar/reiniciar/suspender la PC o un **recordatorio hablado**, dentro de N minutos. **Siempre pide confirmación** |
| `cancelar_accion_programada` | Cancela la última acción programada (o una por `id`) |

### `web_search` (búsqueda web)

| Tool | Qué hace |
|---|---|
| `buscar_en_web` | Abre el navegador con una **búsqueda o sitio**: `mercadolibre`, `youtube`, `google` (default), `wikipedia`, `github`, o un dominio/URL directo. **Solo envía la búsqueda; no lee los resultados** |

### `video_interpolador` (pipeline de interpolación de video)

Requiere `CARPETA_VIDEOS` y `RUTA_BAT_INTERPOLAR` en `config_local.py`.

| Tool | Qué hace |
|---|---|
| `interpolar_video` | Lista los videos (`mp4/mkv/avi/mov/wmv/webm/flv`) de la carpeta y dispara el `.bat` de interpolación **en segundo plano** (sin ventana). Si hay varios y no se aclara cuál, **pregunta** (desambiguación, reutilizando el mecanismo de `buscar_archivo`). Al terminar, **avisa por voz** (o consola) con el resultado. Si falta config, lo dice claro (no adivina rutas) |

### `traductor_juegos` (traductor + portapapeles)

Requiere `IDIOMA_JUEGO` y `MENSAJES_JUEGO` en `config_local.py`.

| Tool | Qué hace |
|---|---|
| `traducir_mensaje_juego` | Toma un mensaje predefinido (por clave, parte de la clave, o la propia frase), lo traduce al `IDIOMA_JUEGO` con **Groq** y lo **copia al portapapeles** para pegar con Ctrl+V en el chat del juego. Reutiliza `core/traduccion.py` (misma lógica que el TTS) con cache |

### `macros` (macros/alias configurables)

Lee `data/macros_config.json`.

| Tool | Qué hace |
|---|---|
| `listar_macros` | Lista las macros/alias definidas |
| `ejecutar_macro` | Ejecuta la macro pedida (secuencia de acciones predefinidas) |

### `discord_control` (moderación de voz por bot)

Requiere `DISCORD_BOT_TOKEN` (y, recomendado, `DISCORD_GUILD_ID`) en `config_local.py`.
El bot corre en **su propio hilo con su propio event loop** (asyncio), sin bloquear
el asistente; las tools (sync) le piden trabajo con `run_coroutine_threadsafe`.
El usuario se resuelve **por nombre** (display/username/nick, tolerante a
acentos y mayúsculas) o por **ID**; si hay **varias coincidencias**, Miku pide
el nombre completo en vez de adivinar.

| Tool | Qué hace |
|---|---|
| `silenciar_usuario_discord` | Silencia (o quita el silencio de) el **micrófono** de un usuario (server mute de voz). **Pide confirmación** |
| `volumen_usuario_discord` | **Mute / ensordecer (deafen)** de voz de un usuario. **Pide confirmación** |
| `expulsar_usuario_discord` | Expulsa (**kick**) a un usuario del servidor. **Pide confirmación** |

> **Límite real de la API de Discord (no nuestro):** un bot **no** puede cambiar
> el **volumen** de reproducción de otro usuario (eso es local de cada cliente).
> Por eso `volumen_usuario_discord` se implementa como **mute/deafen de voz**.
> Requiere los *Privileged Intents* **Server Members** y **Message Content**
> activados en el portal, y que el rol del bot esté **por encima** del usuario
> objetivo con los permisos *Kick Members* / *Mute Members*.

> **Pendiente (fuera de esta tanda):** `traducir_a_canal` (traducir y postear en
> un canal) — el config `discord_canal_default` queda reservado por si se retoma.

---

## Notas técnicas

- **Lazy loading:** todo lo pesado u opcional (PyQt5, pygame, pyttsx3,
  SpeechRecognition, pyaudio, keyboard, AppOpener, pywin32,
  screen-brightness-control, pycaw, comtypes, discord.py) se importa recién
  cuando el modo/comando lo necesita.
- **Threading:** la voz y la escucha de micrófono corren en hilos separados
  para no bloquear la consola; el overlay de subtítulos corre en su propio
  hilo con event loop de Qt; las acciones diferidas usan `threading.Timer`; el
  bot de Discord corre en **su propio hilo con su propio asyncio event loop**
  (las tools sync le piden trabajo con `run_coroutine_threadsafe`).
- **Cola de voz:** las respuestas se encolan y se reproducen una atrás de otra.
- **VOICEVOX:** el texto de la respuesta se **traduce ES→JA** (ver "Traducción"
  más abajo) y se sintetiza con una voz **genérica** de VOICEVOX (configurable
  con `voicevox_speaker_id`), no una voz clonada de Miku. Si VOICEVOX no
  responde, el asistente intenta arrancarlo automáticamente (oculto, sin
  ventana); si eso también falla, cae a `pyttsx3` (voz del sistema).
- **Traducción ES→JA (y compartida):** la traducción se hace con la **API de
  Groq** — NO con `deep-translator`. La lógica vive en `core/traduccion.py` y la
  comparten el **TTS** (ES→JA con la cuenta de STT, `GROQ_API_KEY_STT`) y el
  **traductor de juegos** (con la cuenta principal, `GROQ_API_KEY`). Hay una
  **cache en memoria** (dict) por frase exacta dentro de la sesión, así las
  frases repetidas (p. ej. las de tono: "Listo", "Ya está") se traducen **una
  sola vez**. La traducción actúa sobre el **texto final** de *cualquier*
  respuesta (LLM, fast-path o tool hardcodeada) sin tocar cómo se genera ese texto.
- **Subtítulos sincronizados por frase:** una respuesta larga se divide en
  frases; cada frase se **sintetiza y se subtitula de a una** (con *prefetch*
  de la siguiente) para que el subtítulo coincida con el audio que suena, en
  vez de mostrar el texto completo de golpe.
- **Tono (anti-repetición):** las respuestas **cortas** conocidas ("Listo",
  "Ya está", "Dale", el saludo de arranque) pasan por `core/tono.py`, que elige
  una variante al azar evitando repetir la última usada. Las respuestas largas
  del LLM pasan intactas.
- **Confirmaciones:** el estado de "esperando confirmación" vive en el
  `CommandParser` (no se pierde entre turnos), y es **genérico**
  (`{tool, args}`), así que acciones como apagar la PC o **programar un apagado
  diferido** siempre requieren un sí/no explícito antes de ejecutarse.
- **Acciones diferidas (Scheduler):** `core/scheduler.py` programa callbacks
  con `threading.Timer`. El `Scheduler` vive en el `Asistente` y se pasa a las
  tools vía `contexto["scheduler"]`. Al **cerrar** el asistente se cancelan
  todas las pendientes (no se deja un timer que apague la PC al salir).
- **Memoria:** `core/memoria.py` es una memoria persistente sobre **SQLite**
  (stdlib, sin dependencias). Guarda "recuerdos" (`data/miku_memoria.db`) y los
  consulta por coincidencia de texto (LIKE, sin embeddings por ahora). Se
  activa/desactiva con `memoria_activa` en `config.py`/`config_local.py` y se
  degrada sola (no tumba el arranque) si SQLite falla.
- **Volumen por app:** `ajustar_volumen` acepta un parámetro `app`; cuando se
  indica, ajusta **todas las sesiones de audio** de esa app (vía pycaw
  `GetAllSessions`), no el volumen general. Útil para el "Game Booster".
- **Traductor de juegos:** reutiliza `core/traduccion.py` y copia el resultado
  al portapapeles con `win32clipboard` (pywin32). El diccionario de mensajes y
  el idioma salen de `config_local.py`.
- **Interpolación de video:** `plugins/video_interpolador.py` lanza el `.bat`
  configurado en un hilo aparte (sin ventana), pasándole la ruta del video como
  argumento, y avisa por voz/consola al terminar (según el código de salida).
- **Seguridad:** ningún comando de sistema usa `shell=True`; `cerrar_programa`
  valida contra una whitelist exacta antes de matar un proceso; `buscar_archivo`
  y `buscar_en_web` se ejecutan por `subprocess`/`webbrowser` sin shell.

---

## 🛠️ Próximos pasos (roadmap)

- **Game Booster**: bajar volumen del navegador (**ya existe volumen por app**), pausar Wallpaper Engine, monitorear temperatura.
- **Navegador (Brave) por CDP**: abrir/cerrar pestañas, buscar, autocompletar (más profundo que el `buscar_en_web` actual, que solo abre la búsqueda).
- **Discord (resto)**: `traducir_a_canal` y, a futuro, más moderación.

### Hecho recientemente (ya no son "próximos pasos")

- **Discord** (`discord_control`): mute/deafen de voz y expulsar a usuarios, con la confirmación genérica (bot corriendo en su propio hilo/loop async).
- **Volumen por app** (`ajustar_volumen` con `app`) — base del futuro Game Booster.
- **Traductor para juegos** (`traductor_juegos`): mensajes predefinidos traducidos + portapapeles.
- **Macros personalizadas** (`macros`): conectadas a `data/macros_config.json`.
- **Memoria persistente**: reactivada con backend liviano (SQLite).
- **Interpolación de video** (`video_interpolador`): conecta `carpeta_videos` / `ruta_bat_interpolar` a un comando real, con aviso por voz al terminar.

### Ideas de lanzador / experiencia de escritorio (sesión aparte, NO implementadas)

Deliberadamente **no** hechas todavía porque tocan `main.py`, que ya maneja
voz/push/texto con lógica delicada de confirmaciones y push-to-talk que funciona:

- **Bandeja del sistema** (`QSystemTrayIcon`) con menú de modos.
- **Modo `always_on`** configurable que arranca escucha continua sin preguntar.
- **Hotkey global F22 como toggle** de escucha (distinto del push-to-talk actual, que se mantiene tal cual).
- **Ventanita flotante de selección de modo** (PyQt5) al apretar F22.
- **Empaquetado a `.exe` con PyInstaller** (nota: PyQt5/pygame/speech_recognition suelen necesitar *hooks* específicos; no se armó el `.exe`).
