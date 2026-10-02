# Miku Assistant

Asistente de voz personal para Windows (Python 3.10, PyQt5). Repo público de Misa3L01, "todos los derechos reservados".

## Entrada
- `python main.py` → `miku/app.py` (instancia única; `--silencioso`, `--invocar`).
- Config: defaults (`miku/ajustes/esquema.py`) < `data/preferences.json` < `config_local.py` < variables de entorno.

## Módulos clave
- `miku/ajustes/`: esquema de opciones, carga, escritura segura de `config_local.py`, `python -m miku.ajustes estado|validar|completar|ejemplo`.
- `miku/cerebro/parser.py`: LLM (Groq/OpenAI-compatible), streaming, tools, confirmaciones. `enrutador.py` elige tools.
- `miku/voz/entrada/`: `escucha.py` (bucle mic → palabra clave → comando), `wake.py` (openWakeWord), `vad.py` (silero), `transcriptores.py`.
- `miku/voz/salida/tts.py`: voz japonesa con VOICEVOX o AivisSpeech (misma API HTTP), caché de audio, cambio de motor en caliente.
- `miku/ui/`: bandeja, subtítulos, ventana de Configuración (escribe en `config_local.py`).
- `miku/plugins/`: una carpeta por tema; contrato en `miku/plugins/base.py`.
- `miku/servicios/`: eventos, scheduler, métricas de latencia, proactivo, arranque con Windows.

## Tests
- Uno solo: `venv\Scripts\python.exe -m pytest tests/test_configuracion.py -q --tb=short -x`
- Todos: `python -m pytest` (Qt sin pantalla: `QT_QPA_PLATFORM=offscreen`). Lint: `python -m ruff check .`
- No hacen falta servicios reales: los tests usan fakes (ver `tests/conftest.py`). CI en GitHub Actions (Windows) instala solo requirements.txt: numpy/onnxruntime/openwakeword no están, usar `pytest.importorskip`.
- Modo texto (sin micrófono ni voz): `python main.py` y elegir texto.

## Contratos que no se rompen
- `import onnxruntime` ANTES de PyQt5 (si no, crash).
- Mientras Miku habla el micrófono no graba (`esperar_libre`): nada que bloquee la fila de voz.
- Cada opción nueva va en `esquema.py` y se regenera `config_local.py.example` (`python -m miku.ajustes ejemplo`); el CI lo verifica.
- Repo público: nunca versionar `config_local.py*`, `data/`, `extern/`, `entrenamiento/`, modelos (.onnx/.aivmx), claves ni datos personales.
- Contraseña del comedor: solo en el Administrador de credenciales; no imprimirla.

## No leer
`extern/`, `data/`, `venv/`, `bin/`, `entrenamiento/`, `config_local.py*`, modelos y audios (bloqueado en `.claude/settings.json`). Logs: grep, nunca el archivo entero (`data/miku.log`).

## Estilo
Español rioplatense en código, comentarios y mensajes; commits Conventional Commits en español.
