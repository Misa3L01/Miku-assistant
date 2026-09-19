# Plan de reestructuración y hoja de ruta

## Estado y decisiones (actualizado)

Decisiones del dueño del proyecto: reestructuración **completa** con estructura A (`miku/`);
**eliminar push-to-talk**; **modo texto se conserva como depuración**, elegible en bandeja/ventanita;
**inicio con Windows opcional** y **F22 invoca a Miku** (ver 6); traductor de juegos **para cualquier idioma**
(los juegos eran solo contexto); macro *modo Fortnite* = **1920x1440** (aplicado).

| Fase | Estado |
|---|---|
| A. Tests (pytest) | ✅ hecha: 119 tests en ~5 s |
| B. Esquema de configuración, validación, asistente y `.example` generado | ✅ hecha (y `config_local.py` completado con 38 opciones comentadas, sin tocar valores) |
| C. Mover a `miku/` | ✅ hecha (movimientos confirmados). Desvíos menores de nombre respecto de 4.1: `voz/salida/tts.py` (se llamará `cola.py` recién al partirlo en la fase F), `social/discord_bot.py` y `social/telegram_bot.py` (para no llamar `discord.py`/`telegram.py` a un archivo que hace `import discord`/`import telegram`), y `plugins/asistente/proactivo.py`. `run.bat`/`run.ps1` en la raíz quedan como atajos a `scripts/` |
| D. Dividir `system_control` y capa `plataforma/` | ✅ hecha: `system_control.py` (2.558 líneas) → plugins `programas`, `archivos`, `audio`, `energia`, `ventanas` + `biblioteca_juegos`; capa `plataforma/` con `texto`, `subprocesos`, `pantalla`, `audio`, `everything`; 7 copias de normalización, 3 de `_sin_ventana`, 2 de `DEVMODE` y 2 de acceso a pycaw eliminadas. Las primitivas de ventanas (win32) siguen dentro del plugin `ventanas` porque nadie más las usa |
| G. Quitar push-to-talk, F22 invoca, inicio con Windows | ✅ hecha: sin push-to-talk; modo voz/texto en caliente desde la bandeja; **una sola Miku** (mutex) que se invoca desde otra ejecución; F22 = saludar y escuchar (dentro de Miku, o vía un `.lnk` con F22 como tecla de método abreviado, que la abre aunque esté cerrada: verificado en este equipo); inicio con Windows opcional (clave `Run`, visible en Administrador de tareas > Inicio); modo texto = ventana de depuración; log a `data/miku.log` |
| H. Motor proactivo | ✅ hecha: `servicios/proactivo.py` (motor + política: silencio nocturno, no molestar en juego, cooldown, una vez por día recordada en `data/proactivo_estado.json`, presupuesto por hora) y `servicios/reglas_proactivas.py` (clima, estado al jugar, carga sostenida, GPU caliente, batería, disco); frases con variantes en `voz/frases/banco.py` + `catalogo_proactivo.py`; capa plataforma nueva: `openmeteo`, `hardware` (CPU/RAM/disco/batería/GPU vía nvidia-smi), `procesos`; el plugin de clima y `estado_pc` reutilizan esas piezas (se eliminó la copia duplicada de `_disco_principal`) |
| E. Respuestas naturales | ✅ hecha: `voz/frases/respuesta.py` (`Respuesta` = `str` + `ok`/`intencion`/`datos`; `exito`/`falla`/`hubo_falla`) y `catalogo_respuestas.py` (variantes que rotan, redactadas con honestidad: "no lo encuentro" ≠ "no está instalado"); convertidos los "no encontré…" de programas, ventanas, audio, archivos, todoist, video, memoria, captura, brave, energía y clima, y las confirmaciones más frecuentes (abrir/cerrar programa, volumen, brillo, ventanas). Ya no hay lógica que dependa del texto (`ventanas.organizar` y el briefing usan `hubo_falla`) |
| F, I | pendientes (orden acordado: I y F al final) |

Además, ya resuelto: limpieza de `data/preferences.json` (copia en `preferences.json.bak`; la clave `juegos: {zzz: [tidal, discord]}` parece una idea de "abrir estas apps al lanzar el juego": semilla para los perfiles de juego), prueba real de interpolación (funciona; `.bat` revisado y defecto del `.vpy` en
`docs/interpolacion/`), y verificación de que el driver acepta 1920x1440 (`CDS_TEST`).

