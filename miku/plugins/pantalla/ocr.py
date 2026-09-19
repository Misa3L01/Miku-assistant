# -*- coding: utf-8 -*-
"""
ocr.py - OCR de pantalla: leer texto de lo que se ve (leer_pantalla).

Publica las tools ``leer_pantalla`` (alias ``que_dice_esta_ventana``). Captura
la pantalla (o una ventana/monitor) y extrae el texto con OCR.

Elección de motor (+ justificación):
    1. **Tesseract** (vía ``pytesseract``) como primario: es el motor OCR libre
       más maduro, con paquetes de idioma (``spa`` para español) muy usables.
       Es una dependencia OPCIONAL y lazy: si no está, se degrada.
    2. **Windows.Media.Ocr** (WinRT, vía PowerShell) como fallback SIN instalar
       nada: viene con Windows 10/11. La calidad depende de los paquetes de
       idioma instalados en el sistema, pero sirve cuando Tesseract no está.

Todo es best-effort: si ningún motor está disponible, se avisa con claridad
(no inventa). Imports pesados SIEMPRE lazy.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import os
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.ocr")


class Ocr(Plugin):
    """Lee el texto en pantalla con OCR (Tesseract o Windows.Media.Ocr)."""

    nombre = "ocr"
    descripcion = "Lee el texto que se ve en pantalla (OCR)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "leer_pantalla",
                "description": "Captura la pantalla y devuelve el TEXTO que "
                               "aparece en ella (OCR). Sirve para leer algo "
                               "que no se puede copiar. Ej: 'leé la pantalla', "
                               "'qué dice esta ventana'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "monitor": {
                            "type": "integer",
                            "description": "Opcional: monitor 1-based a leer. "
                                           "Si se omite, toda la pantalla.",
                        },
                    },
                },
            },
        },
    ]

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        logger.info("Plugin ocr listo (tesseract=%s).",
                    "sí" if self._tesseract_disponible() else "no")

    # ---------------- Despacho de tools ---------------- #
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool in ("leer_pantalla", "que_dice_esta_ventana"):
            return self.leer_pantalla(args.get("monitor"))
        return None

    # ---------------- Motor disponible ----------------
    def _tesseract_disponible(self) -> bool:
        """True si pytesseract + tesseract están listos."""
        try:
            import pytesseract  # noqa: F401  (lazy)
        except Exception:  # noqa: BLE001
            return False
        ruta = config_mod.config.tesseract_ruta
        if ruta:
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = ruta
            except Exception:  # noqa: BLE001
                return False
        # Verificamos que el binario responda.
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------------- Acción principal ----------------
    def leer_pantalla(self, monitor: Optional[int] = None) -> str:
        """Captura la pantalla y devuelve el texto reconocido (o un aviso)."""
        ruta_img = self._capturar(monitor)
        if ruta_img is None:
            return "No pude capturar la pantalla para leerla."

        try:
            if self._tesseract_disponible():
                texto = self._ocr_tesseract(ruta_img)
            else:
                texto = self._ocr_windows(ruta_img)
        finally:
            # Limpiamos el archivo temporal.
            try:
                os.remove(ruta_img)
            except Exception:  # noqa: BLE001
                pass

        if texto is None:
            return ("No tengo un motor de OCR disponible. Instalá Tesseract + "
                    "pytesseract (recomendado) o usá Windows 10/11 con el "
                    "paquete de idioma correspondiente.")
        texto = texto.strip()
        if not texto:
            return "Leí la pantalla pero no encontré texto legible."
        # Limitamos el largo para que no sea un muro interminable por voz.
        if len(texto) > 600:
            texto = texto[:600].rsplit(" ", 1)[0] + "..."
        return "Esto dice la pantalla: " + texto

    # ---------------- Captura ----------------
    def _capturar(self, monitor: Optional[int]) -> Optional[str]:
        """Captura a un PNG temporal y devuelve su ruta (o None)."""
        try:
            from PIL import ImageGrab
        except Exception:  # noqa: BLE001
            logger.error("Pillow no disponible para OCR.")
            return None

        bbox = None
        if monitor is not None:
            try:
                bbox = _bbox_monitor(int(monitor))
            except Exception:  # noqa: BLE001
                bbox = None

        try:
            try:
                # ``all_screens=True`` también con bbox (si no, un monitor
                # secundario sale negro en Pillow/Windows).
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
            except TypeError:
                img = ImageGrab.grab(bbox=bbox)
        except Exception as e:  # noqa: BLE001
            logger.error("Error capturando para OCR: %s", e)
            return None

        try:
            fd, ruta = tempfile.mkstemp(prefix="miku_ocr_", suffix=".png")
            os.close(fd)
            img.save(ruta, "PNG")
            return ruta
        except Exception as e:  # noqa: BLE001
            logger.error("No pude guardar la captura para OCR: %s", e)
            return None

    # ---------------- Motor 1: Tesseract ----------------
    def _ocr_tesseract(self, ruta_img: str) -> Optional[str]:
        try:
            import pytesseract
            from PIL import Image
            idioma = config_mod.config.ocr_idioma or "spa"
            with Image.open(ruta_img) as img:
                texto = pytesseract.image_to_string(img, lang=idioma)
            return texto
        except Exception as e:  # noqa: BLE001
            logger.error("Tesseract falló: %s", e)
            return None

    # ---------------- Motor 2: Windows.Media.Ocr (WinRT) ----------------
    def _ocr_windows(self, ruta_img: str) -> Optional[str]:
        """OCR con Windows.Media.Ocr vía PowerShell + WinRT.

        No requiere instalar nada extra. Best-effort: depende del paquete de
        idioma del sistema.
        """
        ruta_ps = ruta_img.replace("'", "''")
        ps = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime > $null; "
            "$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | "
            "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
            "function Await($op, $t) { $m = $asTask.MakeGenericMethod($t); $task = $m.Invoke($null, @($op)); $task.Wait(); $task.Result }; "
            "[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] > $null; "
            "[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] > $null; "
            "[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] > $null; "
            "$f = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('" + ruta_ps + "')) ([Windows.Storage.StorageFile]); "
            "$s = Await ($f.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream]); "
            "$d = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($s)) ([Windows.Graphics.Imaging.BitmapDecoder]); "
            "$bmp = Await ($d.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap]); "
            "$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages(); "
            "if ($eng -eq $null) { Write-Output ''; exit }; "
            "$res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult]); "
            "Write-Output $res.Text;"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle",
                 "Hidden", "-Command", ps],
                shell=False, timeout=25, capture_output=True,
                encoding="utf-8", errors="replace",
                creationflags=_sin_ventana())
            salida = (proc.stdout or "").strip()
            # Si no hay motor/idioma, devolvemos None para que se avise.
            if proc.returncode != 0 and not salida:
                return None
            return salida
        except Exception as e:  # noqa: BLE001
            logger.debug("Windows OCR falló: %s", e)
            return None


def _bbox_monitor(monitor: int) -> Optional[tuple]:
    """Bounding box (x1,y1,x2,y2) del monitor (o None si no se puede)."""
    try:
        import win32api  # type: ignore
        monitores = win32api.EnumDisplayMonitors()
        idx = int(monitor) - 1
        if idx < 0 or idx >= len(monitores):
            return None
        info = win32api.GetMonitorInfo(monitores[idx][0])
        x1, y1, x2, y2 = info["Monitor"]
        return (int(x1), int(y1), int(x2), int(y2))
    except Exception:  # noqa: BLE001
        return None


def _sin_ventana() -> int:
    """Flags de creación para no abrir ventana de consola (Windows)."""
    try:
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return 0x08000000  # CREATE_NO_WINDOW