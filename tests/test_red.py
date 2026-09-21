"""Cliente HTTP compartido: reutiliza la conexión (keep-alive), no guarda cookies y reintenta solo lo debido."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from miku.plataforma import red


class _Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # sin esto el servidor cierra la conexión tras cada respuesta

    def _responder(self):
        largo = int(self.headers.get("Content-Length") or 0)
        if largo:
            self.rfile.read(largo)
        cuerpo = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Set-Cookie", "sesion=abc; Path=/")
        self.end_headers()
        self.wfile.write(cuerpo)

    do_GET = do_POST = _responder

    def log_message(self, *args):          # sin ruido en la salida de los tests
        pass


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *a, **k):
        self.conexiones = 0
        super().__init__(*a, **k)

    def get_request(self):
        pedido = super().get_request()
        self.conexiones += 1               # una por conexión TCP aceptada
        return pedido


@pytest.fixture
def servidor():
    srv = _Servidor(("127.0.0.1", 0), _Manejador)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    red.cerrar()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    red.cerrar()


def test_la_sesion_es_una_sola_y_se_recrea_tras_cerrar():
    red.cerrar()
    a = red.sesion()
    assert red.sesion() is a
    red.cerrar()
    assert red.sesion() is not a
    red.cerrar()


def test_varias_llamadas_reutilizan_la_misma_conexion(servidor):
    srv, base = servidor
    for _ in range(3):
        assert red.post(f"{base}/x", json={"a": 1}, timeout=5).json() == {"ok": True}
    assert red.get(f"{base}/y", timeout=5).status_code == 200
    assert srv.conexiones == 1, "el handshake se paga una sola vez"


def test_sin_la_sesion_compartida_cada_llamada_abre_una_conexion(servidor):
    """Comparación: es lo que pasaba antes con ``requests.post`` suelto."""
    srv, base = servidor
    for _ in range(3):
        requests.post(f"{base}/x", json={"a": 1}, timeout=5)
    assert srv.conexiones == 3


def test_no_guarda_cookies(servidor):
    _, base = servidor
    red.get(f"{base}/", timeout=5)
    assert len(red.sesion().cookies) == 0


def test_si_la_conexion_reutilizada_estaba_caida_reintenta_una_vez(monkeypatch):
    llamadas = []

    def request(metodo, url, **kw):
        llamadas.append(metodo)
        if len(llamadas) == 1:
            raise requests.exceptions.ConnectionError("Connection aborted")
        return "respuesta"

    monkeypatch.setattr(red.sesion(), "request", request)
    assert red.post("http://x", json={}) == "respuesta" and llamadas == ["POST", "POST"]


def test_si_falla_dos_veces_lanza_el_error(monkeypatch):
    def request(metodo, url, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(red.sesion(), "request", request)
    with pytest.raises(requests.exceptions.ConnectionError):
        red.get("http://x")


def test_un_timeout_no_se_reintenta(monkeypatch):
    """Reintentar un timeout duplicaría la espera del usuario."""
    llamadas = []

    def request(metodo, url, **kw):
        llamadas.append(1)
        raise requests.exceptions.ReadTimeout("lento")

    monkeypatch.setattr(red.sesion(), "request", request)
    with pytest.raises(requests.exceptions.ReadTimeout):
        red.post("http://x")
    assert len(llamadas) == 1
