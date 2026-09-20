# -*- coding: utf-8 -*-
"""
comedor.py - Inscripción automática al comedor de la facultad (SIU-Toba, comedorobera.unam.edu.ar).

"Miku, comedor" -> abre un navegador propio (una ventana de Brave aparte, con su perfil en
``data/navegador_miku/``; tu Brave de todos los días no se toca), inicia sesión con tu usuario, va a
*Autogestión > Inscripciones*, mira si hay comida para **mañana** y, si se puede, te inscribe. Te cuenta
cómo salió y (si querés) te manda la captura por Telegram.

La página no guarda sesiones, así que se inicia sesión **cada vez**. La contraseña NO va en
``config_local.py``: se guarda en el Administrador de credenciales de Windows con::

    python -m miku.plugins.productividad.comedor guardar-clave

Configuración (``config_local.py``): ``COMEDOR_USUARIO`` (obligatorio, activa el plugin),
``COMEDOR_HORA`` (aviso diario, "19:00"), ``COMEDOR_AUTO`` (inscribirte sola a esa hora),
``COMEDOR_VER`` (mostrar la ventana), ``COMEDOR_ENVIAR_CAPTURA`` (mandar la captura por Telegram).

Herramientas para ajustar la lectura de la página (necesitan que la contraseña esté guardada)::

    python -m miku.plugins.productividad.comedor probar     # hace todo menos apretar "inscribirse"
    python -m miku.plugins.productividad.comedor explorar   # vuelca la estructura de la página a data/
"""
from __future__ import annotations

import getpass
import json
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plataforma.cdp import Navegador, Pagina
from miku.plataforma.texto import normalizar
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla, responder

logger = logging.getLogger("miku.plugins.comedor")

SERVICIO_CLAVE = "MikuAssistant-comedor"
URL_INICIO = "https://comedorobera.unam.edu.ar/"
URL_INSCRIPCIONES = ("https://comedorobera.unam.edu.ar/comedorfi/1.0/aplicacion.php"
                     "?tm=1&tcm=central&ai=comedorfi||103000019")
_SEL_USUARIO = "#ef_form_103000002_datosusuario"
_SEL_CLAVE = "#ef_form_103000002_datosclave"
_SEL_INGRESAR = "#form_103000002_datos_ingresar"

# Estados de un intento.
INSCRIPTO, YA_INSCRIPTO, SIN_COMIDAS, NO_HABILITADO = "inscripto", "ya_inscripto", "sin_comidas", "no_habilitado"
DESCONOCIDO, LOGIN_FALLO, SIN_USUARIO, SIN_CLAVE = "desconocido", "login_fallo", "sin_usuario", "sin_clave"
SIN_NAVEGADOR, ERROR = "sin_navegador", "error"

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
          "octubre", "noviembre", "diciembre")
_DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")

# --------------------------------------------------------------------------- #
# JavaScript que se ejecuta en la página
# --------------------------------------------------------------------------- #
_JS_INSTANTANEA = r"""(() => {
  const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const limpiar = s => (s || '').replace(/\s+/g, ' ').trim();
  const sinAh = u => (u || '').replace(/([?&])ah=[^&]*&?/, '$1').replace(/[?&]$/, '');
  const els = [...document.querySelectorAll(
    'button, a, input[type=submit], input[type=button], input[type=image], [onclick]')].filter(visible);
  els.forEach((e, i) => e.setAttribute('data-miku-i', i));
  const elementos = els.slice(0, 300).map((e, i) => ({
    i, tag: e.tagName.toLowerCase(), id: e.id || '', texto: limpiar(e.innerText || '').slice(0, 80),
    valor: limpiar(e.value || '').slice(0, 80), titulo: limpiar(e.title || e.alt || '').slice(0, 80),
    href: sinAh(e.getAttribute('href') || '').slice(0, 160),
    deshabilitado: !!e.disabled || e.classList.contains('disabled') || e.getAttribute('aria-disabled') === 'true',
    contexto: limpiar((e.closest('tr, li') || e.parentElement || e).innerText || '').slice(0, 240)}));
  const tablas = [...document.querySelectorAll('table')].filter(visible).slice(0, 10).map(
    t => [...t.rows].slice(0, 40).map(r => [...r.cells].map(c => limpiar(c.innerText).slice(0, 80))));
  return {url: sinAh(location.href), titulo: document.title,
          texto: (document.body ? document.body.innerText : '').slice(0, 8000), elementos, tablas};
})()"""
_JS_SIN_DIALOGOS = "window.confirm = () => true; window.alert = () => {}; window.prompt = () => ''; true"
_JS_CLIC_INDICE = ("(() => { const e = document.querySelector('[data-miku-i=\"%d\"]'); "
                   "if (!e) return false; e.scrollIntoView({block: 'center'}); e.click(); return true; })()")