---

Propuesta original (todo lo de abajo se verificó leyendo el código antes de la Sesión 10).

---

## 1. Verificación de la lista de funciones

Hay 42 tools en 18 plugins activos (+ Game Booster y Telegram, que se cargan solo si están
configurados). Correcciones a la lista:

| Ítem de la lista | Estado real |
|---|---|
| Sección 1 (`system_control`) | ✅ correcta. |
| **`buscar_en_web`** (`web_search`) | ❌ **faltaba**: abre MercadoLibre/YouTube/Google/Wikipedia/GitHub o un dominio. |
| `cerrar_pestana` "por índice o título" | ❌ **no recibe parámetros**: cierra la pestaña activa (la primera de CDP). |
| `buscar_en_pestana_actual` "en la barra de la pestaña activa" | ❌ **abre una búsqueda de Google en una pestaña NUEVA**; no escribe en la pestaña actual. |
| `leer_pantalla` / `ver_pantalla` | ⚠️ son cosas distintas: `leer_pantalla` es **OCR local** (Tesseract, que hoy NO está instalado → usa el OCR de Windows, que anda pero es menos preciso) y **no usa Gemini**. Solo `ver_pantalla` usa `GEMINI_API_KEY`. |
| `estado_pc` "que lo diga solo cuando juego o hay algo pesado" | ❌ **no existe**: solo responde a pedido. El asistente proactivo solo avisa batería baja y disco lleno. |
| `clima` "que avise solo (lluvia, frío…)" | ❌ **no existe**: solo a pedido y en el briefing de arranque. |
| `traducir_mensaje_juego` | ⚠️ **no funciona como pensás**: solo traduce frases **predefinidas** (`MENSAJES_JUEGO`) a **un único idioma fijo** (`IDIOMA_JUEGO`). No acepta "traducí X al portugués" ni distingue CS (portugués) de Genshin (inglés). Ver 3.7. |
| `cambiar_personalidad` | ✅ existe, con parámetro `persistir`; también se puede fijar con `PERSONALIDAD` en `config_local.py`. |
| `salir_modo` / macro "modo fortnite" | ✅ el macro ya dice `1440x1080` y el código lo lee como **ancho×alto** (1440 de ancho, 1080 de alto = 4:3 estirado). "Al revés" (1080x1440) sería una pantalla vertical: **no hay nada que cambiar**. Sí hay un riesgo real: el snapshot vive en memoria, así que si reiniciás Miku estando en el modo, no hay forma de volver (ver 3.6). |
| `listar_macros` | ✅ existe; la respuesta hablada no debería leer una lista larga (ver 3.6). |
| Wake word + **push-to-talk** | ⚠️ existe (`run_modo_push`, F22) pero **hoy es inalcanzable con la ventanita** (solo ofrece Voz/Texto). Se propone eliminarlo (ver 3.5). |
| TTS + subtítulos | ⚠️ aclaración: **la voz siempre habla en japonés** (se traduce ES→JA) y el subtítulo es el texto en **español**. Por eso hoy tiene sentido. Solo dejaría de tenerlo si se agrega voz en español. |
| Memoria | ⚠️ falta lo importante: **no hay forma de olvidar ni listar recuerdos por voz** (`olvidar_recuerdo` existe en `memoria.py` pero nadie lo llama) y **el LLM no recibe historial de conversación** (cada turno es independiente: "y ahora más bajo" no funciona). |
| Automáticos | ✅ Game Booster, proactivo (solo batería/disco), Telegram. El **briefing** es del núcleo, no un plugin. |
| Faltaba en "núcleo" | Fast-path (hora, saludos, "acordate que…"), **desambiguación** ("¿cuál de estos 3?"), tono/personalidad, notificaciones toast, bus de eventos. |

---

## 2. Respuestas directas a tus preguntas

**¿Dónde pongo la clave de Gemini y la carpeta de capturas?** En `config_local.py`:
`GEMINI_API_KEY = "..."` y `CARPETA_CAPTURAS = r"D:\Capturas"`. **Tu `config_local.py` real solo
tiene 13 variables** (Groq, VOICEVOX, micrófono, Brave, TIDAL, video, Discord), por eso
`ver_pantalla` dice "no tengo la clave" y las capturas van a `data/capturas/`. El
`config_local.py.example` está bien como plantilla, pero **no es intuitivo**: se desincroniza de tu
archivo real y no avisa qué te falta ni si escribiste mal un nombre. Propuesta en 4.2 (esquema único +
validación al arrancar + asistente de configuración que **agrega lo que falta como comentarios sin
tocar tus valores**).

