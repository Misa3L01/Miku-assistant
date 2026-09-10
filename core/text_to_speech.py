"""
text_to_speech.py - Síntesis de voz (TTS) usando VOICEVOX.

Estrategia (cambio de arquitectura: ya NO se usa RVC/Kokoro/torch):
    1. El texto de la respuesta está en español (viene del LLM).
    2. Se traduce a japonés con ``deep-translator`` (GoogleTranslator).
    3. Se le pide a VOICEVOX (API HTTP local en ``localhost:50021``) que
       genere el WAV de esa frase en japonés.
    4. Se reproduce el WAV con pygame.

Fallbacks robustos:
    - Si el servidor VOICEVOX no responde -> se usa ``pyttsx3`` (voz del
      sistema) y se avisa por consola.
    - Si la traducción falla -> se intenta VOICEVOX con el texto original
      (pero VOICEVOX está pensado para japonés) o bien pyttsx3 directo.

Subtítulos:
    La clase puede mostrar el texto en español en pantalla (overlay estilo
    anime) mientras se reproduce el audio. Para eso se conecta con el módulo
    ``core.subtitles`` (PyQt5). Los subtítulos NO se muestran en modo texto
    (eso lo decide main.py al no instanciar TTS).
"""
from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time
from typing import Optional

import requests

import config as config_mod

logger = logging.getLogger("miku.tts")

# Ruta por defecto (relativa a la raíz del proyecto) donde está instalado el
# motor interno de VOICEVOX. Se usa para lanzarlo automáticamente si hace
# falta. Puede anularse en config_local.py con VOICEVOX_RUN_EXE (ruta
# absoluta al ``run.exe``).
VOICEVOX_RUN_EXE = (config_mod.BASE_DIR / "extern" / "VOICEVOX"
                    / "vv-engine" / "run.exe")


def limpiar_texto_para_voz(texto: Optional[str]) -> str:
    """Quita markdown y emojis para que la voz no los lea literalmente."""
    if not texto:
        return ""
    t = texto
    t = re.sub(r"\*+", "", t)         # asteriscos (negritas, cursivas)
    t = re.sub(r"_+", "", t)          # guiones bajos
    t = re.sub(r"`+", "", t)          # backticks (código)
    t = re.sub(r"\s+", " ", t).strip()
    return t


