"""
ideas.py - Plugin de ideas guardadas: una captura o foto para revisar más tarde, con recordatorio.

Dos formas de pasarle algo:
    * **Desde el celular:** le mandás la foto o la captura por Telegram (con un pie opcional: "buenísima idea,
      recordámela el sábado"). Miku contesta con el título que le puso y botones para elegir cuándo recordártela.
    * **Desde la PC:** sacás una captura (``Win+Shift+S`` o ``Win+ImprPant``) y le decís "guardá la última captura
      para después" (o "guardá lo que estoy viendo").

La visión (Gemini o Groq) mira la imagen UNA vez para ponerle título y resumen. Después queda en tu PC y a la hora
elegida Miku te la recuerda por voz y por Telegram (con la imagen y botones para marcarla vista o moverla).

Tools: ``guardar_idea``, ``listar_ideas``, ``recordar_idea`` y ``cerrar_idea``. Lo que se guarda y dónde está en
``miku/servicios/ideas.py``.
"""
from __future__ import annotations

import base64
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from miku.ajustes import carga as config_mod
from miku.plataforma import portapapeles
from miku.plugins.base import Plugin
from miku.servicios import ideas
from miku.servicios.ideas import AlmacenIdeas, Idea
from miku.servicios.proactivo import en_horario_de_silencio
from miku.servicios.uso import duracion_en_texto
from miku.voz.frases import catalogo_ideas  # noqa: F401  (registra las frases al importarse)
from miku.voz.frases.banco import frases

logger = logging.getLogger("miku.plugins.ideas")

RUTA_DB = config_mod.BASE_DIR / "data" / "ideas.db"
CARPETA_IMAGENES = config_mod.BASE_DIR / "data" / "ideas"
#: Cada cuántos segundos se revisa si llegó la hora de algún recordatorio.
INTERVALO_SEG = 30.0

#: Botones de "¿cuándo te la recuerdo?": (texto, acción). La acción viaja en el botón: ``idea:<id>:<acción>``.
BOTONES_FECHA: Tuple[Tuple[str, str], ...] = (
    ("🌅 Mañana", "man"), ("📅 Este finde", "finde"), ("🗓 En una semana", "sem"), ("🔕 Sin recordatorio", "no"))
#: Botones del recordatorio.
BOTONES_RECORDATORIO: Tuple[Tuple[str, str], ...] = (
    ("✅ Ya la vi", "vista"), ("⏰ Mañana", "man"), ("📅 Este finde", "finde"), ("🗑 Descartar", "desc"))
#: Lo que dice cada acción de botón al interpretarla con ``parsear_cuando`` (None = no es una fecha).
_FECHA_DE_ACCION = {"man": "mañana", "finde": "este finde", "sem": "en una semana", "no": "sin recordatorio"}


def _lista_de_botones(identificador: int, botones: Tuple[Tuple[str, str], ...], por_fila: int = 2
                      ) -> List[List[Tuple[str, str]]]:
    filas: List[List[Tuple[str, str]]] = []
    for i in range(0, len(botones), por_fila):
        filas.append([(texto, f"idea:{identificador}:{accion}") for texto, accion in botones[i:i + por_fila]])
    return filas