_RE_INSCRIBIR = re.compile(r"\b(inscrib|inscripc|anotar|reservar)")
_RE_ANULAR = re.compile(r"\b(anular|cancelar|baja|desinscrib|dar de baja|eliminar)")
_RE_YA = re.compile(r"(ya (te )?(esta|estas|se encuentra|figura)s? inscript|inscripcion (realizada|confirmada|exitosa)|"
                    r"inscripto|inscripta|reserva confirmada)")
_RE_SIN_SERVICIO = re.compile(r"(paro|suspendid|feriado|sin servicio|no habra|no hay servicio|cerrado|no se prestara)")


# --------------------------------------------------------------------------- #
# Lectura de la página (funciones puras: se prueban con instantáneas guardadas)
# --------------------------------------------------------------------------- #
def formas_de_fecha(d: date) -> List[str]:
    """Maneras normalizadas en que una página puede escribir el día ``d``."""
    dia_semana = _DIAS[d.weekday()]
    return [f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day}/{d.month}/{d.year}", f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d}",
            f"{d.day:02d}-{d.month:02d}-{d.year}", f"{d.year}-{d.month:02d}-{d.day:02d}",
            f"{d.day} de {_MESES[d.month - 1]}", f"{dia_semana} {d.day}", f"{dia_semana} {d.day:02d}"]


def menciona_fecha(texto: str, d: date) -> bool:
    """True si ``texto`` nombra el día ``d`` (en cualquiera de sus formas habituales)."""
    n = normalizar(texto)
    return any(f in n for f in formas_de_fecha(d))


@dataclass
class Decision:
    """Qué hacer con la página de inscripciones."""

    accion: str                       # inscribir | ya_inscripto | sin_comidas | no_habilitado | desconocido
    indice: Optional[int] = None      # elemento a apretar (solo con "inscribir")
    detalle: str = ""


def decidir(inst: Dict[str, Any], objetivo: date) -> Decision:
    """Decide qué hacer según la instantánea de la página y el día a inscribirse."""
    texto_n = normalizar(str(inst.get("texto", "")))
    elementos = [e for e in (inst.get("elementos") or []) if isinstance(e, dict)]

    def _rotulo(e: Dict[str, Any]) -> str:
        return normalizar(f"{e.get('texto', '')} {e.get('valor', '')} {e.get('titulo', '')}")

    del_dia = [e for e in elementos if menciona_fecha(str(e.get("contexto", "")), objetivo)]
    # Un botón de "anular/cancelar" en la fila del día = ya estás inscripto.
    if any(_RE_ANULAR.search(_rotulo(e)) for e in del_dia):
        return Decision("ya_inscripto", detalle="hay un botón para anular la inscripción de ese día")
    tiene_fecha = menciona_fecha(texto_n, objetivo)
    if tiene_fecha and _RE_YA.search(texto_n) and not any(
            _RE_INSCRIBIR.search(_rotulo(e)) and not e.get("deshabilitado") for e in del_dia):
        return Decision("ya_inscripto", detalle="la página dice que ya estás inscripto")

    candidatos = [e for e in del_dia if _RE_INSCRIBIR.search(_rotulo(e)) and not _RE_ANULAR.search(_rotulo(e))]
    habilitados = [e for e in candidatos if not e.get("deshabilitado")]
    if habilitados:
        return Decision("inscribir", int(habilitados[0]["i"]), str(habilitados[0].get("contexto", ""))[:120])
    if candidatos:
        return Decision(NO_HABILITADO, detalle="el botón de inscripción está deshabilitado")
    if tiene_fecha and _RE_SIN_SERVICIO.search(texto_n):
        return Decision(NO_HABILITADO, detalle="la página avisa que no hay servicio ese día")
    if not tiene_fecha and not any(_RE_INSCRIBIR.search(_rotulo(e)) for e in elementos):
        return Decision(SIN_COMIDAS, detalle="no aparece ninguna comida para ese día")
    return Decision(DESCONOCIDO, detalle="no reconozco la página")


