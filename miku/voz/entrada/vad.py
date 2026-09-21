"""
vad.py - Saber cuándo estás hablando y cuándo terminaste (detección de actividad de voz).

Antes, el final de tu frase se decidía por una **pausa fija**: tras X segundos por debajo de un
umbral de energía, Miku daba la orden por terminada. Eso obliga a elegir entre dos males: una pausa
corta te corta cuando pensás a mitad de frase, y una larga te hace esperar en cada orden.

Un VAD distingue *voz* de *ruido* en vez de mirar el volumen, así que se puede usar una pausa corta
sin que un ventilador, un teclado o la tele la disparen.

Motores (``VAD_PROVEEDOR``):

    ``auto``      (por defecto) silero si el modelo está a mano, si no energía.
    ``silero``    Red chica (~2 MB) por ONNX Runtime. La mejor: distingue voz real de ruido.
                  El modelo se baja una vez con ``python -m miku.voz.entrada.vad descargar``
                  (o ``pip install silero-vad``, o apuntando ``VAD_MODELO`` a tu .onnx).
    ``energia``   Sin dependencias: energía de la señal contra un piso de ruido que se adapta solo.
                  Es lo que había antes, pero calibrándose con el ruido de tu habitación.

Todos responden lo mismo: ``es_voz(frame) -> bool`` sobre fragmentos de 32 ms (512 muestras a 16 kHz,
PCM 16 bits mono). Quien captura el audio (``escucha.py``) decide con eso cuándo empezó y terminó tu
frase.
"""
from __future__ import annotations

import array
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger("miku.stt.vad")

#: El audio del VAD: 16 kHz mono, 16 bits. Silero solo acepta esto.
FRECUENCIA = 16000
#: Muestras por fragmento (512 a 16 kHz = 32 ms). Silero v5 exige exactamente este tamaño.
MUESTRAS_FRAME = 512
#: Bytes de un fragmento (2 bytes por muestra).
BYTES_FRAME = MUESTRAS_FRAME * 2
#: De dónde se baja el modelo de silero (repositorio oficial).
URL_MODELO = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"


class Vad(Protocol):
    """Interfaz de un detector de voz."""

    nombre: str

    def es_voz(self, frame: bytes) -> bool:
        """¿Ese fragmento de audio es voz?"""
        ...

    def reiniciar(self) -> None:
        """Olvida lo escuchado (empieza una frase nueva)."""
        ...


