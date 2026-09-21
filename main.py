"""Punto de entrada de Miku Assistant:  python main.py

Antes de importar nada pesado se comprueba si YA hay una Miku corriendo: en ese caso se la invoca
(saluda y escucha un comando) y este proceso termina enseguida. Así, apretar la tecla de invocación (F13) con Miku abierta
es instantáneo, y nunca se abren dos.

Toda la lógica vive en el paquete ``miku`` (ver miku/app.py).
"""


def _principal() -> None:
    from miku.servicios.instancia import InstanciaUnica

    instancia = InstanciaUnica()
    if not instancia.adquirir():
        instancia.invocar_existente()
        return
    from miku.app import main
    main(instancia)


if __name__ == "__main__":
    _principal()