# --------------------------------------------------------------------------- #
# Credenciales
# --------------------------------------------------------------------------- #
def leer_clave(usuario: str) -> Optional[str]:
    """Contraseña guardada en el Administrador de credenciales de Windows (None si no hay)."""
    try:
        import keyring
        return keyring.get_password(SERVICIO_CLAVE, usuario)
    except Exception as e:  # noqa: BLE001
        logger.debug("No pude leer la clave del comedor: %s", e)
        return None


def guardar_clave(usuario: str, clave: str) -> bool:
    """Guarda la contraseña en el Administrador de credenciales de Windows."""
    try:
        import keyring
        keyring.set_password(SERVICIO_CLAVE, usuario, clave)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("No pude guardar la clave: %s", e)
        return False


# --------------------------------------------------------------------------- #
# El plugin
# --------------------------------------------------------------------------- #
@dataclass
class Resultado:
    """Cómo terminó un intento."""

    estado: str
    detalle: str = ""
    dia: Optional[date] = None
    captura: Optional[Path] = None
    decision: Optional[Decision] = field(default=None, repr=False)


def dia_a_inscribirse(hoy: Optional[date] = None) -> date:
    """El día siguiente."""
    return (hoy or date.today()) + timedelta(days=1)


class Comedor(Plugin):
    """Te inscribe al comedor de la facultad para el día siguiente."""

    nombre = "comedor"
    descripcion = "Inscripción automática al comedor de la facultad (día siguiente)."

    tools: List[dict] = [
        {"type": "function", "function": {
            "name": "inscribir_comedor",
            "description": "Inscribe al usuario al comedor de la facultad para la comida del día siguiente: "
                           "entra a la página del comedor, inicia sesión y se inscribe si se puede. Ej: "
                           "'comedor', 'inscribime al comedor', 'anotame en el comedor'.",
            "parameters": {"type": "object", "properties": {}}}},
    ]

    def __init__(self) -> None:
        super().__init__()
        self._bus: Any = None
        self._lock = threading.Lock()
        self._hilo: Optional[threading.Thread] = None

    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._bus = event_bus
        logger.info("Plugin comedor listo (usuario: %s).",
                    "configurado" if config_mod.config.get("comedor_usuario") else "sin configurar")

    # ---------------- Despacho ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "inscribir_comedor":
            return self.inscribir_en_segundo_plano()
        return None

    # ---------------- Estado del día (para no repetir) ----------------
    @staticmethod
    def _ruta_estado() -> Path:
        return config_mod.BASE_DIR / "data" / "comedor_estado.json"

    def ya_resuelto(self, dia: date) -> bool:
        """True si para ``dia`` ya quedó inscripto o ya estaba inscripto."""
        try:
            datos = json.loads(self._ruta_estado().read_text(encoding="utf-8"))
            return datos.get("dia") == dia.isoformat() and datos.get("estado") in (INSCRIPTO, YA_INSCRIPTO)
        except (OSError, ValueError, AttributeError):
            return False

    def _recordar(self, r: Resultado) -> None:
        if r.dia is None:
            return
        try:
            self._ruta_estado().parent.mkdir(parents=True, exist_ok=True)
            self._ruta_estado().write_text(json.dumps({"dia": r.dia.isoformat(), "estado": r.estado,
                                                       "cuando": datetime.now().isoformat(timespec="seconds")}),
                                           encoding="utf-8")
        except OSError:
            logger.debug("No pude guardar el estado del comedor.", exc_info=True)

    # ---------------- Ejecución ----------------
    def inscribir_en_segundo_plano(self, automatico: bool = False) -> str:
        """Empieza el trámite en un hilo y contesta al toque; el resultado se avisa cuando termina."""
        cfg = config_mod.config
        if not str(cfg.get("comedor_usuario", "") or "").strip():
            return falla("comedor.sin_usuario")
        if self._hilo is not None and self._hilo.is_alive():
            return falla("comedor.en_curso")
        self._hilo = threading.Thread(target=self._tramite, args=(automatico,), daemon=True, name="miku_comedor")
        self._hilo.start()
        return exito("comedor.iniciando")

    def _tramite(self, automatico: bool) -> None:
        r = self.ejecutar()
        self._avisar(r)
        if r.estado in (INSCRIPTO, YA_INSCRIPTO):
            self._recordar(r)
            if r.captura and config_mod.config.get("comedor_enviar_captura", True):
                self._enviar_captura(r.captura)

    def ejecutar(self, simulacro: bool = False, dia: Optional[date] = None,
                 navegador: Optional[Navegador] = None) -> Resultado:
        """Hace todo el trámite (bloquea ~30 s). Con ``simulacro`` no aprieta "inscribirse"."""
        cfg = config_mod.config
        usuario = str(cfg.get("comedor_usuario", "") or "").strip()
        if not usuario:
            return Resultado(SIN_USUARIO)
        clave = leer_clave(usuario)
        if not clave:
            return Resultado(SIN_CLAVE)
        dia = dia or dia_a_inscribirse()
        if not self._lock.acquire(blocking=False):
            return Resultado(ERROR, "ya hay un trámite en curso", dia)
        nav = navegador or self._navegador()
        try:
            if nav is None or not nav.abrir():
                return Resultado(SIN_NAVEGADOR, dia=dia)
            pagina = nav.pagina()
            if pagina is None:
                return Resultado(SIN_NAVEGADOR, dia=dia)
            return self._con_pagina(pagina, usuario, clave, dia, simulacro)
        except Exception as e:  # noqa: BLE001
            logger.exception("Falló el trámite del comedor.")
            return Resultado(ERROR, type(e).__name__, dia)
        finally:
            try:
                nav.cerrar() if nav is not None else None
            finally:
                self._lock.release()

    def _navegador(self) -> Optional[Navegador]:
        cfg = config_mod.config
        exe = str(cfg.get("brave_ruta_exe", "") or "").strip()
        if not exe:
            return None
        try:
            puerto = int(cfg.get("navegador_auto_puerto", 9224))
        except (TypeError, ValueError):
            puerto = 9224
        return Navegador(exe, config_mod.BASE_DIR / "data" / "navegador_miku", puerto,
                         visible=bool(cfg.get("comedor_ver", True)))

    def iniciar_sesion(self, pagina: Pagina, usuario: str, clave: str) -> bool:
        """Entra con usuario y contraseña. True si la página ya no muestra el formulario de acceso."""
        if not pagina.ir(str(config_mod.config.get("comedor_url_inicio", "") or URL_INICIO)):
            return False
        if not pagina.esperar(f"!!document.querySelector('{_SEL_USUARIO}')", 15):
            return False
        pagina.escribir(_SEL_USUARIO, usuario)
        pagina.escribir(_SEL_CLAVE, clave)
        pagina.clic(_SEL_INGRESAR)
        time.sleep(1.0)
        pagina.esperar("document.readyState === 'complete'", 15)
        # Si el formulario de acceso sigue ahí, las credenciales no sirvieron.
        return not pagina.evaluar(f"!!document.querySelector('{_SEL_CLAVE}')")

    def _con_pagina(self, pagina: Pagina, usuario: str, clave: str, dia: date, simulacro: bool) -> Resultado:
        if not self.iniciar_sesion(pagina, usuario, clave):
            return Resultado(LOGIN_FALLO, dia=dia)
        url = str(config_mod.config.get("comedor_url_inscripciones", "") or URL_INSCRIPCIONES)
        if not pagina.ir(url):
            return Resultado(ERROR, "no cargó la página de inscripciones", dia)
        time.sleep(1.0)
        decision = decidir(pagina.evaluar(_JS_INSTANTANEA) or {}, dia)
        carpeta = config_mod.BASE_DIR / "data" / "comedor"
        captura = carpeta / f"comedor_{dia.isoformat()}.png"
        if decision.accion == "inscribir" and not simulacro:
            pagina.evaluar(_JS_SIN_DIALOGOS)
            if not pagina.evaluar(_JS_CLIC_INDICE % int(decision.indice or 0)):
                return Resultado(ERROR, "no pude apretar el botón de inscripción", dia, decision=decision)
            time.sleep(2.0)
            pagina.esperar("document.readyState === 'complete'", 15)
            # Se vuelve a leer la página: si ahora figura como inscripto, salió bien.
            pagina.ir(url)
            time.sleep(1.0)
            despues = decidir(pagina.evaluar(_JS_INSTANTANEA) or {}, dia)
            pagina.captura(captura)
            estado = INSCRIPTO if despues.accion == "ya_inscripto" else DESCONOCIDO
            return Resultado(estado, despues.detalle, dia, captura if captura.exists() else None, despues)
        pagina.captura(captura)
        estado = {"inscribir": "listo_para_inscribir"}.get(decision.accion, decision.accion)
        return Resultado(estado, decision.detalle, dia, captura if captura.exists() else None, decision)

    # ---------------- Avisos ----------------
    def _avisar(self, r: Resultado) -> None:
        """Cuenta cómo terminó (por voz y toast)."""
        datos = {"dia": _dia_hablado(r.dia), "detalle": r.detalle}
        intencion = f"comedor.{r.estado}"
        texto = str(responder(intencion, r.estado in (INSCRIPTO, YA_INSCRIPTO), **datos))
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar("Comedor", texto)
        except Exception:  # noqa: BLE001
            logger.debug("Sin toast del comedor.", exc_info=True)
        voz = getattr(self._bus, "voice", None) if self._bus is not None else None
        if voz is not None:
            try:
                voz.decir(texto)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _enviar_captura(ruta: Path) -> None:
        try:
            from miku.plugins.social.telegram_envio import TelegramEnvio
            if config_mod.config.telegram_bot_token:
                TelegramEnvio().enviar(str(ruta), None)
        except Exception:  # noqa: BLE001
            logger.debug("No pude mandar la captura del comedor.", exc_info=True)


