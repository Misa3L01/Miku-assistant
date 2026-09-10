# Miku Assistant

Asistente de voz personal para Windows (Python 3.10+), pensado para programación,
gaming (CS:GO, Fortnite, Genshin Impact), uso diario y comunicación en Discord.
Arquitectura modular: un núcleo (`core/`) + plugins enchufables (`plugins/`).

> **Estado actual:** base funcional operativa — control de sistema, voz (VOICEVOX),
> confirmación de acciones peligrosas, push-to-talk real, búsqueda de archivos y
> control multimedia/volumen. Quedan pendientes los plugins "grandes" heredados
> del sistema anterior (Discord, Game Booster, traductor de juegos, macros) — el
> framework de plugins (`Plugin` + `tools`) ya está listo para sumarlos.

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
│   ├── text_to_speech.py        # TTS: VOICEVOX (auto-arranque) + traducción ES→JA + pygame, fallback pyttsx3
│   ├── command_parser.py        # "Cerebro": fast path local + LLM (tools) + confirmaciones persistentes
│   ├── subtitles.py             # Overlay de subtítulos estilo anime (PyQt5)
│   └── memoria.py               # Memoria persistente — DESACTIVADA a propósito (stub sin backend)
│
├── plugins/                     # Capacidades enchufables
│   ├── __init__.py               # Base Plugin + registro
│   └── system_control.py         # Programas, brillo, ventanas, multimedia, volumen, energía, búsqueda
│
├── data/                         # Datos persistentes del usuario (gitignored)
│   ├── preferences.json          # Preferencias (juegos, programas favoritos, notas)
│   └── macros_config.json        # Macros/alias configurables (aún sin plugin que los lea)
│
├── bin/                          # Binarios externos
│   └── es.exe                    # Everything CLI (búsqueda instantánea de archivos)
│
├── extern/                       # Motores externos
│   └── VOICEVOX/vv-engine/       # Motor local de síntesis de voz (run.exe)
│
├── docs/
│   └── informe_implementacion_base.md   # Bitácora técnica de la implementación
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
   Todas las dependencias pesadas/opcionales (PyQt5, pygame, pyttsx3, deep-translator,
   SpeechRecognition, pyaudio, keyboard, AppOpener, pywin32, screen-brightness-control)
   se importan de forma **lazy**: si falta alguna, el asistente sigue arrancando y
   simplemente esa función particular avisa que no está disponible.

