# -*- coding: utf-8 -*-
"""
telegram_control.py - Control remoto por Telegram (texto del celu → asistente).

Publica un bot que recibe mensajes de Telegram y los pasa al MISMO parser del
asistente en la PC (así "interpolá el último video" o "recordame X" funcionan
remotos). NO prende la PC (fuera de alcance): solo ejecuta comandos mientras el
asistente está corriendo.

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
import logging
import threading
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin

logger = logging.getLogger("miku.plugins.telegram_control")


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
            from telegram.ext import (ApplicationBuilder, CommandHandler,
                                      MessageHandler, filters)
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