def _muestras(frame: bytes) -> array.array:
    """El fragmento como enteros de 16 bits con signo."""
    datos = array.array("h")
    datos.frombytes(frame[:len(frame) // 2 * 2])
    if sys.byteorder == "big":
        datos.byteswap()
    return datos


class VadEnergia:
    """Energía de la señal contra un piso de ruido que se adapta solo (sin dependencias).

    El piso sube rápido y baja lento, así que se acomoda al ruido de fondo de la habitación sin
    tomar por voz un golpe corto. Es el respaldo cuando no está el modelo de silero.
    """

    nombre = "energia"
    #: Cuántas veces por encima del ruido de fondo tiene que estar para contar como voz.
    FACTOR = 3.0
    #: Piso mínimo: sin esto, en una habitación en silencio cualquier soplido sería "voz".
    MINIMO = 120.0
    #: Fragmentos del arranque que se toman como ruido de fondo sí o sí (la captura empieza antes de
    #: que hables). Sin esta calibración, en una habitación ruidosa TODO parecería voz desde el
    #: principio y la frase no terminaría nunca.
    FRAMES_CALIBRACION = 8

    def __init__(self, factor: float = FACTOR) -> None:
        self.factor = factor
        self._ruido = self.MINIMO
        self._calibrados = 0

    def reiniciar(self) -> None:
        self._ruido = self.MINIMO
        self._calibrados = 0

    @staticmethod
    def rms(frame: bytes) -> float:
        """Volumen del fragmento (0 si viene vacío)."""
        datos = _muestras(frame)
        if not datos:
            return 0.0
        return math.sqrt(sum(float(m) * m for m in datos) / len(datos))

    def es_voz(self, frame: bytes) -> bool:
        nivel = self.rms(frame)
        calibrando = self._calibrados < self.FRAMES_CALIBRACION
        habla = not calibrando and nivel > max(self._ruido * self.factor, self.MINIMO)
        if calibrando:
            self._calibrados += 1
        # El piso aprende del fondo (y mientras calibra, de lo que haya): nunca de la voz, para no
        # subir el listón mientras hablás y cortarte a mitad de frase.
        if not habla:
            self._ruido = max(self._ruido * 0.95 + nivel * 0.05, self.MINIMO)
        return habla


class VadSilero:
    """Modelo silero-vad por ONNX Runtime: dice qué tan probable es que el fragmento sea voz."""

    nombre = "silero"

    #: Muestras del fragmento ANTERIOR que el modelo necesita para no perder el arranque de la sílaba.
    #: Sin esto silero devuelve ~0 siempre (comprobado: con voz real pasa de 0 % a 78 % de aciertos).
    CONTEXTO = 64

    def __init__(self, ruta_modelo: str, umbral: float = 0.5) -> None:
        self.ruta_modelo = ruta_modelo
        self.umbral = umbral
        self._sesion: Any = None
        self._estado: Any = None
        self._contexto: Any = None
        self._entradas: tuple = ()

    def _cargar(self) -> Any:
        """Abre el modelo la primera vez (y prepara su memoria interna)."""
        if self._sesion is None:
            import numpy as np
            import onnxruntime
            opciones = onnxruntime.SessionOptions()
            opciones.inter_op_num_threads = 1      # un fragmento cada 32 ms: no hace falta más
            opciones.intra_op_num_threads = 1
            opciones.log_severity_level = 3
            self._sesion = onnxruntime.InferenceSession(
                self.ruta_modelo, sess_options=opciones, providers=["CPUExecutionProvider"])
            self._entradas = tuple(e.name for e in self._sesion.get_inputs())
            self._np = np
            self.reiniciar()
            logger.info("silero-vad listo (%s).", os.path.basename(self.ruta_modelo))
        return self._sesion

    def reiniciar(self) -> None:
        """Vacía la memoria del modelo: una frase nueva no arrastra la anterior."""
        if self._sesion is None:
            return
        # silero v5 lleva un solo tensor de estado; v4 llevaba dos (h y c).
        ceros = self._np.zeros((2, 1, 128), dtype=self._np.float32)
        self._estado = {"state": ceros} if "state" in self._entradas else {"h": ceros, "c": ceros}
        self._contexto = self._np.zeros(self.CONTEXTO, dtype=self._np.float32)

    def es_voz(self, frame: bytes) -> bool:
        try:
            sesion = self._cargar()
            np = self._np
            muestras = np.frombuffer(frame[:BYTES_FRAME], dtype=np.int16).astype(np.float32) / 32768.0
            if len(muestras) < MUESTRAS_FRAME:      # el último fragmento puede venir corto
                muestras = np.pad(muestras, (0, MUESTRAS_FRAME - len(muestras)))
            # El modelo mira este fragmento MÁS el final del anterior (ver ``CONTEXTO``).
            entrada = np.concatenate([self._contexto, muestras]).reshape(1, -1)
            self._contexto = muestras[-self.CONTEXTO:]
            entradas = {"input": entrada,
                        "sr": np.array(FRECUENCIA, dtype=np.int64), **self._estado}
            salidas = sesion.run(None, entradas)
            probabilidad = float(salidas[0].reshape(-1)[0])
            if "state" in self._entradas:
                self._estado = {"state": salidas[1]}
            else:
                self._estado = {"h": salidas[1], "c": salidas[2]}
            return probabilidad >= self.umbral
        except Exception:  # noqa: BLE001
            logger.exception("silero-vad falló; para esta frase uso energía.")
            self._sesion = None
            return VadEnergia().es_voz(frame)


def ruta_modelo(cfg: Any = None) -> Optional[str]:
    """Dónde está el modelo de silero, si está: config -> paquete instalado -> ``data/vad/``."""
    if cfg is not None:
        configurada = str(cfg.get("vad_modelo", "") or "").strip()
        if configurada and os.path.exists(configurada):
            return configurada
    try:                                   # si está el paquete silero-vad, trae el .onnx adentro
        import silero_vad  # type: ignore
        del_paquete = Path(silero_vad.__file__).parent / "data" / "silero_vad.onnx"
        if del_paquete.exists():
            return str(del_paquete)
    except Exception:  # noqa: BLE001
        pass
    propia = _ruta_propia()
    return str(propia) if propia.exists() else None


def _ruta_propia() -> Path:
    """Dónde guarda Miku el modelo que baja ella (``data/vad/silero_vad.onnx``)."""
    from miku.ajustes import carga as config_mod
    return Path(config_mod.BASE_DIR) / "data" / "vad" / "silero_vad.onnx"


def descargar_modelo() -> Optional[str]:
    """Baja el modelo de silero a ``data/vad/``. Devuelve la ruta, o None si no se pudo."""
    from miku.plataforma import red
    destino = _ruta_propia()
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        resp = red.get(URL_MODELO, timeout=60)
        if resp.status_code != 200 or not resp.content:
            logger.error("No pude bajar el modelo de silero (HTTP %s).", resp.status_code)
            return None
        temporal = destino.with_suffix(".tmp")
        temporal.write_bytes(resp.content)
        os.replace(temporal, destino)
        logger.info("Modelo de silero-vad guardado en %s (%d KB).", destino, len(resp.content) // 1024)
        return str(destino)
    except Exception:  # noqa: BLE001
        logger.exception("No pude bajar el modelo de silero.")
        return None


def crear_vad(cfg: Any) -> Optional[Vad]:
    """El detector que pide la config, o None si el VAD está apagado (``STT_VAD = False``)."""
    if not cfg.get("stt_vad", True):
        return None
    pedido = str(cfg.get("vad_proveedor", "auto") or "auto").strip().lower()
    if pedido in ("auto", "silero"):
        ruta = ruta_modelo(cfg)
        if ruta:
            return VadSilero(ruta, float(cfg.get("vad_umbral", 0.5) or 0.5))
        if pedido == "silero":
            logger.warning("VAD_PROVEEDOR = 'silero' pero no encuentro el modelo: bajalo con "
                           "'python -m miku.voz.entrada.vad descargar'. Por ahora uso energía.")
    elif pedido != "energia":
        logger.warning("VAD_PROVEEDOR '%s' desconocido; uso energía.", pedido)
    return VadEnergia()


if __name__ == "__main__":                 # python -m miku.voz.entrada.vad descargar
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "descargar":
        ruta = descargar_modelo()
        print(f"Listo: {ruta}" if ruta else "No se pudo bajar el modelo.")
        sys.exit(0 if ruta else 1)
    actual = ruta_modelo()
    print(f"Modelo de silero: {actual}" if actual else
          "No hay modelo de silero. Bajalo con: python -m miku.voz.entrada.vad descargar")