def _dia_hablado(d: Optional[date]) -> str:
    """"mañana" o "el lunes 22"."""
    if d is None:
        return "mañana"
    if d == date.today() + timedelta(days=1):
        return "mañana"
    return f"el {_DIAS[d.weekday()]} {d.day}"


# --------------------------------------------------------------------------- #
# Herramientas de línea de comandos
# --------------------------------------------------------------------------- #
def _cli_guardar_clave() -> int:
    cfg = config_mod.cargar() or config_mod.config
    usuario = str(cfg.get("comedor_usuario", "") or "").strip() or input("Usuario del comedor: ").strip()
    clave = getpass.getpass(f"Contraseña de {usuario} (no se muestra): ")
    if not clave:
        print("No escribiste ninguna contraseña.")
        return 1
    ok = guardar_clave(usuario, clave)
    print("Guardada en el Administrador de credenciales de Windows." if ok else "No pude guardarla.")
    return 0 if ok else 1


def _cli_probar() -> int:
    config_mod.cargar()
    if not str(config_mod.config.get("comedor_usuario", "") or "").strip():
        print("Falta COMEDOR_USUARIO en config_local.py.")
        return 1
    r = Comedor().ejecutar(simulacro=True)
    print(f"Estado: {r.estado}\nDetalle: {r.detalle}\nDía: {r.dia}\nCaptura: {r.captura}")
    return 0


