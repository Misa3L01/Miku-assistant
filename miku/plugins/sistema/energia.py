"""
energia.py - Energía de la PC, acciones/recordatorios programados y brillo de pantalla.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin
from miku.servicios import recordatorios
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.energia")


class Energia(Plugin):
    """Apagar/reiniciar/suspender, acciones programadas y brillo."""

    nombre = "energia"
    descripcion = "Apagar/reiniciar/suspender, acciones programadas y brillo."
    peligrosas = frozenset({'control_energia', 'programar_accion'})

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "control_energia",
                "description": "Apaga, reinicia o suspende la PC. ACCIÓN "
                               "PELIGROSA: el sistema SIEMPRE pedirá "
                               "confirmación antes de ejecutarla.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["apagar", "reiniciar", "suspender"],
                            "description": "Qué hacer con la energía de la PC.",
                        },
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "programar_accion",
                "description": "Programa una acción DIFERIDA en el tiempo: "
                               "apagar/reiniciar/suspender la PC o un "
                               "recordatorio hablado. Se indica el momento con "
                               "'en_minutos' (dentro de N minutos) O con 'hora' "
                               "(HH:MM, a hora exacta; si esa hora ya pasó hoy, "
                               "se asume mañana). ACCIÓN IMPORTANTE: el sistema "
                               "pedirá confirmación. Ej: 'suspendé la pc en 5 "
                               "minutos', 'recordame sacar la basura en 10 "
                               "minutos', 'recordame a las 18:30 que salgo'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {
                            "type": "string",
                            "enum": ["apagar", "reiniciar", "suspender",
                                     "recordatorio"],
                            "description": "Qué programar.",
                        },
                        "en_minutos": {
                            "type": "integer",
                            "description": "Dentro de cuántos minutos "
                                           "disparar la acción (alternativo a "
                                           "'hora').",
                        },
                        "hora": {
                            "type": "string",
                            "description": "Hora exacta HH:MM (24h) para "
                                           "disparar la acción. Si ya pasó hoy, "
                                           "se entiende mañana.",
                        },
                        "mensaje": {
                            "type": "string",
                            "description": "Texto del recordatorio (solo "
                                           "para accion='recordatorio').",
                        },
                    },
                    "required": ["accion"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancelar_accion_programada",
                "description": "Cancela la última acción programada (apagado/"
                               "suspensión/recordatorio diferido). Ej: "
                               "'cancelá el apagado'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "Opcional: id de la tarea a "
                                           "cancelar. Si se omite, cancela "
                                           "la última programada.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "controlar_brillo",
                "description": "Sube, baja o fija el brillo de la pantalla.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "accion": {"type": "string",
                                   "enum": ["subir", "bajar", "fijar"]},
                        "valor": {"type": "integer",
                                  "description": "Nivel 0-100 para 'fijar', "
                                                 "o pasos para subir/bajar."},
                    },
                    "required": ["accion"],
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()

    def initialize(self, event_bus: Any = None) -> None:
        """Deja el plugin listo."""
        super().initialize(event_bus)
        logger.info("Plugin energia listo.")

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve las tools de energía, acciones programadas y brillo."""
        if nombre_tool == "control_energia":
            return self.control_energia(str(args.get("accion", "")))
        if nombre_tool == "programar_accion":
            return self.programar_accion(
                str(args.get("accion", "")),
                args.get("en_minutos"),
                str(args.get("mensaje", "") or ""),
                contexto,
                str(args.get("hora", "") or ""))
        if nombre_tool == "cancelar_accion_programada":
            return self.cancelar_accion_programada(args.get("id"), contexto)
        if nombre_tool == "controlar_brillo":
            return self.controlar_brillo(str(args.get("accion", "")),
                                         args.get("valor"))
        return None

    # ---------------- Energía de la PC (apagar/reiniciar/suspender) ---------------- #
    def control_energia(self, accion: str) -> str:
        """Apaga, reinicia o suspende la PC.

        SOLO se ejecuta tras confirmación explícita (ese gating lo hace el
        CommandParser antes de llegar acá). `accion` ∈ {apagar, reiniciar,
        suspender}.
        """
        accion = (accion or "").lower().strip()
        try:
            if accion == "apagar":
                subprocess.run(["shutdown", "/s", "/t", "5"], shell=False,
                               check=True)
                return "Voy a apagar la PC en unos segundos."
            if accion == "reiniciar":
                subprocess.run(["shutdown", "/r", "/t", "5"], shell=False,
                               check=True)
                return "Voy a reiniciar la PC en unos segundos."
            if accion == "suspender":
                subprocess.run(["rundll32", "powrprof.dll,SetSuspendState",
                                "0,1,0"], shell=False, check=True)
                return "Voy a suspender la PC."
            return ("No entendí la acción. Puedo apagar, reiniciar o "
                    "suspender la PC.")
        except Exception as e:  # noqa: BLE001
            logger.error("Error en control_energia(%s): %s", accion, e)
            return "No pude ejecutar la acción de energía."

    # ---------------- Acciones DIFERIDAS (scheduler) ---------------- #
    def programar_accion(self, accion: str, en_minutos: Any, mensaje: str,
                         contexto: Dict[str, Any], hora: str = "") -> str:
        """Programa una acción diferida usando el Scheduler del contexto.

        SOLO se llega acá tras la confirmación del usuario (el gating lo hace
        el CommandParser). `accion` ∈ {apagar, reiniciar, suspender,
        recordatorio}.

        El momento se indica con ``hora`` (HH:MM, a hora exacta) O con
        ``en_minutos`` (dentro de N minutos). Para ``hora``, si la hora ya pasó
        hoy, se asume mañana.

        El scheduler vive en ``contexto["scheduler"]`` (lo inyecta miku/app.py). Si
        no está disponible, devolvemos un mensaje claro en vez de romper.
        """
        accion = (accion or "").lower().strip()
        scheduler = (contexto or {}).get("scheduler")
        if scheduler is None:
            logger.error("No hay scheduler en el contexto; no puedo programar.")
            return "No tengo el programador de tareas disponible ahora."

        # Calculamos el momento: hora exacta O dentro de N minutos.
        etiqueta_momento = ""
        if (hora or "").strip():
            delta = self._segundos_hasta_hora(str(hora))
            if delta is None:
                return (f"No entendí la hora '{hora}'. Usá el formato HH:MM "
                        f"(por ejemplo 18:30).")
            segundos = delta
            etiqueta_momento = f"a las {hora.strip()}"
            minutos_txt = None
        else:
            # Convertimos minutos a segundos de forma robusta.
            try:
                minutos = float(en_minutos)
            except (TypeError, ValueError):
                return ("Decime en cuántos minutos lo programo (por ejemplo, 5) "
                        "o la hora exacta (por ejemplo 18:30).")
            if minutos < 0:
                minutos = 0
            segundos = minutos * 60.0
            minutos_txt = int(minutos) if minutos == int(minutos) else minutos
            etiqueta_momento = f"en {minutos_txt} minutos"

        if accion == "recordatorio":
            texto = mensaje or "un recordatorio"
            descripcion = f"Recordatorio: {texto}"
            callback = self._crear_callback_recordatorio(contexto, texto)
            # Un recordatorio se guarda en disco: sobrevive a cerrar y abrir Miku.
            scheduler.programar(segundos, callback, descripcion,
                                persistente=recordatorios.datos_de(texto))
            return f"Dale, {etiqueta_momento} te aviso: {texto}."

        if accion not in ("apagar", "reiniciar", "suspender"):
            return ("No entendí qué programar. Puedo apagar, reiniciar, "
                    "suspender o poner un recordatorio.")

        mapa = {"apagar": "apago la PC", "reiniciar": "reinicio la PC",
                "suspender": "suspendo la PC"}
        descripcion = f"{accion.capitalize()} diferido"

        def _callback() -> None:
            # Al disparar, ejecutamos la MISMA acción de energía.
            logger.info("[Scheduler] Ejecutando '%s' programado.", accion)
            self.control_energia(accion)

        scheduler.programar(segundos, _callback, descripcion)
        return f"Dale, {etiqueta_momento} {mapa[accion]}."

    def _segundos_hasta_hora(self, hora: str) -> Optional[float]:
        """Convierte una hora HH:MM en segundos desde AHORA (o None si inválida).

        Si la hora indicada ya pasó hoy, se asume MAÑANA (así "recordame a las
        8:00" dicho a las 22:00 suena mañana temprano, que es lo esperable).
        """
        import re as _re
        from datetime import datetime, timedelta

        texto = (hora or "").strip()
        # Formato HH:MM (o HH.MM).
        m = _re.match(r"^(\d{1,2})\s*[:.]\s*(\d{2})$", texto)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
        else:
            # "18h", "18hs" o simplemente "18" (hora en punto).
            m2 = _re.match(r"^(\d{1,2})\s*h(?:s)?$", texto, _re.IGNORECASE)
            if m2:
                hh, mm = int(m2.group(1)), 0
            elif _re.match(r"^\d{1,2}$", texto):
                hh, mm = int(texto), 0
            else:
                return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None

        ahora = datetime.now()
        objetivo = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if objetivo <= ahora:
            objetivo = objetivo + timedelta(days=1)
        return (objetivo - ahora).total_seconds()

    def _crear_callback_recordatorio(self, contexto: Dict[str, Any],
                                     mensaje: str):
        """Devuelve un callback que hace decir el recordatorio por voz.

        El `voice` viene en ``contexto["voice"]`` (lo inyecta miku/app.py). Si no
        hay voz, el recordatorio se imprime por consola igual.
        """
        return recordatorios.fabricar_callback(lambda: (contexto or {}).get("voice"),
                                               recordatorios.datos_de(mensaje))

    def cancelar_accion_programada(self, tarea_id: Any,
                                   contexto: Dict[str, Any]) -> str:
        """Cancela una acción diferida (por id, o la última si no se da id)."""
        scheduler = (contexto or {}).get("scheduler")
        if scheduler is None:
            return "No tengo el programador de tareas disponible ahora."

        pendientes = scheduler.listar_pendientes()
        if not pendientes:
            return "No hay ninguna acción programada para cancelar."

        tarea_id = None if tarea_id in (None, "", 0) else tarea_id
        try:
            tarea_id = int(tarea_id) if tarea_id is not None else None
        except (TypeError, ValueError):
            tarea_id = None

        if scheduler.cancelar(tarea_id):
            return "Listo, cancelé la acción programada."
        return falla("energia.accion_no_encontrada")

    # ---------------- Brillo ---------------- #
    def controlar_brillo(self, accion: str, valor: Optional[int] = None) -> str:
        """Sube, baja o fija el brillo de la pantalla principal."""
        try:
            import screen_brightness_control as sbc  # lazy
        except Exception:  # noqa: BLE001
            return "No tengo control de brillo disponible."

        try:
            actual = sbc.get_brightness(display=0)[0]
        except Exception:  # noqa: BLE001
            return "No pude leer el brillo."

        try:
            if accion == "fijar" and valor is not None:
                nuevo = max(0, min(100, int(valor)))
            elif accion == "subir":
                paso = int(valor or 15)
                nuevo = max(0, min(100, actual + paso))
            elif accion == "bajar":
                paso = int(valor or 15)
                nuevo = max(0, min(100, actual - paso))
            else:
                return "No entendí qué querés hacer con el brillo."
            sbc.set_brightness(nuevo)
            return exito("energia.brillo", pct=nuevo)
        except Exception:  # noqa: BLE001
            return "No pude cambiar el brillo."
