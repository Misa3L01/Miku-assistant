# Empaquetado a .exe

Empaqueta Miku como app de escritorio con PyInstaller. La receta está en `scripts/miku.spec` y las
herramientas de build en `requirements-dev.txt`. **No se prueba en cada cambio**: el resultado depende
del entorno, así que después de construirlo seguí la lista de comprobación del final.

El `.exe` no incluye VOICEVOX: el motor de voz se referencia por ruta aparte (`VOICEVOX_RUN_EXE`).

## Hooks conocidos

- **PyQt5** (bandeja, subtítulos, ventana de depuración): hook oficial o `--collect-all PyQt5`
  (incluir `platforms/qwindows.dll`).
- **pygame**: hook oficial.
- **SpeechRecognition + pyaudio**: pyaudio es una extensión C; incluir el `.pyd` / `.dll`.
- **discord.py**: `--collect-data discord`.
- **AppOpener**: `--collect-data AppOpener`.
- **comtypes / pycaw**: `--collect-all comtypes` (sin esto fallan el volumen y el silencio).
- **keyboard / psutil / Pillow**: hooks oficiales.

## Datos y binarios

- `bin/es.exe` (Everything) va con `--add-binary "bin/es.exe;bin"`.
- `data/` y `config_local.py` **no** se empaquetan (secretos y datos del usuario): van **junto al `.exe`**.
  Con el ejecutable (`sys.frozen`), `BASE_DIR` es la carpeta del `.exe` y de ahí se leen
  `config_local.py`, `data/` y `bin/`.
- Los plugins se cargan con `importlib` (`miku/plugins/registro.py`), que PyInstaller no detecta solo:
  `scripts/miku.spec` los incluye con `collect_submodules("miku")`. Agregar un plugin no exige tocar el spec.

## Construir

```bat
pip install -r requirements.txt -r requirements-dev.txt
pyinstaller --clean scripts/miku.spec
```

Salida: `dist/Miku/Miku.exe`.

## Comprobación mínima

1. Abrir el `.exe`: aparece el icono de la bandeja.
2. Cambiar a modo texto desde la bandeja y escribir un comando simple ("qué hora es"): responde sin API.
3. Apretar la tecla de invocación (F13): Miku saluda y escucha.
4. "Salir" en la bandeja: cierra sin dejar el icono.

## Pendiente

- Instalador (Inno Setup) y auto-actualización.
