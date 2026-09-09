# Informe de implementación — Base funcional de Miku Assistant

**Alcance:** Modo texto con voz, auto-arranque de VOICEVOX, control multimedia, volumen, búsqueda con Everything, verificación de imports/dependencias y estado de las funcionalidades base.

---

## 1. Resumen de cambios por archivo

### `core/text_to_speech.py` *(TTS / voz)*
**Añadido:**
- Imports de módulos stdlib: `os`, `subprocess` (en realidad ya en uso por el pipeline de reproducción, y necesarios para el auto-arranque).
- Constante `VOICEVOX_RUN_EXE` = `<raíz>/extern/VOICEVOX/vv-engine/run.exe` (ruta por defecto del motor).
- Método `_buscar_run_voicevox()`: localiza el `run.exe` con prioridad a un override de `config_local.py` (`VOICEVOX_RUN_EXE`) y luego el default relativo.
- Método `_asegurar_voicevox()`:
  1. Si el servidor ya responde (`GET /speakers`), devuelve `True`.
  2. Si no, **lanza** `vv-engine/run.exe` una sola vez en segundo plano (sin GUI/no-window, sin `shell=True`) y espera hasta ~12 s a que levante el puerto.
  3. Devuelve `True/False`; si no quedó activo se avisa y **se usa `pyttsx3` de fallback**.
- Cache interna `_voicevox_intento_lanzado` para no re-lanzar el motor a cada frase.

**Modificado:**
- `_sintetizar_y_reproducir()` ahora llama a `_asegurar_voicevox()` en vez de solo `_verificar_voicevox()`. Es decir, intenta iniciar VOICEVOX automáticamente antes de rendirse al fallback.

### `main.py` *(orquestación)*
**Modificado:**
- `_preparar_voz()` ahora recibe `subtitulos: bool = True` (antes fijo).
- `run_modo_texto()` (opción 3): ahora **fuerza el uso del motor de voz** llamando `_preparar_voz(asistente, subtitulos=True)`. De esta forma, cada respuesta procesada por `Asistente.responder()` se **habla** (TTS) y opcionalmente muestra subtítulos, permitiendo probar el pipeline de voz sin micrófono. Se actualizó la cabecera impresa para reflejarlo.
- `responder()` no cambió de lógica: ya habla si `self.voice is not None`, lo cual ahora ocurre en modo texto.

### `plugins/system_control.py` *(control de sistema)*
**Añadido:**
- Imports: `ctypes` y `os` (stdlib).
- 3 tools nuevas publicables al cerebro:
  - `control_multimedia` (acciones `play|pausa|play_pausa|siguiente|anterior`) → teclas multimedia por VK.
  - `ajustar_volumen` (acciones `subir|bajar|silenciar`, con `paso` opcional).
  - `buscar_archivo` (búsqueda con Everything `es.exe`).
- Métodos:
  - `_enviar_tecla_virtual(clave)`: emula tecla multimedia/volumen. Intenta primero con la librería `keyboard` (ya en requirements) y, si falla, con `ctypes.user32.keybd_event` (VK). → evita dependencias nuevas y reduce flakiness.
  - `control_multimedia(accion)`.
  - `ajustar_volumen(accion, paso)`.
  - `buscar_archivo(nombre)`.
  - `_ruta_es_ejecutable()`: busca `es.exe` (en `bin/` o en la ruta `ruta_everything_es` de config); si no existe devuelve un mensaje claro.
- Despachos en `manejar_tool()` para las 3 tools.

### `requirements.txt`
No cambió en esta sesión; se **verificó** que contiene todas las librerías realmente usadas (ver §3).

### Otros (de la sesión previa, ya aplicados y re-verificados)
Para completar el cuadro, sigue vigente lo hecho en la ronda anterior:
- `config.py`: defaults limpios de rutas privadas + eliminado el bloque de exportación a `globals()`.
- `config_local.py` y `config_local.py.example` (plantilla versionada) creados.
- `core/command_parser.py`: estado de confirmación persistente en el parser + tool `control_energia` con gating de seguridad.
- `core/speech_to_text.py`: push-to-talk real (`capturar_para_push`, `detener_captura_activa`).
- `main.py`: confirmaciones y push-to-talk conectados.

---

## 2. Decisiones técnicas

### Modo texto con voz
- Elegí **reutilizar `responder()`** (que ya hace voz/print según `self.voice`) en lugar de duplicar el flujo. Así, en modo texto basta con forzar `_preparar_voz(...)`. Esto mantiene un único camino de "procesar → emitir".
- Los subtítulos tienen fallback silencioso si falta PyQt5 (se castea `_subtitulos=False`), por lo que degrada sin romper.

