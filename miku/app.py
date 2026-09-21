"""
app.py - Núcleo principal del asistente Miku.

Punto de entrada de la aplicación (``python main.py``). Responsabilidades:
    1. Configurar el logging (consola y archivo) y cargar/validar la configuración.
    2. Asegurar UNA sola Miku (si ya hay una corriendo, la invoca y termina).
    3. Montar el bus de eventos, los plugins y el parser (cerebro).
    4. Poner el icono de la bandeja y elegir el modo.
    5. Esperar hasta que se pida salir, y cerrar todo de forma ordenada.

Miku vive en segundo plano, en la bandeja. Tiene dos modos, que se pueden cambiar en caliente:
    - **voz** (el normal): escucha continua; se la llama diciendo "Miku" o apretando la tecla de invocación (F13).
    - **texto** (depuración): una ventana para escribirle; habla igual.

Argumentos de línea de comandos:
    --silencioso   No muestra la ventanita de modo: usa el modo guardado (así arranca con Windows).
    --invocar      Al terminar de arrancar, saluda y escucha un comando (así abre el atajo de teclado).
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

# onnxruntime (lo usa la memoria semántica, fastembed) TIENE que importarse antes que PyQt5: si Qt
# ya cargó sus DLL, onnxruntime falla con "DLL load failed ... rutina de inicialización". Al
# precargarlo acá (cuesta ~0,3 s) la memoria por significado anda aunque la bandeja use Qt.
try:
    import onnxruntime  # noqa: F401
except Exception:  # noqa: BLE001  (opcional: sin él la memoria busca por palabras)
    pass

# Import de configuración (siempre seguro, sin deps pesadas).
from miku.ajustes import carga as config_mod
from miku.servicios.eventos import EventBus
from miku.cerebro.parser import BrainGroq, CommandParser
from miku.servicios import recordatorios, tecla as tecla_mod
from miku.voz.frases import catalogo_respuestas
from miku.servicios.scheduler import Scheduler
from miku.voz.frases import tono
from miku.ui import bandeja as bandeja_mod

# Los plugins NO se importan acá: ``plugins.registro`` los carga de forma
# perezosa y aislada (un plugin roto no tumba el arranque).
from miku.plugins.base import registrar_plugins
from miku.plugins.registro import instanciar_plugins

logger = logging.getLogger("miku.main")

MODOS = ("voz", "texto")

#: Lo que dice Miku al oír "Miku" (o la tecla de invocación) antes de escuchar el comando.
SALUDO_WAKE = "¿Sí? Decime."


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
    """Configura el logging: consola (si hay) y archivo rotativo ``data/miku.log``.

    El archivo importa cuando Miku arranca con ``pythonw`` (con Windows o con la tecla de invocación): ahí no hay
    consola y, sin archivo, los errores se perderían.
    """
    nivel_obj = getattr(logging, nivel, None)
    if not isinstance(nivel_obj, int):
        nivel_obj = logging.INFO
    manejadores: List[logging.Handler] = []
    if sys.stderr is not None:
        manejadores.append(logging.StreamHandler())
    try:
        ruta = config_mod.BASE_DIR / "data" / "miku.log"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        manejadores.append(logging.handlers.RotatingFileHandler(
            ruta, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=nivel_obj,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=manejadores,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class Asistente:
    """Ensambla bus + parser + plugins y administra los modos (voz / texto).

    Args:
        cfg: Config del programa ya cargada.
    """

    def __init__(self, cfg: "config_mod.Config") -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.parser: Optional[CommandParser] = None
        # Objeto de voz (se crea la primera vez que hace falta hablar).
        self.voice = None
        # Reconocimiento de voz: existe mientras el modo voz esté activo.
        self.stt = None
        # Programador de acciones diferidas (timers). Vive con el asistente y
        # se pasa a las tools vía contexto["scheduler"].
        self.scheduler = Scheduler()
        # Memoria persistente (SQLite). Solo se crea si está habilitada.
        self.memoria = None
        # Bandeja del sistema; si PyQt5 no está, no se arranca (no rompe nada).
        self.bandeja = bandeja_mod.Bandeja()
        # Ventana de depuración del modo texto (se crea la primera vez).
        self.consola = None
        #: Modo actual: "voz", "texto" o None (todavía no se eligió).
        self.modo: Optional[str] = None
        self._lock_modo = threading.RLock()
        self._saludo_dado = False
        #: Recordatorios que vencieron con Miku cerrada (se avisan al saludar).
        self._recordatorios_perdidos: List[Dict[str, Any]] = []
        self._invocacion_pendiente = False
        self._hotkey: Any = None
        #: Valor de ``tecla_invocar`` con el que se registró el hotkey actual.
        self._tecla_registrada: str = ""
        self.instancia: Any = None
        # ``cerrar()`` puede invocarse más de una vez: la segunda llamada no hace nada.
        self._cerrado = False
        self._salir = threading.Event()

    # ---------------- Setup (no toca audio) ----------------
    def instalar_core(self) -> None:
        """Monta bus, parser y plugins. NO crea STT/TTS (es lazy por modo)."""
        # Los recordatorios sobreviven a cerrar Miku: se guardan junto a las preferencias.
        self.scheduler = Scheduler(self.cfg.ruta_archivo.parent / "recordatorios.json")
        self._recordatorios_perdidos = self.scheduler.recuperar(
            lambda datos: recordatorios.fabricar_callback(lambda: self.voice, datos))
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
                if self.memoria.cantidad() > 0:
                    # Cargar el modelo de embeddings tarda segundos: que no lo pague la primera orden.
                    from miku.cerebro.memoria import embeddings
                    threading.Thread(target=embeddings.precalentar, name="miku_precalentar_memoria",
                                     daemon=True).start()
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
        self.bus.parser = self.parser
        self.bus.contexto_base = self._contexto_base

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

    def responder(self, texto_usuario: str) -> str:
        """Procesa el texto del usuario, emite la respuesta y la devuelve.

        Si hay voz, Miku la dice (con subtítulos); si no, se imprime. El texto de la respuesta
        se devuelve para que la ventana de depuración lo muestre.
        """
        if self.parser is None:
            return ""
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
        return respuesta

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

    # ---------------- Voz ----------------
    def _preparar_voz(self, subtitulos: bool = True) -> Any:
        """Crea (lazy) el motor de voz VOICEVOX y lo expone en el bus."""
        if self.voice is None:
            from miku.voz.salida.tts import TextoAVoz  # import tardío
            voz = TextoAVoz(self.cfg, subtitulos_activos=subtitulos)
            self.voice = voz
            # Los plugins con avisos propios (proactivo, game_booster) leen la voz de ``bus.voice``.
            self.bus.voice = voz
            logger.info("Voz (VOICEVOX) preparada. Subtítulos: %s", subtitulos)
        return self.voice

    def _al_comando_voz(self, comando: str) -> None:
        """Lo que se oyó tras "Miku" (o tras la tecla de invocación): se ejecuta y se muestra si hay ventana."""
        print(f"\nVos: {comando}")
        respuesta = self.responder(comando)
        if self.consola is not None:
            self.consola.agregar("Vos (voz)", comando)
            self.consola.agregar("Miku", respuesta)

    def _iniciar_voz(self) -> bool:
        """Arranca la escucha continua. Devuelve False si no se pudo."""
        from miku.voz.entrada.escucha import SpeechToText  # import tardío

        voz = self._preparar_voz()
        # Precalienta VOICEVOX y el saludo del wake en segundo plano: si no está corriendo (p. ej. al abrir
        # Miku con la tecla o con Windows), arrancarlo tarda ~12 s y la primera frase no debería pagar esa espera.
        threading.Thread(target=getattr(voz, "precalentar", lambda *_: None), args=([SALUDO_WAKE],),
                         name="miku_precalentar_voz", daemon=True).start()
        if self.stt is None:
            stt = SpeechToText(self.cfg)
            stt.on_wake = lambda _t: self.decir(SALUDO_WAKE)
            stt.on_comando = self._al_comando_voz
            # Mientras Miku habla, el micrófono espera (no graba su propia voz).
            stt.esperar_silencio = voz.esperar_libre
            stt.on_error = lambda e: logger.error("Error de voz: %s", e)
            self.stt = stt
        try:
            self.stt.iniciar_escucha_continua()
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo empezar la escucha continua.")
            return False
        self.bandeja.actualizar("Miku - Modo Voz (escuchando)")
        print(f"\n=== Miku escuchando (decí 'Miku' o apretá {tecla_mod.nombre_legible(self.tecla_invocacion())}) ===\n")
        mostrar_microfonos(self.stt)
        return True

    def _saludar(self) -> None:
        """Saludo corto al arrancar (sin hora ni clima), una sola vez.

        La hora y el clima ya no se dicen en cada arranque: se dan "de vez en cuando", al volver de una
        ausencia (regla ``BriefingAlVolver``). Con ``BRIEFING_AL_INICIAR = True`` se recupera el viejo
        resumen completo. Un recordatorio que venció con Miku cerrada siempre se avisa.
        """
        if self._saludo_dado:
            return
        self._saludo_dado = True
        perdidos = recordatorios.texto_perdidos(self._recordatorios_perdidos)
        self._recordatorios_perdidos = []
        if not self.cfg.saludo_al_iniciar:
            if perdidos:
                self.decir(perdidos)
            return
        try:
            if self.cfg.get("briefing_al_iniciar", False):
                from miku.servicios import briefing
                saludo = briefing.generar(self._contexto_base())
            else:
                from miku.voz.frases.banco import frases
                catalogo_respuestas.registrar()
                saludo = frases.elegir("app.arranque")
        except Exception:  # noqa: BLE001
            logger.exception("No pude armar el saludo; uso el simple.")
            saludo = "Ya estoy lista"
        self.decir(f"{saludo} {perdidos}".strip())

    def _detener_voz(self) -> None:
        """Detiene la escucha continua (el TTS queda disponible)."""
        if self.stt is not None:
            try:
                self.stt.detener_escucha()
            except Exception:  # noqa: BLE001
                logger.debug("No se pudo detener la escucha.", exc_info=True)

    # ---------------- Texto (depuración) ----------------
    def _enviar_desde_consola(self, texto: str) -> None:
        respuesta = self.responder(texto)
        if self.consola is not None:
            self.consola.agregar("Miku", respuesta)

    def _mostrar_texto(self) -> bool:
        """Muestra la ventana de depuración. False si no hay PyQt5."""
        from miku.ui.consola import ConsolaDebug  # import tardío

        voz = self._preparar_voz()
        try:
            asegurar = getattr(voz, "asegurar_voicevox_inicial", None)
            if callable(asegurar):
                asegurar()
        except Exception:  # noqa: BLE001
            logger.exception("No pude verificar VOICEVOX al entrar al modo texto.")
        if self.consola is None:
            try:
                self.consola = ConsolaDebug(self._enviar_desde_consola)
            except RuntimeError:
                return False
        self.consola.mostrar()
        self.bandeja.actualizar("Miku - Modo texto (depuración)")
        return True

    # ---------------- Modos ----------------
    def cambiar_modo(self, modo: str, persistir: bool = True) -> bool:
        """Pasa al modo ``"voz"`` o ``"texto"`` en caliente.

        Args:
            modo: ``"voz"`` o ``"texto"`` (cualquier otro valor se ignora).
            persistir: Si True, se guarda como modo de arranque.

        Returns:
            True si quedó en el modo pedido.
        """
        if modo not in MODOS:
            logger.warning("Modo desconocido: %r", modo)
            return False
        with self._lock_modo:
            if modo == "voz":
                if self.consola is not None:
                    self.consola.ocultar()
                ok = self.modo == "voz" or self._iniciar_voz()
            else:
                self._detener_voz()
                ok = self._mostrar_texto()
            if not ok:
                return False
            anterior, self.modo = self.modo, modo
        if persistir:
            self.cfg.guardar_preferencias({"modo_entrada": modo})
        if modo == "voz" and anterior is None:
            self._saludar()
        if self._invocacion_pendiente:
            self._invocacion_pendiente = False
            self.invocar()
        logger.info("Modo actual: %s", modo)
        return True

    def invocar(self) -> None:
        """Invocación directa (tecla / atajo): Miku saluda y escucha un comando sin la palabra "Miku".

        En modo texto trae la ventana al frente. Si todavía está arrancando, queda pendiente.
        Es seguro llamarlo desde cualquier hilo.
        """
        with self._lock_modo:
            modo, stt, consola = self.modo, self.stt, self.consola
        if modo == "voz" and stt is not None:
            stt.invocar()
        elif modo == "texto" and consola is not None:
            consola.mostrar()
        else:
            self._invocacion_pendiente = True

    # ---------------- Menú de la bandeja ----------------
    def estado_menu(self) -> Dict[str, Any]:
        """Estado real para marcar las casillas del menú."""
        from miku.servicios import arranque
        return {"modo": self.modo,
                "inicio_windows": arranque.inicio_windows_activo(),
                "atajo_tecla": arranque.atajo_activo()}

    def acciones_menu(self) -> Dict[str, Callable[..., Any]]:
        """Acciones del menú de la bandeja (se llaman en un hilo aparte)."""
        from miku.servicios import arranque

        def inicio_windows(marcado: bool) -> None:
            (arranque.activar_inicio_windows if marcado else arranque.desactivar_inicio_windows)()

        def atajo_tecla(marcado: bool) -> None:
            if marcado:
                arranque.activar_atajo(tecla=self.tecla_invocacion())
            else:
                arranque.desactivar_atajo()
            self.actualizar_hotkey()

        return {
            "invocar": self.invocar,
            "modo_voz": lambda _m=True: self.cambiar_modo("voz"),
            "modo_texto": lambda _m=True: self.cambiar_modo("texto"),
            "inicio_windows": inicio_windows,
            "atajo_tecla": atajo_tecla,
            "elegir_tecla": self.elegir_tecla,
        }

    def tecla_invocacion(self) -> str:
        """Tecla que invoca a Miku (``TECLA_INVOCAR``; F13 por defecto)."""
        return str(self.cfg.get("tecla_invocar", tecla_mod.POR_DEFECTO) or tecla_mod.POR_DEFECTO).strip().lower()

    def actualizar_hotkey(self) -> None:
        """Registra la tecla de invocación dentro de Miku (por defecto F13).

        Con el atajo de Windows activo (y una tecla que un acceso directo admita, como F13), Windows
        lanza una segunda ejecución que invoca a esta; registrar además el hotkey acá haría que la tecla
        disparara la invocación dos veces. Cualquier otra tecla se maneja acá, y solo funciona con
        Miku abierta.
        """
        from miku.servicios import arranque
        try:
            import keyboard  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.info("Sin 'keyboard' no registro la tecla de invocación dentro de Miku: %s", e)
            return
        tecla = self.tecla_invocacion()
        lo_maneja_windows = arranque.atajo_valido(tecla) is not None and arranque.atajo_activo()
        if self._hotkey is not None and (lo_maneja_windows or self._tecla_registrada != tecla):
            try:
                keyboard.remove_hotkey(self._hotkey)
            except Exception:  # noqa: BLE001
                pass
            self._hotkey = None
        if lo_maneja_windows:
            logger.info("La tecla %s la maneja el atajo de Windows.", tecla)
        elif self._hotkey is None:
            try:
                self._hotkey = keyboard.add_hotkey(tecla_mod.a_hotkey(tecla), self.invocar)
                self._tecla_registrada = tecla
                logger.info("Tecla de invocación registrada dentro de Miku: %s.", tecla)
            except Exception as e:  # noqa: BLE001
                logger.error("No pude registrar la tecla '%s': %s", tecla, e)

    def elegir_tecla(self, segundos: float = 10.0) -> Optional[str]:
        """Detecta la próxima tecla que se apriete, la guarda como tecla de invocación y la activa.

        Se llama desde el menú de la bandeja (en un hilo aparte). Avisa por voz/consola.
        """
        from miku.servicios import arranque
        self.decir("Apretá ahora la tecla que querés usar para invocarme. Te escucho diez segundos.")
        elegida = tecla_mod.detectar(segundos)
        if not elegida:
            self.decir("No detecté ninguna tecla. Puede que esa tecla la maneje un programa del fabricante.")
            return None
        self.cfg.guardar_preferencias({"tecla_invocar": elegida})
        if arranque.atajo_activo():
            # El acceso directo lleva su propia tecla: se rehace con la nueva (o se quita si esa tecla
            # no sirve para un acceso directo; entonces Miku la maneja sola, con Miku abierta).
            if not arranque.activar_atajo(tecla=elegida):
                arranque.desactivar_atajo()
        self.actualizar_hotkey()
        self.decir(f"Listo, ahora me invocás con {tecla_mod.nombre_legible(elegida)}.")
        return elegida

    # ---------------- Salida y cierre ----------------
    def pedir_salir(self) -> None:
        """Pide cerrar Miku (desde la bandeja, un plugin, etc.). Nunca bloquea."""
        logger.info("Cierre solicitado.")
        self._salir.set()

        # Red de seguridad: si el cierre ordenado no termina, se fuerza.
        def _forzar() -> None:
            logger.warning("El cierre ordenado no terminó; fuerzo la salida.")
            self.cerrar()
            os._exit(0)

        temporizador = threading.Timer(10.0, _forzar)
        temporizador.daemon = True
        temporizador.start()

    def esperar_salida(self) -> None:
        """Bloquea el hilo principal hasta que se pida salir (interrumpible con Ctrl+C)."""
        while not self._salir.wait(0.5):
            pass

    def cerrar(self) -> None:
        """Limpia recursos (escucha, subtítulos, voz lanzada por Miku y callbacks).

        Es idempotente: solo la primera llamada hace algo.
        """
        if self._cerrado:
            return
        self._cerrado = True
        self._salir.set()
        self._detener_voz()
        # Cancelamos cualquier acción diferida pendiente (no dejamos timers
        # vivos que puedan disparar un apagado/suspensión al cerrar).
        try:
            canceladas = self.scheduler.cancelar_todos(olvidar=False)
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
        if self.instancia is not None:
            try:
                self.instancia.liberar()
            except Exception:  # noqa: BLE001
                logger.debug("No pude liberar la instancia única.")


# ===================================================================== #
#                             Utilidades                                #
# ===================================================================== #
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


def run_modo_texto(asistente: Asistente) -> None:
    """Bucle de texto por consola (respaldo cuando no hay PyQt5 para la ventana de depuración).

    Miku habla igual: sirve también para probar el TTS sin micrófono. Se verifica/arranca
    VOICEVOX antes del primer ``decir()``.
    """
    voz = asistente._preparar_voz()
    try:
        asegurar = getattr(voz, "asegurar_voicevox_inicial", None)
        if callable(asegurar):
            asegurar()
    except Exception:  # noqa: BLE001
        logger.exception("No pude verificar VOICEVOX al entrar al modo texto.")

    print("\n=== Miku lista (modo texto por consola) ===")
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
        except (KeyboardInterrupt, EOFError):
            print("\nChau!")
            break
        except Exception:  # noqa: BLE001
            logger.exception("Error en el bucle de texto.")


def seleccionar_modo_consola(cfg: "config_mod.Config") -> str:
    """Menú por consola (solo cuando no hay ventanita): voz o texto. Enter = el guardado."""
    default = cfg.modo_entrada
    print("\n¿Cómo querés operar esta vez?")
    print("  [1] Voz    (decís 'Miku' o apretás la tecla de invocación)")
    print("  [2] Texto  (consola de depuración)")
    opcion = input(f"Elegí 1 o 2 (Enter = {default}): ").strip()
    return {"1": "voz", "2": "texto"}.get(opcion, default)


def _analizar_argumentos(argv: Optional[List[str]]) -> argparse.Namespace:
    analizador = argparse.ArgumentParser(prog="miku", description="Asistente de voz Miku.")
    analizador.add_argument("--silencioso", action="store_true",
                            help="no muestra la ventanita de modo; usa el modo guardado")
    analizador.add_argument("--invocar", action="store_true",
                            help="al arrancar, saluda y escucha un comando (atajo de teclado)")
    args, _desconocidos = analizador.parse_known_args(argv)
    return args


def _elegir_modo_inicial(asistente: Asistente, args: argparse.Namespace) -> Any:
    """Decide con qué modo arrancar. Devuelve ``(modo, lo_eligio_el_usuario)``."""
    if not args.silencioso:
        try:
            elegido = asistente.bandeja.pedir_modo()
        except Exception:  # noqa: BLE001
            elegido = None
            logger.debug("No pude usar la ventanita para elegir modo.")
        if elegido in MODOS:
            return elegido, True
        if asistente.bandeja._hilo is None:          # sin bandeja/PyQt5: menú por consola
            return seleccionar_modo_consola(asistente.cfg), True
    return asistente.cfg.modo_entrada, False


# ===================================================================== #
#                                main                                   #
# ===================================================================== #
def main(instancia: Any = None, argv: Optional[List[str]] = None) -> None:
    """Función principal del asistente.

    Args:
        instancia: ``InstanciaUnica`` ya adquirida por ``main.py`` (si es None se adquiere acá).
        argv: Argumentos (por defecto ``sys.argv``).
    """
    # Si Miku se abrió desde VS Code (o cualquier app Electron) hereda ELECTRON_RUN_AS_NODE=1, y las
    # apps Electron que ella lance (TIDAL, Discord...) morirían al instante sin abrir ventana.
    os.environ.pop("ELECTRON_RUN_AS_NODE", None)

    args = _analizar_argumentos(argv)

    # 0) Una sola Miku: si ya hay una corriendo, se la invoca y se termina.
    if instancia is None:
        from miku.servicios.instancia import InstanciaUnica
        instancia = InstanciaUnica()
        if not instancia.adquirir():
            instancia.invocar_existente()
            return

    # 1) Configuración + logging.
    cfg = config_mod.cargar()
    configurar_logging(cfg.log_level)
    validar_configuracion(cfg)

    # 2) Asistente ensamblado (no toca audio todavía).
    asistente = Asistente(cfg)
    asistente.instancia = instancia
    try:
        asistente.instalar_core()
    except Exception:  # noqa: BLE001
        logger.exception("Error fatal ensamblando el núcleo.")
        instancia.liberar()
        sys.exit(1)
    instancia.escuchar(asistente.invocar)

    # 3) Bandeja del sistema: el icono es la "cara" de Miku mientras corre en segundo plano.
    try:
        asistente.bandeja.iniciar(on_salir=asistente.pedir_salir,
                                  acciones=asistente.acciones_menu(),
                                  estado=asistente.estado_menu)
    except Exception:  # noqa: BLE001
        logger.debug("No pude iniciar la bandeja (sigo sin ella).", exc_info=True)

    # 4) Modo inicial + tecla de invocación + cambio a ese modo.
    try:
        modo, elegido = _elegir_modo_inicial(asistente, args)
        logger.info("Modo de entrada: %s", modo)
        asistente.actualizar_hotkey()
        if not asistente.cambiar_modo(modo, persistir=elegido):
            # Sin PyQt5 no hay ventana de texto: bucle por consola (respaldo).
            if modo == "texto":
                run_modo_texto(asistente)
                return
            logger.error("No pude arrancar el modo %s.", modo)
        if args.invocar:
            asistente.invocar()
        asistente.esperar_salida()
    except KeyboardInterrupt:
        print("\nChau!")
    finally:
        asistente.cerrar()


if __name__ == "__main__":
    main()