**¿Everything es lo mejor para buscar archivos?** Sí, para *nombres* es la mejor opción en Windows
(indexa el MFT de NTFS, resultados instantáneos) y ya lo tenés corriendo (`es.exe` 1.1.0.37). No lo
reemplazaría. Lo que sí mejoraría:
1. Hablarle por el **SDK/IPC de Everything** (DLL vía `ctypes`) en vez de lanzar `es.exe` por cada
   consulta: mismo motor, más rápido y con resultados estructurados (tamaño, fecha) sin parsear texto.
2. **Ranking propio** ya existe (nombre, carpeta, semántico); sumar *recencia* y *frecuencia de uso*.
3. Para buscar **por contenido** ("el documento que habla de…") Everything no sirve por defecto:
   agregar como segundo motor el **índice de Windows Search**.

**¿Se puede "poné X canción en TIDAL"?** Probablemente sí, en dos pasos, pero **no lo probé** (no quise
tocar tu reproductor sin avisarte):
- TIDAL Desktop tiene registrado el protocolo `tidal://` en tu PC (lo verifiqué en el registro,
  apunta a `app-2.43.2\TIDAL.exe`) y admite enlaces a pistas/álbumes/playlists por ID.
- Falta **encontrar el ID** a partir del nombre: la API pública de TIDAL for Developers (búsqueda de
  catálogo, credenciales gratis) o la librería no oficial `tidalapi`.
- Flujo propuesto: buscar → abrir `tidal://track/<id>` → tecla play → confirmar por SMTC lo que quedó
  sonando ("Puse *X* de *Y*"), así si entendió mal lo notás. Alternativa de respaldo: automatizar la
  UI de TIDAL (frágil). Lo dejo como prueba corta cuando aprobés.

**`interpolar_video`:** dejar el `.bat` en el directorio me sirve (lo reviso; un acceso directo no,
necesito el contenido). Un video de 2–5 s también: haría una prueba real y **borraría la salida**
como pedís. Lo corro solo con tu OK explícito (usa GPU/CPU pesado).

---

## 3. Mejoras propuestas (qué unificar, simplificar o completar)

