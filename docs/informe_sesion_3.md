# Informe — Sesión 3

Resumen de la sesión de trabajo enfocada en: **tono anti-repetición, scheduler
de acciones diferidas, búsqueda web, cierre documental de la traducción,
VOICEVOX oculto + saludo de arranque, y documentación**.

> Nota de alcance: el "Bloque 8" original (resiliencia de traducción) ya estaba
> resuelto en una tanda previa (traducción ES→JA con **API de Groq** + cache en
> memoria). En esta sesión sólo se hizo su **cierre documental**.

---

## 1. Cambios por archivo

### `core/tono.py` *(nuevo — Bloque 4.3)*
- Catálogo de variantes de tono para respuestas **cortas** conocidas ("Listo",
  "Ya está", "Dale", saludo de wake, saludo de arranque).
- `variar(texto)` devuelve una variante evitando repetir la última usada
  (memoria de "última variante" por clave). Las frases fuera del catálogo
  pasan intactas (las respuestas largas del LLM no se tocan).
- Se agregó la clave **"ya estoy lista"** (saludo de arranque, ver Bloque 9).

### `core/scheduler.py` *(nuevo — Bloque 6)*
- Clase `Scheduler` basada en `threading.Timer`, thread-safe (`RLock`).
- API:
  - `programar(cuando_segundos, callback, descripcion) -> id`
  - `cancelar(id=None)` (`None` = cancela la **última** programada)
  - `listar_pendientes() -> [{id, descripcion, segundos_restantes}]`
  - `hay_pendientes()`, `cancelar_todos()`
- Las tareas disparadas/canceladas se autoeliminan de la lista de pendientes.
- Los `Timer` son `daemon` (no bloquean el cierre de la app).

### `core/command_parser.py` *(Bloque 6 + prompt Bloque 7)*
- **Estado de confirmación generalizado**: se reemplazó `_pendiente_energia`
  por `_pendiente_peligroso = {tool, args}` y `_encolar_confirmacion_energia()`
  por `_encolar_confirmacion(tool, args)`.
- Nuevo set `_TOOLS_PELIGROSAS = {control_energia, programar_accion}`: cualquier
  tool de este set que devuelva el LLM se **encola + pide confirmación** en vez
  de ejecutarse.
- `_manejar_confirmacion()` ahora es genérico: al confirmar, ejecuta la **tool
  pendiente con sus args** (antes sólo energía).
- **Fix de colisión**: "recordame X **en N minutos**" ya no lo intercepta el
  fast-path de memoria (`_comandos_inmediatos`) — se detecta con
  `_parece_recordatorio_diferido()` (regex `en <n> min|hora|seg`) y se deja
  pasar a la tool `programar_accion`.
- La cache de respuestas del LLM excluye respuestas con tools peligrosas
  (ahora incluye `programar_accion`).
- **System prompt** actualizado: sabe programar apagados/suspensiones/
  recordatorios diferidos, cancelarlos, y usar `buscar_en_web` para internet.

### `plugins/system_control.py` *(Bloque 6)*
- Tool nueva **`programar_accion`** (`accion` ∈ {apagar, reiniciar, suspender,
  recordatorio}, `en_minutos`, `mensaje` opcional):
  - Usa `contexto["scheduler"]`.
  - Para `recordatorio`: al disparar, `voice.decir(mensaje)` (fallback consola).
  - Para energía: al disparar, ejecuta `control_energia(accion)`.
  - Responde "Dale, en N minutos …".
- Tool nueva **`cancelar_accion_programada`** (`id` opcional):
  - `contexto["scheduler"].cancelar(id)`; sin id cancela la última.

