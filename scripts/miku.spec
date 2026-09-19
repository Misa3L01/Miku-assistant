# miku.spec - Spec base de PyInstaller (Z8). Ajustar rutas segun el entorno.
# Build (desde la raiz):  pyinstaller --clean scripts/miku.spec   ->   dist/Miku/Miku.exe
# NOTA: sin probar desde la reestructuracion (PyInstaller no esta instalado en este entorno).
# Ver docs/empaquetado.md para hooks y notas.

import os
from PyInstaller.utils.hooks import (collect_all, collect_data_files,
                                     collect_submodules)

# Raiz del proyecto = carpeta de arriba de scripts/ (SPECPATH lo define PyInstaller).
RAIZ = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = []
binaries = []
hiddenimports = []

# Paquetes que necesitan datos/hook especial (best-effort).
for pkg in ("PyQt5", "pygame", "comtypes", "discord", "AppOpener"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Everything CLI (si existe).
_es = os.path.join(RAIZ, "bin", "es.exe")
if os.path.exists(_es):
    binaries.append((_es, "bin"))

# Los plugins se cargan con importlib (miku/plugins/registro.py): PyInstaller no los
# ve como imports estaticos, hay que incluirlos a mano o el .exe arranca sin ellos.
hiddenimports += collect_submodules("miku")

hiddenimports += [
    "pycaw.pycaw", "comtypes", "keyboard", "screen_brightness_control",
    "psutil", "PIL.ImageGrab", "speech_recognition", "pyaudio",
]

a = Analysis(
    [os.path.join(RAIZ, "main.py")],
    pathex=[RAIZ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Miku",
    debug=False,
    # console=True para ver logs; poner False para app sin consola (bandeja).
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Miku",
)