**3.1 Respuestas naturales (tu pedido general).** Hoy las tools devuelven **texto fijo** y el código
*depende* de ese texto (`system_control` comprueba `res.startswith("No encontré")`), así que cambiar una
frase puede romper lógica. Propuesta: las tools devuelven un resultado estructurado
(`Respuesta(intent="app_no_encontrada", nombre="X")`) y un **banco de frases** por intención, con
variantes ("Parece que no tenés *X* instalado", "No veo *X* en tu PC", "No encuentro *X* por ningún
lado") que rota sin repetir la última (ya existe `tono.py` para frases cortas; se generaliza), respeta
la personalidad y, opcionalmente, deja que el LLM reformule. Ojo con la honestidad: "no lo encuentro"
≠ "no está instalado". Compatibilidad: una tool que devuelva `str` sigue funcionando.

**3.2 Partir `system_control.py` (2.558 líneas, 16 tools)** en módulos por tema (programas, ventanas,
audio, energía, archivos, biblioteca de juegos) y sacar los helpers de Windows a una capa compartida.
Hoy hay **helpers duplicados 10+ veces** (`_normalizar_texto`, `_sin_acentos`, `_normalizar`,
`_clave_compacta`, `_sin_ventana`) y **dos copias de la estructura `DEVMODE`** (`macros.py` y
`modos.py`).

**3.3 Cerebro más modular.** `command_parser.py` (919 líneas) mezcla LLM, confirmaciones,
desambiguación y fast-path: separarlo. Agregar **historial corto de conversación** (últimos N turnos) y
**enrutado de tools** (hoy el LLM recibe las 42 en cada consulta: más tokens, más latencia, peor
precisión con modelos chicos).

**3.4 Proveedores intercambiables (local/cloud).** Una interfaz `LLMProvider`: Groq hoy; cualquier
endpoint compatible con OpenAI (Ollama, LM Studio, llama.cpp) mañana — Groq ya usa ese formato, así que
es cambiar `base_url` + modelo + capacidad de tools. Igual para STT (Groq Whisper ↔ faster-whisper
local) y TTS (VOICEVOX, voz del sistema, futuros).

**3.5 Voz.** Quitar push-to-talk (`run_modo_push`, `capturar_para_push`, grabación cruda con pyaudio, opción
de menú). Modo único "siempre escuchando" como Siri/OK Google, con la bandeja siempre presente.
**F22 = invocar** (mostrar panel / activar escucha), no elegir modo. Ojo: F22 solo funciona con Miku ya
corriendo; para "que F22 la arranque" hay que dejarla **residente** (inicio con Windows, en la
bandeja). Mejora de fondo: hoy *cada frase* de la escucha continua es una llamada a Whisper por API;
un **wake word local** (openWakeWord/Porcupine con un modelo "Miku") + VAD la dejaría en silencio y sin costo
hasta que la nombres, y respondería más rápido.
**Opción de idioma de voz:** `tts.idioma` (`ja` VOICEVOX / `es` otro motor) y `subtitulos = auto |
siempre | nunca` (auto = solo si el idioma hablado ≠ español). Voces custom (Miku u otra) entran como
nuevo proveedor TTS (Piper en español, XTTS, GPT-SoVITS o RVC si hay GPU).

**3.6 Macros y modos.** Persistir el snapshot en `data/` (sobrevive a reiniciar) y agregar "volver a la
resolución nativa" como red de seguridad; `listar_macros` que diga cuántas hay y solo nombre las
primeras 4–5 (la lista completa va a consola/README); un solo lector de resolución (hoy duplicado).

**3.7 Traductor de juegos.** Nueva forma: `traducir_mensaje_juego(texto, idioma?)`.
El idioma se resuelve así: (1) el que digas ("en portugués"); (2) el **perfil del juego en primer plano**
(`PERFILES_JUEGO = {"cs2": "portugués", "genshinimpact": "inglés"}`, reutilizando la detección de juego
del Game Booster); (3) `IDIOMA_JUEGO` por defecto. Acepta frase libre **o** un atajo predefinido, la copia
al portapapeles y responde corto ("Copiado en portugués: «…»"). Así "traducí *quiero que corran y
salten*" estando en CS va directo a portugués al portapapeles.

**3.8 Automáticos → un motor proactivo único.** Unificar `asistente_proactivo`, los avisos del Game
Booster, el clima y el briefing en un motor de **reglas** con cooldown, presupuesto de avisos por hora,
horas de silencio y "no molestar en juego". Reglas nuevas:
- *Clima:* consulta cada N horas; avisa **una vez** cuando entra lluvia/frío/calor, con frases rotativas
  ("Ojo que hoy se viene lluvia, llevate paraguas").
- *Estado de la PC:* al empezar una partida, o si CPU/RAM/GPU quedan altas varios minutos, lo dice con
  frases variadas; también a pedido. Temperatura/GPU: `nvidia-smi` (psutil en Windows no da temperaturas).

**3.8b Otras mejoras chicas:** exponer `olvidar` y `listar` recuerdos; persistir recordatorios (SQLite)
para que sobrevivan a reiniciar y sumar recurrentes; `cerrar_pestana` por título; `buscar_en_pestana_actual`
que de verdad escriba en la pestaña activa (o renombrarla); reunir avisos por voz/toast en un solo canal;
tests automáticos (hoy solo pruebas de humo manuales).

**Sobra / a medias:** `listar_personalidades` como tool (mejor solo config + respuesta a "¿qué
personalidades tenés?"), `dividir_pantalla`/`acoplar_ventanas` (funciones internas de una tool), el bus
de eventos casi sin uso (`emit`/`on` sin suscriptores reales) y `Plugin.execute()`/`comandos` (nadie los usa),
`discord_canal_default` (reservado sin feature), la ventanita de modo (se vuelve innecesaria con modo único).

---

## 4. Estructura propuesta

### 4.1 Árbol

```
miku-assistant/
├── main.py                     # 3 líneas: arranca miku.app
├── config_local.py             # privado (sigue en la raíz)
├── config_local.py.example     # GENERADO desde el esquema (no se edita a mano)
├── requirements.txt / requirements-dev.txt
├── scripts/                    # run.bat, run.ps1, miku.spec
├── tests/                      # pytest (las pruebas de humo actuales, versionadas)
├── docs/   data/   bin/   extern/
└── miku/
    ├── app.py                  # ensamblado, modos, cierre  (← main.py)
    ├── ajustes/                # ← config.py
    │   ├── esquema.py          # NUEVO: cada clave = tipo, default, descripción, ¿secreta?, plugin
    │   ├── carga.py            # capas defaults → preferences → config_local → entorno + validación
    │   └── asistente_config.py # NUEVO: genera el .example y agrega claves faltantes a config_local
    ├── cerebro/
    │   ├── parser.py           # orquestación  (← core/command_parser.py)
    │   ├── confirmaciones.py   # extraído del parser
    │   ├── desambiguacion.py   # extraído del parser
    │   ├── fastpath.py         # extraído: hora, saludos, memoria, calculadora
    │   ├── calculadora.py      # ← core/calculadora.py
    │   ├── conversacion.py     # NUEVO: historial corto + enrutado de tools
    │   ├── llm/                # base.py (LLMProvider) · groq.py (← BrainGroq) · openai_compat.py (local)
    │   └── memoria/            # almacen.py (← memoria.py) · embeddings.py
    ├── voz/
    │   ├── entrada/            # escucha.py (← speech_to_text.py) · wake_word.py · stt_groq.py
    │   ├── salida/             # cola.py (← text_to_speech.py) · voicevox.py · sistema.py · traduccion.py
    │   └── frases/             # tono.py → banco de frases con variantes (NUEVO)
    ├── ui/                     # qt_hilo.py · bandeja.py · subtitulos.py (← subtitles.py) · selector_modo.py (← ventanita.py, se retira al haber modo único) · panel/ (GUI futura)
    ├── servicios/              # scheduler.py · notificaciones.py · modos.py (snapshots) · briefing.py · personalidad.py · eventos.py (← event_bus.py) · proactivo.py (NUEVO motor de reglas)
    ├── plataforma/             # helpers de Windows compartidos: audio.py (pycaw) · ventanas.py · pantalla.py (resolución/brillo/captura) · procesos.py · texto.py (normalización ÚNICA) · subprocesos.py (sin ventana, PowerShell)
    └── plugins/
        ├── base.py · registro.py                         # ← plugins/__init__.py · registro.py
        ├── sistema/        programas.py · ventanas.py · audio.py · energia.py · archivos.py · biblioteca_juegos.py · estado_pc.py   # ← system_control.py + system_status.py
        ├── navegacion/     web.py (← web_search.py) · brave.py (← browser.py)
        ├── multimedia/     tidal.py
        ├── pantalla/       captura.py · ocr.py · vision.py
        ├── productividad/  todoist.py · clima.py · favoritos.py · macros.py · video.py (← video_interpolador.py)
        ├── gaming/         game_booster.py · traductor.py (← traductor_juegos.py)
        ├── social/         discord.py (← discord_control.py) · telegram.py (← telegram_control.py)
        └── asistente/      personalidad.py · modos.py
```

Se conservan: `data/`, `bin/es.exe`, `extern/VOICEVOX`, `docs/`, y `config_local.py` en la raíz (no se
mueve para no romper tu configuración). Los movimientos se harán con `git mv` para conservar el historial.

### 4.2 Configuración fácil e intuitiva
1. **Esquema único** (`ajustes/esquema.py`): cada opción declarada una sola vez con su descripción.
2. `config_local.py.example` se **genera** de ese esquema (siempre completo y al día, agrupado por tema:
   Claves · Voz · Carpetas · Juegos · Integraciones…).
3. **Validación al arrancar:** avisa claves con typo ("¿quisiste decir `CARPETA_CAPTURAS`?"), tipos
   incorrectos y, para cada plugin, qué configuración le falta ("`ver_pantalla` necesita `GEMINI_API_KEY`").
4. **`python -m miku.ajustes`**: agrega a tu `config_local.py` las opciones que no tenés **como líneas
   comentadas con su explicación**, sin tocar ninguno de tus valores; también puede mostrar el estado de
   cada opción con los secretos enmascarados. Más adelante la GUI usa el mismo esquema para editarlas.

### 4.3 Migración segura (fases; cada una deja el asistente funcionando y se commitea)
| Fase | Contenido | Riesgo |
|---|---|---|
| A | `tests/` con pytest (pruebas de humo actuales) y una línea base verde | bajo |
| B | Esquema de config + validación + generador del `.example` + asistente (y completar tu `config_local.py` con lo que falta, sin tocar valores) | bajo |
| C | Mover a `miku/` con `git mv`, ajustar imports, `registro.py`, `miku.spec`, lanzadores | medio (mecánico; los tests atrapan roturas) |
| D | Partir `system_control`; capa `plataforma/`; eliminar duplicados | medio |
| E | Resultado estructurado + banco de frases (respuestas naturales) | medio |
| F | Interfaces LLM/STT/TTS; opción de idioma y subtítulos automáticos | medio |
| G | Quitar push-to-talk; modo único siempre activo; F22 = invocar; inicio con Windows | bajo |
| H | Motor proactivo (clima, estado de PC en juego, batería/disco) | bajo |
| I | Plugins: traductor con perfiles de juego, TIDAL "poné X", memoria (olvidar/listar/historial), macros persistentes | bajo/medio |

No se cambian nombres de tools ni comportamiento hasta la fase indicada; fases A–D no alteran lo que ve el
usuario.

---

## 5. Ideas de features a futuro

**GUI:** panel de control (estado, activar/desactivar plugins, editor de configuración validada con el
esquema, editor de macros, visor/edición de memoria, historial de conversación, visor de logs); overlay
animado tipo "orbe" con estados *escuchando / pensando / hablando*; ventana de subtítulos configurable.

**Multimodal:** `ver_pantalla` de la *ventana activa* con contexto ("¿qué error me tira?"), OCR de
región/ventana, analizar imagen o texto del portapapeles, cámara; modelo de visión local (LLaVA/Qwen-VL vía
Ollama) como alternativa a Gemini.

**LLM local/cloud:** proveedor por configuración (Groq / OpenAI-compatible / Ollama / Gemini); **fallback
automático** (sin internet o Groq caído → local); modelo chico para enrutar y grande para charlar.

**Voz:** wake word local, STT local (faster-whisper), TTS en español (Piper) y voces custom (XTTS/GPT-SoVITS/RVC),
voz distinta por personalidad, **interrupción** ("callate"), TTS en streaming.

**Gaming:** perfiles por juego (resolución, volumen, apps a cerrar, idioma del traductor), modo "no
molestar" en partida, estado GPU/FPS, resumen post-partida, aviso de descarga de Steam terminada;
Discord: mover/silenciar a todos en un canal, avisar quién entró, leer mensajes en voz alta, lista de quién
habla, recordatorio de sesiones.

**Productividad:** calendario (Google Calendar), correo, pomodoro, notas por voz a Markdown/Obsidian,
dictado ("escribí esto"), resumen de una URL o del portapapeles, temporizadores múltiples, rutinas
("buenos días": clima + tareas + abrir apps).

**Automatizaciones:** motor de rutinas con disparadores (hora, juego abierto, batería, red, USB) y acciones
(cualquier tool) definidas en YAML/GUI; "cuando abra X hacé Y"; macros de varios pasos con condiciones;
recordatorios persistentes y recurrentes ("todos los lunes").

**Calidad y seguridad:** historial de acciones con "deshacer" donde se pueda, modo *dry-run*, permisos
por plugin, confirmación reforzada (frase/PIN) para acciones críticas, logs rotativos a archivo, CI con
tests, instalador con auto-actualización.

---

## 6. Decisiones que necesito de vos

1. **¿Estructura A (`miku/` con subpaquetes, la de arriba) o B (más liviana)?** B = mantener `core/` y
   `plugins/` como están, solo partir `system_control`, extraer confirmaciones/desambiguación del parser y
   crear `plataforma/`. Recomiendo **A** por tus objetivos (GUI, proveedores), a costa de más churn de imports.
2. **¿Confirmás eliminar push-to-talk** y pasar a modo único siempre activo (con texto como consola/panel)?
3. **¿Modo texto?** ¿Lo dejo como consola de depuración (con voz opcional) o lo retiro?
4. **Inicio automático con Windows** (Miku residente en la bandeja, F22 la invoca): ¿sí?
5. **¿Empiezo por la fase A+B** (tests + configuración) y te muestro el resultado antes de mover archivos?
6. Opcional: dejame el `.bat` de interpolación y un video corto, y autorizá la prueba real de
   interpolación y la de `tidal://` (avisándote antes de tocar TIDAL).
