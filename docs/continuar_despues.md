# Continuar después — checklist de la "base modular"

> Contexto: reescritura del asistente Miku a una base modular (sin copiar `codigo_viejo/`,
> sin implementar plugins complejos). Trabajo YA guardado en disco y compilando.

## ✓ Últimas acciones realizadas (retomando la tarea)
- **README.md reescrito completo** (estructura, instalación, cómo correr, flujo, plugins, notas técnicas).
- **`.gitignore`** actualizado: agregado `data/miku_memoria/` (persistencia chromadb).
- **`main.py` limpieza**: quitado el import redundante de `SystemControl` dentro de `instalar_core`
  (ya está en el top-level). Queda con `Plugin` y `SystemControl` importados arriba (usados).
- Todo sigue compilando OK (`py_compile`).

## Estado actual (mantener)
- **Estructuras creada/finalizada en disco**:
  - Raíz: `main.py`, `config.py`, `requirements.txt`, `README.md` (todas reescritas).
  - `core/`: `event_bus.py` (preexistente) · `speech_to_text.py` (corregido) ·
    `text_to_speech.py` (reescrito: cola+hilo, lazy loading) · `command_parser.py` (nuevo:
    `BrainGroq` + fast path + tools de plugins + memoria) · `memoria.py` (nuevo, chromadb).
  - `plugins/`: `__init__.py` (base `Plugin` con `tools`/`manejar_tool`) ·
    `system_control.py` (nuevo, abrir/cerrar/brillo/ventanas, imports lazy).
  - `data/`: `preferences.json`, `macros_config.json` (base). La memoria escribirá a `data/miku_memoria`.
  - `docs/continuar_despues.md` (este archivo de seguimiento).
- **Verificado**: todo compila (`py_compile`). Integración base probada con el venv
  (brain "tonto", sin internet): hora por fast path OK, tools de `system_control` OK,
  manejo graceful de AppOpener ausente, tool desconocida → `None`.
- **Sin corrupción de codificación** (todos los archivos `clean` de mojibake).
- No quedan archivos temporales sueltos (`_prueba.py` fue eliminado).

## Lo que FALTA terminar
1. ~~**README.md** — actualizado~~ → HECHO (arriba).
2. **Validar con librerías reales** — en este venv NO están: `chromadb`, `kokoro`, `rvc-python`,
   `numpy`, `soundfile`, `pygame`, `AppOpener`, `screen-brightness-control`.
   Probar `main.py` completo en la máquina real tras instalar deps.
3. ~~**`data/` en gitignore**~~ → HECHO (agregado `data/miku_memoria/`).
4. **Pulido menor pendiente** — revisar fast-path de memoria con `_quitar_preposicion`
   (aproximado, pero funcional); (el `Plugin` en `main.py` SÍ se usa, ya no es eliminable).
5. **Confirmar alcance** con el usuario: NO se implementan plugins complejos en esta fase
   (navegador, Discord, TIDAL, energía, archivos), solo el marco `Plugin`+`tools` para crecer.

## Cómo seguir (sugerencia)
1. En la máquina real: `pip install -r requirements.txt` → `python main.py` → elegir modo.
2. Validar la voz completa (Kokoro → RVC → pygame) y la búsqueda de memoria (chromadb) con modelos reales.
3. Decidir con el usuario cuál plugin complejo implementar primero sobre el marco modular.

