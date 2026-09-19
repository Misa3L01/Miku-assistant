# Empaquetado a .exe (Z8) — Miku Assistant

Objetivo: empaquetar como app de escritorio (lanzador tipo app + hotkey F22).
El `.spec` está en `scripts/miku.spec`; deps de build en `requirements-dev.txt`.
Requisito: Z7 estable (bandeja/ventanita/F22) — ya lo está.
El `.exe` NO incluye VOICEVOX: el motor local se referencia por ruta aparte.

## 1) Hooks conocidos (PyInstaller)

- **PyQt5** (subtítulos, bandeja, ventanita): hook oficial / `--collect-all PyQt5`
  (incluir `platforms/qwindows.dll`).
- **pygame**: hook oficial OK (SDL sin consola).
- **SpeechRecognition** + **pyaudio**: pyaudio es extensión C; incluir `.pyd`/`.dll`.
- **discord**: `--collect-data discord` (paquetes `.json`).
- **AppOpener**: `--collect-data AppOpener`.
- **comtypes / pycaw**: `--collect-all comtypes` (si no, falla volumen/mute).
- **keyboard / psutil / Pillow**: hooks oficiales.

## 2) Datos y binarios

- `bin/es.exe` → `--add-binary "bin/es.exe;bin"`.
- `data/` y `config_local.py` NO se empaquetan (secretos/datos del usuario):
  se dejan **junto al `.exe`**. Con el ejecutable empaquetado (`sys.frozen`),
  `config.BASE_DIR` es la carpeta del `.exe`, y de ahí se leen
  `config_local.py`, `data/` y `bin/`.
- Los plugins se cargan con `importlib` (`miku/plugins/registro.py`), así que
  PyInstaller no los detecta solo: `scripts/miku.spec` los incluye con
  `collect_submodules("miku")`. Si agregás un plugin, no hace falta tocar el spec.

## 3) Construir

```bat
pip install -r requirements-dev.txt
pyinstaller --clean scripts/miku.spec
```

Salida: `dist/Miku/Miku.exe`.

## 4) Pruebas mínimas

1. Abrir el `.exe` → aparece bandeja + ventanita; elegir Modo Texto.
2. Escribir un comando simple (fast-path, sin API) → responde.
3. F22 → abre la ventanita de nuevo (no en modo push).
4. "Salir" de la bandeja → cierra sin dejar icono.

## 5) Visión a futuro (documentado, no implementado)

- Motores de voz local/API seleccionables (hoy VOICEVOX es local).
- Auto-actualización e instalador (Inno Setup).

## 6) Alcance de esta sesión

Se documentan hooks + `scripts/miku.spec` + `requirements-dev.txt`. **No** se ejecutó
PyInstaller (depende del entorno del usuario).