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
``COMEDOR_TIPOS`` (["almuerzo"]), ``COMEDOR_VER`` (mostrar la ventana), ``COMEDOR_ENVIAR_CAPTURA``
(mandar la captura por Telegram).

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
SIN_NAVEGADOR, ERROR, USUARIO_INVALIDO = "sin_navegador", "error", "usuario_invalido"

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
  const celda = c => limpiar(c.innerText || [...c.querySelectorAll('img')].map(
    i => i.alt || i.title || (i.getAttribute('src') || '').split('/').pop().split('?')[0]).join(',')).slice(0, 80);
  const tablas = [...document.querySelectorAll('table')].filter(visible).slice(0, 10).map(
    t => [...t.rows].slice(0, 40).map(r => [...r.cells].map(celda)));
  return {url: sinAh(location.href), titulo: document.title,
          texto: (document.body ? document.body.innerText : '').slice(0, 8000), elementos, tablas};
})()"""
_JS_SIN_DIALOGOS = "window.confirm = () => true; window.alert = () => {}; window.prompt = () => ''; true"
_JS_CLIC_INDICE = ("(() => { const e = document.querySelector('[data-miku-i=\"%d\"]'); "
                   "if (!e) return false; e.scrollIntoView({block: 'center'}); e.click(); return true; })()")

_RE_INSCRIBIR = re.compile(r"\b(inscrib|inscripc|anotar|reservar)")
_RE_ANULAR = re.compile(r"\b(anular|cancelar|baja|desinscrib|dar de baja|eliminar)")
_RE_YA = re.compile(r"(ya (te )?(esta|estas|se encuentra|figura)s? inscript|inscripcion (realizada|confirmada|exitosa)|"
                    r"reserva confirmada)")
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

    accion: str                       # inscribir | ya_inscripto | sin_comidas | no_habilitado | sin_boton | desconocido
    indice: Optional[int] = None      # elemento a apretar (solo con "inscribir")
    detalle: str = ""


_ENCABEZADOS = ("fecha comida", "inscripto")


def _texto_norm(s: Any) -> str:
    return normalizar(str(s or "")).replace("?", "").strip()


def filas_de_comidas(inst: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Las filas de la tabla de comidas como diccionarios ``{columna: texto, "_n": posición}``.

    None si la página no tiene esa tabla (login, error...). Una tabla sin comidas da ``[]``.
    """
    for tabla in inst.get("tablas") or []:
        if not tabla or not isinstance(tabla[0], list):
            continue
        cabecera = [_texto_norm(c) for c in tabla[0]]
        if all(any(h in c for c in cabecera) for h in _ENCABEZADOS):
            filas = []
            for celdas in tabla[1:]:
                if len(celdas) >= 3 and any(str(c).strip() for c in celdas):
                    fila: Dict[str, Any] = {cabecera[i]: str(celdas[i]).strip() for i in range(min(len(cabecera), len(celdas)))}
                    fila["_n"] = len(filas)
                    filas.append(fila)
            return filas
    return None


