"""
text_to_speech.py - Síntesis de voz (TTS) con la personalidad de Miku.

Pipeline original:
  1. Kokoro (KPipeline) genera la voz base desde texto.
  2. RVC (rvc_python) con el modelo .pth la convierte a la voz de Miku.
  3. pygame reproduce el audio resultante.

Acá se encapsula ese flujo en la clase `TextoAVoz`, con lazy loading de
modelos (así ni Kokoro ni RVC se cargan si no se habla por voz) y una
cola que reproduce en un hilo para no bloquear el main.
"""
from __future__ import annotations

import glob
import logging
import os
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

import config as config_mod

logger = logging.getLogger("miku.tts")

# Cantidad de archivos temporales (wav) que conservamos.
_MAX_WAVS = 4


@dataclass
class VozMiku:
    """Configuración concreta del modelo de voz a usar."""

    modelo_rvc: str = ""            # Ruta al archivo .pth del modelo.
    f0up_key: int = 12              # Corrimiento de tono RVC.
    f0method: str = "rmvpe"         # Método de análisis F0.
    index_rate: float = 0.5         # Peso del índice.
    filter_radius: int = 2          # Radio de filtro mediana.
    rms_mix_rate: float = 0.25      # Mezcla RMS.
    protect: float = 0.33           # Protección de consonantes.


def limpiar_texto_para_voz(texto: Optional[str]) -> str:
    """Quita markdown y emojis para que la voz no los lea literalmente."""
    if not texto:
        return ""
    t = texto
    t = re.sub(r"\*+", "", t)         # asteriscos (negritas, cursivas)
    t = re.sub(r"_+", "", t)          # guiones bajos
    t = re.sub(r"`+", "", t)          # backticks (código)
    # Rango amplio de emojis y símbolos.
    t = re.sub(
        r"[\U0001F000-\U0001FAFF\U00002700-\U000027BF"
        r"\U000024C2-\U0001F251\U0001F300-\U0001F5FF]",
        "", t,
    )
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _limpiar_wavs_viejos(patron: str = "tts_*.wav",
                         mantener: int = _MAX_WAVS) -> None:
    """Borra los wav temporales más viejos, conservando `mantener`."""
    try:
        viejos = sorted(glob.glob(patron), key=lambda p: os.path.getmtime(p))
        for f in viejos[:-mantener]:
            try:
                os.remove(f)
            except OSError:  # noqa: fué borrado / en uso
                continue
    except Exception:  # noqa: BLE001
        logger.debug("No se pudieron limpiar wavs viejos.")


