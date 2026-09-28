"""
wake.py - Quién decide que dijiste "Miku" (en tu PC o en la nube).

Hasta acá, **cada frase** que se oía en la habitación se mandaba a Whisper en Groq solo para ver si
empezaba con "Miku": latencia de ida y vuelta, cuota gastada en conversaciones ajenas y audio de tu
casa saliendo a internet todo el día. Con un detector **local** eso deja de pasar: el audio sale de la
PC recién cuando de verdad te dirigiste a Miku.

Motores (``WAKE_PROVEEDOR``):

    ``auto``          (por defecto) el mejor que esté disponible: openWakeWord -> local -> nube.
    ``openwakeword``  Modelo chico de detección de palabra clave, sin transcribir nada. El más rápido
                      y el más privado, pero necesita un modelo entrenado para "Miku"
                      (``WAKE_MODELO``); openWakeWord no trae uno.
    ``local``         faster-whisper en tu PC (``pip install faster-whisper``), modelo ``tiny`` por
                      defecto: transcribe la frase acá y busca "Miku".
    ``nube``          Whisper en Groq, como siempre.

Todos devuelven lo mismo (``Deteccion``), así que ``escucha.py`` no sabe cuál está usando. Los que
transcriben devuelven además el texto, y eso permite decir la orden en la misma frase
("Miku, qué hora es"). openWakeWord **no transcribe**: solo avisa que te dirigiste a ella, y entonces
Miku saluda y escucha la orden aparte.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, NamedTuple, Optional, Protocol

logger = logging.getLogger("miku.stt.wake")

# Palabras que el reconocedor puede devolver por "Miku". Se buscan como PALABRA COMPLETA: por
# substring, "migo"/"mica"/"iku" activaban a Miku con "amigo", "conmigo", "química", "económica"...
VARIANTES_MIKU: tuple = (
    "miku", "mikú", "mika", "mica", "niku", "mike",
    "miko", "mico", "meco", "meko", "mego", "mecu", "migu",
)
RE_MIKU = re.compile(r"\b(?:%s)\b" % "|".join(VARIANTES_MIKU))

#: Puntaje (0 a 1) desde el cual openWakeWord da por dicha la palabra clave.
UMBRAL_OWW = 0.5
#: Desde este puntaje, una frase que NO alcanzó el umbral se anota en el log (un "casi"): así se ve en
#: uso real si el modelo anda cerca y conviene bajar el umbral, o si directamente no la reconoce.
CASI_OWW = 0.1
#: openWakeWord trabaja de a bloques de 80 ms (1280 muestras a 16 kHz) y puntúa cada uno con lo que
#: viene oyendo. Hay que darle la frase en bloques: de un saque solo puntuaría el final.
BLOQUE_OWW = 1280
FRECUENCIA_OWW = 16000
#: Segundos de silencio que se agregan antes y después de la frase. openWakeWord mira ~1,3 s de audio
#: y una frase como "che Miku" dura medio segundo: sin este relleno el modelo arranca "en frío" y casi
#: nunca la reconoce. Es lo mismo que hace su ``predict_clip`` (comprobado con grabaciones reales).
RELLENO_OWW_SEG = 1


class Deteccion(NamedTuple):
    """Resultado de escuchar un fragmento."""

    #: True si en ese audio se dijo "Miku".
    activo: bool
    #: Lo que se entendió, si el motor transcribe (vacío en openWakeWord).
    texto: str = ""


class Detector(Protocol):
    """Interfaz de un detector de palabra clave."""

    nombre: str

    def detectar(self, audio: Any) -> Deteccion:
        """¿Ese ``AudioData`` contiene la palabra clave?"""
        ...


def contiene_miku(texto: str) -> bool:
    """True si ``texto`` trae "Miku" como palabra completa."""
    return RE_MIKU.search((texto or "").lower()) is not None


class DetectorTranscripcion:
    """Transcribe el audio y busca "Miku" en el texto (Groq o faster-whisper)."""

    def __init__(self, transcriptor: Any, modelo: str, timeout: float, nombre: str) -> None:
        self._transcriptor = transcriptor
        self._modelo = modelo
        self._timeout = timeout
        self.nombre = nombre

    def detectar(self, audio: Any) -> Deteccion:
        try:
            wav = audio.get_wav_data()
        except Exception as e:  # noqa: BLE001
            logger.error("No pude leer el audio capturado: %s", e)
            return Deteccion(False)
        texto = self._transcriptor.transcribir(wav, self._modelo, self._timeout) or ""
        return Deteccion(contiene_miku(texto), texto)


def carpeta_modelos_base() -> str:
    """Dónde guarda openWakeWord sus modelos comunes (la carpeta ``resources/models`` de la librería)."""
    import os

    import openwakeword
    return os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")


class DetectorOpenWakeWord:
    """openWakeWord: detecta la palabra clave sin transcribir (rápido y 100 % local).

    Necesita un modelo entrenado para "Miku" (``WAKE_MODELO``). openWakeWord trae modelos para
    "alexa", "hey jarvis" y otros, pero **no para "Miku"**: se entrena con su propio cuaderno de
    entrenamiento y se guarda el ``.onnx`` resultante.
    """

    nombre = "openwakeword"

    def __init__(self, ruta_modelo: str, umbral: float = UMBRAL_OWW) -> None:
        self.ruta_modelo = ruta_modelo
        self.umbral = umbral
        self._modelo: Any = None
        self._lock = threading.Lock()           # se puede cargar desde dos hilos a la vez

    @staticmethod
    def disponible(ruta_modelo: str) -> bool:
        """True si está la librería y el modelo (sin cargar nada todavía)."""
        import importlib.util
        import os
        return bool(ruta_modelo) and os.path.exists(ruta_modelo) and \
            importlib.util.find_spec("openwakeword") is not None

    @staticmethod
    def _asegurar_modelos_base() -> None:
        """Baja una sola vez los modelos comunes de openWakeWord (sin ellos no puede leer el audio).

        Son dos archivos chicos que convierten el audio antes de tu modelo, iguales para toda palabra
        clave. Se piden por nombre a propósito: con la lista vacía bajaría además todos los modelos de
        ejemplo (alexa, hey jarvis...), que no se usan.
        """
        import os

        import openwakeword
        import openwakeword.utils
        carpeta = carpeta_modelos_base()
        archivos = [os.path.basename(m["download_url"]).replace(".tflite", ".onnx")
                    for m in openwakeword.FEATURE_MODELS.values()]
        if all(os.path.exists(os.path.join(carpeta, a)) for a in archivos):
            return
        logger.info("Bajando los modelos base de openWakeWord (una sola vez)...")
        openwakeword.utils.download_models(model_names=["solo-los-base"])

    def _cargar(self) -> Any:
        with self._lock:
            if self._modelo is None:
                from openwakeword.model import Model  # type: ignore  # import tardío (opcional)
                self._asegurar_modelos_base()
                self._modelo = Model(wakeword_models=[self.ruta_modelo], inference_framework="onnx")
                logger.info("openWakeWord listo con %s.", self.ruta_modelo)
            return self._modelo

    def puntaje(self, muestras: Any) -> float:
        """Qué tan seguro está el modelo (0 a 1) de que en esas muestras (16 kHz, int16) se dijo la
        palabra clave.

        La frase se rellena con silencio (ver ``RELLENO_OWW_SEG``) y se puntúa bloque por bloque,
        quedándose con el mejor: de un saque, openWakeWord devolvería solo el puntaje del final.
        """
        import numpy as np  # viene con onnxruntime
        relleno = np.zeros(FRECUENCIA_OWW * RELLENO_OWW_SEG, dtype=np.int16)
        muestras = np.concatenate([relleno, np.asarray(muestras, dtype=np.int16), relleno])
        modelo = self._cargar()
        mejor = 0.0
        try:
            for i in range(0, len(muestras) - BLOQUE_OWW + 1, BLOQUE_OWW):
                puntajes = modelo.predict(muestras[i:i + BLOQUE_OWW])
                mejor = max(mejor, max(puntajes.values(), default=0.0))
        finally:
            modelo.reset()                      # la frase siguiente empieza sin arrastrar esta
        return float(mejor)

    def detectar(self, audio: Any) -> Deteccion:
        """¿En esa frase se dijo la palabra clave?"""
        try:
            import numpy as np  # viene con onnxruntime
            crudo = audio.get_raw_data(convert_rate=FRECUENCIA_OWW, convert_width=2)
            mejor = self.puntaje(np.frombuffer(crudo, dtype=np.int16))
            if mejor >= self.umbral:
                logger.info("openWakeWord detectó la palabra clave (%.2f).", mejor)
                return Deteccion(True)
            nivel = logging.INFO if mejor >= CASI_OWW else logging.DEBUG
            logger.log(nivel, "openWakeWord: %.2f, no alcanza el umbral %.2f.", mejor, self.umbral)
            return Deteccion(False)
        except Exception:  # noqa: BLE001
            logger.exception("Falló openWakeWord; esta vez no detecto nada.")
            return Deteccion(False)


def _detector_local(cfg: Any) -> Optional[Detector]:
    """faster-whisper en la PC para la palabra clave (None si no está instalado)."""
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        return None
    from miku.voz.entrada.transcriptores import WhisperLocal
    modelo = str(cfg.get("wake_modelo_local", "tiny") or "tiny").strip()
    # El transcriptor local elige el tamaño con ``stt_modelo_local``; para la palabra clave conviene
    # uno más chico (se ejecuta con CADA frase que se oye), así que se le pasa el suyo.
    local = WhisperLocal(_ConfigModelo(cfg, modelo))
    return DetectorTranscripcion(local, modelo, 15.0, "local")


class _ConfigModelo:
    """Config prestada que fuerza el tamaño del modelo local de la palabra clave."""

    def __init__(self, cfg: Any, modelo: str) -> None:
        self._cfg = cfg
        self._modelo = modelo

    def get(self, clave: str, por_defecto: Any = None) -> Any:
        if clave == "stt_modelo_local":
            return self._modelo
        return self._cfg.get(clave, por_defecto)

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._cfg, nombre)


def crear_detector_local(cfg: Any) -> Optional[Detector]:
    """El detector LOCAL que pide la config, o None para resolver la palabra clave en la nube.

    Devolver None (en vez de un detector "de nube") deja intacto el camino de siempre en
    ``escucha.py``, que transcribe con el transcriptor ya configurado. Si lo pedido no está
    disponible, se avisa y se cae a la nube en lugar de dejar a Miku sorda.
    """
    pedido = str(cfg.get("wake_proveedor", "auto") or "auto").strip().lower()
    if pedido == "nube":
        return None
    if pedido not in ("auto", "local", "openwakeword"):
        logger.warning("WAKE_PROVEEDOR '%s' desconocido; uso la nube.", pedido)
        return None

    ruta = str(cfg.get("wake_modelo", "") or "").strip()
    if pedido in ("auto", "openwakeword") and DetectorOpenWakeWord.disponible(ruta):
        return DetectorOpenWakeWord(ruta, float(cfg.get("wake_umbral", UMBRAL_OWW) or UMBRAL_OWW))
    if pedido == "openwakeword":
        logger.warning("WAKE_PROVEEDOR = 'openwakeword' pero falta la librería (pip install "
                       "openwakeword) o el modelo WAKE_MODELO (%r): sigo con la nube.", ruta)
        return None

    local = _detector_local(cfg)
    if local is None and pedido == "local":
        logger.warning("WAKE_PROVEEDOR = 'local' pero falta faster-whisper "
                       "(pip install faster-whisper): sigo con la nube.")
    return local