### Auto-arranque de VOICEVOX
- El motor de VOICEVOX arranca solo **cuando se va a hablar** y solo si el puerto no responde. No se abre GUI: uso `CREATE_NO_WINDOW` para no molestar.
- Prioridad de localización: override en `config_local.VOICEVOX_RUN_EXE` → default relativo `extern/VOICEVOX/vv-engine/run.exe`. Verificado que existe.
- Si no aparece el binario o no levanta, queda un mensaje claro y cae al fallback `pyttsx3` (pensado para cuando la voz original no esté o falle la red).

### Multimedia / volumen
- Uso de **teclas virtuales de Windows (VK)** envueltas en `_enviar_tecla_virtual`. Esto funciona con la mayoría de reproductores (Spotify, navegador, VLC, etc.) sin tocar cada app por separado.
- Primer intento con la librería `keyboard` (ya instalada) y fallback con `ctypes.keybd_event` → no agrego dependencias nuevas (`pycaw`, `pyautogui`).

### Búsqueda con Everything
- Everything expone una CLI segura (`es.exe -s -n <n> <query>`): lo ejecuto por `subprocess.run(..., shell=False)` para **evitar inyección**.
- Si no está el binario, devuelvo un mensaje instructivo (estado "preparado pero requiere `bin/es.exe`") en lugar de fallar.

---

## 3. Verificación de imports y dependencias

Todos los módulos compilan (`python -m py_compile ...`) y el arranque en modo texto **no carga** PyQt5 / pygame / pyttsx3 / speech_recognition / pyaudio en importa de módulo: esos se cargan con *lazy importing* dentro de los métodos, por lo que **no hay rojos al importar el paquete**.

Librerías que quedan a nivel de módulo (no lazy):
- `requests` → usada en `config`-guard `BrainGroq.consultar` y en `text_to_speech` (verificación/síntesis). Instalada.
- Stdlib: `json`, `os`, `subprocess`, `ctypes`, `queue`, `threading`, `re`, `typing`, `datetime`, `pathlib` — todas disponibles.

Lazy dentro de métodos (por lo tanto no generan rojo en import de módulo, sino en ejecución solo si el método se usa):
- `PyQt5` (subtítulos), `pygame` (audio), `pyttsx3` (fallback voz), `deep-translator` (traducción), `SpeechRecognition` + `pyaudio` (STT), `keyboard` (push-to-talk, multimedia), `AppOpener` (abrir programas), `pywin32` (ventanas/brillo), `screen-brightness-control` (brillo).

`requirements.txt` ya lista todas (verificadas): `requests`, `SpeechRecognition`, `pyaudio`, `pygame`, `pyttsx3`, `deep-translator`, `PyQt5`, `AppOpener`, `pywin32`, `screen-brightness-control`, `keyboard`.

---

## 4. Funcionalidades base (estado)

| Capacidad | Estado en esta base | Cómo confirmar |
|---|---|---|
| Abrir programas | ✅ Implementado (`abrir_programa` + AppOpener) | Decí/“abrí Discord” |
| Cerrar programas (whitelist exacta) | ✅ Implementado (`cerrar_programa` + taskkill con `shell=False`) | “cerrá Discord” |
| Brillo | ✅ Implementado | “subí/bajá el brillo” |
| Ventanas: listar/minimizar/mover | ✅ Implementado | “qué ventanas hay”, “minimizá X”, “mové X al monitor 2” |
| Control multimedia (play/pausa/siguiente/anterior) | ✅ **Añadido** en esta sesión (teclas VK) | Reproducir una canción y “siguiente” / “pausá” |
| Volumen (subir/bajar/silenciar) | ✅ **Añadido** en esta sesión | “subí el volumen”, “silenciá” |
| Control energía (apagar/reiniciar/suspender) | ✅ Implementado + confirmación obligatoria | “apagá la pc” → pedirá “sí/no” |
| Búsqueda de archivos (Everything) | 🟡 **Preparado** (requiere `bin/es.exe`) | Copiar `es.exe` y “buscá <archivo>” |
| Respuestas cotidianas vía Groq (tools) | ✅ Implementado en parser + tools de plugins | Requiere `GROQ_API_KEY` configurada |
| Voz VOICEVOX | ✅ Implementado + auto-arranque (esta sesión) | Opción 3 (modo texto con voz) |
| Subtítulos overlay | ✅ Implementado (requiere PyQt5) | Modo voz/push o texto si PyQt present | 

> **Nota importante:** abrir/cerrar/brillo/multimedia/volumen dependen de *hardware y librerías locales*; no es posible validarlos de forma 100% headless. Los métodos devuelven respuestas controladas y no rompen si falta el módulo.

---