### `plugins/web_search.py` *(nuevo — Bloque 7)*
- Tool **`buscar_en_web(termino, sitio=None)`**.
- Armado de URL:
  - `mercadolibre` → `https://listado.mercadolibre.com.ar/<slug-con-guiones>`
  - `youtube` → `https://www.youtube.com/results?search_query=<urlencoded>`
  - `google` (**default**) → `https://www.google.com/search?q=<urlencoded>`
  - `wikipedia` → `https://es.wikipedia.org/w/index.php?search=<urlencoded>`
  - `github` → `https://github.com/search?q=<urlencoded>`
  - Otro valor con `"."` → **dominio/URL directa** (respeta `http/https`; NO le
    pega `?q=` porque no todos los sitios lo soportan).
  - Otro sin `"."` → Google con `"<termino> <sitio>"`.
- Abre con `webbrowser.open(url)` (stdlib).
- Respuesta hablada **siempre aclara** que sólo abre la búsqueda, no lee
  resultados.

### `main.py` *(Bloques 6, 7, 9)*
- Import de `Scheduler` y `WebSearch`.
- `Asistente.__init__`: `self.scheduler = Scheduler()`.
- `_contexto_base()`: inyecta `contexto["scheduler"]` (además de `voice`).
- `Asistente.cerrar()`: `scheduler.cancelar_todos()` (no deja timers vivos que
  puedan apagar la PC al cerrar).
- `instalar_core()`: `candidatos = [SystemControl(), WebSearch()]`.
- `run_modo_voz()`: tras `iniciar_escucha_continua()` OK, Miku dice
  **una vez** `"Ya estoy lista"` (confirmación audible). **No** se tocó
  push-to-talk ni hotkeys.

### `core/text_to_speech.py` *(Bloque 9 + doc Bloque 8)*
- `_asegurar_voicevox()`: lanzamiento del motor **reforzado para quedar oculto**:
  - `creationflags = CREATE_NO_WINDOW`
  - `startupinfo = STARTUPINFO()` con `STARTF_USESHOWWINDOW` + `SW_HIDE`
  - `stdin/stdout/stderr = DEVNULL`
  - Armado defensivo (try/except) para no romper en entornos no-Windows.
- Docstring de cabecera corregido: la traducción ES→JA es con **Groq**, no con
  `deep-translator`.

### `run.bat` / `run.ps1` *(Bloque 9)*
- `run.bat`: `start "" "%VOICEVOX_RUN%"` → **`start "" /B "%VOICEVOX_RUN%"`**
  (con comentario explicando por qué).
- `run.ps1`: `-WindowStyle Minimized` → **`-WindowStyle Hidden`**.

### `README.md` / `CONTEXTO.md` / `requirements.txt` *(Bloque 10)*
- README: tabla de tools con las nuevas (`programar_accion`,
  `cancelar_accion_programada`, `buscar_en_web`, y `ajustar_volumen` con
  "fijar"/"silenciar"/"desmutear"); secciones de **Traducción ES→JA (Groq +
  cache)**, **subtítulos sincronizados por frase**, **tono**, **scheduler**;
  árbol de carpetas actualizado; roadmap con las ideas diferidas de lanzador.
- CONTEXTO: fecha/estado actualizados, bugs de Sesión 1/2 marcados como
  resueltos, entrada **Sesión 3** agregada, TTS corregido a "Groq".
- `requirements.txt`: **sin cambios** — `pycaw` y `comtypes` ya estaban de la
  sesión anterior; en estos bloques no se agregó ninguna dependencia nueva
  (todo stdlib: `threading`, `webbrowser`, `urllib.parse`, `re`).

---

## 2. Decisiones técnicas

- **Scheduler con `threading.Timer`** (no heap + un hilo worker) por simplicidad
  y porque los timers programados son pocos y de larga duración. Cada timer es
  su propio hilo daemon.
- **Confirmación genérica** (`_pendiente_peligroso {tool, args}`) en vez de
  duplicar lógica por tool: sirve para `control_energia`, `programar_accion` y
  cualquier tool peligrosa futura.
- **Fast-path vs. recordatorios**: se detecta una expresión de tiempo para no
  robarle "recordame X en N minutos" al scheduler.
- **Web search solo abre**: no se promete leer resultados; la respuesta hablada
  lo aclara siempre (evita que Miku "finja" saber la respuesta).
