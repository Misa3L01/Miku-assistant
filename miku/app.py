"""
app.py - Núcleo principal del asistente Miku.

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
import time
from typing import Any, Dict, Optional

# Import de configuración (siempre seguro, sin deps pesadas).
from miku.ajustes import carga as config_mod
from miku.servicios.eventos import EventBus
from miku.cerebro.parser import BrainGroq, CommandParser
from miku.servicios.scheduler import Scheduler
from miku.voz.frases import tono
from miku.ui import bandeja as bandeja_mod

# Los plugins NO se importan acá: ``plugins.registro`` los carga de forma
# perezosa y aislada (un plugin roto no tumba el arranque).
from miku.plugins.base import registrar_plugins
from miku.plugins.registro import instanciar_plugins

logger = logging.getLogger("miku.main")


def validar_configuracion(cfg: "config_mod.Config") -> None:
    """Avisa por log de opciones mal escritas o inválidas y de funciones sin configurar.

    Es solo informativo: nunca frena el arranque. Para el detalle: ``python -m miku.ajustes``.
    """
    try:
        from miku.ajustes import validacion
        informe = validacion.validar(cfg.valores, cfg.origenes.keys(), cfg.claves_locales)
    except Exception:  # noqa: BLE001
        logger.debug("No pude validar la configuración.", exc_info=True)
        return
    for linea in informe.avisos():
        logger.warning(linea)
    faltan = informe.informativos()
    if faltan:
        logger.info("Funciones sin configurar (%d): ver `python -m miku.ajustes estado`.",
                    len(faltan))
        for linea in faltan:
            logger.debug(linea)


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
        # Memoria persistente (SQLite). Solo se crea si está habilitada.
        self.memoria = None
        # Bandeja del sistema (Z7, paso 1). Convive con los modos actuales; si
        # PyQt5 no está, no se arranca (no rompe nada).
        self.bandeja = bandeja_mod.Bandeja()
        # ``cerrar()`` puede invocarse más de una vez (finally de un modo y de
        # main): la segunda llamada no debe hacer nada.
        self._cerrado = False

    # ---------------- Setup (no toca audio) ----------------
    def instalar_core(self) -> None:
        """Monta bus, parser y plugins. NO crea STT/TTS (es lazy por modo)."""
        logger.info("Ensamblando el núcleo del asistente...")

        # Memoria persistente: se instancia SOLO si la config la habilita
        # (memoria_activa = true). Si falla, se degrada a None (sin memoria)
        # sin tumbar el arranque.
        if self.cfg.memoria_activa:
            try:
                from miku.cerebro.memoria.almacen import Memoria  # import tardío
                self.memoria = Memoria()
                logger.info("Memoria persistente activa (%d recuerdo(s)).",
                            self.memoria.cantidad())
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo activar la memoria; sigo sin ella.")
                self.memoria = None

        # Brain + parser. La memoria se pasa al parser para que inyecte
        # recuerdos relevantes en el contexto del LLM.
        brain = BrainGroq(self.cfg)
        self.parser = CommandParser(self.cfg, brain, self.bus,
                                    memoria=self.memoria)
        # Exponemos el parser en el bus para que plugins como Telegram puedan
        # ejecutar comandos remotos reutilizando EXACTAMENTE el mismo cerebro.
        try:
            self.bus.parser = self.parser
            self.bus.contexto_base = self._contexto_base
        except Exception:  # noqa: BLE001
            logger.debug("No pude exponer el parser en el bus.")

        # Plugins (catálogo en miku/plugins/registro.py).
        candidatos = instanciar_plugins(self.cfg)
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

        # Adorno de PERSONALIDAD en respuestas cortas (no neutral): le pega una
        # coletilla acorde al perfil activo ("Hmph", "¡Vamos!", etc.). Es
        # best-effort: si falla, no rompe la respuesta.
        try:
            from miku.servicios import personalidad as _pers
            respuesta = _pers.adorno_corto(respuesta)
        except Exception:  # noqa: BLE001
            logger.debug("No pude aplicar el adorno de personalidad.")

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
        """Limpia recursos (subtítulos, voz lanzada por Miku y callbacks).

        Es idempotente: solo la primera llamada hace algo.
        """
        if self._cerrado:
            return
        self._cerrado = True
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
            # Oculta los subtítulos y cierra VOICEVOX SOLO si lo arrancó ESTA
            # instancia del launcher.
            try:
                self.voice.cerrar()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo cerrar la voz.", exc_info=True)
        try:
            import keyboard  # type: ignore
            keyboard.unhook_all()
        except Exception:  # noqa: BLE001
            pass
        # Cerramos la conexión SQLite de la memoria (si estaba activa).
        if self.memoria is not None:
            try:
                cerrar = getattr(self.memoria, "cerrar", None)
                if callable(cerrar):
                    cerrar()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo cerrar la memoria.")
        # Damos a los plugins la chance de cerrar recursos propios (p. ej. el
        # bot de Discord detiene su hilo/loop). Es best-effort: un plugin que
        # falle no debe tumbar el cierre.
        if self.bus:
            for plugin in getattr(self.bus, "plugins", []) or []:
                cerrar = getattr(plugin, "cerrar", None)
                if callable(cerrar):
                    try:
                        cerrar()
                    except Exception:  # noqa: BLE001
                        logger.debug("El plugin '%s' falló al cerrar.",
                                     getattr(plugin, "nombre", "?"))
        # Cerramos la bandeja (best-effort) para no dejar el icono colgado.
        try:
            self.bandeja.detener()
        except Exception:  # noqa: BLE001
            logger.debug("No pude cerrar la bandeja.")
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
        from miku.voz.salida.tts import TextoAVoz  # import tardío
        voz = TextoAVoz(asistente.cfg, subtitulos_activos=subtitulos)
        asistente.voice = voz
        # Los plugins con avisos propios (proactivo, game_booster) leen la voz
        # de ``bus.voice``.
        asistente.bus.voice = voz
        logger.info("Voz (VOICEVOX) preparada. Subtítulos: %s", subtitulos)
    return asistente.voice


def run_modo_voz(asistente: Asistente) -> None:
    """Bucle voice: escucha continua + wake word 'Miku'."""
    from miku.voz.entrada.escucha import SpeechToText  # import tardío

    _preparar_voz(asistente)  # activa TTS sólo cuando hace falta voz
    stt = SpeechToText(asistente.cfg)
    # Z7.5: en Modo Voz, Miku "vive" en la bandeja (escucha continua). Lo
    # reflejamos en el tooltip; la consola sigue disponible para ver logs.
    try:
        asistente.bandeja.actualizar("Miku - Modo Voz (escuchando)")
    except Exception:  # noqa: BLE001
        pass

    def al_wake(_texto: str) -> None:
        asistente.decir("¿Sí? Decime.")

    def al_comando(comando: str) -> None:
        print(f"\nVos: {comando}")
        asistente.responder(comando)

    stt.on_wake = al_wake
    stt.on_comando = al_comando
    # Mientras Miku habla, el micrófono espera (no graba su propia voz).
    stt.esperar_silencio = asistente.voice.esperar_libre
    stt.on_error = lambda e: logger.error("Error de voz: %s", e)

    print("\n=== Miku escuchando (decí 'Miku' para activarla) ===\n")
    try:
        stt.iniciar_escucha_continua()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo empezar la escucha continua.")
        return

    # Saludo de arranque (BRIEFING): confirmación AUDIBLE de que la escucha
    # continua quedó activa, con contexto (hora + clima + pendientes). Se dice
    # UNA sola vez, aquí (no en push-to-talk ni por hotkey).
    try:
        from miku.servicios import briefing
        saludo = briefing.generar(asistente._contexto_base())
    except Exception:  # noqa: BLE001
        logger.exception("No pude armar el briefing; uso el saludo simple.")
        saludo = "Ya estoy lista"
    asistente.decir(saludo)

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
    from miku.voz.entrada.escucha import SpeechToText  # import tardío

    _preparar_voz(asistente)
    stt = SpeechToText(asistente.cfg)
    stt.on_error = lambda e: logger.error("Error de voz: %s", e)

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
            time.sleep(0.5)  # interrumpible con Ctrl+C / "Salir" de la bandeja
    except KeyboardInterrupt:
        pass
    # El cierre (unhook_all + bus/voz) lo hace el ``finally`` de main().


# ===================================================================== #
#                    MODO TEXTO (consola estándar)                      #
# ===================================================================== #
def run_modo_texto(asistente: Asistente) -> None:
    """Bucle de texto (consola) CON voz.

    Desde la Z7.3 el modo texto siempre habla: sirve también para probar el
    TTS sin micrófono. Se verifica/arranca VOICEVOX antes del primer decir()
    y se avisa por consola qué motor se va a usar (VOICEVOX o pyttsx3).

    Args:
        asistente: La instancia del asistente.
    """
    voz = _preparar_voz(asistente, subtitulos=True)
    try:
        asegurar = getattr(voz, "asegurar_voicevox_inicial", None)
        if callable(asegurar):
            asegurar()
    except Exception:  # noqa: BLE001
        logger.exception("No pude verificar VOICEVOX al entrar al modo texto.")

    print("\n=== Miku lista (modo texto con voz) ===")
    print("Escribí tu mensaje y Enter. Escribí 'salir' para cerrar.")
    print("(Si no oís nada, mirá los mensajes [VOICEVOX]/[Voz] de arriba "
          "y los logs; si VOICEVOX no está, cae a pyttsx3.)\n")

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
def _persistir_modo(cfg: "config_mod.Config", modo: str) -> None:
    """Guarda el modo elegido en data/preferences.json (merge atómico)."""
    if cfg.guardar_preferencias({"modo_entrada": modo}):
        logger.info("Modo '%s' guardado para el próximo arranque.", modo)
    else:
        logger.error("No pude persistir el modo elegido.")


def _registrar_f22_ventanita(asistente: Asistente) -> None:
    """Registra F22 (global) para abrir la ventanita y persistir el modo.

    Es best-effort: si 'keyboard' no está, no hace nada. El callback corre en el
    hilo de 'keyboard', así que puede bloquearse mostrando la ventanita sin
    trabar el resto del asistente.
    """
    try:
        import keyboard  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.info("Sin 'keyboard' no registro F22 global: %s", e)
        return

    def _al_f22() -> None:
        try:
            modo = asistente.bandeja.pedir_modo()
            if modo:
                _persistir_modo(asistente.cfg, modo)
                asistente.bandeja.actualizar(f"Miku - modo {modo}")
        except Exception:  # noqa: BLE001
            logger.exception("Error en el hotkey F22.")

    keyboard.add_hotkey("f22", _al_f22)
    logger.info("Hotkey global F22 registrado (abre la ventanita).")


def main() -> None:
    """Función principal del asistente."""
    # 1) Configuración + logging.
    cfg = config_mod.cargar()
    configurar_logging(cfg.log_level)
    validar_configuracion(cfg)

    # 2) Asistente ensamblado (no toca audio todavía).
    asistente = Asistente(cfg)
    try:
        asistente.instalar_core()
    except Exception:  # noqa: BLE001
        logger.exception("Error fatal ensamblando el núcleo.")
        sys.exit(1)

    # 2b) Bandeja del sistema (Z7 paso 1): arranca un icono que convive con los
    #     modos. "Salir" del menú interrumpe el hilo principal para un cierre
    #     ORDENADO (mismo camino que Ctrl+C), no un kill brusco.
    try:
        import _thread as _thread_mod

        def _salir_desde_bandeja() -> None:
            logger.info("Cierre solicitado desde la bandeja.")
            _thread_mod.interrupt_main()

            # ``interrupt_main`` no despierta un ``input()`` bloqueado (modo
            # texto): si el cierre ordenado no ocurrió en unos segundos, lo
            # forzamos igual (cerrar() es idempotente).
            def _forzar_cierre() -> None:
                logger.warning("El cierre ordenado no ocurrió; fuerzo la salida.")
                asistente.cerrar()
                os._exit(0)

            temporizador = threading.Timer(6.0, _forzar_cierre)
            temporizador.daemon = True
            temporizador.start()

        asistente.bandeja.iniciar(on_salir=_salir_desde_bandeja)
    except Exception:  # noqa: BLE001
        logger.debug("No pude iniciar la bandeja (sigo sin ella).")

    # 3) Elegir modo de entrada. Preferimos la VENTANITA (Z7 paso 2); si no
    #    está disponible (sin bandeja/PyQt5), caemos al menú por consola.
    modo = None
    try:
        modo = asistente.bandeja.pedir_modo()
    except Exception:  # noqa: BLE001
        logger.debug("No pude usar la ventanita para elegir modo.")
    if modo is None:
        modo = seleccionar_modo(cfg)
    logger.info("Modo de entrada seleccionado: %s", modo)

    # 3b) Hotkey global F22 (Z7 paso 4): abre la ventanita para elegir modo y
    #     PERSISTE la elección. IMPORTANTE: NO lo registramos en modo push,
    #     porque ahí F22 sigue siendo push-to-talk (evita el conflicto).
    if modo != "push":
        try:
            _registrar_f22_ventanita(asistente)
        except Exception:  # noqa: BLE001
            logger.debug("No pude registrar el hotkey F22.")

    # 4) Correr el modo elegido (la voz/STT solo se crean dentro del modo).
    try:
        if modo == "voz":
            run_modo_voz(asistente)
        elif modo == "push":
            run_modo_push(asistente)
        else:
            # Modo texto: SIEMPRE con voz (Z7.3: se quitó el "texto sin voz").
            run_modo_texto(asistente)
    except KeyboardInterrupt:
        print("\nChau!")
    finally:
        asistente.cerrar()


if __name__ == "__main__":
    main()
