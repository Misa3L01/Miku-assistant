# -*- coding: utf-8 -*-
"""
discord_control.py - Control de Discord vía un bot (discord.py).

Publica tools para moderar/ajustar el audio de usuarios EN CANALES DE VOZ:

    - ``silenciar_usuario_discord``  -> mute de VOZ (server mute) de un usuario.
    - ``volumen_usuario_discord``    -> mute y/o ensordecer (deafen) en voz.
    - ``expulsar_usuario_discord``   -> expulsa (kick) a un usuario del server.

LÍMITE REAL de la API de Discord (importante, no es una limitación nuestra):
    Un BOT **no** puede cambiar el VOLUMEN de reproducción de otro cliente
    (eso es local de cada usuario; no existe endpoint). Lo único que sí puede
    un bot es SILENCIAR/ENSSORDECER a un miembro en un canal de VOZ
    (``member.edit(mute=..., deafen=...)``). Por eso la tool de "volumen" se
    implementa como mute/deafen de voz.

NOTA sobre traducción a canal: la tool ``traducir_a_canal`` se DEJÓ FUERA en
esta tanda (decisión del usuario). El config ``discord_canal_default`` queda
reservado por si se retoma.

Arquitectura (por qué es así):
    ``discord.py`` es asíncrono, pero el resto del asistente es SÍNCRONO y
    corre en su propio hilo. Para no bloquear nada, este plugin arranca el
    cliente de Discord en **un hilo propio con su propio event loop**
    (``asyncio.new_event_loop`` + ``run_forever``). Las tools (que son sync)
    le piden trabajo al loop con ``asyncio.run_coroutine_threadsafe(...)`` y
    esperan el resultado con un timeout acotado.

Degradación elegante:
    - Si falta ``discord.py`` -> el plugin se inicializa pero avisa claro.
    - Si falta ``DISCORD_BOT_TOKEN`` -> inactivo, no rompe el arranque.
    - Si el bot no llega a conectarse -> las tools devuelven un mensaje claro
      (con ``self._ultimo_error``), nunca fallan en silencio.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin
from miku.plataforma.texto import clave_compacta, normalizar

logger = logging.getLogger("miku.plugins.discord_control")

#: Centinela que devuelve `_buscar_en_lista` cuando hay VARIAS coincidencias
#: (no adivinamos a quién se refiere el usuario).
_AMBIGUO = object()

# ``fut.result(timeout=...)`` lanza ``concurrent.futures.TimeoutError``: en
# Python 3.10 NO es ``asyncio.TimeoutError`` (recién son iguales desde 3.11).
_TIMEOUTS = (concurrent.futures.TimeoutError, asyncio.TimeoutError)
_MSG_TIMEOUT = ("Discord tardó en confirmar. La acción pudo haberse aplicado: "
                "fijate en Discord antes de repetirla.")


class DiscordControl(Plugin):
    """Bot de Discord en un hilo propio; expone tools de moderación de voz."""

    nombre = "discord_control"
    descripcion = "Control de Discord (mute/deafen de voz y expulsar)."

    #: Todas afectan a OTRO usuario: exigen confirmación.
    peligrosas = frozenset({"silenciar_usuario_discord",
                            "expulsar_usuario_discord",
                            "volumen_usuario_discord"})

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "silenciar_usuario_discord",
                "description": "Silencia el MICRÓFONO (server mute) de un "
                               "usuario en un canal de VOZ de Discord. "
                               "ACCIÓN IMPORTANTE: pide confirmación. Ej: "
                               "'silenciá a Juan en Discord'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "usuario": {
                            "type": "string",
                            "description": "Nombre (display/username/nick) "
                                           "o ID numérico del usuario.",
                        },
                        "silenciar": {
                            "type": "boolean",
                            "description": "True para silenciar (default), "
                                           "False para quitar el silencio.",
                        },
                    },
                    "required": ["usuario"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "volumen_usuario_discord",
                "description": "Ajusta el audio de un usuario EN VOZ: mute "
                               "y/o ensordecer (deafen). OJO: un bot NO puede "
                               "cambiar el volumen real de otro usuario (no "
                               "existe en la API de Discord); esto hace "
                               "mute/deafen. Ej: 'ensordecé a Juan'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "usuario": {
                            "type": "string",
                            "description": "Nombre o ID del usuario.",
                        },
                        "accion": {
                            "type": "string",
                            "enum": ["silenciar", "desilenciar", "ensordecer",
                                     "desensordecer"],
                            "description": "Qué hacer con el audio de ese "
                                           "usuario en voz.",
                        },
                    },
                    "required": ["usuario", "accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "expulsar_usuario_discord",
                "description": "Expulsa (kick) a un usuario del servidor de "
                               "Discord. ACCIÓN IMPORTANTE E IRREVERSIBLE: "
                               "pide confirmación antes de ejecutar. Ej: "
                               "'expulsá a Pedro del server'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "usuario": {
                            "type": "string",
                            "description": "Nombre o ID del usuario.",
                        },
                        "motivo": {
                            "type": "string",
                            "description": "Opcional: razón de la expulsión.",
                        },
                    },
                    "required": ["usuario"],
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        # Loop e hilo del cliente de Discord (se crean en initialize()).
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._listo = threading.Event()      # set mientras el bot está conectado
        self._ultimo_error: str = ""         # último error de conexión (legible)
        self._activo = False                 # True si el hilo del bot arrancó
        self._cerrando = False               # True mientras se apaga el plugin
        self._token = ""

    # ---------------- Ciclo de vida ----------------
    def initialize(self, event_bus: Any = None) -> None:
        """Arranca el bot en su propio hilo/loop (o queda inactivo si falta
        token/librería). NO bloquea el arranque del asistente."""
        super().initialize(event_bus)

        cfg = self._cargar_cfg()
        self._token = str(getattr(cfg, "discord_bot_token", "") or "").strip()

        # Import tardío: si falta discord.py, avisamos y quedamos inactivos.
        try:
            import discord  # noqa: F401  (solo chequeo de disponibilidad)
        except Exception:  # noqa: BLE001
            logger.warning(
                "discord.py no está instalado; el plugin de Discord queda "
                "inactivo. Instalalo con: pip install discord.py")
            self._ultimo_error = ("Falta la librería discord.py "
                                  "(pip install discord.py).")
            return

        if not self._token:
            logger.info(
                "Discord: no hay DISCORD_BOT_TOKEN configurado; plugin "
                "inactivo (no arranco el bot).")
            self._ultimo_error = ("No configuraste DISCORD_BOT_TOKEN en "
                              "config_local.py.")
            return

        # Creamos el hilo que corre el event loop del bot. NO esperamos a que
        # conecte (tarda segundos): ``_listo`` se levanta en ``on_ready`` y las
        # tools avisan "todavía no está conectado" si se usan antes.
        self._thread = threading.Thread(
            target=self._correr_loop, name="discord-bot", daemon=True)
        self._thread.start()
        self._activo = True
        logger.info("Plugin discord_control: bot arrancando en segundo plano.")

    @staticmethod
    def _cargar_cfg() -> Any:
        """Devuelve la config global del asistente (o None si falla)."""
        try:
            from miku.ajustes import carga as config_mod
            return config_mod.cargar()
        except Exception:  # noqa: BLE001
            return None

    def _correr_loop(self) -> None:
        """Crea el event loop del hilo y arranca el cliente de Discord."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._arrancar_cliente())
        except Exception as e:  # noqa: BLE001
            if self._cerrando:
                # ``cerrar()`` frenó el loop con el cliente todavía conectando.
                logger.debug("Loop de Discord detenido al cerrar (%s).", e)
            else:
                self._ultimo_error = f"Error arrancando el bot: {e}"
                logger.exception("Error arrancando el cliente de Discord.")
        finally:
            if not self._cerrando:
                try:
                    self._loop.run_forever()
                except Exception:  # noqa: BLE001
                    logger.exception("El loop de Discord terminó con error.")

    async def _arrancar_cliente(self) -> None:
        """Construye y lanza el cliente de Discord (async)."""
        import discord

        intents = discord.Intents.default()
        # ``members`` es un intent PRIVILEGIADO (hay que activarlo en el portal)
        # necesario para resolver usuarios por nombre. No pedimos
        # ``message_content``: el bot no lee mensajes.
        intents.members = True
        intents.guilds = True

        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready() -> None:  # noqa: ANN202
            """El bot conectó y ya conoce sus servidores."""
            self._listo.set()
            self._ultimo_error = ""
            logger.info("Discord conectado como %s (en %d servidor(es)).",
                        client.user, len(client.guilds))

        @client.event
        async def on_disconnect() -> None:  # noqa: ANN202
            """Se cayó la conexión: las tools avisan hasta que vuelva."""
            self._listo.clear()
            logger.warning("Discord: conexión perdida (discord.py reintenta).")

        @client.event
        async def on_resumed() -> None:  # noqa: ANN202
            """discord.py restableció la sesión."""
            self._listo.set()
            logger.info("Discord: conexión restablecida.")

        # Intentamos conectar. Si falla (token inválido, sin red), guardamos el
        # motivo para que las tools den un mensaje claro.
        try:
            await client.start(self._token)
        except BaseException as e:  # noqa: BLE001
            if self._cerrando:
                logger.debug("Discord: conexión interrumpida al cerrar (%s).", e)
                return
            self._ultimo_error = self._explicar_error_conexion(e)
            logger.error("Discord: %s", self._ultimo_error)

    @staticmethod
    def _explicar_error_conexion(e: Exception) -> str:
        """Traduce errores de conexión de discord.py a algo legible."""
        nombre = type(e).__name__
        texto = str(e)
        if "LoginFailure" in nombre or "Improper token" in texto:
            return "El token del bot es inválido (revisá DISCORD_BOT_TOKEN)."
        if "PrivilegedIntentsRequired" in nombre or "privileged" in texto.lower():
            return ("Faltan activar los 'Privileged Intents' del bot "
                    "(Server Members + Message Content) en el portal.")
        return f"No pude conectar el bot ({nombre}: {texto})."

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools de Discord."""
        if nombre_tool == "silenciar_usuario_discord":
            silenciar = bool(args.get("silenciar", True))
            return self.silenciar_usuario(
                str(args.get("usuario", "") or ""), silenciar)
        if nombre_tool == "volumen_usuario_discord":
            return self.volumen_usuario(
                str(args.get("usuario", "") or ""),
                str(args.get("accion", "") or ""))
        if nombre_tool == "expulsar_usuario_discord":
            return self.expulsar_usuario(
                str(args.get("usuario", "") or ""),
                str(args.get("motivo", "") or ""))
        return None

    # ---------------- Puente sync -> async ----------------
    def _disponible(self) -> Optional[str]:
        """Devuelve None si el bot está listo, o un mensaje de error claro."""
        if self._client is None or self._loop is None:
            if self._ultimo_error:
                return f"El bot de Discord no está activo: {self._ultimo_error}"
            return ("El bot de Discord no está activo (revisá "
                    "DISCORD_BOT_TOKEN y la conexión).")
        if not self._activo or not self._listo.is_set():
            if self._ultimo_error:
                return f"El bot de Discord no está conectado: {self._ultimo_error}"
            return "El bot de Discord todavía no está conectado. Probá en unos segundos."
        return None

    def _ejecutar_coro(self, coro: Any, timeout: float = 15.0) -> Any:
        """Corre una coroutine en el loop del bot y espera el resultado.

        Usa ``run_coroutine_threadsafe`` (puente thread-safe sync->async).
        Devuelve el resultado o lanza un error de timeout (ver ``_TIMEOUTS``)
        que el caller maneja. La coroutine NO se cancela al vencer: un kick ya
        enviado puede haberse aplicado igual.
        """
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    # ---------------- Resolución de guild / miembro ----------------
    def _guild_por_defecto(self) -> Any:
        """Guild objetivo: el de config (discord_guild_id) o el primero."""
        client = self._client
        if client is None:
            return None
        guild_id = str(getattr(self._cargar_cfg(), "discord_guild_id", "")
                       or "").strip()
        if guild_id:
            try:
                g = client.get_guild(int(guild_id))
                if g is not None:
                    return g
            except (TypeError, ValueError):
                logger.warning("discord_guild_id '%s' no es un número válido.",
                               guild_id)
        # Fallback: el primer guild en el que está el bot.
        guilds = list(getattr(client, "guilds", []) or [])
        return guilds[0] if guilds else None

    @staticmethod
    def _coincide_miembro(miembro: Any, objetivo: str) -> bool:
        """True si `miembro` matchea `objetivo` (nombre/nick/username/id)."""
        objetivo_n = normalizar(objetivo)
        objetivo_c = clave_compacta(objetivo)
        if not objetivo_n:
            return False

        # Por ID numérico directo.
        if objetivo.isdigit() and int(objetivo) == getattr(miembro, "id", -1):
            return True

        candidatos = [
            getattr(miembro, "display_name", "") or "",
            getattr(miembro, "global_name", "") or "",
            getattr(miembro, "name", "") or "",   # username
            getattr(miembro, "nick", "") or "",
        ]
        for cand in candidatos:
            cn = normalizar(cand)
            cc = clave_compacta(cand)
            if not cn:
                continue
            if cn == objetivo_n or cc == objetivo_c:
                return True
        # Substring (tolerante a "juan" -> "Juan Pérez"). Solo sobre
        # candidatos NO vacíos y con la clave compacta, para evitar que un
        # nombre vacío matchee siempre ("" in "x" == True).
        for cand in candidatos:
            cn = normalizar(cand)
            cc = clave_compacta(cand)
            if not cn or not cc:
                continue
            if objetivo_n in cn or objetivo_c in cc:
                return True
        return False

    async def _resolver_miembro(self, usuario: str) -> Any:
        """Encuentra el miembro del guild (o None). Coroutine.

        Hace el ``fetch_members`` (potencialmente costoso) en el loop, y la
        parte CPU (normalización/comparación) vía ``asyncio.to_thread`` para
        no bloquear el loop de Discord.
        """
        guild = self._guild_por_defecto()
        if guild is None:
            return None
        objetivo = (usuario or "").strip()
        if not objetivo:
            return None

        # Discord puede no tener todos los miembros en cache: forzamos fetch
        # de la lista si está vacía (requiere el intent Members).
        miembros = list(guild.members)
        if not miembros:
            miembros = [m async for m in guild.fetch_members(limit=None)]

        # La comparación (CPU) va en un hilo aparte.
        return await asyncio.to_thread(self._buscar_en_lista, miembros, objetivo)

    @classmethod
    def _buscar_en_lista(cls, miembros: List[Any], objetivo: str) -> Any:
        """Busca (sync) el miembro que coincide con `objetivo`.

        Devuelve:
            - el miembro si hay UNA coincidencia EXACTA (nombre/id), o
            - el miembro si hay UNA sola coincidencia por substring, o
            - ``_AMBIGUO`` si hay VARIAS coincidencias (no adivinamos quién),
            - None si no hay ninguna.

        Nota: si hay una coincidencia EXACTA y además otras por substring,
        gana la exacta (desambigua sola).
        """
        objetivo_n = normalizar(objetivo)
        objetivo_c = clave_compacta(objetivo)

        # 1) Exactas (nombre completo / username / id).
        exactos = [m for m in miembros if cls._es_coincidencia_exacta(
            m, objetivo, objetivo_n, objetivo_c)]
        if len(exactos) == 1:
            return exactos[0]
        if len(exactos) > 1:
            return _AMBIGUO

        # 2) Por substring.
        parciales = [m for m in miembros if cls._coincide_miembro(m, objetivo)]
        if len(parciales) == 1:
            return parciales[0]
        if len(parciales) > 1:
            return _AMBIGUO
        return None

    @classmethod
    def _es_coincidencia_exacta(cls, miembro: Any, objetivo: str,
                                objetivo_n: str, objetivo_c: str) -> bool:
        """True si el miembro coincide EXACTAMENTE (nombre/username/id)."""
        if objetivo.isdigit() and int(objetivo) == getattr(miembro, "id", -1):
            return True
        for attr in ("display_name", "global_name", "name", "nick"):
            cand = getattr(miembro, attr, "") or ""
            if not cand:
                continue
            if normalizar(cand) == objetivo_n or \
                    clave_compacta(cand) == objetivo_c:
                return True
        return False

    # ---------------- Acciones (coroutines) ----------------
    async def _coro_silenciar(self, usuario: str, silenciar: bool) -> str:
        """Server-mute (mute de voz) de un usuario."""
        guild = self._guild_por_defecto()
        if guild is None:
            return "El bot no está en ningún servidor."

        miembro = await self._resolver_miembro(usuario)
        if miembro is _AMBIGUO:
            return self._texto_ambiguo(usuario)
        if miembro is None:
            return self._texto_no_encontrado(usuario)

        try:
            # cambiar el mute de VOZ no requiere que el miembro esté en un
            # canal de voz (aunque en la práctica solo tiene efecto si lo está).
            await miembro.edit(mute=silenciar,
                               reason="Pedido por Miku (asistente)")
        except Exception as e:  # noqa: BLE001
            return self._texto_error_accion("silenciar", usuario, e)

        accion = ("Silencié a" if silenciar else "Le quité el silencio a")
        return f"{accion} {miembro.display_name} en Discord."

    async def _coro_deafen(self, usuario: str, accion: str) -> str:
        """Mute y/o ensordecer (deafen) de un usuario en voz."""
        guild = self._guild_por_defecto()
        if guild is None:
            return "El bot no está en ningún servidor."

        miembro = await self._resolver_miembro(usuario)
        if miembro is _AMBIGUO:
            return self._texto_ambiguo(usuario)
        if miembro is None:
            return self._texto_no_encontrado(usuario)

        kwargs: Dict[str, Any] = {}
        if accion in ("silenciar", "desilenciar"):
            kwargs["mute"] = (accion == "silenciar")
        elif accion in ("ensordecer", "desensordecer"):
            kwargs["deafen"] = (accion == "ensordecer")
        else:
            return ("Acción de audio no reconocida. Usá silenciar, "
                    "desilenciar, ensordecer o desensordecer.")
        try:
            await miembro.edit(reason="Pedido por Miku (asistente)", **kwargs)
        except Exception as e:  # noqa: BLE001
            return self._texto_error_accion(accion, usuario, e)

        verbos = {
            "silenciar": "Silencié", "desilenciar": "Reactivé el micro de",
            "ensordecer": "Ensordecí", "desensordecer": "Reactivé el audio de",
        }
        return f"{verbos[accion]} {miembro.display_name} en voz."

    async def _coro_expulsar(self, usuario: str, motivo: str) -> str:
        """Expulsa (kick) a un usuario del servidor."""
        guild = self._guild_por_defecto()
        if guild is None:
            return "El bot no está en ningún servidor."

        miembro = await self._resolver_miembro(usuario)
        if miembro is _AMBIGUO:
            return self._texto_ambiguo(usuario)
        if miembro is None:
            return self._texto_no_encontrado(usuario)

        razon = motivo or "Pedido por Miku (asistente)"
        try:
            await miembro.kick(reason=razon)
        except Exception as e:  # noqa: BLE001
            return self._texto_error_accion("expulsar", usuario, e)
        return f"Expulsé a {miembro.display_name} del servidor."

    # ---------------- Mensajes de error comunes ----------------
    @staticmethod
    def _texto_no_encontrado(usuario: str) -> str:
        return (f"No encontré a ningún usuario que se llame '{usuario}' en el "
                f"servidor. Fijate que el bot tenga activado el intent de "
                f"miembros (Server Members Intent).")

    @staticmethod
    def _texto_ambiguo(usuario: str) -> str:
        return (f"Hay varios usuarios que coinciden con '{usuario}'. "
                f"Dame el nombre completo, el usuario exacto o su ID.")

    @staticmethod
    def _texto_error_accion(accion: str, usuario: str, e: Exception) -> str:
        nombre = type(e).__name__
        texto = str(e).lower()
        if "forbidden" in nombre.lower() or "403" in texto:
            return (f"No tengo permisos para {accion} a '{usuario}'. "
                    f"Revisá que mi rol esté por ENCIMA del suyo y tenga los "
                    f"permisos (Kick Members / Mute Members).")
        if "notfound" in nombre.lower():
            return f"No encontré a '{usuario}' en el servidor."
        logger.error("Error en acción '%s' sobre '%s': %s", accion, usuario, e)
        return f"No pude {accion} a '{usuario}' ({nombre})."

    # ---------------- API pública (llamada por las tools) ----------------
    def silenciar_usuario(self, usuario: str, silenciar: bool = True) -> str:
        """Silencia (o no) el micro de un usuario en voz (server mute)."""
        problema = self._disponible()
        if problema:
            return problema
        try:
            return self._ejecutar_coro(self._coro_silenciar(usuario, silenciar))
        except _TIMEOUTS:
            return _MSG_TIMEOUT
        except Exception as e:  # noqa: BLE001
            logger.exception("Error silenciando a '%s'.", usuario)
            return f"No pude silenciar a '{usuario}' ({type(e).__name__})."

    def volumen_usuario(self, usuario: str, accion: str) -> str:
        """Ajusta audio de un usuario EN VOZ (mute/deafen).

        Nota: el VOLUMEN real no es posible vía bot; esto hace mute/deafen.
        """
        problema = self._disponible()
        if problema:
            return problema
        accion = (accion or "").strip().lower()
        try:
            return self._ejecutar_coro(self._coro_deafen(usuario, accion))
        except _TIMEOUTS:
            return _MSG_TIMEOUT
        except Exception as e:  # noqa: BLE001
            logger.exception("Error ajustando audio de '%s'.", usuario)
            return f"No pude ajustar el audio de '{usuario}' ({type(e).__name__})."

    def expulsar_usuario(self, usuario: str, motivo: str = "") -> str:
        """Expulsa (kick) a un usuario del servidor."""
        problema = self._disponible()
        if problema:
            return problema
        try:
            return self._ejecutar_coro(self._coro_expulsar(usuario, motivo))
        except _TIMEOUTS:
            return _MSG_TIMEOUT
        except Exception as e:  # noqa: BLE001
            logger.exception("Error expulsando a '%s'.", usuario)
            return f"No pude expulsar a '{usuario}' ({type(e).__name__})."

    # ---------------- Cierre ----------------
    def cerrar(self) -> None:
        """Cierra el cliente y detiene el loop/hilo (best-effort)."""
        self._cerrando = True
        if self._loop is None:
            return
        try:
            if self._client is not None and not self._client.is_closed():
                cerrar = asyncio.run_coroutine_threadsafe(
                    self._client.close(), self._loop)
                try:
                    cerrar.result(timeout=5.0)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo cerrar el cliente de Discord limpiamente.")
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass
        self._activo = False