class TextoAVoz:
    """Motor TTS (Kokoro -> RVC -> pygame) reproducido en un hilo dedicado.

    Args:
        cfg: Config del programa (le da `modelo_miku` por defecto).
        voz: Config de voz opcional para sobreescribir parámetros.
    """

    def __init__(self, cfg: "config_mod.Config",
                 voz: Optional[VozMiku] = None) -> None:
        self.cfg = cfg
        self.voz = voz or VozMiku(modelo_rvc=cfg.modelo_miku)

        # Componentes cargados de forma "lazy" (None hasta que se pidan).
        self.rvc = None
        self.kokoro = None
        self.pygame = None

        # Cola de frases a decir + hilo reproductor (no bloquea al caller).
        self._cola: "queue.Queue[str]" = queue.Queue()
        self._hilo_reproductor: Optional[threading.Thread] = None
        self._hablando = threading.Event()

        # Asegura una sola reproducción (chat) a la vez.
        self._lock_voz = threading.Lock()

    # ---------------- Lazy loading de modelos ----------------
    @property
    def listo(self) -> bool:
        """True si los tres componentes (pygame, kokoro, rvc) están."""
        return self.kokoro is not None and self.rvc is not None and self.pygame is not None

    def cargar_modelos(self) -> bool:
        """Carga (si falta) pygame, Kokoro y RVC. Devuelve True si quedó OK."""
        if self.listo:
            return True
        try:
            if self.pygame is None:
                import pygame
                pygame.mixer.init()
                self.pygame = pygame

            if self.kokoro is None:
                from kokoro import KPipeline  # import tardío
                self.kokoro = KPipeline(lang_code="e")

            if self.rvc is None:
                from rvc_python.infer import RVCInference  # import tardío
                self.rvc = RVCInference(device="cuda:0")
                self.rvc.load_model(self.voz.modelo_rvc)
                self.rvc.set_params(
                    f0up_key=self.voz.f0up_key,
                    f0method=self.voz.f0method,
                    index_rate=self.voz.index_rate,
                    filter_radius=self.voz.filter_radius,
                    rms_mix_rate=self.voz.rms_mix_rate,
                    protect=self.voz.protect,
                )
            return True
        except Exception:  # noqa: BLE001
            logger.exception("No se pudieron cargar los modelos de voz.")
            return False

    # ---------------- API pública ----------------
    def decir(self, texto: str) -> None:
        """Encola `texto` para ser hablado sin bloquear al llamador."""
        texto_limpio = limpiar_texto_para_voz(texto)
        if not texto_limpio:
            return
        print(f"\nMiku: {texto_limpio}")
        self._cola.put(texto_limpio)

        # Arranca el hilo reproductor si no está vivo.
        if self._hilo_reproductor is None or not self._hilo_reproductor.is_alive():
            self._hilo_reproductor = threading.Thread(
                target=self._bucle_reproductor,
                daemon=True,
                name="tts_player",
            )
            self._hilo_reproductor.start()

    def decir_sync(self, texto: str) -> None:
        """Habla bloqueando hasta terminar. Util en hooks críticos."""
        with self._lock_voz:
            self._sintetizar_y_reproducir(texto)

    def esperar_hablando(self, timeout: Optional[float] = None) -> bool:
        """Espera (bloqueante) a que terminen las frases encoladas.

        Returns:
            True si se liberó el evento; False si venció el timeout.
        """
        return self._hablando.wait(timeout=timeout)

    # ---------------- Hilo reproductor ----------------
    def _bucle_reproductor(self) -> None:
        """Consume la cola y reproduce de a una la frase encolada."""
        while True:
            try:
                texto = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue  # daemon: queda esperando nuevas frases
            self._hablando.set()
            try:
                with self._lock_voz:
                    self._sintetizar_y_reproducir(texto)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            finally:
                self._hablando.clear()

    # ---------------- Pipeline de síntesis ----------------
    def _sintetizar_y_reproducir(self, texto: str) -> None:
        """Sintetiza Kokoro -> RVC -> pygame. Asume tomado el lock."""
        import datetime  # local para la marca horaria de archivo
        import soundfile as sf

        texto = limpiar_texto_para_voz(texto)
        if not texto:
            return
        if not self.cargar_modelos():
            return

        marca_hora = datetime.datetime.now().strftime("%H%M%S%f")
        archivo_base = f"tts_base_{marca_hora}.wav"
        archivo_final = f"tts_final_{marca_hora}.wav"

        try:
            # 1) Kokoro produce los chunks de audio base.
            chunks: List[np.ndarray] = []
            for _enc, _ids, audio in self.kokoro(texto, voice="ef_dora",
                                                 speed=1.05):
                chunks.append(audio)
            if not chunks:
                logger.warning("Kokoro no generó audio para: %s", texto)
                return

            audio_total = np.concatenate(chunks)
            sf.write(archivo_base, audio_total, 24000)

            # 2) RVC lo transforma a la voz de Miku.
            self.rvc.infer_file(archivo_base, archivo_final)
            if not os.path.exists(archivo_final):
                logger.warning("RVC no generó el archivo de salida.")
                return

            # 3) Reproducción con pygame.
            self.pygame.mixer.music.load(archivo_final)
            self.pygame.mixer.music.play()
            while self.pygame.mixer.music.get_busy():
                time.sleep(0.1)
            self.pygame.mixer.music.unload()

        except Exception as e:  # noqa: BLE001
            logger.error("Error en la síntesis de voz: %s", e)
            traceback.print_exc()
        finally:
            self._liberar_cache_cuda()
            _limpiar_wavs_viejos()

    def _liberar_cache_cuda(self) -> None:
        """Libera la memoria de la VRAM de torch tras reproducir."""
        if _detectar_cuda():
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass


def _detectar_cuda() -> bool:
    """Detecta CUDA una sola vez sin importar torch en el import del módulo."""
    global _cuda_disponible
    if _cuda_disponible is None:
        _cuda_disponible = False
        try:
            import torch
            _cuda_disponible = torch.cuda.is_available()
        except Exception:  # noqa: BLE001
            _cuda_disponible = False
    return _cuda_disponible


# Variable de estado global (perezosa) para saber si hay CUDA.
_cuda_disponible: Optional[bool] = None
