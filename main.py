"""
main.py - Núcleo principal del asistente Miku.

Punto de entrada de la app. Responsabilidades:
    1. Configurar logging y cargar la configuración (config.py).
    2. Montar el bus de eventos y registrar los plugins.
    3. Crear el parser (cerebro) que convierte texto en respuestas.
    4. Elegir el modo de entrada y correr SU bucle.

Particularidades de esta refactorización:
    - **Lazy loading por modo**: el reconocimiento de voz (STT) y la voz de
      Miku (TTS / VOICEVOX) se importan/fabrican SOLO si se entra a modo
      "voz" o "push" (o si en modo texto se elige "con voz"). En modo texto
      silencioso no se toca nada de audio.
    - **Modo texto con o sin voz**: al elegir texto se pregunta si Miku debe
      hablar; con voz se verifica/arranca VOICEVOX antes del primer decir() y
      se informa por consola qué motor se usará; sin voz es 100% limpio.
    - Push-to-talk corregido: arranca con ``on_press_key`` y termina con
      ``on_release_key``, con flag anti-reentrada, y ``unhook_all()`` al salir.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Dict, Optional

# Import de configuración (siempre seguro, sin deps pesadas).
import config as config_mod
from core.event_bus import EventBus
from core.command_parser import BrainGroq, CommandParser
from core.scheduler import Scheduler
from core import tono

# El plugin de control de sistema es ligero (imports heavy son dentro de
# métodos), así que puede importarse al inicio sin penalizar el modo texto.
from plugins import Plugin, registrar_plugins
from plugins.system_control import SystemControl
from plugins.web_search import WebSearch

logger = logging.getLogger("miku.main")


def configurar_logging(nivel: str) -> None:
    """Configura el logging global con formato y nivel."""
    nivel_obj = getattr(logging, nivel, None)
    if not isinstance(nivel_obj, int):
        nivel_obj = logging.INFO
    logging.basicConfig(
        level=nivel_obj,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class Asistente:
    """Ensambla bus + parser + plugins. La voz/STT se inyecta por modo.

    Args:
        cfg: Config del programa ya cargada.
    """

    def __init__(self, cfg: "config_mod.Config") -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.parser: Optional[CommandParser] = None
        # Objeto de voz (solo si el modo elegido lo requiere).
        self.voice = None
        # Manejadores por modo: se instalan cuando entran a correr.
        self.stt = None
        # Programador de acciones diferidas (timers). Vive con el asistente y
        # se pasa a las tools vía contexto["scheduler"].
        self.scheduler = Scheduler()

    # ---------------- Setup (no toca audio) ----------------
    def instalar_core(self) -> None:
        """Monta bus, parser y plugins. NO crea STT/TTS (es lazy por modo)."""
        logger.info("Ensamblando el núcleo del asistente...")
        self.bus = EventBus()

        # Brain + parser. La memoria queda desactivada (None) de momento.
        brain = BrainGroq(self.cfg)
        self.parser = CommandParser(self.cfg, brain, self.bus, memoria=None)

        # Plugins.
        candidatos: list[Plugin] = [SystemControl(), WebSearch()]
        activos = registrar_plugins(candidatos, self.bus)
        for plugin in activos:
            self.bus.registrar_plugin(plugin)

        logger.info("Núcleo listo. %d plugins activos.", len(self.bus.plugins))

    # ---------------- Responder ----------------
    def _contexto_base(self) -> Dict[str, Any]:
        """Contexto compartido entre comandos.

        Nota: el estado de confirmación (espera_confirmacion / pendiente
        de energía) NO vive acá (se recrearía por turno). Ahora reside en el
        objeto CommandParser (``self.parser``), que persiste entre llamadas.
        """
        return {
            "cfg": self.cfg,
            "voice": self.voice,
            "scheduler": self.scheduler,
        }

    def responder(self, texto_usuario: str) -> None:
        """Procesa el texto del usuario y emite la respuesta (voz o texto).

        En modo texto, ``voice`` es None y la respuesta se imprime. En modo
        voz/push, ``voice`` hablará y mostrará subtítulos (si los tiene).
        """
        if self.parser is None:
            return
        contexto = self._contexto_base()
        try:
            respuesta = self.parser.procesar(texto_usuario, contexto)
        except Exception:  # noqa: BLE001
            logger.exception("Error procesando el comando.")
            respuesta = "Disculpá, tuve un problema interno."

        # Variamos el tono de respuestas CORTAS conocidas ("Listo", "Ya está")
        # para que Miku no suene repetitiva. Las respuestas largas del LLM
        # pasan intactas (variar() solo toca frases del catálogo).
        respuesta = tono.variar(respuesta)

        # Emisión.
        if self.voice is not None:
            try:
                self.voice.decir(respuesta)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo hablar la respuesta.")
                print(f"[Miku] {respuesta}")
        else:
            print(f"\nMiku: {respuesta}")

    def decir(self, texto: str) -> None:
        """Habla (o imprime) un texto suelto, p. ej. el saludo de wake."""
        texto = tono.variar(texto)
        if self.voice is not None:
            try:
                self.voice.decir(texto)
            except Exception:  # noqa: BLE001
                print(f"[Miku] {texto}")
        else:
            print(f"[Miku] {texto}")

    # ---------------- Cierre ----------------
    def cerrar(self) -> None:
        """Limpia recursos (subtítulos, voz lanzada por Miku y callbacks)."""
        # Cancelamos cualquier acción diferida pendiente (no dejamos timers
        # vivos que puedan disparar un apagado/suspensión al cerrar).
        try:
            canceladas = self.scheduler.cancelar_todos()
            if canceladas:
                logger.info("Cancelé %d acción(es) programada(s) al cerrar.",
                            canceladas)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudieron cancelar las acciones programadas.")
        if self.voice is not None:
            try:
                self.voice._ocultar_subtitulos()  # noqa: evita ventana abierta
            except Exception:  # noqa: BLE001
                pass
            # Solo cerramos VOICEVOX si lo arrancó ESTA instancia del launcher.
            try:
                detener = getattr(self.voice,
                                  "detener_voicevox_si_lo_arrancamos", None)
                if callable(detener):
                    detener()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo detener VOICEVOX lanzado por Miku.")
        try:
            import keyboard  # type: ignore
            keyboard.unhook_all()
        except Exception:  # noqa: BLE001
            pass
        if self.bus:
            self.bus.detener()


# ===================================================================== #
#                          MODO VOZ (wake word)                         #
# ===================================================================== #
def _preparar_voz(asistente: Asistente, subtitulos: bool = True) -> Any:
    """Crea (lazy) el motor de voz VOICEVOX.

    Args:
        asistente: La instancia del asistente.
        subtitulos: True para mostrar el overlay de subtítulos en pantalla.
    """
    if asistente.voice is None:
        from core.text_to_speech import TextoAVoz  # import tardío
        voz = TextoAVoz(asistente.cfg, subtitulos_activos=subtitulos)
        asistente.voice = voz
        logger.info("Voz (VOICEVOX) preparada. Subtítulos: %s", subtitulos)
    return asistente.voice


def run_modo_voz(asistente: Asistente) -> None:
    """Bucle voice: escucha continua + wake word 'Miku'."""
    from core.speech_to_text import SpeechToText  # import tardío
    import time

    _preparar_voz(asistente)  # activa TTS sólo cuando hace falta voz
    stt = SpeechToText(asistente.cfg)

    def al_wake(_texto: str) -> None:
        asistente.decir("¿Sí? Decime.")

    def al_comando(comando: str) -> None:
        print(f"\nVos: {comando}")
        asistente.responder(comando)

    stt.on_wake = al_wake
    stt.on_comando = al_comando
    stt.on_error = lambda e: logger.error("Error de voz: %s", e)

    print("\n=== Miku escuchando (decí 'Miku' para activarla) ===\n")
    try:
        stt.iniciar_escucha_continua()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo empezar la escucha continua.")
        return

    # Saludo de arranque: confirmación AUDIBLE de que la escucha continua quedó
    # activa. Se dice UNA sola vez, aquí (no en push-to-talk ni por hotkey).
    asistente.decir("Ya estoy lista")

    mostrar_microfonos(stt)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stt.detener_escucha()
    print("\nChau!")


def mostrar_microfonos(stt: Any) -> None:
    """Lista los micrófonos y cuál está en uso según config."""
    if not getattr(stt, "sr", None):
        try:
            stt.sr  # fuerza el import para ver los dispositivos
        except Exception:  # noqa: BLE001
            return
    try:
        nombres = stt.sr.list_microphone_names() or []
        print("Micrófonos encontrados (índice configurado: "
              f"{stt.cfg.microfono_index}):")
        for i, n in enumerate(nombres):
            marca = " <-- usando este" if i == stt.cfg.microfono_index else ""
            print(f"  [{i}] {n}{marca}")
    except Exception:  # noqa: BLE001
        logger.warning("No se pudieron listar los micrófonos.")


# ===================================================================== #
#                       MODO PUSH-TO-TALK (F22)                         #
# ===================================================================== #
_TECLA_PTT = "f22"


def run_modo_push(asistente: Asistente) -> None:
    """Bucle push-to-talk con pulsar-grabar / soltar-detener (correcto).

    Tecnología:
        - ``keyboard.on_press_key(F22, iniciar)``
        - ``keyboard.on_release_key(F22, finalizar)``
        - flag ``_grabando`` para evitar pulsaciones simultáneas.
        - ``keyboard.unhook_all()`` al salir (lo hace Asistente.cerrar()).
    """
    from core.speech_to_text import SpeechToText  # import tardío

    _preparar_voz(asistente)
    stt = SpeechToText(asistente.cfg)

    try:
        import keyboard  # type: ignore
    except Exception:  # noqa: BLE001
        logger.error(
            "No se pudo importar 'keyboard' para el push-to-talk. "
            "Instalala con: pip install keyboard")
        return

    print(f"\n=== Push-to-talk activo (mantené {_TECLA_PTT}) ===\n")

    # Estado simple para la grabación de una sola toma a la vez.
    estado = {"grabando": False}

    def _respuesta(texto: str) -> None:
        if texto:
            print(f"\nVos: {texto}")
            asistente.responder(texto)

    def iniciar_grabacion(_evento) -> None:
        """Se llama al PRESIONAR la tecla. Empieza a grabar audio."""
        if estado["grabando"]:
            return  # evita reentrada
        estado["grabando"] = True
        logger.info("F22 presionada: grabando...")
        try:
            # Graba mientras la tecla esté apretada (corta al soltar).
            stt.capturar_para_push(_respuesta)
        except Exception:  # noqa: BLE001
            logger.exception("Error arrancando la grabación.")
            estado["grabando"] = False

    def finalizar_grabacion(_evento) -> None:
        """Se llama al SOLTAR la tecla. Corta la captura activa YA."""
        if not estado["grabando"]:
            return
        try:
            # Levanta el flag para que el hilo de grabación corte al momento.
            if hasattr(stt, "detener_captura_activa"):
                stt.detener_captura_activa()
        except Exception:  # noqa: BLE001
            logger.exception("Error finalizando la grabación.")
        finally:
            estado["grabando"] = False
            logger.info("F22 soltada: fin de grabación (transcribiendo).")

    # Registro de la tecla (release de eventos de keyup arrancan la toma).
    keyboard.on_press_key(_TECLA_PTT, iniciar_grabacion)
    keyboard.on_release_key(_TECLA_PTT, finalizar_grabacion)

    try:
        while True:
            signal = threading.Event()
            signal.wait(timeout=60)
    except KeyboardInterrupt:
        pass
    finally:
        asistente.cerrar()  # unhook_all + cierre de bus/voz


# ===================================================================== #
#                    MODO TEXTO (consola estándar)                      #
# ===================================================================== #
def run_modo_texto(asistente: Asistente, con_voz: bool = False) -> None:
    """Bucle de texto (consola).

    Args:
        asistente: La instancia del asistente.
        con_voz: Si True, se fuerza el TTS (idea para probar la voz sin
            micrófono). Si False (default), es un modo texto 100% silencioso
            que no carga librerías de audio ni subtítulos.
    """
    if con_voz:
        # Carga el motor de voz de forma lazy pero FORZADA (el usuario lo pidió).
        voz = _preparar_voz(asistente, subtitulos=True)
        # Verificamos/arrancamos VOICEVOX ANTES del primer decir() y avisamos
        # por consola qué motor se va a usar (VOICEVOX o pyttsx3).
        try:
            asegurar = getattr(voz, "asegurar_voicevox_inicial", None)
            if callable(asegurar):
                asegurar()
        except Exception:  # noqa: BLE001
            logger.exception("No pude verificar VOICEVOX al entrar al modo voz.")
        cabecera = "=== Miku lista (modo texto CON VOZ — probá el TTS) ==="
        ayuda = ("(Si no oís nada, mirá los mensajes [VOICEVOX]/[Voz] de arriba "
                 "y los logs; si VOICEVOX no está, cae a pyttsx3.)")
    else:
        cabecera = "=== Miku lista (modo texto silencioso) ==="
        ayuda = "(No se carga audio ni subtítulos en este modo.)"

    print(f"\n{cabecera}")
    print("Escribí tu mensaje y Enter. Escribí 'salir' para cerrar.")
    print(ayuda + "\n")

    while True:
        try:
            comando = input("Vos: ").strip()
            if not comando:
                continue
            if comando.lower() in ("salir", "exit", "quit"):
                print("Chau!")
                break
            # responder() habla si self.voice no es None (solo cuando con_voz)
            # y en caso contrario imprime el texto por consola.
            asistente.responder(comando)
        except KeyboardInterrupt:
            print("\nChau!")
            break
        except EOFError:
            break
        except Exception:  # noqa: BLE001
            logger.exception("Error en el bucle de texto.")
        finally:
            pass


# ===================================================================== #
#                    Selección de modo (al arrancar)                    #
# ===================================================================== #
def preguntar_submodo_texto() -> bool:
    """Pregunta si en modo texto Miku debe hablar o quedarse silenciosa.

    Returns:
        True = con voz (forzar TTS, para probar sin micrófono).
        False = solo texto (silencioso, sin audio ni subtítulos).
    """
    # Permite automatización: MIKU_TEXTO_SIN_VOZ=1 fuerza silencio.
    env = os.getenv("MIKU_TEXTO_SIN_VOZ", "").strip().lower()
    if env in ("1", "true", "sí", "si", "sin"):
        return False
    if env in ("0", "false", "con", "convoz"):
        return True

    print("\nModo texto: ¿querés que Miku hable?")
    print("  [V] Con voz      (probar el TTS sin micrófono)")
    print("  [T] Solo texto   (silencioso, sin audio ni subtítulos)")
    opcion = input("Elegí V o T (Enter = V): ").strip().lower()
    if opcion in ("t", "txt", "sil", "silencio", "no"):
        return False
    if opcion in ("texto", "te"):
        # "te..." aclarativo -> tratamos como T solo si empieza con 't'
        return False
    # Default y cualquier valor "voz" -> con voz.
    return True


def seleccionar_modo(cfg: "config_mod.Config") -> str:
    """Pregunta cómo operar hoy. Usa config como default con Enter."""
    default = cfg.modo_entrada
    mapeo_default = {"voz": "1", "texto": "3", "push": "2"}
    default_num = mapeo_default.get(default, "1")

    print("\n¿Cómo querés operar esta vez?")
    print("  [1] Hablando      (decís 'Miku' para activarla)")
    print("  [2] Push-to-talk  (mantenés F22 para hablar)")
    print("  [3] Escribiendo   (modo texto)")

    opcion = input(f"Elegí 1, 2 o 3 (Enter = {default_num}): ").strip()

    if opcion in ("1", "2", "3"):
        return {"1": "voz", "2": "push", "3": "texto"}[opcion]
    if opcion == "":
        return cfg.modo_entrada
    print(f"No entendí '{opcion}', uso el default ({default}).")
    return default


# ===================================================================== #
#                                main                                   #
# ===================================================================== #
def main() -> None:
    """Función principal del asistente."""
    # 1) Configuración + logging.
    cfg = config_mod.cargar()
    configurar_logging(cfg.log_level)

    # 2) Asistente ensamblado (no toca audio todavía).
    asistente = Asistente(cfg)
    try:
        asistente.instalar_core()
    except Exception:  # noqa: BLE001
        logger.exception("Error fatal ensamblando el núcleo.")
        sys.exit(1)

    # 3) Elegir modo de entrada.
    modo = seleccionar_modo(cfg)
    logger.info("Modo de entrada seleccionado: %s", modo)

    # 4) Correr el modo elegido (la voz/STT solo se crean dentro del modo).
    try:
        if modo == "voz":
            run_modo_voz(asistente)
        elif modo == "push":
            run_modo_push(asistente)
        else:
            # Modo texto: el usuario elige si con o sin voz.
            con_voz = preguntar_submodo_texto()
            run_modo_texto(asistente, con_voz=con_voz)
    except KeyboardInterrupt:
        print("\nChau!")
    finally:
        asistente.cerrar()


if __name__ == "__main__":
    main()
