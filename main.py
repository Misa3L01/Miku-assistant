"""
main.py - Núcleo principal del asistente Miku.

Punto de entrada de la app. Se encarga de:
    1. Configurar logging.
    2. Cargar la configuración (config.py).
    3. Construir el bus de eventos y registrar plugins.
    4. Crear el reconocimiento de voz (STT), la voz de Miku (TTS) y el
       parser de comandos.
    5. Inicializar la memoria persistente (chromadb).
    6. Elegir el modo de entrada y correr su bucle principal.

Modos de entrada (se eligen al arrancar):
    1) Voz       -> decís "Miku" para activarla (escucha continua).
    2) Push-talk -> mantenés la tecla F22 (botón HP Omen) para hablar.
    3) Texto     -> escribís los comandos en la consola.

Todo el cierre se maneja con KeyboardInterrupt (Ctrl+C) de forma limpia.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Dict, Optional

# Import de configuración (raíz).
import config as config_mod
from core.event_bus import EventBus
from core.memoria import Memoria
from core.command_parser import BrainGroq, CommandParser
from core.text_to_speech import TextoAVoz
from plugins import Plugin, registrar_plugins
from plugins.system_control import SystemControl

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
    # Reduce el ruido de librerías de terceros.
    logging.getLogger("phonemizer").setLevel(logging.ERROR)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class Asistente:
    """Ensambla todos los componentes del asistente en una sola entidad.

    Mantiene las dependencias en un solo lugar para que main() pueda
    arrancar el modo de entrada que corresponda.
    """

    def __init__(self, cfg: "config_mod.Config") -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.voice: Optional[TextoAVoz] = None
        self.parser: Optional[CommandParser] = None
        self.memoria: Optional[Memoria] = None
        # Entradas instaladas (se llenan bajo demanda por el modo).
        self.stt = None

    # ---------------- Setup de componentes ----------------
    def instalar_core(self) -> None:
        """Construye bus, memoria, voz (lazy) y parser. Lanza plugins."""
        logger.info("Ensamblando el núcleo del asistente...")

        # 1) Memoria persistente.
        try:
            self.memoria = Memoria()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo inicializar la memoria; sigue sin ella.")
            self.memoria = None

        # 2) Bus de eventos.
        self.bus = EventBus()

        # 3) Voz de Miku (lazy: no carga los modelos todavía).
        self.voice = TextoAVoz(self.cfg)

        # 4) Brain + parser.
        brain = BrainGroq(self.cfg)
        self.parser = CommandParser(self.cfg, brain, self.bus, self.memoria)

        # 5) Plugins (SystemControl y futuros). SystemControl ya está
        #    importado arriba; registrar_plugins lo inicializa y nos devuelve
        #    los que quedaron activos para conectarlos al bus.
        candidatos: list[Plugin] = [SystemControl()]
        activos = registrar_plugins(candidatos, self.bus)
        for plugin in activos:
            self.bus.registrar_plugin(plugin)

        logger.info("Núcleo listo. %d plugins activos.", len(self.bus.plugins))

    # ---------------- Utilidad: generar respuesta ----------------
    def _contexto_base(self) -> Dict[str, Any]:
        """Contexto por defecto compartido entre comandos."""
        return {
            "espera_confirmacion": False,
            "pendiente_energia": None,
            "cfg": self.cfg,
            "voice": self.voice,
        }

    def responder(self, texto_usuario: str) -> None:
        """Procesa un comando/usuario y emite la respuesta por voz/texto."""
        if self.parser is None:
            return
        contexto = self._contexto_base()
        try:
            respuesta = self.parser.procesar(texto_usuario, contexto)
        except Exception:  # noqa: BLE001
            logger.exception("Error procesando el comando.")
            respuesta = "Disculpá, tuve un problema interno."

        # Emite la respuesta por voz (si está disponible y cargada) o texto.
        if self.voice:
            try:
                self.voice.decir(respuesta)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo hablar la respuesta.")
        else:
            print(f"[Miku] {respuesta}")
        return None

    def decir(self, texto: str) -> None:
        """Habla `texto` por la voz de Miku (o lo imprime si no hay)."""
        if self.voice:
            try:
                self.voice.decir(texto)
            except Exception:  # noqa: BLE001
                print(f"[Miku] {texto}")
        else:
            print(f"[Miku] {texto}")


# ===================================================================== #
#                          MODO VOZ (wake word)                         #
# ===================================================================== #
def _hablar_saludo_voz(asistente: Asistente) -> None:
    """Pequeña frase de confirmación al detectar la wake word 'Miku'."""
    asistente.decir("¿Sí? Decime.")


def run_modo_voz(asistente: Asistente) -> None:
    """Bucle voice: escucha continua + wake word 'Miku'."""
    from core.speech_to_text import SpeechToText
    import time

    stt = SpeechToText(asistente.cfg)

    def al_wake(_texto: str) -> None:
        _hablar_saludo_voz(asistente)

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

    # Muestra los micrófonos disponibles para configurar el índice.
    mostrar_microfonos(stt)
    try:
        while True:
            time.sleep(0.5)  # mantenemos vivo el main; la voz va en su hilo
    except KeyboardInterrupt:
        pass
    finally:
        stt.detener_escucha()
    print("\nChau!")


def mostrar_microfonos(stt: Any) -> None:
    """Lista los micrófonos y cuál está en uso según config."""
    if not getattr(stt, "sr", None):
        try:
            stt.sr  # noqa: B018  fuerza el import para ver dispositivos
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
    """Bucle push-to-talk: se escucha mientras se mantiene F22."""
    from core.speech_to_text import SpeechToText

    stt = SpeechToText(asistente.cfg)

    # Importamos la librería de teclado (ya es dependencia opcional).
    try:
        import keyboard  # type: ignore
    except Exception:  # noqa: BLE001
        logger.error(
            "No se pudo importar 'keyboard' para el modo push-to-talk. "
            "Instalala con: pip install keyboard")
        return

    print(f"\n=== Push-to-talk activo (mantené {_TECLA_PTT}) ===\n")

    def tomar_comando() -> None:
        """Captura un comando mientras se suelta la tecla."""
        def _callback(texto: str) -> None:
            if texto:
                print(f"\nVos: {texto}")
                asistente.responder(texto)

        # El STT ya está "escuchando" sólo cuando la tecla termina de dar
        # una toma (se explica en el siguiente bloque con F22).
        stt.capturar_comando(_callback)

    def en_soltar(_evento) -> None:
        # Se llamará al capturar porque listamos on release.
        tomar_comando()

    # Registramos la tecla en el hilo del mainloop de keyboard.
    def monitor_ptt() -> None:
        try:
            keyboard.on_release_key(_TECLA_PTT, en_soltar)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo registrar la tecla %s.", _TECLA_PTT)
            return

        logger.info("Esperando que presiones %s para hablar...", _TECLA_PTT)
        # Mantenemos vivo este hilo hasta Ctrl+C.
        while True:
            try:
                thread_wait = threading.Event()
                thread_wait.wait(timeout=60)
            except KeyboardInterrupt:
                break

    monitor_ptt()


# ===================================================================== #
#                    MODO TEXTO (consola estándar)                      #
# ===================================================================== #
def run_modo_texto(asistente: Asistente) -> None:
    """Bucle en texto: lee de la consola y responde en voz/texto."""
    print("\n=== Miku lista (modo texto) ===")
    print("Escribí tu mensaje y Enter. Escribí 'salir' para cerrar.\n")

    while True:
        try:
            comando = input("Vos: ").strip()
            if not comando:
                continue
            if comando.lower() in ("salir", "exit", "quit"):
                print("Chau!")
                break
            asistente.responder(comando)
        except KeyboardInterrupt:
            print("\nChau!")
            break
        except EOFError:
            break
        except Exception:  # noqa: BLE001
            logger.exception("Error en el bucle de texto.")


# ===================================================================== #
#                    Selección de modo (al arrancar)                    #
# ===================================================================== #
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

    # 2) Asistente ensamblado.
    asistente = Asistente(cfg)
    try:
        asistente.instalar_core()
    except Exception:  # noqa: BLE001
        logger.exception("Error fatal ensamblando el núcleo.")
        sys.exit(1)

    # 3) Elegir modo de entrada.
    modo = seleccionar_modo(cfg)
    logger.info("Modo de entrada seleccionado: %s", modo)

    # 4) Correr el modo elegido.
    try:
        if modo == "voz":
            run_modo_voz(asistente)
        elif modo == "push":
            run_modo_push(asistente)
        else:
            run_modo_texto(asistente)
    except KeyboardInterrupt:
        print("\nChau!")
    finally:
        if asistente.bus:
            asistente.bus.detener()


if __name__ == "__main__":
    main()
