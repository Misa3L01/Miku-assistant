"""
tts.py - Síntesis de voz (TTS) usando VOICEVOX.

Estrategia (cambio de arquitectura: ya NO se usa RVC/Kokoro/torch):
    1. El texto de la respuesta está en español (viene del LLM).
    2. Se traduce a japonés con la API de Groq (cuenta de STT, ``GROQ_API_KEY_STT``),
       con cache en memoria por frase exacta (ver ``_CACHE_TRADUCCIONES``).
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
    ``miku.ui.subtitulos`` (PyQt5). Los subtítulos NO se muestran en modo texto
    (eso lo decide miku/app.py al no instanciar TTS).
    El subtítulo se SINCRONIZA POR FRASE: cada frase se sintetiza y se
    subtitula de a una, con prefetch de la siguiente.
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

from miku.ajustes import carga as config_mod
from miku.plataforma import red
from miku.voz.salida import traduccion
from miku.voz.salida.cache_audio import CacheAudio
from miku.voz.salida.motores import MotorComando, nombre_de_idioma

logger = logging.getLogger("miku.tts")

# Ruta por defecto (relativa a la raíz del proyecto) donde está instalado el
# motor interno de VOICEVOX. Se usa para lanzarlo automáticamente si hace
# falta. Puede anularse en config_local.py con VOICEVOX_RUN_EXE (ruta
# absoluta al ``run.exe``).
VOICEVOX_RUN_EXE = (config_mod.BASE_DIR / "extern" / "VOICEVOX"
                    / "vv-engine" / "run.exe")

# Carpeta de la caché de audio en disco (frases ya sintetizadas; ver ``cache_audio.py``).
CARPETA_CACHE = config_mod.BASE_DIR / "data" / "tts_cache"

# Segundos que se da por bueno un chequeo de VOICEVOX antes de volver a preguntarle.
_VALIDEZ_CHEQUEO_VOICEVOX = 60.0
# Segundos que se le da a un VOICEVOX recién lanzado para levantar el puerto (en frío tarda ~15 s).
_ESPERA_ARRANQUE_VOICEVOX = 30.0

# Cache de traducciones (clave = texto EXACTO a traducir -> japonés).
# Vive a nivel de módulo para sobrevivir entre instancias y aprovechar que las
# frases de tono ("Listo", "Ya está", "Dale") se repiten mucho.
_CACHE_TRADUCCIONES: dict = {}

# Re-export por compatibilidad: la normalización de texto para traducir ahora
# vive en ``miku.voz.salida.traduccion`` (compartida con el traductor de juegos).
normalizar_para_traducir = traduccion.normalizar_para_traducir


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


# Puntuación FUERTE: siempre corta frase (cierra oración).
_FIN_FRASE_FUERTE = re.compile(r"(?<=[.!?;…])\s+")
# Puntuación DÉBIL: corta solo si el fragmento acumulado supera ~80 caracteres.
_FIN_FRASE_DEBIL = re.compile(r"(?<=[,:])\s+")
_LARGO_MAX_SIN_CORTE = 80


def dividir_en_frases(texto: str) -> list:
    """Divide un texto en "frases hablables" para subtitular/reproducir.

    Reglas:
      - Corta siempre tras puntuación fuerte: ``.`` ``!`` ``?`` ``;`` ``…``.
      - Corta tras ``,`` ``:`` solo si el fragmento acumulado supera ~80 chars.
      - Si tras todo el proceso queda un fragmento muy largo sin puntuación,
        lo parte por palabras para no mostrar/decir líneas kilométricas.

    Devuelve una lista de frases (sin vacíos), preservando el orden.
    """
    texto = (texto or "").strip()
    if not texto:
        return []

    # 1) Juntamos oraciones separadas por puntuación fuerte.
    oraciones = [o.strip() for o in _FIN_FRASE_FUERTE.split(texto) if o.strip()]

    # 2) Dentro de cada oración, partimos por ','/':' si quedó larga.
    fragmentos: list = []
    for oracion in oraciones:
        if len(oracion) <= _LARGO_MAX_SIN_CORTE:
            fragmentos.append(oracion)
            continue
        partes = _FIN_FRASE_DEBIL.split(oracion)
        actual = ""
        for parte in partes:
            if not parte:
                continue
            if actual and len(actual) + 1 + len(parte) > _LARGO_MAX_SIN_CORTE:
                fragmentos.append(actual.strip())
                actual = parte
            else:
                actual = f"{actual} {parte}".strip() if actual else parte
        if actual.strip():
            fragmentos.append(actual.strip())

    # 3) Red de seguridad: fragmentos aún larguísimos sin puntuación -> por palabras.
    resultado: list = []
    for frag in fragmentos:
        if len(frag) <= _LARGO_MAX_SIN_CORTE * 2:
            resultado.append(frag)
            continue
        palabras = frag.split(" ")
        buffer = ""
        for palabra in palabras:
            if buffer and len(buffer) + 1 + len(palabra) > _LARGO_MAX_SIN_CORTE:
                resultado.append(buffer.strip())
                buffer = palabra
            else:
                buffer = f"{buffer} {palabra}".strip() if buffer else palabra
        if buffer.strip():
            resultado.append(buffer.strip())

    return [f for f in resultado if f]


class TextoAVoz:
    """Motor TTS basado en VOICEVOX reproducido en un hilo dedicado.

    Args:
        cfg: Config del programa (le da ``voicevox_url`` y el speaker).
        subtitulos_activos: True para mostrar subtítulos en pantalla.
            (miku/app.py lo activa siempre: el modo voz y el de texto hablan).
    """

    def __init__(self, cfg: "config_mod.Config",
                 subtitulos_activos: bool = False) -> None:
        self.cfg = cfg
        self.voicevox_url = cfg.voicevox_url.rstrip("/")
        self.speaker_id = cfg.voicevox_speaker_id

        # Lista de URLs a probar: la configurada primero y luego el fallback
        # localhost <-> 127.0.0.1 (en Windows a veces uno resuelve y el otro no).
        self._urls_a_probar = self._construir_urls_a_probar(self.voicevox_url)

        # Motor alternativo (comando externo) para voces custom / otros idiomas.
        self._motor_comando = MotorComando(str(cfg.get("tts_comando", "") or ""))

        # Subtítulos (cargado de forma perezosa vía miku.ui.subtitulos).
        self._subs_enabled = subtitulos_activos
        self._subtitulos = None

        # Cola de frases a decir + hilo reproductor (no bloquea al caller).
        self._cola: "queue.Queue[str]" = queue.Queue()
        self._hilo_reproductor: Optional[threading.Thread] = None
        # ``decir`` se llama desde varios hilos (STT, tecla de invocación, Timers, Telegram):
        # el lock evita crear dos hilos reproductores y mantiene el contador
        # de textos pendientes (para ``esperar_libre``).
        self._lock_decir = threading.Lock()
        self._pendientes = 0
        # Set cuando NO hay nada encolado ni sonando (Miku está en silencio).
        self._libre = threading.Event()
        self._libre.set()

        # Control del proceso VOICEVOX que hubiéramos lanzado nosotros.
        # Si VOICEVOX ya estaba corriendo NO lo tocamos (no lo matamos al
        # cerrar); solo manejamos el Popen si fue esta instancia la que lo
        # arrancó.
        self._proceso_voicevox: Optional[subprocess.Popen] = None
        self._voicevox_lo_lanzamos = False

        # URL que efectivamente responde (se fija en la primera verificación ok).
        self._voicevox_url_activa: Optional[str] = None
        # Arranque de VOICEVOX: un solo intento de lanzarlo y, si está caído,
        # no volver a sondearlo en cada frase (cada sondeo cuesta hasta ~4 s).
        self._voicevox_lock = threading.Lock()
        self._voicevox_intento_lanzado = False
        self._voicevox_caido_hasta = 0.0
        # Hasta cuándo (time.monotonic) vale el último chequeo exitoso: evita un GET /speakers por frase.
        self._voicevox_ok_hasta = 0.0

        # Caché de audio en disco (se crea la primera vez que se usa; ver ``_cache_audio``).
        self._cache: Optional[CacheAudio] = None
        self._cache_resuelta = False

    @staticmethod
    def _construir_urls_a_probar(url_config: str) -> list:
        """Arma la lista de URLs base a probar para VOICEVOX.

        Devuelve, sin duplicados y en orden:
          1. Con ``localhost``, primero su variante ``127.0.0.1``: VOICEVOX escucha solo en IPv4 y, mientras
             no responde, sondear ``localhost`` cuesta el doble (Windows prueba IPv6 y IPv4 por separado).
          2. La URL configurada (y, si era ``127.0.0.1``, su variante ``localhost``: por si Windows resuelve
             distinto entre ambos nombres).
        """
        urls = [url_config]
        if "localhost" in url_config:
            urls.insert(0, url_config.replace("localhost", "127.0.0.1"))
        elif "127.0.0.1" in url_config:
            urls.append(url_config.replace("127.0.0.1", "localhost"))
        # Sin duplicados, preservando el orden.
        return list(dict.fromkeys(u for u in urls if u))

    # ---------------- Subtítulos (lazy) ----------------
    def _obtener_subtitulos(self):
        """Crea el overlay de subtítulos la primera vez que se usa.

        Si la creación falla, se cachea como False para no reintentar en cada
        frase, pero se AVISA una vez (log con traceback COMPLETO + mensaje en
        consola) para que el fallo no pase inadvertido.
        """
        if self._subtitulos is None and self._subs_enabled:
            try:
                from miku.ui.subtitulos import SubtitulosOverlay  # import tardío
                self._subtitulos = SubtitulosOverlay()
                logger.info("Overlay de subtítulos creado correctamente.")
            except Exception:  # noqa: BLE001
                logger.exception(
                    "No se pudo cargar el overlay de subtítulos (se desactivan "
                    "para esta sesión).")
                # Aviso en CONSOLA (no solo logger): ayuda a diagnosticar en vivo.
                print("[Subtítulos] No se pudo crear el overlay; no habrá "
                      "subtítulos en pantalla. Revisá el log para el detalle.")
                self._subtitulos = False  # no reintentar con errores cada vez
        return self._subtitulos

    # ---------------- Comprobación de VOICEVOX ----------------
    def _url_activa(self) -> str:
        """Devuelve la URL de VOICEVOX que respondió (o la configurada)."""
        return self._voicevox_url_activa or self.voicevox_url

    def _verificar_voicevox(self, timeout: float = 2.0) -> bool:
        """Devuelve True si el servidor VOICEVOX responde en alguna URL.

        Prueba la URL configurada y su variante localhost <-> 127.0.0.1 (cada una con ``timeout``).
        Loguea a nivel INFO la URL consultada y el status (o el error).
        """
        for url in self._urls_a_probar:
            endpoint = f"{url}/speakers"
            try:
                resp = red.get(endpoint, timeout=timeout)
                if resp.status_code == 200:
                    self._voicevox_url_activa = url
                    logger.info("[VOICEVOX] OK en %s (HTTP %s).",
                                endpoint, resp.status_code)
                    return True
                logger.info("[VOICEVOX] %s respondió HTTP %s (no 200). "
                            "Cuerpo: %s", endpoint, resp.status_code,
                            (resp.text or "")[:200])
            except Exception as e:  # noqa: BLE001
                logger.info("[VOICEVOX] No pude conectar a %s: %s",
                            endpoint, e)
        logger.info("[VOICEVOX] Ninguna URL respondió (probadas: %s).",
                    ", ".join(self._urls_a_probar))
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
        with self._voicevox_lock:
            ok = self._asegurar_voicevox_sin_lock()
            if ok:
                self._voicevox_caido_hasta = 0.0
                self._voicevox_ok_hasta = time.monotonic() + _VALIDEZ_CHEQUEO_VOICEVOX
            else:
                # Caído: no lo sondeamos de nuevo durante 30 s.
                self._voicevox_caido_hasta = time.monotonic() + 30.0
            return ok

    def _asegurar_voicevox_sin_lock(self) -> bool:
        """Cuerpo de ``_asegurar_voicevox`` (se llama con el lock tomado)."""
        # Ya respondió hace instantes (y ninguna síntesis falló desde entonces): no se le vuelve a preguntar.
        if time.monotonic() < self._voicevox_ok_hasta:
            return True
        # Marcado como caído hace poco: no gastamos timeouts en cada frase.
        if time.monotonic() < self._voicevox_caido_hasta:
            return False

        # Si ya está corriendo, respetamos el proceso externo del usuario.
        if self._verificar_voicevox():
            logger.info("[VOICEVOX] Ya estaba activo en %s.", self._url_activa())
            return True

        # No reintentamos arrancarlo si ya estuvimos en ese intento.
        if self._voicevox_intento_lanzado:
            logger.info("[VOICEVOX] Ya intenté arrancarlo antes y sigue sin "
                        "responder.")
            return False
        self._voicevox_intento_lanzado = True

        ruta_run = self._buscar_run_voicevox()
        if ruta_run is None:
            logger.warning(
                "[VOICEVOX] No responde en %s y no encontré su run.exe "
                "(busqué en config_local.VOICEVOX_RUN_EXE y en %s). "
                "Usaré pyttsx3 como fallback (voz del sistema).",
                self.voicevox_url, VOICEVOX_RUN_EXE)
            return False

        logger.info("[VOICEVOX] Intentando arrancar el ENGINE desde: %s",
                    ruta_run)
        if not self._es_engine_voicevox(ruta_run):
            logger.warning(
                "[VOICEVOX] AVISO: la ruta '%s' no parece el ENGINE HTTP de "
                "VOICEVOX (no encontré engine_manifest.json ni "
                "voicevox_core.dll junto a run.exe). Si lanzaste el editor con "
                "GUI, este proceso NO expondrá el puerto 50021.", ruta_run)

        # Flags/refuerzos para que el motor quede REALMENTE oculto:
        #   - CREATE_NO_WINDOW: no crea consola para el proceso.
        #   - STARTUPINFO + SW_HIDE: refuerzo extra (en algunas versiones de
        #     Windows / .exe de consola, CREATE_NO_WINDOW solo no alcanza).
        #   - stdout/stderr -> DEVNULL: evita que el motor escriba en la
        #     consola del asistente (y descarta su salida verbosa).
        creationflags = 0
        startupinfo = None
        try:
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags |= subprocess.CREATE_NO_WINDOW
            # STARTUPINFO con SW_HIDE (solo en Windows).
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        except Exception:  # noqa: BLE001
            startupinfo = None  # no bloquear si algo no existe (no-Windows)

        try:
            proc = subprocess.Popen(
                [str(ruta_run)], cwd=os.path.dirname(str(ruta_run)),
                shell=False,  # sin shell por seguridad
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=startupinfo)
        except Exception as e:  # noqa: BLE001
            logger.error("[VOICEVOX] No pude lanzar run.exe (%s): %s. "
                         "Uso pyttsx3.", ruta_run, e)
            return False

        # Guardamos el proceso: fue esta instancia quien lo arrancó.
        self._proceso_voicevox = proc
        self._voicevox_lo_lanzamos = True

        # Esperamos (con cortes) a que levante el puerto.
        # Se sondea con un corte por TIEMPO (cada sondeo puede tardar lo suyo mientras el puerto no existe) y
        # seguido: antes se esperaba 2 s entre sondeos y se perdía hasta ese tiempo tras estar listo.
        logger.info("[VOICEVOX] Esperando a que levante el puerto (hasta ~%d s)...", int(_ESPERA_ARRANQUE_VOICEVOX))
        inicio = time.monotonic()
        while time.monotonic() - inicio < _ESPERA_ARRANQUE_VOICEVOX:
            time.sleep(0.5)
            if self._verificar_voicevox(timeout=1.0):
                logger.info("[VOICEVOX] Quedó activo tras %.1f s.", time.monotonic() - inicio)
                return True
            if proc.poll() is not None:
                logger.warning("[VOICEVOX] El proceso terminó solo (código %s); no va a levantar.", proc.returncode)
                return False
        logger.warning("[VOICEVOX] No respondió tras ~%d s. PID del proceso "
                       "lanzado: %s (sigue vivo: %s). Uso pyttsx3 por ahora.",
                       int(_ESPERA_ARRANQUE_VOICEVOX), getattr(proc, "pid", "?"), proc.poll() is None)
        return False

    def _es_engine_voicevox(self, ruta_run: str) -> bool:
        """Heurística: ¿la carpeta de `run.exe` es el ENGINE (server), no el editor?

        El engine HTTP de VOICEVOX trae junto a ``run.exe`` los archivos
        ``engine_manifest.json`` y/o ``voicevox_core.dll``. El editor con GUI
        no. Se usa solo para AVISAR en el log, no para bloquear.
        """
        try:
            carpeta = os.path.dirname(str(ruta_run))
            marcadores = ("engine_manifest.json", "voicevox_core.dll")
            return any(os.path.exists(os.path.join(carpeta, m))
                       for m in marcadores)
        except Exception:  # noqa: BLE001
            return False

    def asegurar_voicevox_inicial(self) -> bool:
        """Verifica/arranca VOICEVOX y AVISA por consola qué motor se usará.

        Pensado para llamarse UNA vez al entrar a un modo con voz, antes del
        primer ``decir()``. Devuelve True si VOICEVOX quedó disponible.

        Muestra en consola (feedback directo al usuario, no solo logger):
        - "VOICEVOX detectado ✓, usando voz VOICEVOX"
        - "VOICEVOX no disponible, usando pyttsx3 (voz del sistema)"
        """
        ok = self._asegurar_voicevox()
        if ok:
            # Feedback directo en consola (además del log). ASCII plano para no
            # romper consolas Windows en cp1252 (evita UnicodeEncodeError).
            print("[Voz] VOICEVOX detectado OK, usando voz VOICEVOX "
                  f"({self._url_activa()}).")
        else:
            print("[Voz] VOICEVOX no disponible, usando pyttsx3 "
                  "(voz del sistema).")
        return ok

    def precalentar(self, frases: Optional[list] = None) -> None:
        """Deja listo VOICEVOX y las frases que Miku va a decir seguro (para llamar en segundo plano).

        Verifica/arranca VOICEVOX y sintetiza las ``frases`` que todavía no estén en la caché de audio (la
        primera vez de la vida; después ya están). Así, el "¿Sí? Decime." de cada invocación suena al
        instante en vez de pagar la traducción y la síntesis.
        """
        if not self.asegurar_voicevox_inicial() or self._motor_elegido() != "voicevox":
            return                      # con voz de sistema o motor propio no hay nada que precalentar
        for frase in dividir_en_frases(limpiar_texto_para_voz(" ".join(frases or []))):
            try:
                self._preparar_audio_frase(frase)
            except Exception:  # noqa: BLE001
                logger.debug("No pude precalentar %r.", frase, exc_info=True)

    def cerrar(self) -> None:
        """Oculta los subtítulos y cierra VOICEVOX si lo lanzó esta instancia."""
        self._ocultar_subtitulos()
        self.detener_voicevox_si_lo_arrancamos()

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

        Prioridad: opción ``VOICEVOX_RUN_EXE`` de la config -> default relativo.
        """
        ruta_conf = str(self.cfg.get("voicevox_run_exe", "") or "").strip()
        if ruta_conf and os.path.exists(ruta_conf):
            return ruta_conf
        if VOICEVOX_RUN_EXE and VOICEVOX_RUN_EXE.exists():
            return str(VOICEVOX_RUN_EXE)
        return None

    # ---------------- API pública ----------------
    def decir(self, texto: str) -> None:
        """Encola `texto` para ser hablado sin bloquear al llamador.

        NOTA: la división en frases y la actualización del subtítulo ocurren
        DENTRO del hilo reproductor (para poder sincronizar cada frase con su
        audio real). Acá solo se encola el texto completo.
        """
        texto_limpio = limpiar_texto_para_voz(texto)
        if not texto_limpio:
            return
        print(f"\nMiku: {texto_limpio}")

        with self._lock_decir:
            self._pendientes += 1
            self._libre.clear()
            self._cola.put(texto_limpio)
            if (self._hilo_reproductor is None
                    or not self._hilo_reproductor.is_alive()):
                self._hilo_reproductor = threading.Thread(
                    target=self._bucle_reproductor,
                    daemon=True,
                    name="tts_player",
                )
                self._hilo_reproductor.start()

    def _actualizar_subtitulos(self, texto: str) -> None:
        """Actualiza el subtítulo SIN ocultar/mostrar (anti-parpadeo).

        Usa ``actualizar_texto`` si el overlay lo soporta; si no, cae a
        ``mostrar`` para no romper con versiones anteriores.
        """
        overlay = self._obtener_subtitulos()
        if not overlay:
            return
        try:
            actualizar = getattr(overlay, "actualizar_texto", None)
            if callable(actualizar):
                actualizar(texto)
            else:
                overlay.mostrar(texto)
        except Exception:  # noqa: BLE001
            logger.exception("Error actualizando subtítulos.")

    def _ocultar_subtitulos(self) -> None:
        """Oculta los subtítulos cuando termina la reproducción."""
        if self._subtitulos:
            try:
                self._subtitulos.ocultar()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudieron ocultar subtítulos.")

    def esperar_libre(self, timeout: Optional[float] = 30.0) -> bool:
        """Bloquea hasta que Miku termina de hablar (nada encolado ni sonando).

        Sirve para que el micrófono no grabe la propia voz de Miku.

        Args:
            timeout: Segundos máximos a esperar (None = sin límite).

        Returns:
            True si quedó en silencio, False si venció el timeout.
        """
        return self._libre.wait(timeout=timeout)

    # ---------------- Hilo reproductor ----------------
    def _bucle_reproductor(self) -> None:
        """Consume la cola y reproduce el texto EN FRASES, en orden."""
        while True:
            try:
                texto = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue  # daemon: queda esperando nuevas frases
            try:
                self._reproducir_texto_por_frases(texto)
            except Exception:  # noqa: BLE001
                logger.exception("Error reproduciendo frase.")
            finally:
                with self._lock_decir:
                    self._pendientes = max(0, self._pendientes - 1)
                    silencio = self._pendientes == 0
                if silencio:
                    self._ocultar_subtitulos()
                    self._libre.set()

    def _reproducir_texto_por_frases(self, texto_es: str) -> None:
        """Divide `texto_es` en frases y las reproduce en orden, con prefetch.

        Flujo por frase:
          1. Sintetizar el audio de la frase (con VOICEVOX o pyttsx3).
          2. Actualizar el subtítulo a esa frase JUSTO antes de reproducir.
          3. Reproducir.
          4. Pasar a la siguiente (ya pre-sintetizada en background).

        Para evitar cortes entre frases por la latencia de VOICEVOX, mientras
        se reproduce la frase N se va pre-sintetizando la N+1 en OTRO hilo
        (prefetch de un paso).
        """
        frases = dividir_en_frases(limpiar_texto_para_voz(texto_es))
        if not frases:
            return
        logger.debug("Reproduciendo en %d frase(s).", len(frases))

        # Pre-sintetizamos la PRIMERA frase antes de entrar al bucle.
        actual = (frases[0], self._preparar_audio_frase(frases[0]))

        for i in range(len(frases)):
            texto_frase, artefacto = actual

            # Prefetch: arrancamos la síntesis de la SIGUIENTE en background
            # mientras se reproduce la actual.
            siguiente_holder: dict = {}
            hilo_prefetch = None
            if i + 1 < len(frases):
                siguiente = frases[i + 1]

                def _prefetch(frase_sig: str = siguiente, destino: dict = siguiente_holder) -> None:
                    try:
                        destino["artefacto"] = self._preparar_audio_frase(frase_sig)
                    except Exception:  # noqa: BLE001
                        logger.exception("Error en prefetch de la frase siguiente.")
                        destino["artefacto"] = None

                hilo_prefetch = threading.Thread(
                    target=_prefetch, daemon=True, name="tts_prefetch")
                hilo_prefetch.start()

            # Subtítulo sincronizado a ESTA frase, justo antes de reproducir.
            if self._mostrar_subtitulo(artefacto):
                self._actualizar_subtitulos(texto_frase)

            # Reproductor (bloqueante hasta que termina la frase).
            self._reproducir_frase(texto_frase, artefacto)

            # Esperar el prefetch y preparar la siguiente iteración.
            if hilo_prefetch is not None:
                hilo_prefetch.join()
                actual = (frases[i + 1], siguiente_holder.get("artefacto"))

    # ---------------- Pipeline de síntesis ----------------
    def _preparar_audio_frase(self, texto_es: str) -> dict:
        """Sintetiza UNA frase y devuelve un "artefacto" reproducible.

        Artefacto: dict ``{"motor": "voicevox"|"sistema", "wav": bytes|None}``.

        - VOICEVOX: traduce ES->JA y sintetiza; devuelve los bytes WAV.
        - Cualquier fallo (servidor, traducción, HTTP): cae a pyttsx3
          (motor="sistema"), que se reproduce con ``say()+runAndWait()``.
        """
        texto_es = limpiar_texto_para_voz(texto_es)
        if not texto_es:
            return {"motor": "sistema", "wav": None, "idioma": "es"}

        motor = self._motor_elegido()
        if motor == "sistema":
            return {"motor": "sistema", "wav": None, "idioma": "es"}
        if motor == "comando":
            return self._preparar_con_comando(texto_es)

        # Frase ya sintetizada antes con esta voz: suena de inmediato, sin traducir ni consultar a VOICEVOX.
        cache, voz = self._cache_audio(), f"vv{self.speaker_id}"
        if cache is not None:
            guardado = cache.obtener(texto_es, voz)
            if guardado:
                logger.debug("[TTS] Caché de audio: %r", texto_es[:40])
                return {"motor": "voicevox", "wav": guardado, "idioma": "ja"}

        # Sin VOICEVOX disponible -> voz del sistema.
        if not self._asegurar_voicevox():
            logger.warning("[TTS] VOICEVOX no activo -> fallback pyttsx3.")
            return {"motor": "sistema", "wav": None, "idioma": "es"}

        # 1) Traducir a japonés.
        texto_ja = self._traducir_a_japones(texto_es)
        if texto_ja is None:
            logger.warning("[TTS] Falló la traducción ES->JA -> fallback pyttsx3.")
            return {"motor": "sistema", "wav": None, "idioma": "es"}

        # 2) Obtener WAV de VOICEVOX.
        wav_bytes = self._sintetizar_voicevox(texto_ja)
        if not wav_bytes:
            logger.warning(
                "[TTS] _asegurar_voicevox() dio True pero la síntesis devolvió "
                "None (revisá los logs [VOICEVOX]: HTTP/estado/cuerpo). "
                "Uso pyttsx3 para esta frase.")
            return {"motor": "sistema", "wav": None, "idioma": "es"}

        if cache is not None:
            cache.guardar(texto_es, voz, wav_bytes)
        return {"motor": "voicevox", "wav": wav_bytes, "idioma": "ja"}

    def _cache_audio(self) -> Optional[CacheAudio]:
        """La caché de audio en disco, o None si está desactivada (``TTS_CACHE = False``)."""
        if not self._cache_resuelta:
            self._cache_resuelta = True
            if self.cfg.get("tts_cache", True):
                self._cache = CacheAudio(CARPETA_CACHE, max_archivos=int(self.cfg.get("tts_cache_max", 300) or 300))
        return self._cache

    # ---------------- Motores y subtítulos ----------------
    def _motor_elegido(self) -> str:
        """``voicevox`` (por defecto), ``sistema`` o ``comando`` según ``tts_motor``."""
        motor = str(self.cfg.get("tts_motor", "voicevox") or "voicevox").strip().lower()
        return motor if motor in ("voicevox", "sistema", "comando") else "voicevox"

    def _preparar_con_comando(self, texto_es: str) -> dict:
        """Sintetiza con el comando externo, traduciendo antes si ``tts_idioma`` no es español."""
        idioma = str(self.cfg.get("tts_idioma", "es") or "es").strip().lower()
        texto = texto_es
        if idioma != "es":
            traducido = self._traducir_a(texto_es, idioma)
            if traducido is None:
                logger.warning("[TTS] Falló la traducción a '%s' -> fallback pyttsx3.", idioma)
                return {"motor": "sistema", "wav": None, "idioma": "es"}
            texto = traducido
        wav = self._motor_comando.sintetizar(texto)
        if not wav:
            logger.warning("[TTS] El motor por comando no generó audio -> fallback pyttsx3.")
            return {"motor": "sistema", "wav": None, "idioma": "es"}
        return {"motor": "comando", "wav": wav, "idioma": idioma}

    def _mostrar_subtitulo(self, artefacto: Optional[dict]) -> bool:
        """Política de subtítulos: ``siempre``, ``nunca`` o ``auto`` (solo si NO se habla en español)."""
        politica = str(self.cfg.get("subtitulos", "auto") or "auto").strip().lower()
        if politica == "siempre":
            return True
        if politica == "nunca":
            return False
        idioma = (artefacto or {}).get("idioma", "ja")
        return idioma != "es"

    def _reproducir_frase(self, texto_es: str, artefacto: Optional[dict]) -> None:
        """Reproduce UNA frase ya preparada (VOICEVOX=bytes o pyttsx3)."""
        if artefacto is None:
            # El prefetch pudo fallar; re-preparamos en línea (sin prefetch).
            artefacto = self._preparar_audio_frase(texto_es)
        motor = artefacto.get("motor")
        wav_bytes = artefacto.get("wav")
        if motor in ("voicevox", "comando") and wav_bytes:
            self._reproducir_bytes(wav_bytes, texto_es)
        else:
            self._hablar_sistema(texto_es)

    # ---------------- Traducción ----------------
    def _traducir_a_japones(self, texto_es: str) -> Optional[str]:
        """Traduce a japonés usando la cuenta STT de Groq (con cache).

        Delegado a ``traduccion.traducir_con_groq`` (compartido con el
        traductor de juegos). Mantiene la cache en memoria del módulo
        (``_CACHE_TRADUCCIONES``) para no repetir llamadas por frases de tono.

        Devuelve None si no hay traducción disponible (el caller cae a pyttsx3).
        """
        key = str(self.cfg.groq_api_key_stt).strip()
        if not key:
            logger.info("[TTS] No hay groq_api_key_stt para traducir; "
                        "voy a pyttsx3.")
            return None
        return traduccion.traducir_con_groq(
            texto_es, "japonés", key, cache=_CACHE_TRADUCCIONES)

    def _traducir_a(self, texto_es: str, codigo_idioma: str) -> Optional[str]:
        """Traduce a cualquier idioma (por código: en, pt, ja...) con la cuenta STT de Groq."""
        key = str(self.cfg.groq_api_key_stt).strip()
        if not key:
            return None
        return traduccion.traducir_con_groq(texto_es, nombre_de_idioma(codigo_idioma), key,
                                            cache=_CACHE_TRADUCCIONES)

    # ---------------- VOICEVOX ----------------
    def _sintetizar_voicevox(self, texto_ja: str) -> Optional[bytes]:
        """Genera y devuelve el WAV para `texto_ja` con VOICEVOX.

        Loguea explícitamente por qué falla si no hay audio (status HTTP,
        cuerpo del error, o excepción de red) — no falla en silencio.
        """
        speaker = self.speaker_id
        base = self._url_activa()
        try:
            # Paso 1: audio_query.
            url_q = f"{base}/audio_query"
            rq = red.post(
                url_q,
                params={"text": texto_ja, "speaker": speaker},
                timeout=60,
            )
            if rq.status_code != 200:
                logger.error("[VOICEVOX] audio_query Falló: %s -> HTTP %s. "
                             "Cuerpo: %s", url_q, rq.status_code,
                             (rq.text or "")[:300])
                return None

            # Paso 2: synthesis con el JSON resultado.
            url_s = f"{base}/synthesis"
            rs = red.post(
                url_s,
                params={"speaker": speaker},
                headers={"Content-Type": "application/json"},
                data=rq.content,
                timeout=90,
            )
            if rs.status_code != 200:
                logger.error("[VOICEVOX] synthesis falló: %s -> HTTP %s. "
                             "Cuerpo: %s", url_s, rs.status_code,
                             (rs.text or "")[:300])
                return None

            logger.debug("[VOICEVOX] Síntesis OK (%d bytes) vía %s.",
                         len(rs.content), base)
            return rs.content
        except requests.exceptions.RequestException as e:
            logger.error("[VOICEVOX] Error de red en la síntesis vía %s: %s",
                         base, e)
            self._voicevox_ok_hasta = 0.0        # que el próximo intento vuelva a comprobar el servidor
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception("[VOICEVOX] Error inesperado en la síntesis: %s", e)
            self._voicevox_ok_hasta = 0.0
            return None

    # ---------------- Reproducción / fallback ----------------
    def _reproducir_bytes(self, wav_bytes: bytes, texto_es: str = "") -> None:
        """Escribe el WAV a tempfile y lo reproduce con pygame.

        ``texto_es`` es la frase original: si pygame no está disponible se
        dice con la voz del sistema en vez de quedarse mudo.
        """
        import tempfile
        import os
        try:
            import pygame  # import tardío
        except Exception:  # noqa: BLE001
            logger.warning("pygame no disponible para reproducir audio.")
            self._hablar_sistema(texto_es)
            return

        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except Exception:  # noqa: BLE001
                logger.warning("No se pudo inicializar pygame.mixer.")
                self._hablar_sistema(texto_es)
                return

        # Escribir a un archivo temporal ÚNICO por reproducción (evita que dos
        # reproducciones concurrentes se pisen el mismo archivo).
        archivo_tmp = os.path.join(
            tempfile.gettempdir(),
            f"miku_tts_{threading.get_ident()}_{int(time.time() * 1000)}.wav")
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
            # Borramos el temporal (mejor esfuerzo; pygame pudo haberlo soltado).
            try:
                pygame.mixer.music.unload()
            except Exception:  # noqa: BLE001
                pass
            try:
                os.remove(archivo_tmp)
            except Exception:  # noqa: BLE001
                pass

    def _hablar_sistema(self, texto: str) -> None:
        """Fallback final con pyttsx3 (voz del sistema). Bloquea hasta terminar."""
        if not texto:
            return
        try:
            import pyttsx3  # import tardío
            motor = pyttsx3.init()
            motor.say(texto)
            motor.runAndWait()
        except Exception:  # noqa: BLE001
            logger.warning("pyttsx3 no disponible tampoco; solo hay subtítulo/print.")