## 5. Instrucciones de prueba

### Prerequisito
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### A) Probar el modo texto con voz (TTS) sin micrófono
1. En `config_local.py` definí tu `GROQ_API_KEY` (si querés respuestas del LLM; sin clave igual responde los fast-path como hora/saludos).
2. Ejecutá `python main.py`, elegí la **opción 3**.
3. Escribí por ejemplo `hola` o `qué hora es`.
   - Verás el texto en consola **y** la voz (VOICEVOX si está activo, o pyttsx3 de fallback). Esa opción fuerza el TTS.
   - Opcional: si tenés `extern/VOICEVOX` completo y no está corriendo, la primera frase intentará **arrancar** `vv-engine/run.exe` automáticamente.

### B) Probar control multimedia / volumen / programas
1. Iniciá una reproducción en Spotify/Tidal (o un video).
2. En consola (opción 3) escribí: `pausá la música`, `siguiente tema`, `subí el volumen`, `silenciá`.
   - Signo de éxito: la reproducción responde (las teclas VK). Verás la confirmación de Miku.
3. `abrí Discord`, `cerrá Discord` (whitelist), `qué ventanas hay`, `minimizá <ventana>`.

### C) Probar confirmación de acciones peligrosas
- `apagá la pc` → Miku debe responder pidiendo confirmación. Respondé `sí`/`no`. *(En un equipo de pruebas, evitar confirmar para no apagar.)*

### D) Probar búsqueda con Everything
- Copiá `es.exe` a la carpeta `bin/` (junto al proyecto) o setea `ruta_everything_es` en `config_local.py`.
- `buscá informe` → devolverá los primeros resultados.

### E) Probar respuesta cotidiana (LLM con tools)
- Teniendo `GROQ_API_KEY` en `config_local.py`, preguntá algo cotidiano: “¿qué es un agujero negro?”, y después una acción: “abrímelo tal programa”. El parser usará function-calling para invocar la tool.

---

## 6. Tareas completadas y pendientes

### Completadas
- Modo texto (opción 3) ahora reproduce voz (TTS), cargándolo lazy pero forzado.
- Auto-arranque de VOICEVOX cuando el servidor no está (con cierre y fallback a pyttsx3).
- Control multimedia (play/pausa/siguiente/anterior) mediante teclas virtuales.
- Ajuste de volumen (subir/bajar/silenciar).
- Búsqueda preparada con Everything (`es.exe`), con manejo claro si no está.
- Verificación de imports/dependencias y de que `requirements.txt` cubre lo usado.
- Compilación y smoke tests (10 tools registradas, voz instanciada).

### Pendientes / no realizado
- **Verificación auditiva y de hardware** (volumen/multimedia/búsqueda) requiere correr en la máquina real; no pude validar salida de audio/allí.
- **Disponibilidad de `es.exe`**: no viene incluido; hay que copiar el binario de Everything.
- **Respuestas del LLM** sin clave de Groq no se prueban (solo fast-path locales). Se necesita `GROQ_API_KEY`.
- **Calidad del español**: VOICEVOX está pensado para japonés → el texto se traduce ES→JA vía `deep-translator`; si la red/Google falla, se usa `pyttsx3`. La aceptación depende de la configuración del hablante (`voicevox_speaker_id`).

---

## 7. Roadmap sugerido para la siguiente etapa

1. **Plugins complejos heredados (prioridad alta):**
   - **Navegador (Brave/Chrome/Edge)** por CDP: abrir/cerrar pestañas, buscar, autocompletar. (El sistema viejo ya usaba un debug port → reusar.)
   - **Discord**: silenciar/desilenciar, volumen por usuario, expulsar, traducción al chat.
   - **Game Booster**: bajar volumen del navegador, pausar Wallpaper Engine, monitorizar temperatura/uso.
   - **TIDAL / multimedia** por app (ahora es global con VK; el control "por aplicación" requiere API de cada reproductor).
2. **Sistema de memoria** (reactivar un backend liviano, p. ej. SQLite en `data/`) y conectarlo al parser.
3. **Macros** desde `data/macros_config.json`: implementar "modo Fortnite" (resolución), "comedor" (web + autocompletar).
4. **Interpolador de video y búsqueda Everything robusta** (enlazar rutas privadas a `config_local.py` y añadir el binario).
5. **Calidad de voz**: testear distintos `speaker_id` de VOICEVOX y decidir persistencia.
6. **Tests automatizados** para el flujo `procesar()` (fast-path, confirmación, tools) con un cerebro simulado, para blindar el refactor.
7. **Empaquetado/instalación**: crear `run.ps1`/`.bat` que active el venv, arranque VOICEVOX si hace falta y lance `python main.py`.
