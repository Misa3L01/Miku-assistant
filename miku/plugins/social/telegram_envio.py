# -*- coding: utf-8 -*-
"""
telegram_envio.py - Mandar archivos por Telegram: "mandame la última captura al Telegram".

Usa el bot que ya tenés configurado (``TELEGRAM_BOT_TOKEN``) con la API HTTP de Telegram, sin
depender de ``python-telegram-bot``. Dos tools:

    enviar_a_telegram(archivo)
        Manda el archivo a **tu** chat (``TELEGRAM_CHAT_ID``). Inofensiva: solo te llega a vos.
    enviar_a_contacto_telegram(archivo, contacto)
        Manda el archivo a otra persona de ``TELEGRAM_CONTACTOS = {"juan": "123456789"}``. Es una
        acción hacia afuera: **pide confirmación** antes de enviar.

Qué se puede pedir como ``archivo``:
    * "ultima captura" / "captura": la captura más reciente de ``CARPETA_CAPTURAS``.
    * "ultima descarga" / "descarga": el archivo más nuevo de la carpeta Descargas.
    * Una ruta completa, o el nombre de un archivo (se busca con Everything si está configurado).

Limitación de Telegram: un bot solo puede escribirle a quien ya le habló (``/start``); por eso los
contactos van por ID numérico y deben haber iniciado el bot alguna vez.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from miku.ajustes import carga as config_mod
from miku.plataforma.texto import normalizar
from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import exito, falla

logger = logging.getLogger("miku.plugins.telegram_envio")

_API = "https://api.telegram.org/bot{token}/{metodo}"
#: Telegram acepta hasta 50 MB por archivo con la API de bots.
_MAX_BYTES = 50 * 1024 * 1024
#: Las imágenes hasta 10 MB se mandan como foto (se ve directo en el chat); el resto, como archivo.
_MAX_FOTO_BYTES = 10 * 1024 * 1024
_EXT_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _mas_nuevo(carpeta: Path, extensiones: Optional[set] = None) -> Optional[Path]:
    """El archivo modificado más recientemente de ``carpeta`` (opcionalmente solo ciertas extensiones)."""
    try:
        candidatos = [p for p in carpeta.iterdir()
                      if p.is_file() and (extensiones is None or p.suffix.lower() in extensiones)
                      and not p.name.startswith(".") and p.suffix.lower() not in (".tmp", ".crdownload", ".part")]
    except OSError:
        return None
    return max(candidatos, key=lambda p: p.stat().st_mtime, default=None)


class TelegramEnvio(Plugin):
    """Manda archivos por Telegram."""

    nombre = "telegram_envio"
    descripcion = "Envía archivos (capturas, descargas...) por Telegram."
    peligrosas = frozenset({"enviar_a_contacto_telegram"})

    tools: List[dict] = [
        {"type": "function", "function": {
            "name": "enviar_a_telegram",
            "description": "Manda un archivo al Telegram del usuario. Ej: 'mandame la última captura al "
                           "Telegram', 'enviame el archivo informe.pdf por Telegram', 'pasame mi última "
                           "descarga al Telegram'.",
            "parameters": {"type": "object", "properties": {
                "archivo": {"type": "string",
                            "description": "'ultima captura', 'ultima descarga', una ruta o el nombre de "
                                           "un archivo. Por defecto, la última captura."}}}}},
        {"type": "function", "function": {
            "name": "enviar_a_contacto_telegram",
            "description": "Manda un archivo por Telegram a OTRA persona (un contacto configurado). Ej: "
                           "'mandale la última captura a Juan por Telegram'.",
            "parameters": {"type": "object", "properties": {
                "archivo": {"type": "string", "description": "'ultima captura', una ruta o un nombre."},
                "contacto": {"type": "string", "description": "Nombre del contacto."}},
                "required": ["contacto"]}}},
    ]

    # ---------------- Despacho ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> Any:
        archivo = str(args.get("archivo") or "ultima captura")
        if nombre_tool == "enviar_a_telegram":
            return self.enviar(archivo, None)
        if nombre_tool == "enviar_a_contacto_telegram":
            return self.enviar(archivo, str(args.get("contacto") or ""))
        return None

    # ---------------- Resolución ----------------
    def resolver_archivo(self, pedido: str) -> Optional[Path]:
        """Convierte lo que dijo el usuario en un archivo real (o None si no se encuentra)."""
        pedido = (pedido or "").strip().strip('"')
        if not pedido:
            return None
        directo = Path(os.path.expandvars(pedido)).expanduser()
        if directo.is_file():
            return directo
        n = normalizar(pedido)
        if "captura" in n or "screenshot" in n:
            return _mas_nuevo(Path(config_mod.config.carpeta_capturas), _EXT_IMAGEN)
        if "descarga" in n:
            return _mas_nuevo(Path.home() / "Downloads")
        return self._buscar_por_nombre(pedido)

    @staticmethod
    def _buscar_por_nombre(nombre: str) -> Optional[Path]:
        """Busca un archivo por nombre con Everything (el más reciente entre los mejores)."""
        try:
            from miku.plataforma.everything import Everything
            motor = Everything()
            ruta_es = motor.ruta_es()
            if not ruta_es:
                return None
            rutas = motor.ejecutar(ruta_es, f"file: {nombre}", 20)
            archivos = [Path(r) for r in rutas if Path(r).is_file()]
            terminos = [t for t in normalizar(nombre).split() if t]
            archivos.sort(key=lambda p: (-motor.puntaje(str(p), terminos), -p.stat().st_mtime))
            return archivos[0] if archivos else None
        except Exception as e:  # noqa: BLE001
            logger.debug("Búsqueda por nombre falló: %s", e)
            return None

    # ---------------- Envío ----------------
    def _destino(self, contacto: Optional[str]) -> Optional[str]:
        """Chat destino: el del usuario, o el ID del contacto pedido (None si no existe)."""
        cfg = config_mod.config
        if not contacto:
            return cfg.telegram_chat_id or None
        contactos = cfg.get("telegram_contactos", {}) or {}
        clave = normalizar(contacto).strip()
        if isinstance(contactos, dict):
            for nombre, chat_id in contactos.items():
                if normalizar(str(nombre)).strip() == clave and str(chat_id).strip():
                    return str(chat_id).strip()
        return None

    def enviar(self, archivo: str, contacto: Optional[str]) -> str:
        """Resuelve el archivo y lo manda a Telegram."""
        cfg = config_mod.config
        token = cfg.telegram_bot_token
        if not token:
            return falla("telegram.sin_token")
        destino = self._destino(contacto)
        if destino is None:
            return falla("telegram.sin_contacto" if contacto else "telegram.sin_chat", contacto=contacto or "")
        ruta = self.resolver_archivo(archivo)
        if ruta is None:
            return falla("telegram.archivo_no_encontrado", archivo=archivo)
        try:
            tamano = ruta.stat().st_size
        except OSError:
            return falla("telegram.archivo_no_encontrado", archivo=archivo)
        if tamano > _MAX_BYTES:
            return falla("telegram.archivo_grande", nombre=ruta.name, mb=round(tamano / 1024 / 1024))

        como_foto = ruta.suffix.lower() in _EXT_IMAGEN and tamano <= _MAX_FOTO_BYTES
        metodo, campo = ("sendPhoto", "photo") if como_foto else ("sendDocument", "document")
        try:
            with open(ruta, "rb") as f:
                resp = requests.post(_API.format(token=token, metodo=metodo), data={"chat_id": destino},
                                     files={campo: (ruta.name, f, mimetypes.guess_type(ruta.name)[0]
                                                    or "application/octet-stream")}, timeout=120)
            datos = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("No pude enviar por Telegram (%s).", type(e).__name__)
            return falla("telegram.error_red")
        if not datos.get("ok"):
            logger.error("Telegram rechazó el envío: %s", datos.get("description"))
            return falla("telegram.rechazado", motivo=str(datos.get("description") or "sin detalle")[:120])
        return exito("telegram.enviado", nombre=ruta.name, a=f" a {contacto}" if contacto else "")
