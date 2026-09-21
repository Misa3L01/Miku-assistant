"""
red.py - Cliente HTTP compartido con conexiones persistentes (keep-alive).

Cada ``requests.post()`` suelto abre una conexión nueva (TCP + TLS: 100-300 ms contra una API en la
nube). En el camino de una orden de voz hay varias llamadas al mismo servidor (Whisper, LLM, traducción
para el TTS); con una **sesión compartida** la primera paga el handshake y las siguientes reutilizan
la conexión.

    from miku.plataforma import red
    resp = red.post(url, headers=..., json=..., timeout=20)     # misma firma que requests.post

Detalles:
    * Una sola ``Session`` para todo el proceso (el pool de urllib3 es seguro entre hilos y separa las
      conexiones por servidor).
    * No guarda cookies: son APIs, y así ninguna sesión "ensucia" a otra.
    * Si el servidor cerró una conexión ociosa justo cuando la reutilizábamos, se reintenta UNA vez con una
      conexión nueva. Los timeouts no se reintentan (duplicarían la espera).
"""
from __future__ import annotations

import threading
from http.cookiejar import DefaultCookiePolicy
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

_lock = threading.Lock()
_sesion: Optional[requests.Session] = None


def sesion() -> requests.Session:
    """La sesión compartida (se crea la primera vez que se pide)."""
    global _sesion
    with _lock:
        if _sesion is None:
            s = requests.Session()
            s.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
            adaptador = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
            s.mount("https://", adaptador)
            s.mount("http://", adaptador)
            _sesion = s
        return _sesion


def cerrar() -> None:
    """Cierra las conexiones abiertas (al salir, o para forzar conexiones nuevas)."""
    global _sesion
    with _lock:
        if _sesion is not None:
            _sesion.close()
            _sesion = None


def _pedir(metodo: str, url: str, **kwargs: Any) -> requests.Response:
    """``Session.request`` con un reintento si la conexión reutilizada estaba caída."""
    try:
        return sesion().request(metodo, url, **kwargs)
    except requests.exceptions.Timeout:
        raise
    except requests.exceptions.ConnectionError:
        return sesion().request(metodo, url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    """Igual que ``requests.post`` pero sobre la sesión compartida."""
    return _pedir("POST", url, **kwargs)


def get(url: str, **kwargs: Any) -> requests.Response:
    """Igual que ``requests.get`` pero sobre la sesión compartida."""
    return _pedir("GET", url, **kwargs)
