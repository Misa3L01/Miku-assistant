"""
command_parser.py - Parser de comandos (el "cerebro" del asistente).

Convierte el texto del usuario en una acción. Para no copiar el código viejo
(aunque mucho es referencia) este módulo implementa un flujo claro:

  1. Comandos inmediatos (fast path) resueltos localmente sin LLM.
  2. Despacho por match de palabras clave a plugins (vía event_bus).
  3. Si ninguno respondió, se consulta al LLM (Groq) con herramientas
     (tools) declaradas por los plugins, se ejecutan y se arma la respuesta.

De mantenerlo modular, toda la "inteligencia" se descarga a un cliente de
LLM intercambiable (ver clase `BrainGroq`), así se pueden probar respuestas
sin tocar el resto.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

import requests

from miku.ajustes import carga as config_mod
from miku.plataforma.texto import sin_acentos

logger = logging.getLogger("miku.parser")

# Cache LRU trivial para respuestas frecuentes (optimización). Se accede desde
# varios hilos (STT, push-to-talk, Telegram), así que va protegida con un lock.
_CACHE = {}
_CACHE_ORDEN: List[str] = []
_CACHE_MAX = 64
_CACHE_LOCK = threading.Lock()


def cache_respuesta(clave: str, respuesta: str) -> None:
    """Guarda una respuesta frecuente en la cache con límite simple."""
    with _CACHE_LOCK:
        if clave in _CACHE:
            _CACHE_ORDEN.remove(clave)
        _CACHE[clave] = respuesta
        _CACHE_ORDEN.append(clave)
        if len(_CACHE) > _CACHE_MAX:
            vieja = _CACHE_ORDEN.pop(0)
            _CACHE.pop(vieja, None)


def obtener_cache(clave: str) -> Optional[str]:
    """Devuelve la respuesta cacheada si existe."""
    with _CACHE_LOCK:
        return _CACHE.get(clave)


def _normalizar_frase(texto: str) -> str:
    """Normaliza una frase para comparar: sin acentos ni signos de puntuación.

    Whisper devuelve "¿Qué hora es?" con acentos y signos; esto lo deja como
    "que hora es" para poder comparar contra las frases del fast-path.
    """
    limpio = re.sub(r"[¿?¡!.,;:\"']", " ", sin_acentos(texto))
    return re.sub(r"\s+", " ", limpio).strip()


# ---------------- Brain (cliente de LLM) ---------------- #

class BrainGroq:
    """Cliente mínimo de chat con tools usando la API de Groq.

    El objetivo es separar el transporte (HTTP) de la lógica del asistente,
    para poder reemplazar Groq por otro proveedor sin tocar el parser.
    """

    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, cfg: "config_mod.Config", system_prompt: str = "") -> None:
        self.cfg = cfg
        self.system_prompt = system_prompt or self._prompt_default()
        self._ultima_llamada: float = 0.0
        self._lock = threading.Lock()

    def _prompt_default(self) -> str:
        """Prompt de sistema base de Miku."""
        return (
            "Sos Hatsune Miku, una asistente virtual alegre, entusiasta y un poco "
            "juguetona. Respondé siempre en español, de forma natural y concisa "
            "(máximo 2-3 oraciones salvo que haga falta más detalle).\n\n"
            "REGLAS IMPORTANTES:\n"
            "- Usá herramientas SOLO cuando el usuario pide una acción real "
            "(abrir, cerrar, minimizar, buscar, reproducir, mover ventana, "
            "organizar ventanas, etc.).\n"
            "- Para partir la pantalla entre DOS ventanas (una a cada lado, "
            "estilo Snap de Windows 11) o para mandar UNA sola ventana a una "
            "mitad (izquierda/derecha), usá organizar_ventanas "
            "(ventana_izquierda + ventana_derecha, o ventana_izquierda + "
            "posicion). Para mover una ventana a OTRO monitor, usá "
            "mover_ventana. NO existe una tool de 'dividir'/'acoplar': esa "
            "acción es organizar_ventanas.\n"
            "- Para abrir un JUEGO o un programa (aunque no sea un programa "
            "instalado, como un juego de Steam, Epic o instalado aparte) usá "
            "abrir_programa igual: sabe buscar en Steam, en Epic y, si no, lo "
            "busca por nombre en el disco.\n"
            "- Para acciones en DISCORD sobre OTROS usuarios (silenciar el "
            "micro, ensordecer, expulsar), usá las tools de Discord "
            "(silenciar_usuario_discord, volumen_usuario_discord, "
            "expulsar_usuario_discord). NO se puede cambiar el VOLUMEN real de "
            "otro usuario (Discord no lo permite): eso se hace como mute/"
            "deafen de voz.\n"
            "- Si el usuario pide algo DENTRO DE UN TIEMPO, como 'apagá la pc en "
            "10 minutos', 'suspendé en 5 minutos' o 'recordame X en N minutos', "
            "usá la tool programar_accion (NO control_energia). Para cancelarlo, "
            "usá cancelar_accion_programada.\n"
            "- Si pide buscar o abrir algo en INTERNET (MercadoLibre, YouTube, "
            "Google, Wikipedia, GitHub o un sitio), usá buscar_en_web, NO "
            "abrir_programa.\n"
            "- Si el usuario menciona el nombre de una MACRO configurada "
            "(por ejemplo 'modo fortnite', 'comedor' u otro atajo conocido), "
            "usá ejecutar_macro con ese nombre. Si no sabés qué macros hay, "
            "podés usar listar_macros.\n"
            "- Si es una pregunta de conocimiento, charla, chiste o curiosidad: "
            "respondé directo SIN herramientas.\n"
            "- Nunca inventes parámetros de las tools.\n"
            "- Sé útil con personalidad de Hatsune Miku.\n"
            "- NUNCA uses markdown (asteriscos, guiones) ni emojis en tus "
            "respuestas porque se leen en voz alta."
        )

    def consultar(self, texto: str, contexto: Dict[str, Any],
                  tools: List[dict]) -> Dict[str, Any]:
        """Hace una consulta al LLM y devuelve un dict con 'respuesta'
        (str) y 'tools_call' (lista de dicts) o 'error'.
        """
        # Guard: si NO hay API key configurada, informar con claridad y
        # devolver un mensaje amigable en vez de fallar con un HTTP 401.
        if not str(self.cfg.groq_api_key).strip():
            logger.error(
                "Falta GROQ_API_KEY. Configuralo en tu variable de entorno "
                "o en config_local.py (config.GROQ_API_KEY = 'gsk_...').")
            return {"error":
                    "No tengo mi clave de acceso configurada todavía. "
                    "Agregá GROQ_API_KEY y reiniciame."}

        claves = [t["function"]["name"] for t in tools]

        # Prompt de sistema con el perfil de PERSONALIDAD activo (se lee en
        # caliente para que cambiarlo por voz tenga efecto de inmediato).
        sistema = self.system_prompt
        _extra_pers = ""
        try:
            from miku.servicios import personalidad as _pers  # import tardío
            _extra_pers = _pers.prompt_extra() or ""
            if _extra_pers:
                sistema = f"{sistema}\n\nEstilo de personalidad:\n{_extra_pers}"
        except Exception:  # noqa: BLE001
            logger.debug("Sin perfil de personalidad para el prompt.")

        # Cache: la respuesta depende del texto, de las tools, de la
        # personalidad y del DÍA (una respuesta con "hoy" no vale mañana). No se
        # usa si hay recuerdos en el contexto (la respuesta depende de ellos).
        usa_cache = not contexto.get("miku_memoria")
        cache_key = (f"{texto.lower()}|{sorted(claves)}|{hash(_extra_pers)}|"
                     f"{date.today().isoformat()}")
        if usa_cache:
            hit = obtener_cache(cache_key)
            if hit:
                return {"respuesta": hit, "tools_call": []}

        extra_ctx = []
        if contexto.get("fecha"):
            extra_ctx.append(f"Fecha y hora actual: {contexto['fecha']}")
        if contexto.get("miku_memoria"):
            extra_ctx.append(f"Recuerdos relevantes:\n{contexto['miku_memoria']}")
        if extra_ctx:
            sistema = f"{sistema}\n\nContexto actual:\n" + "\n".join(extra_ctx)

        payload = {
            "model": self.cfg.modelo_api_externa,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": texto},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.4,
            "max_tokens": 400,
        }

        headers = {
            "Authorization": f"Bearer {self.cfg.groq_api_key}",
            "Content-Type": "application/json",
        }

        # Protección de rate-limit básica.
        with self._lock:
            espera = 0.25 - (time.monotonic() - self._ultima_llamada)
            if espera > 0:
                time.sleep(espera)

        try:
            resp = requests.post(self.BASE_URL, headers=headers,
                                 json=payload, timeout=20)
            data = resp.json()
            if "choices" not in data:
                logger.error("Error Groq: %s", data)
                return {"error": "Tuve un problema conectándome con mi cerebro."}

            mensaje = data["choices"][0]["message"]
            texto_resp = (mensaje.get("content") or "").strip()
            tool_calls = mensaje.get("tool_calls") or []

            parsed_calls = []
            for call in tool_calls:
                funcion = call.get("function", {})
                raw = funcion.get("arguments", "{}")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:  # noqa: BLE001
                    args = {}
                parsed_calls.append({
                    "nombre": funcion.get("name", ""),
                    "args": args,
                })

            with self._lock:
                self._ultima_llamada = time.monotonic()

            # Solo se cachean respuestas de PURA conversación. Si el LLM pidió
            # alguna tool, NO se cachea: un acierto de cache devuelve
            # ``tools_call=[]`` y repetir "abrí Discord" diría el texto sin
            # ejecutar la acción.
            if texto_resp and not parsed_calls and usa_cache:
                cache_respuesta(cache_key, texto_resp)

            return {"respuesta": texto_resp, "tools_call": parsed_calls}
        except Exception:  # noqa: BLE001
            logger.exception("Error general en consulta al LLM.")
            return {"error": "Uy, tuve un problema técnico."}


# ---------------- Parser principal ---------------- #

class CommandParser:
    """Coordina texto del usuario -> acción del asistente.

    Args:
        cfg: Config del programa.
        brain: Cliente de LLM para el parseo semántico.
        event_bus: Bus de eventos para despachar a plugins.
        memoria: Objeto opcional con guardar/buscar/olvidar recuerdos.
    """

    def __init__(self, cfg: "config_mod.Config", brain: BrainGroq,
                 event_bus, memoria: Any = None) -> None:
        self.cfg = cfg
        self.brain = brain
        self.bus = event_bus
        self.memoria = memoria

        # ---- Estado persistente de confirmación (sobrevive entre turnos) ----
        # Vive en el parser (no en el contexto, que se recrea en cada turno).
        # GENERALIZADO: no guardamos solo energía, sino cualquier acción
        # peligrosa pendiente como {tool: str, args: dict}. Así sirve para
        # control_energia, programar_accion y futuras tools peligrosas sin
        # duplicar la lógica de confirmación.
        self._espera_confirmacion = False
        self._pendiente_peligroso: Optional[Dict[str, Any]] = None
        # Momento (time.monotonic) en que se pidió la confirmación: pasado
        # ``_TTL_CONFIRMACION`` segundos se descarta, para que un "sí" perdido
        # más tarde no ejecute una acción peligrosa que nadie recuerda.
        self._confirmacion_desde: float = 0.0

        # ``procesar`` se llama desde hilos distintos (escucha de voz,
        # push-to-talk, Telegram) y comparte el estado de confirmación /
        # desambiguación: se serializa con un RLock.
        self._lock = threading.RLock()

        # ---- Estado persistente de DESAMBIGUACIÓN (genérico) ----
        # Cuando una tool devuelve {"desambiguar": True, "opciones": [...]},
        # guardamos ese estado acá para que el SIGUIENTE mensaje del usuario
        # ("el tercero", "3", o parte del nombre) pueda resolverse sin LLM.
        # Estructura: {tool: str, args_base: dict, opciones: list[dict]}
        self._pendiente_desambiguacion: Optional[Dict[str, Any]] = None

    # ---------------- Construcción de tools desde plugins ----------------
    def recopilar_tools(self) -> List[dict]:
        """Junta los schemas de tools declaradas por todos los plugins."""
        tools: List[dict] = []
        vistos: set = set()
        for p in self.bus.plugins:
            for t in getattr(p, "tools", []):
                nombre = t.get("function", {}).get("name")
                if nombre in vistos:
                    logger.warning("Tool '%s' repetida (plugin '%s'): se ignora.",
                                   nombre, getattr(p, "nombre", "?"))
                    continue
                vistos.add(nombre)
                tools.append(t)
        return tools

    def despachar_tool(self, nombre_tool: str, args: Dict[str, Any],
                       contexto: Dict[str, Any]) -> Any:
        """Busca el plugin que maneja la tool `nombre_tool` y la ejecuta."""
        for p in self.bus.plugins:
            if getattr(p, "manejar_tool", None):
                try:
                    resultado = p.manejar_tool(nombre_tool, args, contexto)
                except Exception:  # noqa: BLE001
                    logger.exception("Plugin '%s' falló en tool '%s'.",
                                     p.nombre, nombre_tool)
                    continue
                if resultado is not None:
                    return resultado
        return None

    # ---------------- Flujo principal ----------------
    def procesar(self, texto: str, contexto: Dict[str, Any]) -> str:
        """Procesa `<texto>` del usuario y devuelve la respuesta a decir.

        Es seguro llamarlo desde varios hilos: las llamadas se serializan.

        Args:
            texto: Comando/consulta del usuario.
            contexto: Diccionario de runtime (cfg, voice, scheduler).

        Returns:
            Respuesta de texto final.
        """
        with self._lock:
            return self._procesar(texto, contexto)

    def _procesar(self, texto: str, contexto: Dict[str, Any]) -> str:
        """Implementación de ``procesar`` (se llama con el lock tomado)."""
        texto = (texto or "").strip()
        if not texto:
            return "Sí? No escuché nada."

        # 1) Confirmación de acción peligrosa pendiente (estado persistente).
        if self._espera_confirmacion and self._confirmacion_vencida():
            logger.info("La confirmación pendiente venció; se descarta.")
            self._limpiar_confirmacion()
        if self._espera_confirmacion:
            resultado = self._manejar_confirmacion(texto, contexto)
            if resultado is not None:
                return resultado

        # 1b) DESAMBIGUACIÓN pendiente: si hay una tool esperando que el usuario
        #     elija entre opciones, intentamos resolver ESTE mensaje contra esa
        #     lista ANTES de ir al LLM. Si no se puede resolver con confianza,
        #     se cancela y el mensaje se trata como nuevo (no nos trabamos).
        if self._pendiente_desambiguacion is not None:
            resultado = self._manejar_desambiguacion(texto, contexto)
            if resultado is not None:
                return resultado
            # Si devolvió None, ya se canceló internamente: seguimos como normal.

        # 2) Fast path sin LLM: comandos locales triviales (hora, saludo...).
        respuesta_memoria = self._comandos_inmediatos(texto, contexto)
        if respuesta_memoria is not None:
            return respuesta_memoria

        # 2b) Fast path a plugins: si un plugin declara este comando.
        respuesta_plugin = self.bus.despachar_comando(texto, contexto)
        if respuesta_plugin:
            return respuesta_plugin

        # 3) Construir contexto de memoria para el prompt.
        contexto_brain = self._agregar_memoria(texto)

        # 4) Consultar al LLM y ejecutar tools si hace falta.
        tools = self.recopilar_tools()
        resultado = self.brain.consultar(texto, contexto_brain, tools)

        if resultado.get("error"):
            return resultado["error"]

        respuesta_base = resultado.get("respuesta") or ""
        tool_calls = resultado.get("tools_call", []) or []

        # 4) ENCADENADO (F7): si el LLM devuelve VARIAS tool_calls en un mismo
        #    turno, se procesan TODAS en orden (no solo la primera).
        #    - Las tools SEGURAS se ejecutan de inmediato, en el orden dado.
        #    - Las tools PELIGROSAS no se ejecutan: se encola la PRIMERA para
        #      pedir confirmación (las demás peligrosas del turno no se pierden
        #      del todo: el usuario puede pedirlas de nuevo tras confirmar).
        #    Esto permite casos como "abrí VS Code y Discord y bajá el brillo":
        #    las tres se ejecutan en una sola frase.
        extras: List[str] = []
        pregunta_peligrosa: Optional[str] = None
        nombres_conocidos = {t.get("function", {}).get("name") for t in tools}
        peligrosas = self._tools_peligrosas()
        for call in tool_calls:
            nombre = call.get("nombre", "")
            args = call.get("args", {}) or {}

            # Tool inventada por el LLM: no la ejecutamos ni decimos "Listo".
            if nombre not in nombres_conocidos:
                logger.warning("El LLM pidió una tool inexistente: '%s'.", nombre)
                extras.append("No tengo una herramienta para eso.")
                continue

            if nombre in peligrosas:
                # Encolamos UNA sola confirmación (la primera peligrosa) y
                # seguimos para no perder las tools seguras del mismo turno.
                if pregunta_peligrosa is None:
                    pregunta_peligrosa = self._encolar_confirmacion(nombre, args)
                else:
                    logger.info("Tool peligrosa extra '%s' ignorada en este "
                                "turno (ya hay una confirmación pendiente).",
                                nombre)
                continue

            logger.debug("Tool invocada: %s(%s)", nombre, args)
            extra = self.despachar_tool(nombre, args, contexto)

            # ¿La tool pide DESAMBIGUAR (lista de opciones para elegir)?
            # En ese caso guardamos el estado pendiente y mostramos la pregunta.
            if self._es_resultado_desambiguacion(extra):
                return self._guardar_desambiguacion(extra)

            if isinstance(extra, str) and extra.strip():
                extras.append(extra)

        # Respuesta final = texto del LLM + resultados de herramientas.
        partes = [p for p in [respuesta_base, " ".join(extras)] if p]
        final = " ".join(partes).strip()

        # Si quedó una acción peligrosa esperando confirmación, se agrega su
        # pregunta al final (y el estado ya quedó guardado en el parser).
        if pregunta_peligrosa:
            return (final + " " + pregunta_peligrosa).strip() \
                if final else pregunta_peligrosa

        return final or "¡Listo!"

    # ---------------- Manejo de confirmación ----------------
    def _tools_peligrosas(self) -> set:
        """Tools que exigen CONFIRMACIÓN explícita antes de ejecutarse.

        Las declara cada plugin en su atributo ``peligrosas`` (acciones
        irreversibles o que afectan a otros: apagar la PC, expulsar a alguien).
        Si el LLM propone una, se encola y se pregunta al usuario en vez de
        ejecutarla directo.
        """
        peligrosas: set = set()
        for plugin in self.bus.plugins:
            peligrosas |= set(getattr(plugin, "peligrosas", ()) or ())
        return peligrosas

    # Palabras (sin acentos) con las que el usuario confirma una acción. Se
    # comparan como PALABRAS COMPLETAS: por substring, "así" contendría "si".
    _CONFIRMAR = frozenset({
        "si", "dale", "confirmo", "confirma", "hacelo", "ok", "okay", "yes",
        "adelante", "apagala", "reiniciala",
    })
    # Palabras (sin acentos) para cancelar. Tienen PRIORIDAD sobre confirmar
    # ("no, dale" cancela): ante la duda, no se ejecuta la acción peligrosa.
    _CANCELAR = frozenset({
        "no", "cancelar", "cancela", "cancel", "para", "frenar", "abortar",
        "tranca", "nunca",
    })
    # Segundos que una confirmación pendiente sigue vigente.
    _TTL_CONFIRMACION = 60.0

    # Descripciones legibles por tool para el texto de confirmación.
    _DESCRIPCION_PELIGROSA = {
        "control_energia": {
            "apagar": "apagar la PC",
            "reiniciar": "reiniciar la PC",
            "suspender": "suspender la PC",
        },
    }

    def _encolar_confirmacion(self, tool: str, args: Dict[str, Any]) -> str:
        """Registra una acción peligrosa pendiente y pide confirmación.

        Generaliza el flujo (antes exclusivo de control_energia): guarda
        ``{tool, args}`` y arma una pregunta acorde. Al confirmar, se ejecutará
        ESA tool con ESOS args (ver ``_manejar_confirmacion``).
        """
        tool = str(tool or "").strip()
        if not tool:
            return "No entendí qué acción querés que confirme."

        self._pendiente_peligroso = {"tool": tool, "args": dict(args or {})}
        self._espera_confirmacion = True
        self._confirmacion_desde = time.monotonic()

        if tool == "control_energia":
            accion = str((args or {}).get("accion", "")).lower().strip()
            if not accion:
                return "¿Qué querés hacer: apagar, reiniciar o suspender la PC?"
            texto_accion = self._DESCRIPCION_PELIGROSA["control_energia"].get(
                accion, accion)
            return (f"¿Confirmás que quiero {texto_accion}? "
                    f"Decime 'sí' para confirmar o 'no' para cancelar.")

        if tool == "programar_accion":
            return self._describir_programar(args)

        if tool in ("silenciar_usuario_discord", "expulsar_usuario_discord",
                    "volumen_usuario_discord"):
            return self._describir_discord(tool, args)

        # Genérico para futuras tools peligrosas.
        return ("Esto es una acción importante, ¿la confirmás? "
                "Decime 'sí' para confirmar o 'no' para cancelar.")

    def _describir_discord(self, tool: str, args: Dict[str, Any]) -> str:
        """Arma la pregunta de confirmación para las tools de Discord."""
        usuario = str((args or {}).get("usuario", "") or "").strip() or "ese usuario"
        if tool == "expulsar_usuario_discord":
            return (f"¿Confirmás que expulse a {usuario} del servidor de "
                    f"Discord? Decime 'sí' para confirmar o 'no' para cancelar.")
        if tool == "silenciar_usuario_discord":
            silenciar = bool((args or {}).get("silenciar", True))
            verbo = "silencie" if silenciar else "le quite el silencio a"
            return (f"¿Confirmás que {verbo} {usuario} en Discord? "
                    f"Decime 'sí' para confirmar o 'no' para cancelar.")
        # volumen_usuario_discord
        accion = str((args or {}).get("accion", "") or "").strip().lower()
        verbos = {
            "silenciar": "silencie", "desilenciar": "reactive el micro de",
            "ensordecer": "ensordezca", "desensordecer": "reactive el audio de",
        }
        verbo = verbos.get(accion, "ajuste el audio de")
        return (f"¿Confirmás que {verbo} {usuario} en Discord? "
                f"Decime 'sí' para confirmar o 'no' para cancelar.")

    def _describir_programar(self, args: Dict[str, Any]) -> str:
        """Arma la pregunta de confirmación para `programar_accion`."""
        accion = str((args or {}).get("accion", "")).lower().strip()
        minutos = (args or {}).get("en_minutos")
        mensaje = str((args or {}).get("mensaje", "") or "").strip()
        try:
            minutos_txt = f"{int(minutos)} minutos" if minutos is not None else "?"
        except (TypeError, ValueError):
            minutos_txt = str(minutos)
        if accion == "recordatorio":
            detalle = f"te recuerde '{mensaje}'" if mensaje else "te avise"
            return (f"¿Confirmás que en {minutos_txt} {detalle}? "
                    f"Decime 'sí' para confirmar o 'no' para cancelar.")
        mapa = {"apagar": "apague la PC", "reiniciar": "reinicie la PC",
                "suspender": "suspenda la PC"}
        detalle = mapa.get(accion, accion)
        return (f"¿Confirmás que en {minutos_txt} {detalle}? "
                f"Decime 'sí' para confirmar o 'no' para cancelar.")

    def _limpiar_confirmacion(self) -> None:
        """Borra el estado de confirmación pendiente."""
        self._espera_confirmacion = False
        self._pendiente_peligroso = None
        self._confirmacion_desde = 0.0

    def _confirmacion_vencida(self) -> bool:
        """True si la confirmación pendiente superó ``_TTL_CONFIRMACION``."""
        return (time.monotonic() - self._confirmacion_desde) > self._TTL_CONFIRMACION

    def _manejar_confirmacion(self, texto: str,
                              contexto: Dict[str, Any]) -> Optional[str]:
        """Resuelve una confirmación de acción peligrosa GENÉRICA.

        Usa el estado persistente guardado en `self`, que sobrevive entre
        llamadas a ``procesar()`` (no depende del contexto transitorio).
        Sirve para cualquier tool declarada como peligrosa por un plugin.
        """
        pendiente = self._pendiente_peligroso
        if not pendiente:
            self._espera_confirmacion = False
            return None

        palabras = set(_normalizar_frase(texto).split())

        # Cancelar tiene prioridad: "no, dale" o "no lo hagas, ok" NO ejecutan.
        if palabras & self._CANCELAR:
            self._limpiar_confirmacion()
            return "Cancelado, no hice nada."

        # Si el usuario confirma -> se ejecuta la tool pendiente.
        if palabras & self._CONFIRMAR:
            tool = pendiente.get("tool", "")
            args = dict(pendiente.get("args", {}) or {})
            self._limpiar_confirmacion()
            res = self.despachar_tool(tool, args, contexto)
            return res if isinstance(res, str) and res else "Listo."

        # Sin confirmación ni cancelación clara: seguimos esperando.
        tool = pendiente.get("tool", "")
        args = pendiente.get("args", {}) or {}
        if tool == "control_energia":
            accion = str(args.get("accion", "")).lower().strip()
            texto_accion = self._DESCRIPCION_PELIGROSA["control_energia"].get(
                accion, accion)
            return (f"Todavía no me confirmaste. ¿{texto_accion}? "
                    f"Decime 'sí' o 'no'.")
        if tool == "programar_accion":
            return ("Todavía no me confirmaste. ¿Lo programo? "
                    "Decime 'sí' o 'no'.")
        if tool in ("silenciar_usuario_discord", "expulsar_usuario_discord",
                    "volumen_usuario_discord"):
            return ("Todavía no me confirmaste la acción en Discord. "
                    "Decime 'sí' o 'no'.")
        return "Todavía no me confirmaste. Decime 'sí' o 'no'."

    # ---------------- Desambiguación (mecanismo genérico) ----------------
    @staticmethod
    def _es_resultado_desambiguacion(resultado: Any) -> bool:
        """¿El resultado de una tool es una petición de desambiguación?

        Convención: un dict con ``desambiguar=True`` y una lista ``opciones``.
        Cualquier otro tipo (str, None, etc.) NO lo es.
        """
        return (isinstance(resultado, dict)
                and resultado.get("desambiguar") is True
                and isinstance(resultado.get("opciones"), list)
                and len(resultado.get("opciones")) > 0)

    def _guardar_desambiguacion(self, resultado: Dict[str, Any]) -> str:
        """Guarda el estado pendiente y devuelve la PREGUNTA para el usuario.

        Estructura guardada (esperada por el resto del mecanismo):
            {tool: str, args_base: dict, opciones: list[dict]}
        """
        self._pendiente_desambiguacion = {
            "tool": resultado.get("tool_origen", ""),
            "args_base": resultado.get("args_origen", {}) or {},
            "opciones": resultado.get("opciones", []),
        }
        logger.info("Desambiguación pendiente de '%s' con %d opciones.",
                    self._pendiente_desambiguacion["tool"],
                    len(self._pendiente_desambiguacion["opciones"]))
        return self._formatear_pregunta(self._pendiente_desambiguacion)

    def _formatear_pregunta(self, pendiente: Dict[str, Any]) -> str:
        """Arma el texto de la pregunta con las opciones numeradas."""
        lineas = []
        for op in pendiente.get("opciones", []):
            idx = op.get("indice")
            etiqueta = op.get("etiqueta", "?")
            lineas.append(f"{idx}. {etiqueta}")
        listado = "\n".join(lineas)
        return (f"Encontré varias opciones, ¿cuál querés?\n{listado}\n"
                f"Decime el número, 'el primero'… o parte del nombre.")

    def _cancelar_desambiguacion(self) -> None:
        """Borra el estado de desambiguación pendiente."""
        self._pendiente_desambiguacion = None

    # Ordinales en texto -> índice (1-based).
    _ORDINALES = {
        "primero": 1, "primera": 1, "primer": 1,
        "segundo": 2, "segunda": 2,
        "tercero": 3, "tercera": 3, "tercer": 3,
        "cuarto": 4, "cuarta": 4,
        "quinto": 5, "quinta": 5,
    }

    def _resolver_opcion(self, texto: str,
                         opciones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Intenta resolver el texto del usuario contra las opciones.

        Acepta:
          - Números directos: "3", "el 2", "opción 1".
          - Ordinales en texto: "el tercero", "la segunda".
          - Coincidencia parcial con la etiqueta (parte del nombre del archivo).

        Devuelve la opción elegida o None si no hay match con confianza.
        """
        bajo = (texto or "").lower().strip()
        if not bajo:
            return None

        def _op_por_indice(idx: int) -> Optional[Dict[str, Any]]:
            for op in opciones:
                if int(op.get("indice", -1)) == idx:
                    return op
            return None

        # 1) Ordinal en texto ("el tercero").
        for palabra, idx in self._ORDINALES.items():
            if palabra in bajo:
                op = _op_por_indice(idx)
                if op:
                    return op

        # 2) Número directo ("3", "opción 2"). Tomamos el PRIMER entero del texto
        #    y verificamos que exista una opción con ese índice.
        m = re.search(r"\d+", bajo)
        if m:
            op = _op_por_indice(int(m.group()))
            if op:
                return op

        # 3) Coincidencia parcial con la etiqueta (parte del nombre del archivo).
        #    Ignoramos palabras muy cortas para no matchear de más.
        palabras = [w for w in re.findall(r"\w+", bajo) if len(w) >= 3]
        candidatas = []
        for op in opciones:
            etiqueta = str(op.get("etiqueta", "")).lower()
            etiqueta_sin_ext = etiqueta.rsplit(".", 1)[0]
            if any(w in etiqueta or w in etiqueta_sin_ext for w in palabras):
                candidatas.append(op)
        # Solo resolvemos si hay UNA sola candidata (si hay varias, es ambiguo).
        if len(candidatas) == 1:
            return candidatas[0]
        return None

    def _manejar_desambiguacion(self, texto: str,
                                contexto: Dict[str, Any]) -> Optional[str]:
        """Resuelve (o cancela) una desambiguación pendiente.

        Returns:
            - str con la respuesta si se pudo resolver o si se canceló por
              pedido explícito.
            - None si NO se pudo resolver con confianza: en ese caso cancela
              la desambiguación y devuelve None para que el mensaje se procese
              como uno nuevo (no nos quedamos trabados).
        """
        pendiente = self._pendiente_desambiguacion
        if not pendiente:
            return None

        bajo = (texto or "").lower().strip()

        # Cancelación explícita.
        if bajo in ("cancelar", "cancela", "cancelá", "olvidalo", "olvídalo",
                    "nada", "dejalo", "dejá"):
            self._cancelar_desambiguacion()
            return "Listo, lo dejo."

        opcion = self._resolver_opcion(texto, pendiente.get("opciones", []))
        if opcion is None:
            # No se pudo resolver: cancelamos y tratamos el mensaje como nuevo.
            logger.info("Desambiguación no resuelta con '%s'; se cancela.", texto)
            self._cancelar_desambiguacion()
            return None

        # Resolvemos: completamos args_base con el "valor" de la opción elegida
        # y ejecutamos la tool de origen DIRECTAMENTE (sin LLM).
        tool = pendiente.get("tool", "")
        args = dict(pendiente.get("args_base", {}) or {})
        valor = opcion.get("valor", {}) or {}
        if isinstance(valor, dict):
            args.update(valor)
        self._cancelar_desambiguacion()
        logger.info("Desambiguación resuelta: %s %s -> %s",
                    tool, args, opcion.get("etiqueta"))
        res = self.despachar_tool(tool, args, contexto)
        if isinstance(res, str) and res.strip():
            return res
        return "Listo."

    # ---------------- Comandos inmediatos (sin LLM) ----------------
    def _comandos_inmediatos(self, texto: str, contexto: Dict[str, Any]) -> Optional[str]:
        """Atiende comandos triviales que no requieren el LLM.

        Devuelve una respuesta si la maneja localmente, o None si hay que
        pasar al LLM. Mantenerlo acotado ayuda a la velocidad, no a cubrir
        todo (el LLM se encarga del resto).
        """
        # ``t``: minúsculas SIN acentos (Whisper devuelve "¿Qué hora es?");
        # ``frase``: además sin signos, para comparar frases exactas.
        t = sin_acentos(texto).strip()
        frase = _normalizar_frase(texto)

        from datetime import datetime

        # Calculadora rápida (sin LLM): "cuánto es 15 por 23 más 10%".
        # Solo se activa con frases claramente matemáticas, para no pisar
        # consultas que casualmente tengan números.
        respuesta_calc = self._intentar_calculo(t)
        if respuesta_calc is not None:
            return respuesta_calc

        # Hora/fecha simple.
        if frase in ("que hora es", "hora", "decime la hora", "la hora"):
            ahora = datetime.now()
            return (f"Hoy es {_fecha_legible(ahora)} y son las "
                    f"{ahora.strftime('%H:%M')}.")

        # Saludos minimalistas (para no gastar la API).
        if frase in ("hola", "buenas", "hey", "hola miku"):
            return "¡Hola! ¿En qué te ayudo?"

        # Listado de plugins.
        if frase in ("que plugins tienes", "plugins", "que sabes hacer"):
            nombres = self.bus.listar_plugins()
            disp = ", ".join(nombres) if nombres else "por ahora solo el núcleo."
            return f"Tengo disponibles: {disp if disp else 'nada aún'}."

        # Memoria: guardar un recuerdo explícito ("acordate que X", "recordame
        # que X", "guardá que X"). IMPORTANTE: NO interceptamos cuando es un
        # RECORDATORIO DIFERIDO ("recordame sacar la basura en 10 minutos"):
        # eso lo maneja la tool programar_accion.
        m = self._RE_RECUERDO.match(t)
        if m:
            if self._parece_recordatorio_diferido(t):
                return None  # que lo maneje la tool programar_accion
            if self.memoria:
                # El match se hizo sobre texto sin acentos; el recuerdo se
                # guarda con el texto ORIGINAL (mismos offsets si no cambió el
                # largo al quitar acentos).
                fuente = texto if len(texto) == len(t) else t
                dato = _quitar_preposicion(fuente[m.start(1):]).strip()
                if dato:
                    self.memoria.guardar_recuerdo(dato)
                return "Anotado, no me olvido."
            return "No tengo memoria activa en este modo."

        return None

    # "acordate que X", "recordame que X", "recordá X", "guardá que X", "anotá X".
    # Se aplica sobre texto SIN acentos, por eso "recorda"/"guarda"/"anota".
    _RE_RECUERDO = re.compile(
        r"^(?:acordate|recordame|recorda|guarda|anota)\s+(?:de\s+)?(.+)$")

    # Prefijos que marcan claramente una intención de cálculo.
    _PREFIJOS_CALCULO = (
        "cuanto es ", "cuanto da ", "cuanto seria ", "cuanto son ",
        "cuanto sale ", "calcula ", "calculame ", "calculemos ",
        "resolveme ", "resuelve ", "haceme la cuenta ", "resultado de ",
    )

    def _intentar_calculo(self, t: str) -> Optional[str]:
        """Fast-path de calculadora local. Devuelve la respuesta o None.

        Se activa cuando el texto empieza con un prefijo de cálculo
        ("cuánto es...") O cuando es una expresión aritmética pura con
        operadores y números (p. ej. "15 por 23"). Nunca llama a la API.
        """
        bajo = (t or "").strip()
        if not bajo:
            return None

        # Normalizamos para comparar prefijos sin acentos.
        from miku.cerebro.calculadora import calcular, parece_calculo  # import tardío

        es_prefijo = any(bajo.startswith(p) for p in self._PREFIJOS_CALCULO)
        if not es_prefijo:
            # Sin prefijo, exigimos forma de expresión aritmética pura: que
            # tenga un operador Y que NO sea una pregunta de conocimiento.
            if not parece_calculo(bajo):
                return None
            # Evitamos pisar cosas como "tengo 2 monitores" (sin operador real).
            # Si no hay un operador SIMBÓLICO ni palabras operadoras claras,
            # dejamos pasar al flujo normal.
            tiene_operador = any(
                op in bajo for op in ("+", "-", "*", "/", "%")) or any(
                p in bajo for p in (" mas ", " menos ", " por ", " entre ",
                                    " dividido "))
            if not tiene_operador:
                return None

        resultado = calcular(bajo)
        if resultado is None:
            return None
        logger.debug("Cálculo local resuelto: '%s' -> %s", bajo, resultado)
        return f"Son {resultado}."

    # Expresión de tiempo diferido: "en 5 minutos", "en 2 horas", "en 30 seg".
    # OJO con el plural: el sufijo opcional "s?" va ANTES del \b para que
    # "minutos"/"horas"/"segundos" también matcheen (antes "minutos" no
    # matcheaba por el \b entre 'o' y 's').
    _RE_TIEMPO_DIFERIDO = re.compile(
        r"\ben\s+\d+\s*(minutos?|mins?|horas?|hs?|segundos?|segs?|s)\b")

    def _parece_recordatorio_diferido(self, t: str) -> bool:
        """True si el texto parece "recordame X en N minutos/horas" (diferido)."""
        return bool(self._RE_TIEMPO_DIFERIDO.search(t))

    def _agregar_memoria(self, texto: str) -> Dict[str, Any]:
        """Devuelve dict con contextos adicionales (memoria, fecha)."""
        c: Dict[str, Any] = {}
        from datetime import datetime
        ahora = datetime.now()
        c["fecha"] = f"{_fecha_legible(ahora)} {ahora.strftime('%H:%M')}"
        if self.memoria is not None:
            try:
                recuerdos = self.memoria.buscar_recuerdos(texto)
                if recuerdos:
                    c["miku_memoria"] = recuerdos
            except Exception:  # noqa: BLE001
                logger.warning("No se pudo consultar la memoria.")
        return c


_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
         "domingo")


def _fecha_legible(momento) -> str:
    """Fecha en español ("sábado 19/09/2026"), sin depender del locale.

    ``strftime('%A')`` devuelve el día en inglés en un Windows sin locale es-AR.
    """
    return f"{_DIAS[momento.weekday()]} {momento.strftime('%d/%m/%Y')}"


def _quitar_preposicion(dato: str) -> str:
    """Quita el "que"/"que tengo" inicial de una frase de memoria.

    Ej.: "que mañana tengo turno" -> "mañana tengo turno". Se hace ``strip()``
    ANTES de comparar para tolerar espacios sobrantes al principio.
    """
    dato = (dato or "").strip()
    for pre in ("que tengas ", "que ", "que tengo ", "tengo "):
        if dato.lower().startswith(pre):
            dato = dato[len(pre):]
            break
    return dato.strip()