3. Copiar `config_local.py.example` → `config_local.py` y completar tus datos reales:
   - `GROQ_API_KEY` / `GROQ_API_KEY_STT` — claves de [console.groq.com](https://console.groq.com) (STT vacío = usa la principal).
   - `VOICEVOX_URL` / `VOICEVOX_SPEAKER_ID` — si tu VOICEVOX corre en otro puerto o preferís otra voz.
   - `MICROFONO_INDEX` — si tenés varios micrófonos y querés fijar uno.
   - `BRAVE_RUTA_EXE`, `BRAVE_PERFIL_DIR`, `TIDAL_RUTA_EXE` — rutas de instalación (para cuando se implementen esos plugins).
   - `CARPETA_VIDEOS`, `RUTA_BAT_INTERPOLAR` — rutas del pipeline de interpolación de video.

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

1. **Hablando** — escucha continua; decís "Miku" para activarla y luego el comando.
2. **Push-to-talk** — mantenés apretada la tecla F22: graba mientras la tenés
   presionada y corta al soltarla (implementación real, no depende solo del
   timeout de silencio).
3. **Escribiendo** — modo texto por consola. Preguntá si querés que además
   hable (para probar el pipeline de TTS) o que sea 100% silencioso.

Para salir: `Ctrl+C` (o escribí "salir" en modo texto).

---

## Cómo funciona (flujo de un comando)

1. **`main.py`** prepara logging, carga la config y ensambla el `Asistente`.
2. Crea el `EventBus` y registra los plugins activos (`SystemControl` por ahora).
3. El `CommandParser` recibe el texto y:
   - atiende **fast path local** (hora, saludos, "acordate que...") sin gastar API;
   - deja responder a un **plugin** si conoce el comando directamente;
   - si hay una **confirmación pendiente** (p. ej. apagar la PC), la resuelve
     antes de seguir — este estado vive en el propio parser y persiste entre
     turnos, no se resetea en cada mensaje;
   - si nada de lo anterior aplica, consulta al **LLM** (Groq, vía `BrainGroq`)
     con las `tools` que publicaron los plugins. Si el LLM llama una tool
     peligrosa (`control_energia`), se pide confirmación explícita antes de
     ejecutarla.
4. La respuesta se habla (VOICEVOX + subtítulos) o se imprime, según el modo.

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

**Disponible hoy:** `plugins/system_control.py`, con las siguientes tools:

| Tool | Qué hace |
|---|---|
| `abrir_programa` | Abre un programa por nombre/alias (AppOpener) |
| `cerrar_programa` | Cierra un proceso vía `taskkill`, solo si está en la whitelist (coincidencia exacta) |
| `controlar_brillo` | Sube, baja o fija el brillo de la pantalla |
| `listar_ventanas` | Lista las ventanas visibles actualmente |
| `mover_ventana` | Mueve una ventana a otro monitor |
| `minimizar_ventana` | Minimiza la ventana de un programa |
| `control_multimedia` | Play / pausa / siguiente / anterior (teclas virtuales) |
| `ajustar_volumen` | Sube, baja o silencia el volumen del sistema |
| `buscar_archivo` | Búsqueda instantánea con Everything (`bin/es.exe`) |
| `control_energia` | Apagar / reiniciar / suspender — **siempre pide confirmación** antes de ejecutar |

---

## Notas técnicas

- **Lazy loading:** todo lo pesado u opcional (PyQt5, pygame, pyttsx3,
  deep-translator, SpeechRecognition, pyaudio, keyboard, AppOpener, pywin32,
  screen-brightness-control) se importa recién cuando el modo/comando lo necesita.
- **Threading:** la voz y la escucha de micrófono corren en hilos separados
  para no bloquear la consola; el overlay de subtítulos corre en su propio
  hilo con event loop de Qt.
- **Cola de voz:** las respuestas se encolan y se reproducen una atrás de otra.
- **VOICEVOX:** el texto de la respuesta se traduce ES→JA (deep-translator) y
  se sintetiza con una voz **genérica** de VOICEVOX (configurable con
  `voicevox_speaker_id`), no una voz clonada de Miku. Si VOICEVOX no responde,
  el asistente intenta arrancarlo automáticamente; si eso también falla, cae
  a `pyttsx3` (voz del sistema).
- **Confirmaciones:** el estado de "esperando confirmación" vive en el
  `CommandParser` (no se pierde entre turnos), así que acciones como apagar
  la PC siempre requieren un sí/no explícito antes de ejecutarse.
- **Memoria:** `core/memoria.py` existe como interfaz pero está **desactivada
  a propósito** (no hay backend conectado todavía). Se puede reactivar más
  adelante con algo liviano como SQLite.
- **Seguridad:** ningún comando de sistema usa `shell=True`; `cerrar_programa`
  valida contra una whitelist exacta antes de matar un proceso.

---

## 🛠️ Próximos pasos (roadmap)

- **Discord**: silenciar/desilenciar, volumen por usuario, expulsar, traducción al chat.
- **Game Booster**: bajar volumen del navegador, pausar Wallpaper Engine, monitorear temperatura.
- **Navegador (Brave)**: abrir/cerrar pestañas, buscar, autocompletar (vía debug port, como el sistema original).
- **Traductor para juegos**: mensajes predefinidos para CS:GO (portugués) y Genshin Impact (inglés).
- **Macros personalizadas**: conectar `data/macros_config.json` a un plugin real ("modo Fortnite", "comedor", etc.).
- **Memoria persistente**: decidir si se reactiva con un backend liviano.
- **Interpolación de video**: conectar `carpeta_videos` / `ruta_bat_interpolar` de `config_local.py` a un comando real.