class Ideas(Plugin):
    """Guarda capturas y fotos para revisar más tarde y te las recuerda."""

    nombre = "ideas"
    descripcion = "Ideas guardadas: captura o foto para revisar más tarde, con recordatorio."

    tools: List[dict] = [
        {"type": "function", "function": {
            "name": "guardar_idea",
            "description": "Guarda una captura de pantalla (o lo que se ve ahora) como idea guardada para revisarla "
                           "más tarde y recordarla. Ej: 'guardá la última captura para después', 'guardá esta "
                           "captura y recordámela el sábado', 'guardá lo que estoy viendo para después', 'anotá "
                           "esta idea'.",
            "parameters": {"type": "object", "properties": {
                "cuando": {"type": "string",
                           "description": "Cuándo recordarla, tal como lo dijo: 'mañana', 'el sábado', 'en una "
                                          "semana'. Vacío si no dijo (se usa mañana)."},
                "nota": {"type": "string", "description": "Una nota del usuario sobre la idea (opcional)."},
                "fuente": {"type": "string", "enum": ["ultima_captura", "pantalla"],
                           "description": "'ultima_captura' (por defecto) o 'pantalla' para guardar lo que se ve ahora."}}}}},
        {"type": "function", "function": {
            "name": "listar_ideas",
            "description": "Dice qué ideas guardadas hay pendientes de revisar. Ej: 'qué ideas tengo guardadas', "
                           "'mis ideas pendientes', 'qué tengo para revisar'.",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "recordar_idea",
            "description": "Cambia cuándo se recuerda una idea guardada. Ej: 'recordámela el sábado', 'recordame "
                           "la idea 3 en una semana', 'no me recuerdes más esa idea'.",
            "parameters": {"type": "object", "properties": {
                "cuando": {"type": "string", "description": "'mañana', 'el sábado', 'en una semana', 'sin recordatorio'."},
                "numero": {"type": "integer", "description": "Número de la idea (por defecto, la última guardada)."}},
                "required": ["cuando"]}}},
        {"type": "function", "function": {
            "name": "cerrar_idea",
            "description": "Marca una idea guardada como vista o la descarta. Ej: 'ya vi la idea', 'descartá la "
                           "idea 2', 'sacá esa idea de la lista'.",
            "parameters": {"type": "object", "properties": {
                "accion": {"type": "string", "enum": ["vista", "descartar"]},
                "numero": {"type": "integer", "description": "Número de la idea (por defecto, la última guardada)."}},
                "required": ["accion"]}}},
    ]

    def __init__(self) -> None:
        super().__init__()
        self._almacen: Optional[AlmacenIdeas] = None
        self._reloj: Callable[[], datetime] = datetime.now
        self._detener = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    # ------------------------------------------------------------ ciclo de vida
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._almacen = AlmacenIdeas(RUTA_DB, CARPETA_IMAGENES, ahora=lambda: self._reloj())
        self._detener.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="miku_ideas")
        self._hilo.start()
        logger.info("Ideas guardadas listas (%d pendiente(s)).", len(self._almacen.pendientes()))

    def cerrar(self) -> None:
        self._detener.set()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=3.0)
        self._hilo = None
        if self._almacen is not None:
            self._almacen.cerrar()
            self._almacen = None

    def _bucle(self) -> None:
        while not self._detener.is_set():
            try:
                self.revisar_recordatorios()
            except Exception:  # noqa: BLE001
                logger.exception("Error revisando los recordatorios de ideas; sigo.")
            self._detener.wait(INTERVALO_SEG)

    # ------------------------------------------------------------ utilidades
    def _plugin(self, nombre: str) -> Any:
        for p in getattr(self._event_bus, "plugins", []) or []:
            if getattr(p, "nombre", "") == nombre:
                return p
        return None

    def _cuando(self, texto: str) -> Tuple[bool, Optional[datetime]]:
        """``parsear_cuando`` con las horas de tu config (``IDEAS_HORA_AVISO`` / ``IDEAS_HORA_FINDE``)."""
        cfg = config_mod.config
        return ideas.parsear_cuando(texto, self._reloj(), str(cfg.get("ideas_hora_aviso", ideas.HORA_AVISO)),
                                    str(cfg.get("ideas_hora_finde", ideas.HORA_FINDE)))

    @staticmethod
    def _carpetas_de_capturas() -> List[Path]:
        """Dónde buscar "la última captura": la carpeta de Miku y las de Windows (Win+ImprPant)."""
        casa = Path.home()
        candidatas = [Path(config_mod.config.carpeta_capturas), casa / "Pictures" / "Screenshots",
                      casa / "OneDrive" / "Pictures" / "Screenshots"]
        return [c for c in candidatas if c.is_dir()]

    def _describir(self, jpeg: bytes, nota: str) -> Tuple[str, str]:
        """``(título, resumen)`` que la visión le pone a la imagen; ``("", "")`` si no se pudo."""
        vision = self._plugin("vision")
        if vision is None or not hasattr(vision, "analizar_imagen"):
            return "", ""
        pedido = ideas.PROMPT_IDEA.format(nota=f"El usuario agregó esta nota: «{nota[:200]}». " if nota else "")
        try:
            texto, problema = vision.analizar_imagen(base64.b64encode(jpeg).decode("ascii"), pedido)
        except Exception:  # noqa: BLE001
            logger.exception("La visión falló al describir la idea.")
            return "", ""
        if not texto:
            logger.info("No pude describir la idea: %s", problema)
            return "", ""
        return ideas.separar_titulo_resumen(texto)

    def _ahora_texto(self, cuando: Optional[datetime]) -> str:
        return ideas.cuando_en_texto(cuando, self._reloj())

    # ------------------------------------------------------------ guardar
    def guardar_desde_imagen(self, datos: bytes, nota: str = "", origen: str = "",
                             cuando: str = "") -> Tuple[Optional[Idea], str]:
        """Guarda una imagen como idea. Devuelve ``(idea, qué decir)`` (idea None si no era una imagen).

        ``cuando`` es lo que dijo el usuario ("el sábado"); si no hay, se busca en la ``nota`` (el pie de la foto) y,
        si tampoco, se usa mañana.
        """
        if self._almacen is None:
            return None, "Todavía no tengo lista la libreta de ideas."
        jpeg = ideas.comprimir_imagen(datos)
        if jpeg is None:
            return None, "Eso no parece una imagen que pueda leer."
        reconocido, recordar_en = self._cuando(cuando) if cuando.strip() else (False, None)
        if not reconocido:
            reconocido, recordar_en = self._cuando(nota)
        if not reconocido:
            _, recordar_en = self._cuando("mañana")
        titulo, resumen = self._describir(jpeg, nota)
        sin_vision = not titulo
        if sin_vision:
            titulo = (nota.strip()[:60] or "Idea sin título")
        idea = self._almacen.guardar(titulo, resumen, nota, jpeg, recordar_en, origen)
        if recordar_en is None:
            texto = frases.elegir("idea.guardada_sin_aviso", titulo=titulo)
        else:
            texto = frases.elegir("idea.guardada", titulo=titulo, cuando=self._ahora_texto(recordar_en))
        if sin_vision:
            texto += " " + frases.elegir("idea.sin_vision")
        return idea, texto

    def botones_de_fecha(self, identificador: int) -> List[List[Tuple[str, str]]]:
        """Los botones de "¿cuándo te la recuerdo?" para una idea recién guardada."""
        return _lista_de_botones(identificador, BOTONES_FECHA)

    # ------------------------------------------------------------ botones del celular
    def responder_boton(self, identificador: int, accion: str) -> str:
        """Lo que pasa al tocar un botón de una idea (cambiar el recordatorio, marcarla vista o descartarla)."""
        if self._almacen is None:
            return "Todavía no tengo lista la libreta de ideas."
        idea = self._almacen.obtener(identificador)
        if idea is None:
            return frases.elegir("idea.no_existe")
        titulo = idea.titulo or "esa idea"
        if accion == "vista":
            self._almacen.cambiar_estado(identificador, "hecha")
            return frases.elegir("idea.hecha", titulo=titulo)
        if accion == "desc":
            self._almacen.cambiar_estado(identificador, "descartada")
            return frases.elegir("idea.descartada", titulo=titulo)
        if accion in _FECHA_DE_ACCION:
            _, cuando = self._cuando(_FECHA_DE_ACCION[accion])
            self._almacen.recordar(identificador, cuando)
            if cuando is None:
                return frases.elegir("idea.sin_aviso", titulo=titulo)
            return frases.elegir("idea.recordar", titulo=titulo, cuando=self._ahora_texto(cuando))
        return "No entendí ese botón."

    # ------------------------------------------------------------ recordatorios
    def revisar_recordatorios(self, ahora: Optional[datetime] = None) -> int:
        """Avisa de las ideas cuya hora llegó. Devuelve cuántas avisó.

        De noche (el horario de silencio de los avisos) espera: no te habla ni te manda nada hasta que termine.
        """
        if self._almacen is None:
            return 0
        ahora = ahora or self._reloj()
        cfg = config_mod.config
        if en_horario_de_silencio(ahora, cfg.get("proactivo_silencio_desde"), cfg.get("proactivo_silencio_hasta")):
            return 0
        avisadas = 0
        for idea in self._almacen.vencidas(ahora):
            try:
                self._avisar(idea)
            finally:
                self._almacen.marcar_avisada(idea.id)       # aunque falle el aviso: no se repite cada 30 s
            avisadas += 1
        return avisadas

    def _avisar(self, idea: Idea) -> None:
        """Voz, notificación y Telegram (con la imagen y botones)."""
        titulo = idea.titulo or "una idea"
        frase = frases.elegir("idea.recordatorio", titulo=titulo)
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar("Idea guardada", frase)
        except Exception as e:  # noqa: BLE001
            logger.debug("Ideas: sin toast: %s", e)
        try:
            voz = getattr(self._event_bus, "voice", None)
            if voz is not None:
                voz.decir(frase)
        except Exception:  # noqa: BLE001
            logger.debug("Ideas: no pude decir el recordatorio.", exc_info=True)
        telegram = self._plugin("telegram_control")
        if telegram is not None and hasattr(telegram, "enviar"):
            texto = f"💡 {titulo}" + (f"\n{idea.resumen}" if idea.resumen else "")
            if idea.nota:
                texto += f"\n📝 {idea.nota[:300]}"
            foto = idea.imagen if idea.imagen and Path(idea.imagen).is_file() else None
            try:
                telegram.enviar(texto, foto, _lista_de_botones(idea.id, BOTONES_RECORDATORIO))
            except Exception:  # noqa: BLE001
                logger.exception("No pude mandar el recordatorio de la idea %d a Telegram.", idea.id)
        logger.info("Ideas: te recordé la idea %d.", idea.id)

    # ------------------------------------------------------------ tools
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        args = args or {}
        if nombre_tool == "guardar_idea":
            return self._tool_guardar(args)
        if nombre_tool == "listar_ideas":
            return self._tool_listar()
        if nombre_tool == "recordar_idea":
            return self._tool_recordar(args)
        if nombre_tool == "cerrar_idea":
            return self._tool_cerrar(args)
        return None

    def _tool_guardar(self, args: Dict[str, Any]) -> str:
        fuente = str(args.get("fuente") or "ultima_captura")
        if fuente == "pantalla":
            captura = self._capturar_pantalla()
        else:
            captura = ideas.buscar_ultima_captura(self._carpetas_de_capturas(), portapapeles.leer_imagen)
        if captura is None:
            return frases.elegir("idea.sin_captura")
        _, texto = self.guardar_desde_imagen(captura.datos, str(args.get("nota") or ""),
                                             "pantalla" if fuente == "pantalla" else "captura",
                                             str(args.get("cuando") or ""))
        if captura.minutos is not None and captura.minutos > ideas.FRESCURA_MIN:
            texto += f" (Era {captura.origen}, de hace {duracion_en_texto(captura.minutos * 60)}.)"
        return texto

    @staticmethod
    def _capturar_pantalla() -> Optional[ideas.Captura]:
        try:
            from PIL import ImageGrab
            return ideas.Captura(ideas.jpeg_de(ImageGrab.grab(all_screens=True)), "la pantalla", None)
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude capturar la pantalla: %s", e)
            return None

    def _tool_listar(self) -> str:
        if self._almacen is None:
            return "Todavía no tengo lista la libreta de ideas."
        pendientes = self._almacen.pendientes()
        if not pendientes:
            return frases.elegir("idea.no_hay")
        partes = []
        for idea in pendientes[-5:]:
            aviso = f", te la recuerdo {self._ahora_texto(idea.recordar_en)}" if (
                idea.recordar_en and not idea.avisada) else ""
            partes.append(f"{idea.id}: {idea.titulo or 'sin título'}{aviso}")
        total = len(pendientes)
        cabeza = f"Tenés {total} idea" + ("" if total == 1 else "s") + " pendiente" + ("" if total == 1 else "s")
        return cabeza + (" (las últimas 5)" if total > 5 else "") + ": " + "; ".join(partes) + "."

    def _idea_pedida(self, args: Dict[str, Any]) -> Optional[Idea]:
        if self._almacen is None:
            return None
        try:
            numero = int(args.get("numero")) if args.get("numero") not in (None, "") else None
        except (TypeError, ValueError):
            numero = None
        return self._almacen.obtener(numero) if numero is not None else self._almacen.ultima()

    def _tool_recordar(self, args: Dict[str, Any]) -> str:
        idea = self._idea_pedida(args)
        if idea is None or self._almacen is None:
            return frases.elegir("idea.no_existe")
        reconocido, cuando = self._cuando(str(args.get("cuando") or ""))
        if not reconocido:
            return frases.elegir("idea.no_entendi_cuando")
        self._almacen.recordar(idea.id, cuando)
        if cuando is None:
            return frases.elegir("idea.sin_aviso", titulo=idea.titulo or "esa idea")
        return frases.elegir("idea.recordar", titulo=idea.titulo or "esa idea", cuando=self._ahora_texto(cuando))

    def _tool_cerrar(self, args: Dict[str, Any]) -> str:
        idea = self._idea_pedida(args)
        if idea is None:
            return frases.elegir("idea.no_existe")
        return self.responder_boton(idea.id, "desc" if str(args.get("accion")) == "descartar" else "vista")