class TextoAVoz:
    """Motor TTS basado en VOICEVOX reproducido en un hilo dedicado.

    Args:
        cfg: Config del programa (le da ``voicevox_url`` y el speaker).
        subtitulos_activos: True para mostrar subtítulos en pantalla.
            (main.py debe pasarlo solo en modo voz/push).
    """

    def __init__(self, cfg: "config_mod.Config",
                 subtitulos_activos: bool = False) -> None:
        self.cfg = cfg
        self.voicevox_url = cfg.voicevox_url.rstrip("/")
        self.speaker_id = cfg.voicevox_speaker_id

        # Subtítulos (cargado de forma perezosa vía core.subtitles).
        self._subs_enabled = subtitulos_activos
        self._subtitulos = None

        # Cola de frases a decir + hilo reproductor (no bloquea al caller).
        self._cola: "queue.Queue[str]" = queue.Queue()
        self._hilo_reproductor: Optional[threading.Thread] = None
        self._hablando = threading.Event()

        # Control del proceso VOICEVOX que hubiéramos lanzado nosotros.
        # Si VOICEVOX ya estaba corriendo NO lo tocamos (no lo matamos al
        # cerrar); solo manejamos el Popen si fue esta instancia la que lo
        # arrancó.
        self._proceso_voicevox: Optional[subprocess.Popen] = None
        self._voicevox_lo_lanzamos = False

    # ---------------- Subtítulos (lazy) ----------------
    def _obtener_subtitulos(self):
        """Crea el overlay de subtítulos la primera vez que se usa."""
        if self._subtitulos is None and self._subs_enabled:
            try:
                from core.subtitles import SubtitulosOverlay  # import tardío
                self._subtitulos = SubtitulosOverlay()
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo cargar el overlay de subtítulos.")
                self._subtitulos = False  # no reintentar con errores cada vez
        return self._subtitulos

    # ---------------- Comprobación de VOICEVOX ----------------
    def _verificar_voicevox(self) -> bool:
        """Devuelve True si el servidor VOICEVOX responde en su puerto."""
        # Podría cachearse; por ahora consulta rápida con timeout corto.
        try:
            resp = requests.get(f"{self.voicevox_url}/speakers", timeout=2)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ---------------- Auto-arranque de VOICEVOX ----------------
    def _asegurar_voicevox(self) -> bool:
        """Comprueba VOICEVOX y, si no está, intenta lanzarlo una sola vez.

        La primera vez que se llama: si el servidor no responde, busca el
        ``run.exe`` (por defecto en ``extern/VOICEVOX/vv-engine/run.exe`` y,
        mejor, la ruta definida en config_local VOICEVOX_RUN_EXE), lo lanza
        en segundo plano y espera unos segundos a que quede operativo.

        Si el servidor ya estaba activo al llegar aquí, NO guardamos ningún
        Popen y NO marcamos ``_voicevox_lo_lanzamos``: significa que lo abrió
        el usuario manualmente y por lo tanto no lo mataremos al cerrar.

        Returns:
            True si el servidor quedó accesible (o ya lo estaba).
        """
        # Si ya está corriendo, respetamos el proceso externo del usuario.
        if self._verificar_voicevox():
            return True

        # No reintentamos arrancarlo si ya estuvimos en ese intento (cache).
        if getattr(self, "_voicevox_intento_lanzado", False):
            return self._verificar_voicevox()
        self._voicevox_intento_lanzado = True

        ruta_run = self._buscar_run_voicevox()
        if ruta_run is None:
            logger.warning(
                "VOICEVOX no responde en %s y no encontré su run.exe. "
                "Usaré pyttsx3 como fallback (voz del sistema).", self.voicevox_url)
            return False

        logger.info("Intentando arrancar VOICEVOX desde: %s", ruta_run)
        try:
            proc = subprocess.Popen(
                [str(ruta_run)], cwd=str(ruta_run.parent),
                shell=False,  # sin shell por seguridad
                stdin=None, stdout=None, stderr=None,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
        except Exception as e:  # noqa: BLE001
            logger.error("No pude lanzar VOICEVOX: %s. Uso pyttsx3.", e)
            return False

        # Guardamos el proceso: fue esta instancia quien lo arrancó.
        self._proceso_voicevox = proc
        self._voicevox_lo_lanzamos = True

        # Esperamos (con cortes) a que levante el puerto.
        for _ in range(6):  # hasta ~12 s
            time.sleep(2)
            if self._verificar_voicevox():
                logger.info("VOICEVOX quedó activo.")
                return True
        logger.warning("VOICEVOX tardó en responder; uso pyttsx3 por ahora.")
        return False

    def detener_voicevox_si_lo_arrancamos(self) -> None:
        """Cierra el run.exe de VOICEVOX SOLO si fue esta instancia quien lo lanzó.

        Si VOICEVOX ya estaba corriendo (lo abriste vos manualmente), este
        método no hace nada y no te mata el proceso.
        """
        if not self._voicevox_lo_lanzamos or self._proceso_voicevox is None:
            return
        proc = self._proceso_voicevox
        if proc.poll() is None:  # aún en ejecución
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    proc.kill()
                logger.info("VOICEVOX (lanzado por Miku) terminado al cerrar.")
            except Exception as e:  # noqa: BLE001
                logger.warning("No pude terminar VOICEVOX: %s", e)
        self._proceso_voicevox = None
        self._voicevox_lo_lanzamos = False

    def _buscar_run_voicevox(self):
        """Devuelve la ruta al run.exe de VOICEVOX (o None si no se halla).

        Prioridad: config_local (VOICEVOX_RUN_EXE) -> default relativo.
        """
        # Ruta configurable desde config_local.py (se lee del singleton).
        try:
            import config_local  # noqa: F401
            ruta_conf = str(getattr(config_local, "VOICEVOX_RUN_EXE", "")).strip()
            if ruta_conf and os.path.exists(ruta_conf):
                return ruta_conf
        except Exception:  # noqa: BLE001
            pass
        if VOICEVOX_RUN_EXE and VOICEVOX_RUN_EXE.exists():
            return str(VOICEVOX_RUN_EXE)
        return None

    # ---------------- API pública ----------------
    def decir(self, texto: str) -> None:
        """Encola `texto` para ser hablado sin bloquear al llamador."""
        texto_limpio = limpiar_texto_para_voz(texto)
        if not texto_limpio:
            return
        print(f"\nMiku: {texto_limpio}")

        # Mostrar subtítulos con el TEXTO EN ESPAÑOL (no la traducción).
        self._mostrar_subtitulos(texto_limpio)

        self._cola.put(texto_limpio)

        if self._hilo_reproductor is None or not self._hilo_reproductor.is_alive():
            self._hilo_reproductor = threading.Thread(
                target=self._bucle_reproductor,
                daemon=True,
                name="tts_player",
            )
            self._hilo_reproductor.start()

    def _mostrar_subtitulos(self, texto: str) -> None:
        """Muestra el subtítulo en pantalla (si está habilitado)."""
        overlay = self._obtener_subtitulos()
        if overlay:
            try:
                overlay.mostrar(texto)
            except Exception:  # noqa: BLE001
                logger.exception("Error mostrando subtítulos.")

    def _ocultar_subtitulos(self) -> None:
        """Oculta los subtítulos cuando termina la reproducción."""
        if self._subtitulos:
            try:
                self._subtitulos.ocultar()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudieron ocultar subtítulos.")

    def esperar_hablando(self, timeout: Optional[float] = None) -> bool:
        """Espera (bloqueante) a que terminen las frases encoladas."""
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
                self._sintetizar_y_reproducir(texto)
            except Exception:  # noqa: BLE001
                logger.exception("Error reproduciendo frase.")
            finally:
                self._hablando.clear()
                self._ocultar_subtitulos()

    # ---------------- Pipeline de síntesis ----------------
    def _sintetizar_y_reproducir(self, texto_es: str) -> None:
        """Sintetiza (traduce->VOICEVOX) y reproduce. Con fallbacks."""
        texto_es = limpiar_texto_para_voz(texto_es)
        if not texto_es:
            return

        # Aseguramos que el servidor esté (lo arranca solo si hace falta).
        if not self._asegurar_voicevox():
            logger.warning("Voicevox no activo, usando voz del sistema.")
            self._hablar_sistema(texto_es)
            return

        # 1) Traducir a japonés.
        texto_ja = self._traducir_a_japones(texto_es)
        if texto_ja is None:
            # La traducción falló -> usamos voz del sistema con el original.
            logger.warning("Falló la traducción; usando voz del sistema.")
            self._hablar_sistema(texto_es)
            return

        # 2) Obtener WAV de VOICEVOX (si vino vacío, fallback a sistema).
        wav_bytes = self._sintetizar_voicevox(texto_ja)
        if not wav_bytes:
            logger.warning("Voicevox no generó audio; usando voz del sistema.")
            self._hablar_sistema(texto_es)
            return

        # 3) Reproducir con pygame.
        self._reproducir_bytes(wav_bytes)

    # ---------------- Traducción ----------------
    def _traducir_a_japones(self, texto_es: str) -> Optional[str]:
        """Traduce a japonés con deep-translator. Devuelve None si falla."""
        try:
            from deep_translator import GoogleTranslator  # import tardío
            traductor = GoogleTranslator(source="es", target="ja")
            return traductor.translate(texto_es)
        except Exception as e:  # noqa: BLE001
            logger.warning("No se pudo traducir a japonés: %s", e)
            return None

    # ---------------- VOICEVOX ----------------
    def _sintetizar_voicevox(self, texto_ja: str) -> Optional[bytes]:
        """Genera y devuelve el WAV para `texto_ja` con VOICEVOX."""
        speaker = self.speaker_id
        try:
            # Paso 1: audio_query.
            rq = requests.post(
                f"{self.voicevox_url}/audio_query",
                params={"text": texto_ja, "speaker": speaker},
                timeout=60,
            )
            if rq.status_code != 200:
                logger.error("audio_query falló: HTTP %s - %s",
                             rq.status_code, rq.text[:200])
                return None

            # Paso 2: synthesis con el JSON resultado.
            rs = requests.post(
                f"{self.voicevox_url}/synthesis",
                params={"speaker": speaker},
                headers={"Content-Type": "application/json"},
                data=rq.content,
                timeout=90,
            )
            if rs.status_code != 200:
                logger.error("synthesis falló: HTTP %s - %s",
                             rs.status_code, rs.text[:200])
                return None
            return rs.content
        except Exception:  # noqa: BLE001
            logger.exception("Error en la síntesis con VOICEVOX.")
            return None

    # ---------------- Reproducción / fallback ----------------
    def _reproducir_bytes(self, wav_bytes: bytes) -> None:
        """Escribe el WAV a tempfile y lo reproduce con pygame."""
        import tempfile
        import os
        try:
            import pygame  # import tardío
        except Exception:  # noqa: BLE001
            logger.warning("pygame no disponible para reproducir audio.")
            self._hablar_sistema("[audio no reproducido]")
            return

        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except Exception:  # noqa: BLE001
                logger.warning("No se pudo inicializar pygame.mixer.")
                self._hablar_sistema("[audio no reproducido]")
                return

        # Escribir a un archivo temporal.
        archivo_tmp = os.path.join(tempfile.gettempdir(), "miku_tts.wav")
        try:
            with open(archivo_tmp, "wb") as f:
                f.write(wav_bytes)
        except Exception as e:  # noqa: BLE001
            logger.error("No se pudo escribir el WAV temporal: %s", e)
            return

        try:
            pygame.mixer.music.load(archivo_tmp)
            pygame.mixer.music.play()
            # Espera a que termine o que dejen de estar "ocupados".
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
            pygame.mixer.music.unload()
        except Exception:  # noqa: BLE001
            logger.exception("Error reproduciendo con pygame.")
        finally:
            # No borramos de inmediato (a veces pygame mantiene el archivo).
            pass

    def _hablar_sistema(self, texto: str) -> None:
        """Fallback final con pyttsx3 (voz del sistema). No bloquea audio."""
        try:
            import pyttsx3  # import tardío
            motor = pyttsx3.init()
            motor.say(texto)
            motor.runAndWait()
        except Exception:  # noqa: BLE001
            logger.warning("pyttsx3 no disponible tampoco; solo hay subtítulo/print.")
