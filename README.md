# Miku Assistant

Asistente de voz con personalidad de **Hatsune Miku**, reescrito sobre una **base
modular y escalable** para Windows (Python 3.10+). A diferencia del código viejo
(que quedó sólo de referencia en `codigo_viejo/` para no perderlo), esta versión
se organiza en módulos independientes y conectables.

> **Estado actual:** se implementó el **núcleo/base modular**. Los plugins
> complejos del sistema anterior (navegador, Discord, TIDAL, temperatura,
> energía, búsqueda de archivos, interpolar video, cálculos de precio...) **no**
> forman parte de esta fase; el marco de plugins (`Plugin` + `tools`) ya expone
> el "enchufe" para crecer por partes.

---

## Estructura de carpetas

```
miku-assistant/
├── main.py                  # Punto de entrada (orquesta todo el asistente)
├── config.py                # Carga de config.json + claves (Config)
├── config_local.py          # (opcional, NO versionar) secretos locales
├── requirements.txt         # Dependencias agrupadas por función
│
├── core/                    # Núcleo reutilizable
│   ├── __init__.py
│   ├── event_bus.py         # Bus de eventos + registro de plugins
│   ├── speech_to_text.py    # STT (wake word + comandos, API Groq/Whisper)
│   ├── text_to_speech.py    # TTS (Kokoro → RVC → pygame), cola + lazy loading
│   ├── command_parser.py    # "Cerebro": fast path local + LLM (tools)
│   └── memoria.py           # Memoria persistente (chromadb) en data/miku_memoria
│
├── plugins/                 # Capacidades enchufables
│   ├── __init__.py          # Base Plugin + registro
│   └── system_control.py    # Abrir/cerrar programas, brillo, ventanas
│
├── data/                    # Datos persistentes del usuario (gitignored)
│   ├── preferences.json     # Preferencias (juegos, programas, notas)
│   ├── macros_config.json   # Macros/alias configurables
│   └── miku_memoria/        # Base de memoria del asistente (auto)
│
├── codigo_viejo/            # Referencia del sistema original (NO se usa)
├── .gitignore
└── docs/continuar_despues.md
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
   > La voz de Miku (RVC) y la memoria (chromadb) necesitan `torch` con CUDA.
   > En `requirements.txt` aparecen comentadas; descomentá/installá según tu GPU.

3. Configurar claves y rutas en `config.json` (y opcionalmente `config_local.py`
   para secretos). Claves mínimas:
   - `groq_api_key` — usada por el cerebro y (si no hay otra) por el STT.
   - `modelo_miku` — ruta al `.pth` de RVC.

   Si no tenés aún la clave de Groq ni los modelos de voz, el asistente igual
   arranca en **modo texto** con las respuestas mostradas por consola.

---

## Cómo correr

```bash
python main.py
```

Al arrancar te va a preguntar el **modo de entrada** (Enter usa el default de
`config.json`):

1. **Hablando** — escucha continua; decís "Miku" para activarla y luego el comando.
2. **Push-to-talk** — grabás una frase al soltar la tecla de voz (F22 del HP Omen).
3. **Escribiendo** — escribís los comandos en la consola (ideal para probar).

Para salir: `Ctrl+C` (o escribí "salir").

> Sugerencia de arranque rápido sin modelos de voz instalados:
> editá `config.json` → `"modo_entrada": "texto"` y corré `python main.py`,
> elegí la opción 3.

---

## Cómo funciona (flujo de un comando)

1. **`main.py`** prepara logging, config y ensambla el `Asistente`.
2. Crea el `EventBus` y registra los plugins activos (`core/event_bus.py`).
3. instancia la memoria (`core/memoria.py`), la voz (`core/text_to_speech.py`),
   y el parser (`core/command_parser.py`).
4. Según el modo, entra texto/voz al `CommandParser.procesar()`, que:
   - atiende **fast path local** (hora, saludos, "acordate que...") sin gastar API;
   - deja responder a un **plugin** si conoce el comando;
   - si no, consulta al **LLM** (`BrainGroq`) con las `tools` que declararon los
     plugins. Si el LLM llama una tool, se ejecuta vía `manejar_tool()` y se
     combinan respuesta + resultado.
5. La respuesta se pasa a la voz de Miku (o se imprime en modo texto).

---

## Plugins (cómo agregar capacidades en el futuro)

Cada plugin es una clase en `plugins/` que:
- hereda de la base `Plugin`,
- define `nombre` y `descripcion`,
- opcionalmente publica `tools` (schemas tipo OpenAI function-calling) y
  el método `manejar_tool(nombre, args, contexto)`.

Registrarlo en `main.instalar_core()` dentro de `candidatos`. El framework
(`core.event_bus.EventBus` y `plugins.registrar_plugins`) hace el resto:
el cerebro "verá" esas tools y podrá invocarlas por voz/texto.

**Ya disponible:** `plugins/system_control.py` — abrir/cerrar programas,
brillo de pantalla y gestión básica de ventanas (listar/minimizar/mover).

---

## Notas técnicas

- **Lazy loading:** los modelos pesados (Kokoro, RVC, chromadb, win32, etc.) se
  cargan recién cuando hace falta, para acelerar el arranque.
- **Threading:** la voz de Miku y la escucha del micrófono corren en hilos
  separados para no bloquear la consola.
- **Cola de voz:** las respuestas se encolan y reproducen una atrás de la otra.
- **Memoria:** guarda recuerdos enchromadb con fecha; se le inyectan al prompt
  los relevantes al consultar al cerebro.
- **Cache:** respuestas frecuentes se cachean para no repetir llamadas a la API.

---

## 🎯 Funcionalidades Base y Heredadas

El asistente hereda y mejora las siguientes capacidades del sistema original:

### Control de programas y sistema
- **Abrir/cerrar aplicaciones** favoritas (Brave, Discord, Tidal, VSCode) y cualquier otra registrada.
- **Cierre seguro** de procesos mediante lista blanca (sin `shell=True`).
- **Control multimedia** global: pausa/reanudación, siguiente/anterior (teclas multimedia).
- **Volumen del sistema**: subir/bajar volumen general y por aplicación (navegador, reproductor).
- **Gestión de ventanas**: listar, minimizar, maximizar, mover entre monitores.
- **Cambio de resolución** de monitores (preparado para macros de gaming).
- **Gestion de recuerdos para recordatorios** por ejemplo alarmas  orecordatorios de hacer x cosa en x minutos.

### Búsqueda y archivos
- **Búsqueda instantánea** mediante indexación con Everything (`es.exe`).

### Respuestas inteligentes
- **Preguntas cotidianas** resueltas vía LLM (Groq) con herramientas (tools) declaradas por plugins.
- **Memoria persistente** (opcional, vía chromadb) para recordar información relevante.

### Interfaz de voz mejorada
- **Activación dual**: palabra clave "Miku" (escucha continua) o **push-to-talk** (tecla F22).
- **Voz japonesa** con VOICEVOX (traducción ES→JA mediante deep-translator) y fallback a pyttsx3.
- **Subtítulos estilo anime** en overlay transparente (PyQt5) mostrando el texto en español.

### Configuración flexible
- **Preferencias de usuario** en `data/preferences.json`.
- **Secretos y rutas privadas** en `config_local.py` (no versionado) con plantilla de ejemplo.
- **Modo texto** para pruebas sin micrófono, y ahora **con salida de voz obligatoria** para verificar TTS.

### Seguridad
- **Validación de comandos peligrosos** (apagar/reiniciar/suspender) con flujo de confirmación.
- **Whitelist** de procesos permitidos para cierre.

---

## 🛠️ Próximos pasos (roadmap)

- Verificar que todas las funcionalidades anteriores esten implementadas correctamente.
- **Discord**: silenciar/desilenciar, volumen por usuario, expulsar, traducción al chat.
- **Game Booster**: bajar volumen del navegador, pausar Wallpaper Engine, monitorizar temperatura.
- **Traductor para juegos**: mensajes predefinidos para CS:GO (portugués) y Genshin Impact (inglés).
- **Macros personalizadas**: "modo Fortnite" (cambiar resolución), "comedor" (abrir web y autocompletar).
- **Push-to-talk real**: grabación continua mientras se mantiene presionada la tecla (buffer propio con pyaudio).
