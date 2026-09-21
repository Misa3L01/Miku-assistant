# -*- coding: utf-8 -*-
"""
whatsapp.py - Mandar mensajes y archivos por WhatsApp (WhatsApp Web en un Brave aparte).

    "Mandame la última captura al WhatsApp"     -> al contacto por defecto (WHATSAPP_CONTACTO_DEFAULT)
    "Mandale a mamá por WhatsApp que llego tarde" -> a un contacto de WHATSAPP_CONTACTOS (pide confirmación)
    "Mandale a Mati / al grupo Familia ..."        -> busca ese chat por nombre en tu WhatsApp (pide confirmación)

Cómo funciona: igual que el comedor, usa un Brave **aparte** con su propio perfil
(``data/navegador_whatsapp/``); tu Brave de siempre no se toca. WhatsApp Web guarda la sesión en ese
perfil, así que **el QR se escanea una sola vez**. La cuenta que queda vinculada es la del teléfono que
escanee el QR: mandará los mensajes *desde* ese número.

Puesta en marcha (ver los pasos en ``config_local.py``)::

    python -m miku.plugins.social.whatsapp conectar    # una vez: se abre WhatsApp Web, escaneás el QR
    python -m miku.plugins.social.whatsapp probar      # manda un mensaje de prueba al contacto por defecto

Nota: WhatsApp no ofrece una API para cuentas personales; esto maneja la página web como lo harías
vos. Si WhatsApp cambia su web, puede dejar de encontrar algún botón: en ese caso Miku lo dice.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from miku.ajustes import carga as config_mod
from miku.plataforma.cdp import Navegador, Pagina
from miku.plataforma.texto import normalizar
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla, responder

logger = logging.getLogger("miku.plugins.whatsapp")

URL = "https://web.whatsapp.com/"
#: Con "HeadlessChrome" WhatsApp Web se niega a abrir; también se usa con ventana por consistencia.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/153.0.0.0 Safari/537.36")
#: WhatsApp acepta hasta 16 MB para fotos/videos y 100 MB para documentos; se limita a algo prudente.
MAX_BYTES = 16 * 1024 * 1024
#: Segundos que el chat tiene que llevar sin cambiar de cantidad de mensajes antes de contarlos (se carga de a poco).
_QUIETO = 1.5
_TROZO = 700_000                       # caracteres base64 por mensaje al navegador

# Estados de la página de WhatsApp Web.
CHAT, LISTA, QR, INVALIDO, CARGANDO = "chat", "lista", "qr", "invalido", "cargando"

_JS_ESTADO = r"""(() => {
  const texto = (document.body ? document.body.innerText : '').toLowerCase();
  if (/(no es v[aá]lido|no est[aá] en whatsapp|is invalid|isn't on whatsapp)/.test(texto)
      && !document.querySelector('footer [contenteditable="true"]')) return 'invalido';
  if (document.querySelector('footer [contenteditable="true"], #main [contenteditable="true"]')) return 'chat';
  if (document.querySelector('#pane-side')) return 'lista';
  if (/(escanea|escane[aá]|scan|vincular con el n[uú]mero|link with phone number)/.test(texto)) return 'qr';
  return 'cargando';
})()"""
# Si WhatsApp Web quedó abierto en otra ventana, muestra "WhatsApp está abierto en otra ventana" con un botón
# "Usar aquí": se aprieta para que trabaje esta.
_JS_USAR_AQUI = r"""(() => {
  const texto = (document.body ? document.body.innerText : '').toLowerCase();
  if (!/(abierto en otra ventana|open in another window|abierto en otro)/.test(texto)) return false;
  const boton = [...document.querySelectorAll('button, [role="button"]')]
    .find(b => /^(usar aqu[ií]|use here)$/i.test((b.innerText || '').trim()));
  if (!boton) return false; boton.click(); return true; })()"""
# Carteles que WhatsApp pone encima de la página ("Novedades en WhatsApp Web") y tapan todo hasta apretar "Continuar".
_JS_CERRAR_AVISOS = r"""(() => {
  const boton = [...document.querySelectorAll('div[role="dialog"] button, div[role="dialog"] [role="button"]')]
    .find(b => /^(continuar|entendido|aceptar|ok|continue|got it)$/i.test((b.innerText || '').trim()));
  if (!boton) return false; boton.click(); return true; })()"""
# La búsqueda de chats (lupa de la lista): pone el cursor en la caja y selecciona lo que hubiera escrito.
_JS_FOCO_BUSQUEDA = ("(() => { const i = document.querySelector('#side input[role=\"textbox\"], #side input[type=\"text\"]'); "
                     "if (!i) return false; i.focus(); i.select(); return true; })()")
# Vacía la caja de búsqueda avisándole a la página (una caja "controlada" ignora un cambio que no dispare 'input').
_JS_BORRAR_BUSQUEDA = r"""(() => {
  const i = document.querySelector('#side input[role="textbox"], #side input[type="text"]'); if (!i) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, '');
  i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()"""
# Los resultados vienen en secciones ("Chats", "Contactos", "Grupos en común", "Mensajes"...): una fila con un
# título (h2) abre sección y las que siguen son los resultados. ``i`` es la posición de la fila en la lista.
_JS_RESULTADOS = r"""(() => {
  const g = [...document.querySelectorAll('#side [role="grid"]')].find(x => x.querySelector('h2'));
  if (!g) return [];
  let seccion = ''; const salida = [];
  [...g.querySelectorAll(':scope > [role="row"]')].forEach((f, i) => {
    const h = f.querySelector('h2');
    if (h) { seccion = (h.innerText || '').trim(); return; }
    // El nombre está en un span con title; la primera línea del texto puede ser "1 mensaje no leído" o la inicial del avatar.
    const t = f.querySelector('span[dir="auto"][title]');
    const titulo = (t && t.getAttribute('title')) || (f.innerText || '').split('\n').map(x => x.trim()).filter(Boolean)[0] || '';
    if (titulo) salida.push({i: i, seccion: seccion, titulo: titulo});
  });
  return salida; })()"""
# Dónde está (en pantalla) una fila de resultados, para hacerle un clic de mouse de verdad: WhatsApp no
# reacciona a un ``click()`` de JavaScript en esa lista.
_JS_CENTRO_RESULTADO = r"""(() => {
  const g = [...document.querySelectorAll('#side [role="grid"]')].find(x => x.querySelector('h2'));
  const f = g && g.querySelectorAll(':scope > [role="row"]')[%d];
  if (!f) return null;
  const r = f.getBoundingClientRect();
  return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)}; })()"""
# Nombre del chat abierto (primera línea de la cabecera).
_JS_TITULO_CHAT = (r"(() => { const h = document.querySelector('#main header'); return h ? ((h.innerText || '').split('\n')"
                   r".map(t => t.trim()).filter(Boolean)[0] || '') : ''; })()")
# Identificador del último mensaje del chat: cambia cuando entra uno nuevo. (Contar los mensajes no sirve:
# WhatsApp descarta los viejos de la lista, así que al llegar uno nuevo la cantidad puede quedar igual.)
_JS_ULTIMO_ID = ("(() => { const m = document.querySelectorAll('#main [data-id]'); "
                 "return m.length ? m[m.length - 1].getAttribute('data-id') : ''; })()")
# Verdadero cuando ningún mensaje del chat sigue "pendiente" (subiendo el archivo o sin salir todavía): recién
# entonces se puede cerrar el navegador; si se cierra antes, el mensaje queda trabado con un signo de error.
_JS_SIN_PENDIENTES = ("![...document.querySelectorAll('#main [data-id] title')]"
                      ".some(t => /status-pending|msg-time|ic-close/.test(t.textContent))")
_JS_FOCO_CAJA = ("(() => { const c = document.querySelector('footer [contenteditable=\"true\"]') || "
                 "document.querySelector('#main [contenteditable=\"true\"]'); if (!c) return false; c.focus(); return true; })()")
# WhatsApp guarda como borrador lo que quedó escrito sin enviar: se vacía la caja antes de escribir.
_JS_LIMPIAR_CAJA = ("(() => { const c = document.querySelector('footer [contenteditable=\"true\"]') || "
                    "document.querySelector('#main [contenteditable=\"true\"]'); if (!c) return false; c.focus(); "
                    "document.execCommand('selectAll', false, null); document.execCommand('delete', false, null); "
                    "return true; })()")
_JS_CAJA_VACIA = ("(() => { const c = document.querySelector('footer [contenteditable=\"true\"]') || "
                  "document.querySelector('#main [contenteditable=\"true\"]'); return !!c && c.innerText.trim().length === 0; })()")
_JS_PEGAR = r"""(async () => {
  const bin = atob(window.__mk.join('')); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const dt = new DataTransfer(); dt.items.add(new File([bytes], %(nombre)s, {type: %(mime)s}));
  const caja = document.querySelector('footer [contenteditable="true"]') || document.querySelector('#main [contenteditable="true"]');
  if (!caja) return false; caja.focus();
  caja.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  window.__mk = []; return true; })()"""
# La vista previa de un archivo pegado: hay un botón de enviar cuyo rótulo lleva la cantidad de archivos
# ("Enviar 1 seleccionado"), a diferencia del botón de un texto escrito, que se llama solo "Enviar".
_JS_HAY_VISTA_PREVIA = r"""(() => {
  const icono = document.querySelector('[data-icon="send"], [data-icon="wds-ic-send-filled"]');
  const boton = icono ? icono.closest('button, [role="button"]')
                      : document.querySelector('[aria-label^="Enviar "], [aria-label^="Send "]');
  if (!boton) return false;
  if (/\d/.test(boton.getAttribute('aria-label') || '')) return true;
  return !!document.querySelector('div[role="dialog"], [data-animate-media-viewer], [data-testid="media-caption"]');
})()"""
# El botón verde de enviar (sirve para un texto escrito y para la vista previa de un archivo).
_JS_APRETAR_ENVIAR = r"""(() => {
  const icono = document.querySelector('[data-icon="send"], [data-icon="wds-ic-send-filled"]');
  const boton = (icono && icono.closest('button, [role="button"]')) ||
                document.querySelector('[aria-label="Enviar"], [aria-label="Send"]');
  if (!boton) return false; boton.click(); return true; })()"""


# --------------------------------------------------------------------------- #
# Números
# --------------------------------------------------------------------------- #
def normalizar_numero(texto: Any) -> Optional[str]:
    """Número en el formato de WhatsApp (solo dígitos, con código de país), o None si no parece un número.

    Para Argentina un celular va como ``549`` + código de área + número, sin el ``15``:
    ``+54 3751 123456`` -> ``5493751123456``.
    """
    d = re.sub(r"\D", "", str(texto or ""))
    if len(d) < 8:
        return None
    if d.startswith("549") and len(d) == 13:
        return d
    if d.startswith("54") and len(d) == 12:            # +54 + área + número, sin el 9 de celular
        return "549" + d[2:]
    if len(d) == 10:                                   # área + número
        return "549" + d
    if d.startswith("0") and len(d) == 11:             # 0 + área + número
        return "549" + d[1:]
    return d


def formato_legible(numero: str) -> str:
    """"5493751123456" -> "+54 9 3751 123456" (para leerlo o mostrarlo)."""
    if numero.startswith("549") and len(numero) == 13:
        return f"+54 9 {numero[3:7]} {numero[7:]}"
    return f"+{numero}"


#: Secciones de la búsqueda donde hay chats a los que escribir (se ignoran "Mensajes", "Multimedia"...).
_SECCIONES_CHATS = re.compile(r"^(chats?|contactos?|contacts?|grupos?|groups?)\b")
#: "el grupo Familia" -> "Familia" (el nombre real del chat no suele llevar esas palabras).
_PREFIJO_CHAT = re.compile(r"^(?:(?:el|la|al)\s+)?(?:grupo|chat)\s+(?:(?:de la|del|de)\s+)?", re.IGNORECASE)


def _palabras(texto: Any) -> List[str]:
    return re.findall(r"\w+", normalizar(str(texto or "")))


def puntaje_titulo(consulta: str, titulo: str) -> int:
    """Qué tan bien coincide el nombre de un chat con lo pedido: 3 mismo nombre, 2 el nombre empieza con
    lo pedido ("Mati" -> "Mati Rojas"), 1 lo pedido aparece como palabras enteras ("Ana (Mati)"), 0 no
    ("Matias" NO es "Mati")."""
    q, t = _palabras(consulta), _palabras(titulo)
    if not q or not t:
        return 0
    if q == t:
        return 3
    if t[:len(q)] == q:
        return 2
    return 1 if all(p in t for p in q) else 0


def elegir_chat(consulta: str, filas: List[Dict[str, Any]]) -> tuple:
    """Elige a qué chat mandar entre los resultados de la búsqueda de WhatsApp.

    Devuelve ``("ok", fila)`` si hay uno solo que gane, ``("ambiguo", [filas])`` si empatan varios
    (no se manda: se le pregunta al usuario) o ``("ninguno", [])``. Nunca adivina entre empates.
    """
    vistos: set = set()
    candidatos: List[Dict[str, Any]] = []
    for f in filas:
        if not _SECCIONES_CHATS.match(normalizar(str(f.get("seccion", "")))):
            continue
        clave = " ".join(_palabras(f.get("titulo", "")))
        if clave and clave not in vistos:                  # la misma persona sale como chat y como contacto
            vistos.add(clave)
            candidatos.append(f)
    puntuados = [(puntaje_titulo(consulta, str(f.get("titulo", ""))), f) for f in candidatos]
    mejor = max((p for p, _ in puntuados), default=0)
    if mejor == 0:
        return "ninguno", []
    ganadores = [f for p, f in puntuados if p == mejor]
    return ("ok", ganadores[0]) if len(ganadores) == 1 else ("ambiguo", ganadores)


class WhatsApp(Plugin):
    """Manda mensajes y archivos por WhatsApp Web."""

    nombre = "whatsapp"
    descripcion = "Envía mensajes y archivos por WhatsApp (WhatsApp Web en un navegador aparte)."
    peligrosas = frozenset({"enviar_whatsapp_a_contacto"})

    tools: List[dict] = [
        {"type": "function", "function": {
            "name": "enviar_a_whatsapp",
            "description": "Manda un mensaje y/o un archivo por WhatsApp al contacto por defecto del usuario "
                           "(su otro número). Ej: 'mandame la última captura al WhatsApp', 'enviame por "
                           "WhatsApp que compre leche', 'pasame informe.pdf al WhatsApp'.",
            "parameters": {"type": "object", "properties": {
                "mensaje": {"type": "string", "description": "Texto a enviar (opcional si hay archivo)."},
                "archivo": {"type": "string", "description": "'ultima captura', 'ultima descarga', una ruta o "
                                                             "el nombre de un archivo (opcional)."}}}}},
        {"type": "function", "function": {
            "name": "enviar_whatsapp_a_contacto",
            "description": "Manda un mensaje y/o archivo por WhatsApp a OTRA persona o a un GRUPO, buscándolo por "
                           "nombre entre los chats del usuario. Es la vía por defecto para escribirle a alguien si el "
                           "usuario no dice por qué app. Ej: 'mandale a Mati que llego tarde', 'mandale a mamá por "
                           "WhatsApp que ya voy', 'mandá al grupo Familia que confirmo'.",
            "parameters": {"type": "object", "properties": {
                "contacto": {"type": "string", "description": "Nombre de la persona o del grupo, tal como lo dijo el "
                                                              "usuario y sin las palabras 'el grupo'."},
                "mensaje": {"type": "string", "description": "Texto a enviar (opcional si hay archivo)."},
                "archivo": {"type": "string", "description": "Archivo a enviar (opcional)."}},
                "required": ["contacto"]}}},
        {"type": "function", "function": {
            "name": "conectar_whatsapp",
            "description": "Abre WhatsApp Web para vincular la cuenta escaneando un código QR (una sola vez). "
                           "Ej: 'conectá WhatsApp', 'vinculá el WhatsApp'.",
            "parameters": {"type": "object", "properties": {}}}},
    ]

    def __init__(self) -> None:
        super().__init__()
        self._bus: Any = None
        self._lock = threading.Lock()
        self._hilo: Optional[threading.Thread] = None
        #: Nombre real del chat al que se mandó cuando se lo buscó por nombre (para decirlo al avisar).
        self.ultimo_chat = ""

    def initialize(self, event_bus: Any = None) -> None:
        super().initialize(event_bus)
        self._bus = event_bus
        logger.info("Plugin whatsapp listo (contacto por defecto: %s).",
                    "configurado" if config_mod.config.get("whatsapp_contacto_default") else "sin configurar")

    # ---------------- Despacho ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        mensaje, archivo = str(args.get("mensaje") or "").strip(), str(args.get("archivo") or "").strip()
        if nombre_tool == "enviar_a_whatsapp":
            return self.enviar_en_segundo_plano(None, mensaje, archivo)
        if nombre_tool == "enviar_whatsapp_a_contacto":
            return self.enviar_en_segundo_plano(str(args.get("contacto") or ""), mensaje, archivo)
        if nombre_tool == "conectar_whatsapp":
            return self.conectar_en_segundo_plano()
        return None

    # ---------------- Destinatario y archivo ----------------
    def destino(self, contacto: Optional[str]) -> Optional[str]:
        """Número de WhatsApp del destinatario: el por defecto, o el contacto pedido (None si no existe)."""
        cfg = config_mod.config
        if not contacto:
            return normalizar_numero(cfg.get("whatsapp_contacto_default", ""))
        contactos = cfg.get("whatsapp_contactos", {}) or {}
        clave = normalizar(contacto).strip()
        if isinstance(contactos, dict):
            for nombre, numero in contactos.items():
                if normalizar(str(nombre)).strip() == clave:
                    return normalizar_numero(numero)
        return None

    @staticmethod
    def resolver_archivo(pedido: str) -> Optional[Path]:
        """Misma resolución que el envío por Telegram (última captura, última descarga, ruta o nombre)."""
        from miku.plugins.social.telegram_envio import TelegramEnvio
        return TelegramEnvio().resolver_archivo(pedido)

    # ---------------- Navegador ----------------
    def _navegador(self, ventana: Optional[str] = None) -> Optional[Navegador]:
        cfg = config_mod.config
        exe = str(cfg.get("brave_ruta_exe", "") or "").strip()
        if not exe:
            return None
        try:
            puerto = int(cfg.get("whatsapp_puerto", 9225))
        except (TypeError, ValueError):
            puerto = 9225
        modo = (ventana or str(cfg.get("whatsapp_ventana", "minimizada") or "minimizada")).strip().lower()
        return Navegador(exe, config_mod.BASE_DIR / "data" / "navegador_whatsapp", puerto,
                         visible=modo != "oculta", minimizada=modo == "minimizada", user_agent=USER_AGENT)

    @staticmethod
    def estado(pagina: Pagina) -> str:
        """``chat``, ``lista``, ``qr``, ``invalido`` o ``cargando``."""
        return str(pagina.evaluar(_JS_ESTADO) or CARGANDO)

    def _esperar_estado(self, pagina: Pagina, segundos: float, aceptables: tuple) -> str:
        """Espera a que la página esté en uno de los ``aceptables``; devuelve el estado con el que terminó."""
        limite = time.monotonic() + segundos
        estado = CARGANDO
        while time.monotonic() < limite:
            estado = self.estado(pagina)
            if estado in aceptables:
                return estado
            if estado == CARGANDO and pagina.evaluar(_JS_USAR_AQUI):
                logger.info("WhatsApp estaba abierto en otra ventana: se aprieta 'Usar aquí'.")
            time.sleep(0.7)
        return estado

    # ---------------- Envío ----------------
    def enviar_en_segundo_plano(self, contacto: Optional[str], mensaje: str, archivo: str) -> str:
        """Valida y empieza el envío en un hilo; el resultado se avisa cuando termina."""
        cfg = config_mod.config
        if not str(cfg.get("brave_ruta_exe", "") or "").strip():
            return falla("whatsapp.sin_navegador")
        if not contacto and self.destino(None) is None:
            return falla("whatsapp.sin_numero")
        if not mensaje and not archivo:
            return falla("whatsapp.sin_contenido")
        if archivo and self.resolver_archivo(archivo) is None:
            return falla("whatsapp.archivo_no_encontrado", archivo=archivo)
        if self._hilo is not None and self._hilo.is_alive():
            return falla("whatsapp.en_curso")
        self._hilo = threading.Thread(target=self._tramite_envio, args=(contacto, mensaje, archivo),
                                      daemon=True, name="miku_whatsapp")
        self._hilo.start()
        return exito("whatsapp.enviando", a=f" a {contacto}" if contacto else "")

    def _tramite_envio(self, contacto: Optional[str], mensaje: str, archivo: str) -> None:
        estado, detalle = self.enviar(contacto, mensaje, archivo)
        a = f" a {self.ultimo_chat or contacto}" if contacto else ""
        self._avisar(f"whatsapp.{estado}", estado == "enviado", detalle=detalle, a=a, contacto=contacto or "")

    def enviar(self, contacto: Optional[str], mensaje: str, archivo: str = "",
               navegador: Optional[Navegador] = None) -> tuple:
        """Manda el mensaje y/o archivo. Devuelve ``(estado, detalle)``; bloquea ~20 s."""
        self.ultimo_chat = ""
        numero = self.destino(contacto)
        por_nombre = bool(contacto) and numero is None          # no está en WHATSAPP_CONTACTOS: se busca en los chats
        if numero is None and not por_nombre:
            return "sin_numero", ""
        ruta: Optional[Path] = None
        if archivo:
            ruta = self.resolver_archivo(archivo)
            if ruta is None:
                return "archivo_no_encontrado", archivo
            if ruta.stat().st_size > MAX_BYTES:
                return "archivo_grande", f"{ruta.name} ({ruta.stat().st_size // (1024 * 1024)} MB)"
        if not self._lock.acquire(blocking=False):
            return "en_curso", ""
        # Para buscar por nombre el navegador va SIN ventana: con la ventana minimizada o tapada la página queda
        # "oculta" y WhatsApp no llena la lista de resultados de la búsqueda.
        nav = navegador or self._navegador("oculta" if por_nombre else None)
        try:
            if nav is None or not nav.abrir():
                return "sin_navegador", ""
            pagina = nav.pagina()
            if pagina is None:
                return "sin_navegador", ""
            if por_nombre:
                pagina.ir(URL, 30)
                estado = self._esperar_estado(pagina, 60, (LISTA, CHAT, QR))
                if estado == QR:
                    return "sin_sesion", ""
                if estado not in (LISTA, CHAT):
                    return "no_cargo", ""
                estado, detalle = self._abrir_chat_por_nombre(pagina, str(contacto))
                if estado != "ok":
                    return estado, detalle
                self.ultimo_chat = detalle
            else:
                pagina.ir(f"{URL}send?phone={numero}", 30)
                estado = self._esperar_estado(pagina, 60, (CHAT, QR, INVALIDO))
                if estado == QR:
                    return "sin_sesion", ""
                if estado == INVALIDO:
                    return "numero_invalido", formato_legible(str(numero))
                if estado != CHAT:
                    return "no_cargo", ""
            self._esperar_chat_quieto(pagina)
            pagina.evaluar(_JS_LIMPIAR_CAJA)
            if mensaje and not self._mandar_texto(pagina, mensaje):
                return "no_envio", "el texto"
            if ruta is not None and not self._mandar_archivo(pagina, ruta):
                return "no_envio", ruta.name
            return "enviado", ruta.name if ruta else ""
        except Exception as e:  # noqa: BLE001
            logger.exception("Falló el envío por WhatsApp.")
            return "error", type(e).__name__
        finally:
            try:
                time.sleep(1.0)                          # que WhatsApp termine de guardar antes de cerrar
                if nav is not None:
                    nav.cerrar()
            finally:
                self._lock.release()

    def _esperar_chat_quieto(self, pagina: Pagina, maximo: float = 10.0) -> str:
        """Espera a que termine de cargar el historial del chat y devuelve el id de su último mensaje.

        Al abrir un chat WhatsApp va agregando mensajes de a poco: si se mirara enseguida, esa carga se
        confundiría con un mensaje nuestro recién enviado.
        """
        limite = time.monotonic() + maximo
        ultimo, desde = self._ultimo_id(pagina), time.monotonic()
        while time.monotonic() < limite and time.monotonic() - desde < _QUIETO:
            time.sleep(0.3)
            actual = self._ultimo_id(pagina)
            if actual != ultimo:
                ultimo, desde = actual, time.monotonic()
        return ultimo

    # ---------------- Buscar un chat por nombre ----------------
    def _leer_resultados(self, pagina: Pagina, maximo: float = 8.0) -> List[Dict[str, Any]]:
        """Espera a que la búsqueda termine de mostrar resultados (aparecen por tandas) y los devuelve."""
        inicio = time.monotonic()
        ultimo: List[Dict[str, Any]] = []
        desde = time.monotonic()
        while time.monotonic() - inicio < maximo:
            time.sleep(0.4)
            actual = pagina.evaluar(_JS_RESULTADOS) or []
            if actual != ultimo:
                ultimo, desde = actual, time.monotonic()
            elif ultimo and time.monotonic() - desde >= _QUIETO:
                break
            elif not ultimo and time.monotonic() - inicio >= 4.0:
                break                                        # no hay resultados: no esperar todo el tiempo
        return ultimo

    @staticmethod
    def _clic(pagina: Pagina, x: int, y: int) -> None:
        """Un clic de mouse de verdad en (x, y) de la página."""
        for tipo in ("mouseMoved", "mousePressed", "mouseReleased"):
            pagina.llamar("Input.dispatchMouseEvent", {
                "type": tipo, "x": x, "y": y, "button": "none" if tipo == "mouseMoved" else "left",
                "buttons": 1 if tipo == "mousePressed" else 0, "clickCount": 0 if tipo == "mouseMoved" else 1})

    @staticmethod
    def _borrar_busqueda(pagina: Pagina) -> None:
        pagina.evaluar(_JS_BORRAR_BUSQUEDA)

    def _buscar(self, pagina: Pagina, consulta: str, intentos: int = 3) -> List[Dict[str, Any]]:
        """Escribe ``consulta`` en la lupa y devuelve los resultados. A veces la página no reacciona a la primera
        (se estaba cargando): se borra y se vuelve a escribir."""
        filas: List[Dict[str, Any]] = []
        time.sleep(1.5)                       # recién cargada la lista, las filas de resultados salen vacías
        for _ in range(intentos):
            pagina.evaluar(_JS_CERRAR_AVISOS)
            if not pagina.evaluar(_JS_FOCO_BUSQUEDA):
                return []
            pagina.llamar("Input.insertText", {"text": consulta})
            filas = self._leer_resultados(pagina)
            if filas:
                break
            self._borrar_busqueda(pagina)
            time.sleep(1.0)
        return filas

    def _abrir_chat_por_nombre(self, pagina: Pagina, nombre: str) -> tuple:
        """Busca ``nombre`` en la lupa de WhatsApp Web y abre el chat. ``("ok", titulo)``, ``("ambiguo", nombres)``
        (empate: no se manda) o ``("no_encontrado", nombre)``."""
        consultas = [nombre.strip()]
        sin_prefijo = _PREFIJO_CHAT.sub("", nombre.strip()).strip()
        if sin_prefijo and sin_prefijo.lower() != nombre.strip().lower():
            consultas.append(sin_prefijo)
        for consulta in consultas:
            pagina.evaluar(_JS_CERRAR_AVISOS)
            if not pagina.evaluar(_JS_FOCO_BUSQUEDA):
                return "no_cargo", ""
            # Con una segunda variante ("Familia" tras "el grupo Familia") no se reintenta la primera: rara vez existe.
            intentos = 3 if consulta is consultas[-1] else 1
            veredicto, dato = elegir_chat(consulta, self._buscar(pagina, consulta, intentos))
            if veredicto == "ambiguo":
                return "ambiguo", ", ".join(str(f["titulo"]) for f in dato[:4])
            if veredicto != "ok":
                continue
            titulo = str(dato["titulo"])
            centro = pagina.evaluar(_JS_CENTRO_RESULTADO % int(dato["i"]))
            if not isinstance(centro, dict):
                return "no_cargo", ""
            self._clic(pagina, int(centro["x"]), int(centro["y"]))
            limite = time.monotonic() + 15.0
            while time.monotonic() < limite:                 # se comprueba que se abrió EL chat elegido
                if _palabras(pagina.evaluar(_JS_TITULO_CHAT)) == _palabras(titulo):
                    return "ok", titulo
                time.sleep(0.4)
            return "no_cargo", ""
        return "no_encontrado", nombre

    @staticmethod
    def _ultimo_id(pagina: Pagina) -> str:
        return str(pagina.evaluar(_JS_ULTIMO_ID) or "")

    def _esperar_mensaje_nuevo(self, pagina: Pagina, antes: str, segundos: float = 20.0, subida: float = 30.0) -> bool:
        """True cuando entra un mensaje nuevo al chat Y termina de enviarse (deja de estar "pendiente").

        Un archivo aparece enseguida en el chat pero se sube después: cerrar el navegador antes de que
        termine lo deja trabado sin llegar nunca.
        """
        if not pagina.esperar(f"{_JS_ULTIMO_ID} !== {json.dumps(antes)}", segundos):
            return False
        return pagina.esperar(_JS_SIN_PENDIENTES, subida)

    def _mandar_texto(self, pagina: Pagina, texto: str) -> bool:
        """Escribe el texto en la caja del chat y lo envía. True si aparece como mensaje enviado."""
        antes = self._ultimo_id(pagina)
        if not pagina.evaluar(_JS_FOCO_CAJA):
            return False
        pagina.llamar("Input.insertText", {"text": texto})
        time.sleep(0.4)
        if not pagina.evaluar(_JS_APRETAR_ENVIAR):        # el Enter por CDP no siempre lo toma la página
            self._apretar_enter(pagina)
        return self._esperar_mensaje_nuevo(pagina, antes)

    @staticmethod
    def _apretar_enter(pagina: Pagina) -> None:
        for tipo in ("rawKeyDown", "char", "keyUp"):
            params = {"type": tipo, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                      "nativeVirtualKeyCode": 13}
            if tipo == "char":
                params["text"] = "\r"
            pagina.llamar("Input.dispatchKeyEvent", params)

    def _mandar_archivo(self, pagina: Pagina, ruta: Path) -> bool:
        """Pega el archivo en el chat (como si lo copiaras y pegaras) y lo envía desde la vista previa."""
        antes = self._ultimo_id(pagina)
        datos = base64.b64encode(ruta.read_bytes()).decode("ascii")
        pagina.evaluar("window.__mk = []; true")
        for i in range(0, len(datos), _TROZO):
            pagina.evaluar(f"window.__mk.push({json.dumps(datos[i:i + _TROZO])}); true")
        mime = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        js = _JS_PEGAR % {"nombre": json.dumps(ruta.name), "mime": json.dumps(mime)}
        if not pagina.evaluar(js):
            return False
        if not pagina.esperar(_JS_HAY_VISTA_PREVIA, 20):
            return False
        time.sleep(0.8)                                   # que cargue la miniatura
        if not pagina.evaluar(_JS_APRETAR_ENVIAR):
            self._apretar_enter(pagina)                   # último recurso: Enter con el foco en la descripción
        return self._esperar_mensaje_nuevo(pagina, antes, 40.0, 120.0)      # subir hasta 16 MB puede tardar

    # ---------------- Conectar (QR) ----------------
    def conectar_en_segundo_plano(self) -> str:
        if not str(config_mod.config.get("brave_ruta_exe", "") or "").strip():
            return falla("whatsapp.sin_navegador")
        if self._hilo is not None and self._hilo.is_alive():
            return falla("whatsapp.en_curso")
        self._hilo = threading.Thread(target=self._tramite_conexion, daemon=True, name="miku_whatsapp_qr")
        self._hilo.start()
        return exito("whatsapp.conectando")

    def _tramite_conexion(self) -> None:
        ok = self.conectar()
        self._avisar("whatsapp.conectado" if ok else "whatsapp.conexion_fallida", ok)

    def conectar(self, espera: float = 240.0, navegador: Optional[Navegador] = None) -> bool:
        """Abre WhatsApp Web (ventana visible) y espera a que se escanee el QR. True si quedó vinculado."""
        if not self._lock.acquire(blocking=False):
            return False
        nav = navegador or self._navegador("normal")
        try:
            if nav is None or not nav.abrir():
                return False
            pagina = nav.pagina()
            if pagina is None:
                return False
            pagina.ir(URL, 30)
            estado = self._esperar_estado(pagina, espera, (LISTA, CHAT))
            if estado in (LISTA, CHAT):
                time.sleep(6.0)                       # deja que WhatsApp guarde la sesión en el perfil
                return True
            return False
        finally:
            try:
                if nav is not None:
                    nav.cerrar()
            finally:
                self._lock.release()

    # ---------------- Avisos ----------------
    def _avisar(self, intencion: str, ok: bool, **datos: Any) -> None:
        texto = str(responder(intencion, ok, **datos))
        try:
            from miku.servicios import notificaciones
            notificaciones.notificar("WhatsApp", texto)
        except Exception:  # noqa: BLE001
            logger.debug("Sin toast de WhatsApp.", exc_info=True)
        voz = getattr(self._bus, "voice", None) if self._bus is not None else None
        if voz is not None:
            try:
                voz.decir(texto)
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# Herramientas de línea de comandos
# --------------------------------------------------------------------------- #
def _cli_conectar() -> int:
    config_mod.cargar()
    print("Se abre WhatsApp Web. En el TELÉFONO del número que va a ENVIAR los mensajes:\n"
          "  WhatsApp > Ajustes (o los tres puntos) > Dispositivos vinculados > Vincular un dispositivo,\n"
          "  y escaneá el código QR de la ventana. Tenés 4 minutos.")
    ok = WhatsApp().conectar()
    print("Listo: WhatsApp quedó vinculado." if ok else "No se completó la vinculación (¿venció el QR?). Probá de nuevo.")
    return 0 if ok else 1


def _cli_probar() -> int:
    config_mod.cargar()
    p = WhatsApp()
    numero = p.destino(None)
    if numero is None:
        print("Falta WHATSAPP_CONTACTO_DEFAULT en config_local.py.")
        return 1
    print(f"Mando un mensaje de prueba a {formato_legible(numero)}...")
    estado, detalle = p.enviar(None, "Mensaje de prueba de Miku ✔")
    print(f"Resultado: {estado} {detalle}".strip())
    return 0 if estado == "enviado" else 1


def _cli_explorar() -> int:
    """Vuelca lo que ve en un chat (caja de texto, botones) para ajustar los selectores si WhatsApp cambia."""
    config_mod.cargar()
    p = WhatsApp()
    numero = p.destino(None)
    nav = p._navegador("normal")
    if numero is None or nav is None or not nav.abrir():
        print("Falta WHATSAPP_CONTACTO_DEFAULT o BRAVE_RUTA_EXE.")
        return 1
    try:
        pagina = nav.pagina()
        pagina.ir(f"{URL}send?phone={numero}", 30)
        estado = p._esperar_estado(pagina, 60, (CHAT, QR, INVALIDO))
        info = {"estado": estado, "url": pagina.url().split("?")[0],
                "caja": pagina.evaluar("!!document.querySelector('footer [contenteditable=\"true\"]')"),
                "ultimo_id": pagina.evaluar(_JS_ULTIMO_ID),
                "iconos": pagina.evaluar("[...new Set([...document.querySelectorAll('[data-icon]')].map(e => e.getAttribute('data-icon')))]"),
                "aria": pagina.evaluar("[...new Set([...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label')))].slice(0, 60)")}
        destino = config_mod.BASE_DIR / "data" / "whatsapp_exploracion.json"
        destino.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Listo: {destino}")
        return 0
    finally:
        nav.cerrar()


def _cli_buscar() -> int:
    """``buscar <nombre>``: abre el chat de ese nombre y dice cuál encontró. No manda nada."""
    config_mod.cargar()
    nombre = " ".join(sys.argv[2:]).strip()
    if not nombre:
        print("Uso: python -m miku.plugins.social.whatsapp buscar <nombre de la persona o del grupo>")
        return 2
    p = WhatsApp()
    nav = p._navegador("oculta")                      # igual que al enviar por nombre: sin ventana
    if nav is None or not nav.abrir():
        print("No pude abrir el navegador (¿BRAVE_RUTA_EXE?).")
        return 1
    try:
        pagina = nav.pagina()
        pagina.ir(URL, 30)
        if p._esperar_estado(pagina, 60, (LISTA, CHAT, QR)) == QR:
            print("WhatsApp no está vinculado: primero `... whatsapp conectar`.")
            return 1
        estado, detalle = p._abrir_chat_por_nombre(pagina, nombre)
        print({"ok": f"Abrí el chat: {detalle}", "ambiguo": f"Hay varios: {detalle}",
               "no_encontrado": f"No encontré ningún chat que se llame '{nombre}'."}.get(estado, f"Falló ({estado})."))
        return 0 if estado == "ok" else 1
    finally:
        nav.cerrar()


if __name__ == "__main__":
    comandos = {"conectar": _cli_conectar, "probar": _cli_probar, "explorar": _cli_explorar, "buscar": _cli_buscar}
    elegido = comandos.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if elegido is None:
        print("Uso: python -m miku.plugins.social.whatsapp [conectar | probar | explorar | buscar <nombre>]")
        sys.exit(2)
    sys.exit(elegido())
