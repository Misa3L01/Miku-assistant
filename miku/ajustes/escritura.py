"""
escritura.py - Cambiar opciones de ``config_local.py`` desde el programa (la ventana de configuración).

``config_local.py`` es el archivo que editás a mano, y sigue siéndolo: acá se cambia **solo la línea de
cada opción**, sin tocar el resto (tus comentarios, tus claves, el orden). Así la ventana y el archivo
dicen siempre lo mismo, y lo que elijas en la ventana gana sobre ``preferences.json`` igual que si lo
hubieras escrito vos.

Por cada opción:

    * Si ya está activa (``STT_VAD = True``), se reemplaza esa línea (o líneas, si el valor ocupaba varias).
    * Si está comentada (``# STT_VAD = True``), se le saca el ``#`` y se pone el valor nuevo.
    * Si no aparece, se agrega al final.

Antes de escribir se guarda una copia en ``config_local.py.bak`` y se comprueba que el resultado sea
Python válido: si algo saliera mal, el archivo original queda intacto.
"""
from __future__ import annotations

import ast
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from miku.ajustes.ejemplo import _formatear_valor

logger = logging.getLogger("miku.ajustes.escritura")

#: Encabezado para las opciones que no estaban en el archivo y hay que agregar al final.
_ENCABEZADO = "# ---------- Cambiado desde la ventana de configuración ----------"


def _formatear(valor: Any) -> str:
    """El valor como código Python. Las rutas de Windows van como ``r"C:\\..."`` (se leen igual que
    en el Explorador); con comillas normales habría que duplicar cada barra y es fácil equivocarse a mano.
    """
    if (isinstance(valor, str) and "\\" in valor and '"' not in valor
            and not valor.endswith("\\") and "\n" not in valor):
        return f'r"{valor}"'
    return _formatear_valor(valor)


def _lineas_activas(codigo: str) -> Dict[str, Tuple[int, int]]:
    """``{NOMBRE: (primera línea, última línea)}`` de cada asignación activa (base 0, inclusive)."""
    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return {}
    ubicaciones: Dict[str, Tuple[int, int]] = {}
    for nodo in arbol.body:                                   # solo las de primer nivel
        if isinstance(nodo, ast.Assign):
            for objetivo in nodo.targets:
                if isinstance(objetivo, ast.Name) and objetivo.id.isupper():
                    ubicaciones[objetivo.id] = (nodo.lineno - 1, (nodo.end_lineno or nodo.lineno) - 1)
    return ubicaciones


def aplicar_cambios(codigo: str, cambios: Dict[str, Any]) -> str:
    """Devuelve ``codigo`` con cada opción de ``cambios`` (clave en minúsculas) puesta en su valor.

    Raises:
        ValueError: si el resultado no fuera Python válido (nunca se devuelve un archivo roto).
    """
    lineas = codigo.split("\n")
    al_final: List[str] = []
    # De abajo hacia arriba: así reemplazar una asignación de varias líneas no corre las demás.
    activas = _lineas_activas(codigo)
    pendientes = sorted(cambios.items(), key=lambda kv: -activas.get(kv[0].upper(), (-1, -1))[0])
    for clave, valor in pendientes:
        nombre = clave.upper()
        nueva = f"{nombre} = {_formatear(valor)}"
        if nombre in activas:
            inicio, fin = activas[nombre]
            lineas[inicio:fin + 1] = [nueva]
            continue
        comentada = _buscar_comentada(lineas, nombre)
        if comentada is not None:
            lineas[comentada] = nueva
        else:
            al_final.append(nueva)
    if al_final:
        while lineas and lineas[-1] == "":
            lineas.pop()
        if _ENCABEZADO not in lineas:
            lineas += ["", _ENCABEZADO]
        lineas += al_final
    resultado = "\n".join(lineas).rstrip("\n") + "\n"
    try:
        ast.parse(resultado)
    except SyntaxError as e:
        raise ValueError(f"El cambio dejaría config_local.py inválido: {e}") from e
    return resultado


def _buscar_comentada(lineas: List[str], nombre: str) -> Optional[int]:
    """Índice de la línea ``# NOMBRE = ...`` (la última, que es la que agrega ``completar``)."""
    patron = re.compile(rf"^\s*#\s*{re.escape(nombre)}\s*=")
    for i in range(len(lineas) - 1, -1, -1):
        if patron.match(lineas[i]):
            return i
    return None


def ruta_por_defecto() -> Path:
    """El ``config_local.py`` del proyecto."""
    from miku.ajustes import carga as config_mod
    return Path(config_mod.BASE_DIR) / "config_local.py"


def guardar(cambios: Dict[str, Any], ruta: Optional[Path] = None, config: Any = None) -> Path:
    """Escribe ``cambios`` en ``config_local.py`` y los aplica en memoria (sin reiniciar Miku).

    Args:
        cambios: ``{clave en minúsculas: valor}``. Solo valores simples (texto, número, sí/no).
        config: la ``Config`` en uso, para que los cambios valgan ya (por defecto, la global).

    Returns:
        La ruta escrita.

    Raises:
        ValueError: si el resultado no fuera Python válido (el archivo no se toca).
        OSError: si no se pudo escribir.
    """
    ruta = Path(ruta or ruta_por_defecto())
    if not cambios:
        return ruta
    if ruta.exists():
        crudo = ruta.read_bytes().decode("utf-8")
        crlf = "\r\n" in crudo
        codigo = crudo.replace("\r\n", "\n")
    else:
        crlf, codigo = True, "# config_local.py - tus opciones (no se sube a git).\n"
    nuevo = aplicar_cambios(codigo, cambios)
    if ruta.exists():
        shutil.copy2(ruta, ruta.with_name(ruta.name + ".bak"))
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_bytes((nuevo.replace("\n", "\r\n") if crlf else nuevo).encode("utf-8"))
    os.replace(temporal, ruta)                                 # atómico: nunca queda a medio escribir
    logger.info("config_local.py: guardé %s.", ", ".join(k.upper() for k in cambios))

    if config is None:
        from miku.ajustes import carga as config_mod
        config = config_mod.config
    for clave, valor in cambios.items():
        config.valores[clave] = valor
        config.origenes[clave] = "config_local"
        config.claves_locales.add(clave)
    return ruta
