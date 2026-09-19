"""Ajustes de Miku: esquema único de opciones, validación y asistente de configuración.

Uso desde la línea de comandos (ver ``__main__``)::

    python -m miku.ajustes estado      # qué tenés configurado, qué falta y qué está mal escrito
    python -m miku.ajustes completar   # agrega a config_local.py las opciones nuevas (comentadas)
    python -m miku.ajustes ejemplo     # regenera config_local.py.example
    python -m miku.ajustes validar     # solo valida (código de salida 1 si hay errores)
"""
