# -*- coding: utf-8 -*-
"""
telegram_control.py - Control remoto por Telegram (texto del celu → asistente).

Publica un bot que recibe mensajes de Telegram y los pasa al MISMO parser del
asistente en la PC (así "interpolá el último video" o "recordame X" funcionan
remotos). NO prende la PC (fuera de alcance): solo ejecuta comandos mientras el
asistente está corriendo.

Preguntas con botones: si Miku te pregunta algo ("¿Apago la PC?") y no contestás a tiempo, la app la manda
acá con botones Sí / No (``preguntar``); un toque la resuelve en la PC (``_on_boton``).

Ideas guardadas: una foto o captura que le mandes (con un pie opcional) la guarda el plugin ``ideas`` y te
pregunta con botones cuándo recordártela; a la hora elegida el recordatorio llega por acá (``enviar``).

Seguridad: solo responde al ``TELEGRAM_CHAT_ID`` autorizado (en config_local).
Si no hay token/chat, el plugin queda inactivo (no rompe el arranque).

Arquitectura: igual patrón que el bot de Discord — el bot corre en un hilo
daemon con su propio event loop (``python-telegram-bot`` v20+ es async). El
parser se toma del bus de eventos (``bus.parser``), que lo inyecta miku/app.py.

Degradación elegante en todos los niveles (falta lib, falta token, error de
red): nunca tumba el asistente.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from miku.plataforma import red
from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.telegram_control")

_API = "https://api.telegram.org/bot{token}/{metodo}"
#: Una imagen de más de esto no se baja (Telegram deja bajar hasta 20 MB; una captura no pesa tanto).
_MAX_FOTO_BYTES = 15 * 1024 * 1024


class TelegramControl(Plugin):
    """Puente Telegram → parser del asistente (texto remoto)."""

    nombre = "telegram_control"
    descripcion = "Control remoto por Telegram: texto del celu ejecuta comandos."

    # Sin tools: se comunica por el bot, no por el LLM.
    tools: List[dict] = []

    def __init__(self) -> None:
        super().__init__()
        self._hilo: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._app = None
        self._parser = None
        # Preguntas con botones que quedaron en el chat: número -> (chat, id del mensaje, texto del mensaje).
        self._preguntas: Dict[int, Tuple[str, int, str]] = {}

    # ---------------------------------------------------------- #
    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        # El parser lo inyecta miku/app.py como `bus.parser`.
        self._parser = getattr(event_bus, "parser", None)

        from miku.ajustes import carga as config_mod
        token = config_mod.config.telegram_bot_token
        chat_id = config_mod.config.telegram_chat_id
        if not token:
            logger.info("Telegram inactivo: sin TELEGRAM_BOT_TOKEN.")
            return
        if not chat_id:
            logger.warning("Telegram sin TELEGRAM_CHAT_ID: no autorizo a nadie.")

        try:
            import telegram  # noqa: F401  (verificación lazy)
        except Exception as e:  # noqa: BLE001
            logger.warning("python-telegram-bot no disponible (%s): inactivo.", e)
            return

        self._hilo = threading.Thread(target=self._correr_bot, daemon=True,
                                      name="miku_telegram")
        self._hilo.start()
        logger.info("Telegram activo (chat autorizado: %s).", chat_id or "-")

    def cerrar(self) -> None:
        """Detiene el bot y espera a que termine su hilo (lo llama main al cerrar).

        ``run_polling`` es quien controla el loop: se le pide parar con
        ``stop_running()`` (si esta versión de python-telegram-bot lo tiene) en
        vez de frenar el loop a la fuerza, lo que dejaba el ``getUpdates``
        colgado y daba un error 409 al reiniciar.
        """
        loop, app = self._loop, self._app
        if loop is not None and not loop.is_closed():
            try:
                parar = getattr(app, "stop_running", None)
                loop.call_soon_threadsafe(parar if callable(parar) else loop.stop)
            except Exception:  # noqa: BLE001
                logger.debug("No pude pedirle al bot de Telegram que pare.",
                             exc_info=True)
        if self._hilo is not None and self._hilo.is_alive():
            self._hilo.join(timeout=3.0)
        logger.debug("Telegram detenido.")

    # ---------------- Hilo del bot ---------------- #
    def _correr_bot(self) -> None:
        """Arranca el bot en su propio event loop (hilo daemon).

        Corre ``run_polling`` hasta que el loop se detiene (cerrar()).
        """
        try:
            from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                                      CommandHandler, MessageHandler, filters)
        except Exception as e:  # noqa: BLE001
            logger.error("No pude importar telegram.ext: %s", e)
            return

        from miku.ajustes import carga as config_mod
        token = config_mod.config.telegram_bot_token

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            app = ApplicationBuilder().token(token).build()
            self._app = app
            app.add_handler(CommandHandler("start", self._cmd_start))
            app.add_handler(CommandHandler("estado", self._cmd_estado))
            app.add_handler(CommandHandler("pendientes", self._cmd_pendientes))
            app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND, self._on_texto))
            app.add_handler(CallbackQueryHandler(self._on_boton))
            app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self._on_foto))
            logger.info("Telegram: iniciando polling...")
            # ``stop_signals=None``: no instalar handlers de señales (solo se
            # puede desde el hilo principal y acá corremos en un hilo aparte).
            app.run_polling(close_loop=False, stop_signals=None)
        except Exception as e:  # noqa: BLE001
            logger.error("Telegram: el bot se detuvo por error: %s", e)
        finally:
            try:
                if self._loop and not self._loop.is_closed():
                    self._loop.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------------- Autorización ----------------
    def _autorizado(self, update: Any) -> bool:
        """True si el mensaje lo manda el USUARIO autorizado.

        Se compara el id del usuario (no el del chat): en un grupo el id del
        chat es el del grupo y cualquier miembro pasaría el filtro. En el chat
        privado con el bot, id de usuario == id de chat, así que
        ``TELEGRAM_CHAT_ID`` es tu id de usuario.
        """
        from miku.ajustes import carga as config_mod
        permitido = str(config_mod.config.telegram_chat_id or "").strip()
        if not permitido:
            # Sin chat configurado, por seguridad no respondemos a nadie.
            return False
        try:
            return str(update.effective_user.id) == permitido
        except Exception:  # noqa: BLE001
            return False

    # ---------------- Handlers ----------------
    async def _cmd_start(self, update: Any, context: Any) -> None:
        if not self._autorizado(update):
            return
        await update.message.reply_text(
            "¡Hola! Soy Miku (control remoto). Mandame un comando de texto.")

    async def _cmd_estado(self, update: Any, context: Any) -> None:
        if not self._autorizado(update):
            return
        respuesta = await asyncio.to_thread(self._respuesta_local,
                                            "estado de la pc")
        await update.message.reply_text(respuesta)

    async def _cmd_pendientes(self, update: Any, context: Any) -> None:
        if not self._autorizado(update):
            return
        respuesta = await asyncio.to_thread(
            self._respuesta_local, "qué acciones tengo programadas")
        await update.message.reply_text(respuesta)

    async def _on_texto(self, update: Any, context: Any) -> None:
        """Recibe un mensaje de texto y lo pasa al parser."""
        if not self._autorizado(update):
            return
        texto = (update.message.text or "").strip()
        if not texto:
            return
        # El parser es bloqueante (LLM, tools): fuera del event loop para no
        # congelar el polling del bot.
        respuesta = await asyncio.to_thread(self._respuesta_local, texto)
        # Telegram limita a ~4096 chars por mensaje: recortamos por seguridad.
        await update.message.reply_text(respuesta[:4000])

    # ---------------- Preguntas con botones ----------------
    def preguntar(self, numero: int, texto: str) -> bool:
        """Manda ``texto`` al celular con botones Sí / No. True si el mensaje salió.

        ``numero`` es el de la pregunta en el parser (``pregunta_pendiente``): el botón lo lleva consigo para que
        un toque viejo no resuelva una pregunta nueva. Solo escribe al ``TELEGRAM_CHAT_ID`` configurado.
        """
        from miku.ajustes import carga as config_mod
        token, chat = config_mod.config.telegram_bot_token, config_mod.config.telegram_chat_id
        if not token or not chat:
            return False
        mensaje = f"🎙️ Miku pregunta:\n{texto}"
        teclado = {"inline_keyboard": [[{"text": "✅ Sí", "callback_data": f"ok:{numero}"},
                                        {"text": "❌ No", "callback_data": f"no:{numero}"}]]}
        try:
            resp = red.post(_API.format(token=token, metodo="sendMessage"), timeout=10,
                            json={"chat_id": chat, "text": mensaje, "reply_markup": teclado})
            datos = resp.json()
        except Exception as e:  # noqa: BLE001
            # Solo el tipo del error: el texto de una excepción de red trae la URL, y la URL lleva el token.
            logger.warning("No pude mandar la pregunta a Telegram (%s).", type(e).__name__)
            return False
        if resp.status_code != 200 or not datos.get("ok"):
            logger.warning("Telegram no aceptó la pregunta (HTTP %s).", resp.status_code)
            return False
        self._preguntas[numero] = (str(chat), int(datos["result"]["message_id"]), mensaje)
        logger.info("Pregunta mandada al celular: %s", texto)
        return True

    def cerrar_pregunta(self, numero: int, nota: str) -> None:
        """Quita los botones de una pregunta que ya se resolvió en la PC y le deja una nota."""
        datos = self._preguntas.pop(numero, None)
        if datos is None:
            return
        chat, mensaje_id, texto = datos
        from miku.ajustes import carga as config_mod
        token = config_mod.config.telegram_bot_token
        try:
            red.post(_API.format(token=token, metodo="editMessageText"), timeout=10,
                     json={"chat_id": chat, "message_id": mensaje_id, "text": f"{texto}\n\n{nota}"})
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude actualizar la pregunta en Telegram (%s).", type(e).__name__)

    async def _on_boton(self, update: Any, context: Any) -> None:
        """Un toque en Sí / No de una pregunta de Miku: se resuelve en la PC y se avisa el resultado."""
        consulta = update.callback_query
        if consulta is None:
            return
        if not self._autorizado(update):
            await consulta.answer()
            return
        if str(consulta.data or "").startswith("idea:"):
            await self._on_boton_idea(consulta)
            return
        accion, _, numero = str(consulta.data or "").partition(":")
        if accion not in ("ok", "no") or not numero.isdigit():
            await consulta.answer()
            return
        acepta = accion == "ok"
        # Ejecutar la acción puede tardar (es la tool de verdad): fuera del event loop del bot.
        resultado = await asyncio.to_thread(self._resolver_boton, int(numero), acepta)
        self._preguntas.pop(int(numero), None)
        original = (consulta.message.text if consulta.message else "") or ""
        if resultado is None:
            await consulta.answer("Eso ya estaba resuelto.")
            nota = "Ya estaba resuelto en la PC."
        else:
            await consulta.answer("Listo")
            nota = ("✅ Sí: " if acepta else "❌ No: ") + resultado
        try:
            await consulta.edit_message_text(f"{original}\n\n{nota}"[:4000])    # sin reply_markup: quita los botones
        except Exception:  # noqa: BLE001
            logger.debug("No pude actualizar el mensaje de la pregunta.", exc_info=True)

    # ---------------- Ideas guardadas ----------------
    def _plugin(self, nombre: str) -> Any:
        """Otro plugin del bus por su nombre (None si no está cargado)."""
        for p in getattr(self._event_bus, "plugins", []) or []:
            if getattr(p, "nombre", "") == nombre:
                return p
        return None

    @staticmethod
    def _teclado(filas: List[List[Tuple[str, str]]]) -> Any:
        """Filas de ``(texto, dato)`` como teclado de botones de Telegram."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in fila] for fila in filas])

    async def _on_foto(self, update: Any, context: Any) -> None:
        """Una foto o captura: se guarda como idea y se pregunta con botones cuándo recordarla."""
        if not self._autorizado(update):
            return
        mensaje = update.message
        ideas = self._plugin("ideas")
        if ideas is None:
            await mensaje.reply_text("No tengo activado el guardado de ideas.")
            return
        archivo = None
        if getattr(mensaje, "photo", None):
            archivo = mensaje.photo[-1]                          # la de mayor resolución
        elif getattr(mensaje, "document", None) and str(getattr(mensaje.document, "mime_type", "") or "").startswith("image/"):
            archivo = mensaje.document
        if archivo is None:
            return
        if (getattr(archivo, "file_size", 0) or 0) > _MAX_FOTO_BYTES:
            await mensaje.reply_text("Esa imagen es demasiado grande para guardarla (más de 15 MB).")
            return
        try:
            fichero = await context.bot.get_file(archivo.file_id)
            datos = bytes(await fichero.download_as_bytearray())
        except Exception as e:  # noqa: BLE001
            logger.warning("No pude bajar la imagen de Telegram (%s).", type(e).__name__)
            await mensaje.reply_text("No pude bajar esa imagen. Probá de nuevo.")
            return
        await mensaje.reply_text("Mirando la imagen…")           # la visión tarda unos segundos
        idea, texto = await asyncio.to_thread(ideas.guardar_desde_imagen, datos,
                                              (mensaje.caption or "").strip(), "telegram")
        if idea is None:
            await mensaje.reply_text(texto)
            return
        await mensaje.reply_text(texto + "\n\n¿La recordamos en otro momento?",
                                 reply_markup=self._teclado(ideas.botones_de_fecha(idea.id)))

    async def _on_boton_idea(self, consulta: Any) -> None:
        """Un botón de una idea: cambiar cuándo recordarla, marcarla vista o descartarla."""
        partes = str(consulta.data or "").split(":")
        ideas = self._plugin("ideas")
        if len(partes) != 3 or not partes[1].isdigit() or ideas is None:
            await consulta.answer("Eso ya no está disponible.")
            return
        texto = await asyncio.to_thread(ideas.responder_boton, int(partes[1]), partes[2])
        await consulta.answer("Listo")
        mensaje = consulta.message
        try:
            if getattr(mensaje, "photo", None):                  # el recordatorio con imagen: el texto es el pie
                await consulta.edit_message_caption(f"{(mensaje.caption or '')}\n\n{texto}"[:1000])
            else:
                await consulta.edit_message_text(f"{(getattr(mensaje, 'text', '') or '')}\n\n{texto}"[:4000])
        except Exception:  # noqa: BLE001
            logger.debug("No pude actualizar el mensaje de la idea.", exc_info=True)

    def enviar(self, texto: str, foto: Optional[str] = None,
               botones: Optional[List[List[Tuple[str, str]]]] = None) -> bool:
        """Manda un mensaje a tu chat (con una foto y botones si se pide). True si salió.

        Lo usa el recordatorio de las ideas. Solo escribe al ``TELEGRAM_CHAT_ID`` configurado.
        """
        from miku.ajustes import carga as config_mod
        token, chat = config_mod.config.telegram_bot_token, config_mod.config.telegram_chat_id
        if not token or not chat:
            return False
        teclado = ({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in botones]}
                   if botones else None)
        try:
            if foto:
                datos = {"chat_id": chat, "caption": texto[:1000]}
                if teclado:
                    datos["reply_markup"] = json.dumps(teclado)
                with open(foto, "rb") as imagen:
                    resp = red.post(_API.format(token=token, metodo="sendPhoto"), data=datos, timeout=30,
                                    files={"photo": (Path(foto).name, imagen, "image/jpeg")})
            else:
                cuerpo: Dict[str, Any] = {"chat_id": chat, "text": texto[:4000]}
                if teclado:
                    cuerpo["reply_markup"] = teclado
                resp = red.post(_API.format(token=token, metodo="sendMessage"), json=cuerpo, timeout=10)
            return resp.status_code == 200 and bool(resp.json().get("ok"))
        except Exception as e:  # noqa: BLE001
            logger.warning("No pude mandar el mensaje a Telegram (%s).", type(e).__name__)   # sin la URL: lleva el token
            return False

    def _resolver_boton(self, numero: int, acepta: bool) -> Optional[str]:
        """Resuelve la pregunta ``numero`` en el parser (None si ya no está pendiente)."""
        parser = self._parser or getattr(self._event_bus, "parser", None)
        if parser is None:
            return None
        try:
            return parser.resolver_confirmacion(numero, acepta, self._contexto())
        except Exception:  # noqa: BLE001
            logger.exception("Error resolviendo la pregunta desde Telegram.")
            return "Tuve un problema ejecutándolo."

    # ---------------- Puente al parser ----------------
    def _respuesta_local(self, texto: str) -> str:
        """Ejecuta `texto` en el parser local y devuelve la respuesta.

        Es la ejecución LOCAL: la PC hace el trabajo, Telegram solo transporta.
        """
        if self._parser is None:
            # Reintento tardío: por si el parser se inyectó luego del init.
            self._parser = getattr(self._event_bus, "parser", None)
        if self._parser is None:
            return "Todavía no estoy lista en la PC (parser no disponible)."
        try:
            contexto = self._contexto()
            return self._parser.procesar(texto, contexto)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error procesando comando remoto: %s", e)
            return "Uy, tuve un problema procesando eso."

    def _contexto(self) -> Dict[str, Any]:
        """Contexto para el parser en modo remoto.

        Usa el MISMO contexto que los comandos locales (``bus.contexto_base``,
        que trae el scheduler y la voz), para que "recordame en 10 minutos"
        funcione también desde el celular. Si el bus no lo expone, cae a un
        contexto mínimo sin scheduler.
        """
        base = getattr(self._event_bus, "contexto_base", None)
        if callable(base):
            try:
                return base()
            except Exception:  # noqa: BLE001
                logger.debug("contexto_base falló; uso contexto mínimo.",
                             exc_info=True)
        try:
            from miku.ajustes import carga as config_mod
            cfg = config_mod.config
        except Exception:  # noqa: BLE001
            cfg = None
        return {"cfg": cfg, "voice": None, "scheduler": None}