def _boton_de_la_fila(fila: Dict[str, Any], elementos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """El botón "Inscribirse" de una fila: por el número que lleva en su id (``cuadro..._cuadroN_seleccion``)
    y, si no, por el texto de la fila que lo rodea."""
    n = fila["_n"]
    for e in elementos:
        if re.search(rf"cuadro{n}_seleccion$", str(e.get("id", ""))):
            return e
    fecha, descripcion = fila.get("fecha comida", ""), fila.get("descripcion", "")[:30]
    for e in elementos:
        contexto = str(e.get("contexto", ""))
        if fecha and fecha in contexto and descripcion in contexto and _RE_INSCRIBIR.search(
                normalizar(f"{e.get('texto', '')} {e.get('valor', '')} {e.get('titulo', '')}")):
            return e
    return None


def _fecha_hora(texto: str) -> Optional[datetime]:
    """"20/09/2026 14:00" -> datetime (None si no tiene ese formato)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})", texto or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def decidir(inst: Dict[str, Any], objetivo: date, ahora: Optional[datetime] = None,
            tipos: Optional[List[str]] = None) -> Decision:
    """Decide qué hacer según la instantánea de la página y el día a inscribirse.

    Args:
        tipos: Tipos de comida a inscribir ("almuerzo"); vacío/None = todos los que haya ese día.
    """
    ahora = ahora or datetime.now()
    filas = filas_de_comidas(inst)
    if filas is not None:
        return _decidir_con_tabla(inst, filas, objetivo, ahora, [_texto_norm(t) for t in (tipos or []) if str(t).strip()])
    return _decidir_por_texto(inst, objetivo)


def indice_de_confirmacion(inst: Dict[str, Any]) -> Optional[int]:
    """El botón "Inscribirse" del segundo paso ("¿Inscribirse?  [Inscribirse] [Cancelar]"), o None.

    La página pide confirmar: tras apretar "Inscribirse" en la fila aparece un formulario con
    "Inscribirse" y "Cancelar". Se reconoce porque el botón comparte contexto con un "Cancelar" y con
    la pregunta; nunca se devuelve el "Cancelar".
    """
    elementos = [e for e in (inst.get("elementos") or []) if isinstance(e, dict)]

    def _rotulo(e: Dict[str, Any]) -> str:
        return normalizar(f"{e.get('texto', '')} {e.get('valor', '')} {e.get('titulo', '')}")

    for e in elementos:
        contexto = normalizar(str(e.get("contexto", "")))
        if (_RE_INSCRIBIR.search(_rotulo(e)) and not _RE_ANULAR.search(_rotulo(e)) and not e.get("deshabilitado")
                and "cancelar" in contexto and "?" in str(e.get("contexto", ""))):
            return int(e["i"])
    return None


def _decidir_con_tabla(inst: Dict[str, Any], filas: List[Dict[str, Any]], objetivo: date, ahora: datetime,
                       tipos: List[str]) -> Decision:
    elementos = [e for e in (inst.get("elementos") or []) if isinstance(e, dict)]
    del_dia = [f for f in filas if menciona_fecha(f.get("fecha comida", ""), objetivo)]
    if not del_dia:
        return Decision(SIN_COMIDAS, detalle="no aparece ninguna comida para ese día")
    candidatas = [f for f in del_dia if not tipos or any(t in _texto_norm(f.get("tipo de comida", "")) for t in tipos)]
    if not candidatas:
        cargados = ", ".join(sorted({f.get("tipo de comida", "?") for f in del_dia}))
        return Decision(SIN_COMIDAS, detalle=f"ese día solo hay: {cargados}")

    pendientes: List[Decision] = []
    sin_boton = None
    for fila in candidatas:
        celda = _texto_norm(fila.get("inscripto", ""))
        if celda and celda not in ("no", "0", "false"):
            continue                                               # esta comida ya está inscripta
        desde = _fecha_hora(fila.get("habilitado desde", ""))
        if desde is not None and ahora < desde:
            pendientes.append(Decision(NO_HABILITADO, detalle=f"la inscripción se habilita el {desde:%d/%m} a las {desde:%H:%M}"))
            continue
        boton = _boton_de_la_fila(fila, elementos)
        if boton is None:
            sin_boton = sin_boton or Decision("sin_boton", detalle="la fila no tiene botón de inscripción")
        elif boton.get("deshabilitado"):
            pendientes.append(Decision(NO_HABILITADO, detalle="el botón de inscripción está deshabilitado"))
        else:
            return Decision("inscribir", int(boton["i"]), str(fila.get("descripcion", ""))[:120])
    if pendientes:
        return pendientes[0]
    if sin_boton is not None:
        return sin_boton
    return Decision("ya_inscripto", detalle="la columna Inscripto? ya está marcada")


def _decidir_por_texto(inst: Dict[str, Any], objetivo: date) -> Decision:
    """Respaldo para páginas sin la tabla conocida (menos preciso: nunca inscribe sin ver el día)."""
    texto_n = normalizar(str(inst.get("texto", "")))
    elementos = [e for e in (inst.get("elementos") or []) if isinstance(e, dict)]

    def _rotulo(e: Dict[str, Any]) -> str:
        return normalizar(f"{e.get('texto', '')} {e.get('valor', '')} {e.get('titulo', '')}")

    del_dia = [e for e in elementos if menciona_fecha(str(e.get("contexto", "")), objetivo)]
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
    en_inscripciones = "inscripciones" in normalizar(str(inst.get("titulo", ""))) or "autogestion" in texto_n
    if en_inscripciones and not tiene_fecha and not any(_RE_INSCRIBIR.search(_rotulo(e)) for e in elementos):
        return Decision(SIN_COMIDAS, detalle="no aparece ninguna comida para ese día")
    # Cualquier otra página (login, error, sesión vencida...) no se interpreta como "no hay comida".
    return Decision(DESCONOCIDO, detalle="no reconozco la página")


# --------------------------------------------------------------------------- #
# Credenciales
# --------------------------------------------------------------------------- #
#: Lo que la página del comedor acepta como usuario (lo dice su propio formulario).
_RE_USUARIO = re.compile(r"^[A-Za-z0-9_]+$")


def limpiar_usuario(usuario: Any) -> str:
    """El usuario sin espacios ni comillas sueltas alrededor (``'12345678`` -> ``12345678``).

    Un apóstrofe pegado adelante es un error típico al copiar un número desde una planilla.
    """
    return str(usuario or "").strip().strip("'\"`´ \t")


def usuario_valido(usuario: str) -> bool:
    """True si ``usuario`` tiene solo letras, números y guion bajo (como exige el formulario del comedor)."""
    return bool(_RE_USUARIO.match(usuario or ""))


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


def _lista(valor: Any) -> List[str]:
    """Una lista de textos a partir de lo que haya en la config (lista, texto suelto o nada)."""
    if isinstance(valor, str):
        return [valor] if valor.strip() else []
    return [str(v) for v in (valor or []) if str(v).strip()]


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
        #: Lo que dijo la página cuando el inicio de sesión falló ("Usuario no es válido"...).
        self.motivo_login = ""

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

    #: Resultados que no se arreglan reintentando (falta configurar algo): no se vuelve a probar ese día.
    _NO_REINTENTABLES = ("login_fallo", "usuario_invalido", "sin_clave", "sin_usuario", "sin_navegador")

    def _estado_guardado(self, dia: date) -> Optional[str]:
        """Último resultado guardado para ``dia`` (None si no hay o es de otro día)."""
        try:
            datos = json.loads(self._ruta_estado().read_text(encoding="utf-8"))
            return str(datos.get("estado")) if datos.get("dia") == dia.isoformat() else None
        except (OSError, ValueError, AttributeError):
            return None

    def ya_resuelto(self, dia: date) -> bool:
        """True si para ``dia`` ya quedó inscripto o ya estaba inscripto."""
        return self._estado_guardado(dia) in (INSCRIPTO, YA_INSCRIPTO)

    def puede_reintentar(self, dia: date) -> bool:
        """False si el último intento falló por algo que reintentar no arregla (usuario, contraseña...)."""
        return self._estado_guardado(dia) not in self._NO_REINTENTABLES

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
        if not limpiar_usuario(cfg.get("comedor_usuario", "")):
            return falla("comedor.sin_usuario")
        if not usuario_valido(limpiar_usuario(cfg.get("comedor_usuario", ""))):
            return falla("comedor.usuario_invalido")
        if self._hilo is not None and self._hilo.is_alive():
            return falla("comedor.en_curso")
        self._hilo = threading.Thread(target=self._tramite, args=(automatico,), daemon=True, name="miku_comedor")
        self._hilo.start()
        return exito("comedor.iniciando")

    def _tramite(self, automatico: bool) -> None:
        previo = self._estado_guardado(dia_a_inscribirse())
        r = self.ejecutar()
        # En los reintentos automáticos no se repite el mismo "todavía no hay comida / no está habilitado".
        repetido = automatico and previo == r.estado and r.estado in (SIN_COMIDAS, NO_HABILITADO)
        if not repetido:
            self._avisar(r)
        self._recordar(r)
        if r.estado in (INSCRIPTO, YA_INSCRIPTO):
            if r.captura and config_mod.config.get("comedor_enviar_captura", True):
                self._enviar_captura(r.captura)

    def ejecutar(self, simulacro: bool = False, dia: Optional[date] = None,
                 navegador: Optional[Navegador] = None) -> Resultado:
        """Hace todo el trámite (bloquea ~30 s). Con ``simulacro`` no aprieta "inscribirse"."""
        cfg = config_mod.config
        usuario = limpiar_usuario(cfg.get("comedor_usuario", ""))
        if not usuario:
            return Resultado(SIN_USUARIO)
        if not usuario_valido(usuario):
            return Resultado(USUARIO_INVALIDO)
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
        ventana = str(cfg.get("comedor_ventana", "normal") or "normal").strip().lower()
        if not cfg.get("comedor_ver", True):
            ventana = "oculta"                       # compatibilidad: COMEDOR_VER = False
        return Navegador(exe, config_mod.BASE_DIR / "data" / "navegador_miku", puerto,
                         visible=ventana != "oculta", minimizada=ventana == "minimizada")

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
        # Si el formulario de acceso sigue ahí, las credenciales no sirvieron: se guarda el motivo.
        sigue = bool(pagina.evaluar(f"!!document.querySelector('{_SEL_CLAVE}')"))
        self.motivo_login = ""
        if sigue:
            texto = " ".join(str(pagina.texto(600)).split())
            m = re.search(r"problemas:(.{0,80})", texto)
            self.motivo_login = m.group(1).strip() if m else ""
        return not sigue

    def _con_pagina(self, pagina: Pagina, usuario: str, clave: str, dia: date, simulacro: bool) -> Resultado:
        if not self.iniciar_sesion(pagina, usuario, clave):
            return Resultado(LOGIN_FALLO, self.motivo_login, dia)
        url = str(config_mod.config.get("comedor_url_inscripciones", "") or URL_INSCRIPCIONES)
        if not pagina.ir(url):
            return Resultado(ERROR, "no cargó la página de inscripciones", dia)
        time.sleep(1.0)
        tipos = _lista(config_mod.config.get("comedor_tipos", ["almuerzo"]))
        decision = decidir(pagina.evaluar(_JS_INSTANTANEA) or {}, dia, tipos=tipos)
        carpeta = config_mod.BASE_DIR / "data" / "comedor"
        captura = carpeta / f"comedor_{dia.isoformat()}.png"
        if decision.accion != "inscribir" or simulacro:
            pagina.captura(captura)
            estado = {"inscribir": "listo_para_inscribir"}.get(decision.accion, decision.accion)
            if estado == "sin_boton":
                estado = DESCONOCIDO
            return Resultado(estado, decision.detalle, dia, captura if captura.exists() else None, decision)

        for _ in range(3):                                     # por si ese día hay más de una comida elegida
            pagina.evaluar(_JS_SIN_DIALOGOS)
            if not pagina.evaluar(_JS_CLIC_INDICE % int(decision.indice or 0)):
                return Resultado(ERROR, "no pude apretar el botón de inscripción", dia, decision=decision)
            time.sleep(2.0)
            pagina.esperar("document.readyState === 'complete'", 15)
            # La página pide CONFIRMAR ("¿Inscribirse? [Inscribirse] [Cancelar]"): hay que apretar de nuevo
            # ANTES de volver a cargar nada, o la confirmación pendiente se pierde.
            paso2 = pagina.evaluar(_JS_INSTANTANEA) or {}
            confirmar = indice_de_confirmacion(paso2)
            if confirmar is not None:
                pagina.evaluar(_JS_SIN_DIALOGOS)
                if not pagina.evaluar(_JS_CLIC_INDICE % confirmar):
                    return Resultado(ERROR, "no pude confirmar la inscripción", dia, decision=decision)
                time.sleep(2.0)
                pagina.esperar("document.readyState === 'complete'", 15)
            # Se vuelve a leer la página: la inscripción se confirma mirándola, no dándola por hecha.
            pagina.ir(url)
            time.sleep(1.0)
            despues = pagina.evaluar(_JS_INSTANTANEA) or {}
            self._guardar_instantanea(despues)
            decision = decidir(despues, dia, tipos=tipos)
            if decision.accion != "inscribir":
                break
        pagina.captura(captura)
        confirmada = decision.accion in ("ya_inscripto", "sin_boton")
        estado = INSCRIPTO if confirmada else DESCONOCIDO
        detalle = decision.detalle if not confirmada else ""
        return Resultado(estado, detalle, dia, captura if captura.exists() else None, decision)

    @staticmethod
    def _guardar_instantanea(inst: Dict[str, Any]) -> None:
        """Deja la última lectura de la página en ``data/comedor/`` (para ver cómo quedó tras inscribirse)."""
        try:
            destino = config_mod.BASE_DIR / "data" / "comedor" / "ultima_lectura.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(json.dumps(inst, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            logger.debug("No pude guardar la lectura de la página.", exc_info=True)

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
    usuario = limpiar_usuario(cfg.get("comedor_usuario", "")) or limpiar_usuario(input("Usuario del comedor: "))
    if not usuario_valido(usuario):
        print("El usuario tiene caracteres que la página no acepta (solo letras, números y _). "
              "Revisá COMEDOR_USUARIO en config_local.py.")
        return 1
    print(f"Usuario: {usuario}")
    print("Al escribir la contraseña NO se ve nada en pantalla (ni asteriscos): es normal.")
    clave = getpass.getpass("Contraseña (se pide dos veces): ")
    if not clave:
        print("No escribiste ninguna contraseña.")
        return 1
    if getpass.getpass("Repetí la contraseña: ") != clave:
        print("Las dos contraseñas no coinciden: no guardé nada. Probá de nuevo.")
        return 1
    if not guardar_clave(usuario, clave) or leer_clave(usuario) != clave:
        print("No pude guardarla.")
        return 1
    print(f"Guardada en el Administrador de credenciales de Windows ({len(clave)} caracteres).")
    return 0


def _cli_probar() -> int:
    config_mod.cargar()
    if not limpiar_usuario(config_mod.config.get("comedor_usuario", "")):
        print("Falta COMEDOR_USUARIO en config_local.py.")
        return 1
    r = Comedor().ejecutar(simulacro=True)
    print(f"Estado: {r.estado}\nDetalle: {r.detalle}\nDía: {r.dia}\nCaptura: {r.captura}")
    return 0


def _cli_explorar() -> int:
    """Vuelca la estructura de la página de inscripciones para poder ajustar su lectura."""
    config_mod.cargar()
    cfg = config_mod.config
    usuario = limpiar_usuario(cfg.get("comedor_usuario", ""))
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