def _cli_explorar() -> int:
    """Vuelca la estructura de la página de inscripciones para poder ajustar su lectura."""
    config_mod.cargar()
    cfg = config_mod.config
    usuario = str(cfg.get("comedor_usuario", "") or "").strip()
    c = Comedor()
    nav = c._navegador()
    if nav is None:
        print("Falta BRAVE_RUTA_EXE en config_local.py.")
        return 1
    nav.visible = True
    if not nav.abrir():
        print("No pude abrir el navegador.")
        return 1
    try:
        pagina = nav.pagina()
        clave = leer_clave(usuario) if usuario else None
        if usuario and clave and c.iniciar_sesion(pagina, usuario, clave):
            pagina.ir(str(cfg.get("comedor_url_inscripciones", "") or URL_INSCRIPCIONES))
        else:
            pagina.ir(URL_INICIO)
            print("Iniciá sesión en la ventana que se abrió y andá a Autogestión > Inscripciones.")
        input("Cuando estés en la página donde aparecen las comidas, apretá Enter acá...")
        inst = pagina.evaluar(_JS_INSTANTANEA) or {}
        destino = config_mod.BASE_DIR / "data" / "comedor_exploracion.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(inst, ensure_ascii=False, indent=2), encoding="utf-8")
        pagina.captura(config_mod.BASE_DIR / "data" / "comedor_exploracion.png")
        print(f"Listo: {destino}\nEse archivo tiene lo que muestra la página (puede incluir tu nombre); "
              f"queda solo en tu PC.")
        return 0
    finally:
        nav.cerrar()


if __name__ == "__main__":
    comandos = {"guardar-clave": _cli_guardar_clave, "probar": _cli_probar, "explorar": _cli_explorar}
    elegido = comandos.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if elegido is None:
        print("Uso: python -m miku.plugins.productividad.comedor [guardar-clave | probar | explorar]")
        sys.exit(2)
    sys.exit(elegido())
