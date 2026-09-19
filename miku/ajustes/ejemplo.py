"""
ejemplo.py - Genera ``config_local.py.example`` y completa el ``config_local.py`` del usuario.

Todo sale del esquema (``esquema.py``), así que la plantilla nunca se desactualiza.

Convención de lo generado:
    - Las CLAVES/cuentas (grupo "claves") aparecen activas y vacías: es lo primero que hay
      que completar.
    - El resto aparece COMENTADO con su valor por defecto: descomentá solo lo que quieras
      cambiar. Una opción comentada significa "uso el valor por defecto".
"""
from __future__ import annotations

import ast
import datetime
import json
import re
import shutil
import textwrap
from pathlib import Path
from typing import Iterable, List, Set

from miku.ajustes.esquema import GRUPOS, OPCIONES, Opcion, opciones_del_grupo

_ANCHO = 88

_ENCABEZADO = '''# -*- coding: utf-8 -*-
r"""
config_local.py.example - PLANTILLA de configuración privada de Miku (NO se sube al repo).

Para usarla: copiá este archivo como ``config_local.py`` (en la raíz del proyecto) y completá
lo que necesites. ``config_local.py`` está en .gitignore porque guarda claves y rutas tuyas.

Reglas simples:
  * Todas las opciones van en MAYÚSCULAS.
  * Una línea que empieza con ``#`` está desactivada: Miku usa el valor por defecto.
    Para cambiar una opción, sacale el ``#`` y poné tu valor.
  * Las rutas de Windows se escriben con una ``r`` delante: r"D:\\Capturas".
  * Para ver qué opciones tenés, cuáles faltan y cuáles no están bien escritas:
        python -m miku.ajustes estado
  * Para agregar a TU archivo las opciones nuevas (como comentarios, sin tocar tus valores):
        python -m miku.ajustes completar

NUNCA pongas valores reales en este archivo .example ni en ningún archivo versionado.
Este archivo se GENERA desde miku/ajustes/esquema.py: no lo edites a mano
(regenerarlo: python -m miku.ajustes ejemplo).
"""
'''


def _formatear_valor(valor) -> str:
    """Valor como código Python (los textos con comillas dobles, como en el resto del proyecto)."""
    if isinstance(valor, str):
        return json.dumps(valor, ensure_ascii=False)
    return repr(valor)


def lineas_de_opcion(opcion: Opcion, activa: bool) -> List[str]:
    """Líneas (comentario + asignación) de una opción."""
    lineas = [f"# {t}" for t in textwrap.wrap(opcion.descripcion, _ANCHO - 2)]
    if opcion.permitidos:
        lineas.append("# Valores válidos: " + ", ".join(str(p) for p in opcion.permitidos))
    if opcion.usado_por:
        lineas.append(f"# (usado por: {opcion.usado_por})")
    valor = _formatear_valor(opcion.default)
    if activa:
        lineas.append(f"{opcion.nombre_local} = {valor}")
    elif opcion.ejemplo and not opcion.default:
        # Sin valor por defecto útil: se muestra un ejemplo realista.
        ejemplo = opcion.ejemplo
        if not ejemplo.startswith(opcion.nombre_local):
            ejemplo = f"{opcion.nombre_local} = {ejemplo}"
        lineas.append(f"# {ejemplo}")
    else:
        lineas.append(f"# {opcion.nombre_local} = {valor}")
    return lineas


def _bloque_grupo(titulo: str, opciones: Iterable[Opcion], activas_en_claves: bool) -> List[str]:
    opciones = list(opciones)
    if not opciones:
        return []
    salida = ["", "# " + "=" * (_ANCHO - 2), f"# {titulo}", "# " + "=" * (_ANCHO - 2), ""]
    for o in opciones:
        salida.extend(lineas_de_opcion(o, activa=activas_en_claves and o.grupo == "claves"
                                       and o.tipo == "secreto"))
        salida.append("")
    return salida


def generar_ejemplo() -> str:
    """Texto completo de ``config_local.py.example``."""
    lineas: List[str] = [_ENCABEZADO.rstrip("\n")]
    for clave_grupo, titulo in GRUPOS:
        lineas.extend(_bloque_grupo(titulo, opciones_del_grupo(clave_grupo), True))
    return "\n".join(lineas).rstrip() + "\n"


# --------------------------------------------------------------------------- completar
def nombres_definidos(codigo: str) -> Set[str]:
    """Nombres en MAYÚSCULAS asignados en el código (sin ejecutarlo)."""
    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return set()
    nombres: Set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign):
            for objetivo in nodo.targets:
                if isinstance(objetivo, ast.Name) and objetivo.id.isupper():
                    nombres.add(objetivo.id)
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            if nodo.target.id.isupper():
                nombres.add(nodo.target.id)
    return nombres


def nombres_comentados(codigo: str) -> Set[str]:
    """Nombres que ya aparecen como ``# NOMBRE = ...`` (para no duplicarlos)."""
    return set(re.findall(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=", codigo, flags=re.MULTILINE))


def opciones_faltantes(codigo: str) -> List[Opcion]:
    """Opciones del esquema que el archivo no define ni menciona comentadas."""
    presentes = nombres_definidos(codigo) | nombres_comentados(codigo)
    return [o for o in OPCIONES.values() if o.nombre_local not in presentes]


def bloque_para_agregar(faltantes: List[Opcion]) -> str:
    """Bloque de texto (todo comentado) para agregar al final de ``config_local.py``."""
    fecha = datetime.date.today().isoformat()
    lineas = [
        "", "",
        "# " + "#" * (_ANCHO - 2),
        f"# Opciones agregadas por `python -m miku.ajustes completar` ({fecha}).",
        "# Están comentadas: descomentá (sacá el '#') solo las que quieras cambiar.",
        "# " + "#" * (_ANCHO - 2),
    ]
    por_grupo = {g: [o for o in faltantes if o.grupo == g] for g, _ in GRUPOS}
    for clave_grupo, titulo in GRUPOS:
        lineas.extend(_bloque_grupo(titulo, por_grupo[clave_grupo], False))
    return "\n".join(lineas).rstrip() + "\n"


def completar(ruta: Path, con_copia: bool = True) -> List[str]:
    """Agrega a ``ruta`` las opciones que faltan, comentadas. No toca nada de lo existente.

    Args:
        ruta: ``config_local.py`` del usuario.
        con_copia: Si True, guarda antes una copia ``config_local.py.bak``.

    Returns:
        Nombres (MAYÚSCULAS) de las opciones agregadas.
    """
    codigo = ruta.read_text(encoding="utf-8")
    faltantes = opciones_faltantes(codigo)
    if not faltantes:
        return []
    if con_copia:
        shutil.copy2(ruta, ruta.with_name(ruta.name + ".bak"))
    ruta.write_text(codigo.rstrip("\n") + "\n" + bloque_para_agregar(faltantes), encoding="utf-8")
    return [o.nombre_local for o in faltantes]
