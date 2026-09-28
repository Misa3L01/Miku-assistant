r"""
diagnostico_wake.py - Probar la palabra clave con tu micrófono, por el mismo camino que usa Miku.

Escucha como Miku (el mismo micrófono, el mismo recorte de frases con el VAD y el mismo detector) y, por
cada frase que oye, muestra cuánto duró, con qué volumen llegó y qué puntaje le dio el modelo. Todo queda
guardado en ``data/wake/diagnostico/`` (no se sube a GitHub) para poder analizarlo después.

    venv\Scripts\python.exe -m miku.voz.entrada.diagnostico_wake              # 40 s, micrófono de Miku
    venv\Scripts\python.exe -m miku.voz.entrada.diagnostico_wake --mic 21     # probar otro micrófono
    venv\Scripts\python.exe -m miku.voz.entrada.diagnostico_wake --listar     # ver los micrófonos

Conviene cerrar Miku antes: si no, ella también te va a contestar mientras probás.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

from miku.ajustes import carga as config_mod

#: Frases que reconoce el modelo entrenado (las que se sugiere decir).
FRASES = ("che miku", "miku", "eu miku")


def listar_microfonos() -> List[Tuple[int, str, str]]:
    """``[(índice, nombre, sistema de audio)]`` de los micrófonos, como los ve Miku (PyAudio)."""
    import pyaudio
    p = pyaudio.PyAudio()
    try:
        salida = []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d.get("maxInputChannels", 0) > 0:
                api = p.get_host_api_info_by_index(d["hostApi"])["name"]
                salida.append((i, str(d["name"]), str(api)))
        return salida
    finally:
        p.terminate()


def nombre_microfono(indice: Optional[int]) -> str:
    """Nombre del micrófono con ese índice (None = el predeterminado de Windows)."""
    import pyaudio
    p = pyaudio.PyAudio()
    try:
        d = p.get_default_input_device_info() if indice is None else p.get_device_info_by_index(indice)
        return f"[{d['index']}] {d['name']}"
    except Exception as e:  # noqa: BLE001
        return f"(no pude leerlo: {e})"
    finally:
        p.terminate()


def dbfs(muestras: Any) -> float:
    """Pico de la señal en dBFS (0 = lo más fuerte posible; -90 si es silencio)."""
    pico = float(abs(muestras).max()) if len(muestras) else 0.0
    return 20 * math.log10(pico / 32768.0) if pico > 0 else -90.0


def nivel_entrenamiento() -> Optional[float]:
    """Volumen típico (mediana del pico, en dBFS) de tus grabaciones de entrenamiento, si están."""
    import numpy as np
    carpeta = Path(config_mod.BASE_DIR) / "entrenamiento" / "grabaciones" / "real_clips"
    picos = []
    for ruta in sorted(carpeta.glob("*.wav"))[:60]:
        with wave.open(str(ruta)) as f:
            picos.append(dbfs(np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)))
    return float(np.median(picos)) if picos else None


def _guardar_wav(ruta: Path, datos: bytes, frecuencia: int, ancho: int) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(ancho)
        f.setframerate(frecuencia)
        f.writeframes(datos)


class _Espejo:
    """Envuelve el micrófono y guarda una copia de todo lo que se lee (la sesión completa)."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self.copia: List[bytes] = []

    def read(self, n: int) -> bytes:
        datos = self._stream.read(n)
        self.copia.append(datos)
        return datos


def main(argv: Optional[List[str]] = None) -> int:
    import numpy as np

    from miku.voz.entrada.escucha import SpeechToText

    parser = argparse.ArgumentParser(description="Probar la palabra clave con tu micrófono.")
    parser.add_argument("--segundos", type=float, default=40.0, help="cuánto escuchar (por defecto 40)")
    parser.add_argument("--mic", type=int, default=None, help="índice de otro micrófono (ver --listar)")
    parser.add_argument("--listar", action="store_true", help="mostrar los micrófonos y salir")
    args = parser.parse_args(argv)

    if args.listar:
        for i, nombre, api in listar_microfonos():
            print(f"  [{i:2}] {nombre[:55]:55} {api}")
        return 0

    config_mod.cargar()
    cfg = config_mod.config
    if args.mic is not None:
        cfg.valores["microfono_index"] = args.mic
    stt = SpeechToText(cfg)
    stt._importar_dependencias()
    detector = stt._obtener_detector()
    if detector is None or not hasattr(detector, "puntaje"):
        print("La palabra clave no está usando openWakeWord. Revisá WAKE_PROVEEDOR y WAKE_MODELO en "
              "config_local.py.")
        return 1
    vad = stt._obtener_vad()
    print(f"Micrófono : {nombre_microfono(cfg.microfono_index)}")
    print(f"Modelo    : {detector.ruta_modelo}  (umbral {detector.umbral:.2f})")
    print(f"Fin frase : {getattr(vad, 'nombre', 'pausa fija')}")
    print("Cargando…")
    stt.precalentar()

    mic, fuente = stt._abrir_microfono()
    espejo = _Espejo(fuente.stream)
    fuente.stream = espejo
    frecuencia, ancho = int(fuente.SAMPLE_RATE), int(fuente.SAMPLE_WIDTH)
    carpeta = Path(config_mod.BASE_DIR) / "data" / "wake" / "diagnostico" / datetime.now().strftime("%Y%m%d_%H%M%S")
    resultados: List[Tuple[float, float, float]] = []
    print(f"\nListo. Durante {args.segundos:.0f} s decí \"{FRASES[0]}\" (o \"{FRASES[1]}\" / \"{FRASES[2]}\") "
          "unas 8 veces, dejando 2 o 3 segundos entre cada una, como le hablarías a Miku.\n")
    try:
        import speech_recognition as sr
        fin = time.monotonic() + args.segundos
        while time.monotonic() < fin:
            try:
                audio = stt._capturar(fuente, 1.0, 8)
            except sr.WaitTimeoutError:
                continue
            muestras = np.frombuffer(audio.get_raw_data(convert_rate=16000, convert_width=2), dtype=np.int16)
            puntaje = detector.puntaje(muestras)
            duracion, volumen = len(muestras) / 16000.0, dbfs(muestras)
            resultados.append((duracion, volumen, puntaje))
            n = len(resultados)
            _guardar_wav(carpeta / f"frase_{n:02d}_{puntaje:.2f}.wav", muestras.tobytes(), 16000, 2)
            veredicto = "¡LA DESPERTARÍA!" if puntaje >= detector.umbral else "no alcanza"
            print(f"  frase {n:2}: {duracion:4.1f} s | volumen {volumen:4.0f} dBFS | puntaje {puntaje:.2f} -> {veredicto}")
    finally:
        try:
            mic.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        _guardar_wav(carpeta / "sesion_completa.wav", b"".join(espejo.copia), frecuencia, ancho)

    print("\n--- Resumen")
    if not resultados:
        print("No oí ninguna frase. ¿Es el micrófono correcto? Probá con --listar y --mic N.")
    else:
        despertaria = sum(1 for _, _, p in resultados if p >= detector.umbral)
        print(f"Frases oídas: {len(resultados)} | la despertarían: {despertaria}")
        print(f"Volumen de tu voz ahora: {np.median([v for _, v, _ in resultados]):.0f} dBFS (mediana)")
        entreno = nivel_entrenamiento()
        if entreno is not None:
            print(f"Volumen en tus grabaciones de entrenamiento: {entreno:.0f} dBFS")
    print(f"Guardé todo en: {carpeta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
