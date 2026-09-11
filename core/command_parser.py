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
import threading
import time
from typing import Any, Dict, List, Optional

import requests

import config as config_mod

logger = logging.getLogger("miku.parser")

# Cache LRU trivial para respuestas frecuentes (optimización).
_CACHE = {}
_CACHE_ORDEN: List[str] = []
_CACHE_MAX = 64


def cache_respuesta(clave: str, respuesta: str) -> None:
    """Guarda una respuesta frecuente en la cache con límite simple."""
    global _CACHE, _CACHE_ORDEN
    if clave in _CACHE:
        _CACHE_ORDEN.remove(clave)
    _CACHE[clave] = respuesta
    _CACHE_ORDEN.append(clave)
    if len(_CACHE) > _CACHE_MAX:
        vieja = _CACHE_ORDEN.pop(0)
        _CACHE.pop(vieja, None)


def obtener_cache(clave: str) -> Optional[str]:
    """Devuelve la respuesta cacheada si existe."""
    return _CACHE.get(clave)


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
            "(abrir, cerrar, minimizar, buscar, reproducir, mover ventana, etc.).\n"
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

        # Fast path en cache (solo fuera de confirmaciones peligrosas).
        cache_key = f"{texto.lower()}|{sorted(claves)}"
        hit = obtener_cache(cache_key)
        if hit:
            return {"respuesta": hit, "tools_call": []}

        # Prompt de sistema con contexto dinámico (fecha / memoria).
        sistema = self.system_prompt
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

            # Cache de respuestas típicas (no para acciones peligrosas).
            hay_danger = any(
                c["nombre"] == "control_energia" for c in parsed_calls
            )
            if texto_resp and not hay_danger:
                cache_respuesta(cache_key, texto_resp)

            return {"respuesta": texto_resp, "tools_call": parsed_calls}
        except Exception as e:  # noqa: BLE001
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
        self._espera_confirmacion = False
        self._pendiente_energia: Optional[Dict[str, Any]] = None

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
        for p in self.bus.plugins:
            for t in getattr(p, "tools", []):
                if t not in tools:
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

        Args:
            texto: Comando/consulta del usuario.
            contexto: Diccionario de runtime (rvc, kokoro, preferencias,
                espera_confirmacion, etc.).

        Returns:
            Respuesta de texto final.
        """
        texto = (texto or "").strip()
        if not texto:
            return "Sí? No escuché nada."

        # 1) Confirmación de acción peligrosa pendiente (estado persistente).
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

        # 4a) Acciones peligrosas (control_energia) NUNCA se ejecutan directo:
        #     se encolan y se pide confirmación al usuario.
        for call in tool_calls:
            if call.get("nombre") == "control_energia":
                return self._encolar_confirmacion_energia(call.get("args", {}))

        # 4b) El resto de tools se ejecutan de inmediato.
        extras: List[str] = []
        for call in tool_calls:
            nombre = call["nombre"]
            args = call["args"]
            logger.debug("Tool invocada: %s(%s)", nombre, args)
            extra = self.despachar_tool(nombre, args, contexto)

            # ¿La tool pide DESAMBIGUAR (lista de opciones para elegir)?
            # En ese caso guardamos el estado pendiente y mostramos la pregunta.
            if self._es_resultado_desambiguacion(extra):
                return self._guardar_desambiguacion(extra)

            if isinstance(extra, str) and extra.strip():
                extras.append(extra)

        # Respuesta final = texto del LLM + resultado de herramientas.
        partes = [p for p in [respuesta_base, " ".join(extras)] if p]
        final = " ".join(partes).strip()
        return final or "¡Listo!"

    # ---------------- Manejo de confirmación ----------------
    # Palabras que el usuario usa para confirmar una acción.
    _CONFIRMAR = ("sí", "si", "dale", "confirmo", "hacelo", "ok", "okay",
                  "yes", "confirmá", "adelante", "apagala", "reiniciala")
    # Palabras para cancelar explícitamente.
    _CANCELAR = ("no", "cancelar", "cancelá", "cancel", "para", "frenar",
                 "abortar", "salí", "tranca", "no apagues", "no apagues la pc")

    def _encolar_confirmacion_energia(self, args: Dict[str, Any]) -> str:
        """Registra una acción de energía pendiente y pide confirmación."""
        accion = str(args.get("accion", "")).lower().strip()
        if not accion:
            return "¿Qué querés hacer: apagar, reiniciar o suspender la PC?"
        self._pendiente_energia = {"accion": accion}
        self._espera_confirmacion = True
        texto_accion = {"apagar": "apagar la PC",
                        "reiniciar": "reiniciar la PC",
                        "suspender": "suspender la PC"}.get(accion, accion)
        return (f"¿Confirmás que quiero {texto_accion}? "
                f"Decime 'sí' para confirmar o 'no' para cancelar.")

    def _limpiar_confirmacion(self) -> None:
        """Borra el estado de confirmación pendiente."""
        self._espera_confirmacion = False
        self._pendiente_energia = None

    def _manejar_confirmacion(self, texto: str,
                              contexto: Dict[str, Any]) -> Optional[str]:
        """Resuelve una confirmación de acción peligrosa (energía).

        Usa el estado persistente guardado en `self`, que sobrevive entre
        llamadas a ``procesar()`` (no depende del contexto transitorio).
        """
        pendiente = self._pendiente_energia
        if not pendiente:
            self._espera_confirmacion = False
            return None

        bajo = (texto or "").lower().strip()

        # Si el usuario confirma -> se ejecuta la acción.
        if any(p in bajo for p in self._CONFIRMAR):
            accion = dict(pendiente)
            self._limpiar_confirmacion()
            res = self.despachar_tool("control_energia", accion, contexto)
            return res or "Listo."

        # Si cancela explícitamente -> no se hace nada y se destraba.
        if any(n in bajo for n in self._CANCELAR):
            self._limpiar_confirmacion()
            return "Cancelado, no hice nada."

        # Sin confirmación ni cancelación clara: seguimos esperando.
        texto_accion = {"apagar": "apagar la PC",
                        "reiniciar": "reiniciar la PC",
                        "suspender": "suspender la PC"}.get(
                            pendiente.get("accion", ""), pendiente.get("accion", ""))
        return (f"Todavía no me confirmaste. ¿{texto_accion}? "
                f"Decime 'sí' o 'no'.")

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
        import re as _re
        m = _re.search(r"\d+", bajo)
        if m:
            op = _op_por_indice(int(m.group()))
            if op:
                return op

        # 3) Coincidencia parcial con la etiqueta (parte del nombre del archivo).
        #    Ignoramos palabras muy cortas para no matchear de más.
        palabras = [w for w in _re.findall(r"\w+", bajo) if len(w) >= 3]
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
        t = texto.lower().strip()

        from datetime import datetime

        # Hora/fecha simple.
        if t in ("que hora es", "hora", "decime la hora", "que hora es?"):
            ahora = datetime.now()
            return (f"Hoy es {ahora.strftime('%A %d/%m/%Y')} y son las "
                    f"{ahora.strftime('%H:%M')}.")

        # Saludos minimalistas (para no gastar la API).
        if t in ("hola", "buenas", "hey", "hola miku"):
            return "¡Hola! ¿En qué te ayudo?"

        # Listado de plugins.
        if t in ("que plugins tienes", "plugins", "que sabes hacer"):
            nombres = self.bus.listar_plugins()
            disp = ", ".join(nombres) if nombres else "por ahora solo el núcleo."
            return f"Tengo disponibles: {disp if disp else 'nada aún'}."

        # Memoria: guardar un recuerdo explícito ("acordate que X").
        if t.startswith(("acordate", "recorda", "acordate que", "guarda que")):
            if self.memoria:
                dato = t.replace("acordate", "").replace("recorda", "")
                dato = _quitar_preposicion(dato)
                self.memoria.guardar_recuerdo(dato.strip())
                return "Anotado, no me olvido."
            return "No tengo memoria activa en este modo."

        return None

    def _agregar_memoria(self, texto: str) -> Dict[str, Any]:
        """Devuelve dict con contextos adicionales (memoria, fecha)."""
        c: Dict[str, Any] = {}
        from datetime import datetime
        c["fecha"] = datetime.now().strftime('%A %d/%m/%Y %H:%M')
        if self.memoria is not None:
            try:
                recuerdos = self.memoria.buscar_recuerdos(texto)
                if recuerdos:
                    c["miku_memoria"] = recuerdos
            except Exception:  # noqa: BLE001
                logger.warning("No se pudo consultar la memoria.")
        return c


def _quitar_preposicion(dato: str) -> str:
    """Limpia una frase de memoria de las preposiciones iniciales típicas."""
    for pre in ("que ", "que tengas "):
        if dato.startswith(pre):
            dato = dato[len(pre):]
            break
    return dato
