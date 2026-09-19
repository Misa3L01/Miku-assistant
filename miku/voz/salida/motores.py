"""
motores.py - Motores de voz alternativos a VOICEVOX ("voces custom").

VOICEVOX habla japonés con voces de anime; para hablar **en español** (o con una voz propia) hace
falta otro motor. En vez de acoplarse a uno, Miku ejecuta **un comando que vos configurás** y que
genera un WAV: sirve para Piper, XTTS, GPT-SoVITS, RVC o cualquier programa con línea de comandos.

    TTS_MOTOR = "comando"
    TTS_IDIOMA = "es"
    TTS_COMANDO = r'piper --model C:\\voces\\es_AR-daniela.onnx --output_file {salida}'

Cómo se llama:
    * ``{salida}`` se reemplaza por la ruta del WAV que el comando debe escribir (obligatorio).
    * ``{texto}`` (opcional) se reemplaza por el texto; si no aparece, el texto entra por la entrada
      estándar (stdin) en UTF-8, que es lo que hace Piper.
    * Se ejecuta **sin shell** (no se interpretan ``&``, ``|``, etc.), con tiempo máximo.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import tempfile
from typing import List, Optional

from miku.plataforma.subprocesos import sin_ventana

logger = logging.getLogger("miku.tts.motores")

#: Nombres de idioma (para pedirle la traducción a Groq) por código.
IDIOMAS = {"es": "español", "ja": "japonés", "en": "inglés", "pt": "portugués", "fr": "francés",
           "de": "alemán", "it": "italiano", "ko": "coreano", "zh": "chino"}


def nombre_de_idioma(codigo: str) -> str:
    """Nombre en español de un código de idioma ("ja" -> "japonés"); si no lo conoce, el mismo código."""
    return IDIOMAS.get((codigo or "").strip().lower(), (codigo or "").strip())


def dividir_comando(plantilla: str) -> List[str]:
    """Parte la plantilla en argumentos respetando comillas (rutas de Windows con espacios)."""
    partes = shlex.split(plantilla, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in partes]


class MotorComando:
    """Sintetiza voz ejecutando un comando externo que escribe un WAV.

    Args:
        plantilla: Comando con ``{salida}`` (y opcionalmente ``{texto}``).
        timeout: Segundos máximos por frase.
    """

    def __init__(self, plantilla: str, timeout: float = 60.0) -> None:
        self.plantilla = (plantilla or "").strip()
        self.timeout = timeout

    @property
    def valido(self) -> bool:
        """True si la plantilla existe y tiene ``{salida}``."""
        return bool(self.plantilla) and "{salida}" in self.plantilla

    def sintetizar(self, texto: str) -> Optional[bytes]:
        """WAV de ``texto`` o None si el motor falla (sin comando, error, sin archivo, timeout)."""
        if not self.valido or not (texto or "").strip():
            return None
        fd, ruta = tempfile.mkstemp(prefix="miku_motor_", suffix=".wav")
        os.close(fd)
        try:
            args = [a.replace("{salida}", ruta).replace("{texto}", texto)
                    for a in dividir_comando(self.plantilla)]
            por_stdin = "{texto}" not in self.plantilla
            r = subprocess.run(args, input=texto if por_stdin else None, capture_output=True,
                               text=True, encoding="utf-8", timeout=self.timeout, shell=False,
                               creationflags=sin_ventana(),
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            if r.returncode != 0:
                logger.error("El motor de voz salió con código %s: %s", r.returncode,
                             (r.stderr or "")[:300])
                return None
            with open(ruta, "rb") as f:
                datos = f.read()
            if not datos:
                logger.error("El motor de voz no generó audio (¿usa {salida}?).")
                return None
            return datos
        except subprocess.TimeoutExpired:
            logger.error("El motor de voz tardó más de %.0f s.", self.timeout)
        except (OSError, ValueError) as e:
            logger.error("No pude ejecutar el motor de voz: %s", e)
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass
        return None