- **Dominios directos sin `?q=`**: no todos los sitios soportan ese parámetro;
  se abre el dominio tal cual para no romper la URL.
- **Bloque 8**: se mantiene **una sola** capa de traducción (Groq + cache), sin
  `config.traductor`. El flag `traductor: "google"|"groq"` propuesto en el
  enunciado **no aplica** porque ya no existe `deep-translator` en el proyecto.
  No se cambió la arquitectura de generación de respuestas: la traducción actúa
  sobre el texto final de cualquier respuesta.
- **VOICEVOX oculto**: se combinó `CREATE_NO_WINDOW` + `STARTUPINFO/SW_HIDE` +
  `DEVNULL` porque en algunos `.exe` de consola `CREATE_NO_WINDOW` solo no
  alcanza.
- **Ideas de lanzador/escritorio**: NO implementadas (ver §4), para no arriesgar
  `main.py` que ya maneja voz/push/texto con lógica delicada.

---

## 3. Cómo probar cada mejora

### Scheduler + acciones diferidas (Bloque 6)
En modo voz o texto:
- "recordame sacar la basura en 1 minuto" → Miku **pide confirmación**; decir
  "sí" → "Dale, en 1 minutos te aviso…". A los ~60 s dice el recordatorio por voz.
- "suspendé la pc en 5 minutos" → pide confirmación; al confirmar, "Dale, en 5
  minutos suspendo la PC." (probá con 1 minuto si no querés esperar).
- "cancelá el apagado" / "cancelá la acción programada" → cancela la última.
- Cerrá el asistente con una acción diferida pendiente → se cancela (no dispara).

### Búsqueda web (Bloque 7)
- "buscá zapatillas nike en mercadolibre" → abre
  `https://listado.mercadolibre.com.ar/zapatillas-nike`.
- "buscá lofi en youtube" → abre YouTube.
- "buscá python" (sin sitio) → abre Google.
- "buscá agujeros negros en wikipedia" → abre Wikipedia.
- "abrí infobae.com" → abre el dominio directo.
- Verificá que la respuesta hablada diga que **sólo abre la búsqueda**.

### Tono anti-repetición (Bloque 4.3)
- Pedí varias acciones cortas seguidas ("pausá la música", "subí el volumen") y
  escuchá que la confirmación ("Listo", "Dale"…) no repita siempre lo mismo.

### VOICEVOX oculto (Bloque 9)
- Lanzá con `run.ps1` o `run.bat`: VOICEVOX debe arrancar **sin ventana visible**.
- Verificá que no haya `run.exe` huérfano al cerrar:
  `Get-Process run -ErrorAction SilentlyContinue`.

### Saludo de arranque (Bloque 9)
- Modo 1 (voz): al iniciar la escucha continua, Miku dice una vez
  **"Ya estoy lista"** (o una variante de tono).

### Traducción (Bloque 8 — cierre)
- Repetí una misma frase corta dos veces y mirá el log: la segunda debe salir de
  **cache** (`[TTS] Traducción desde CACHE`), sin llamar a la API.

---

## 4. Pendientes explícitamente marcados (NO implementados)

> Buenas ideas, pero tocan `main.py` (voz/push/texto + confirmaciones + PTT real
> que ya funciona). Se dejan para una sesión dedicada.

- **Bandeja del sistema** (`QSystemTrayIcon`) con menú de modos.
- **Modo `always_on`** configurable (arranca escucha continua sin preguntar).
- **Hotkey global F22 como toggle** de escucha (distinto del push-to-talk actual,
  que se mantiene tal cual).
- **Ventanita flotante de selección de modo** (PyQt5) al apretar F22.
- **Empaquetado a `.exe` con PyInstaller**: PyQt5/pygame/speech_recognition suelen
  necesitar *hooks* específicos. No se armó el `.exe` ahora.

Otros pendientes (heredados, no de esta sesión):
- Plugins "grandes": Discord, Game Booster, traductor de juegos, macros.
- Navegador (Brave) por CDP.
- Reactivar/descartar `memoria.py`.
