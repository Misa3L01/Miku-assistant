"""Asistente de configuración: ``python -m miku.ajustes <estado|completar|ejemplo|validar>``.

Nunca imprime el valor de una opción secreta (claves, tokens): solo si está configurada.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, List

from miku.ajustes import ejemplo, validacion
from miku.ajustes.esquema import GRUPOS, OPCIONES, Opcion, opciones_del_grupo

_ORIGENES = {"config_local": "config_local.py", "preferences": "preferencias", "entorno": "variable de entorno"}


def _raiz() -> Path:
    from miku.ajustes import carga as config_mod
    return Path(config_mod.BASE_DIR)


def _mostrar_valor(opcion: Opcion, valor: Any) -> str:
    """Valor legible; los secretos se ocultan siempre."""
    if opcion.tipo == "secreto":
        return "(configurada)" if valor else "(vacía)"
    if isinstance(valor, (list, tuple)):
        return f"lista de {len(valor)}" if len(valor) > 4 else repr(list(valor))
    if isinstance(valor, dict):
        return f"{len(valor)} entrada(s)"
    return repr(valor) if valor not in ("", None) else "(vacío)"


def comando_estado() -> int:
    """Muestra cada opción: su valor (secretos ocultos), de dónde viene y qué falta."""
    from miku.ajustes import carga as config_mod
    cfg = config_mod.cargar()
    print("Configuración de Miku   ('*' = la definiste vos; sin marca = valor por defecto)\n")
    for clave_grupo, titulo in GRUPOS:
        print(f"== {titulo} ==")
        for o in opciones_del_grupo(clave_grupo):
            origen = cfg.origenes.get(o.clave)
            marca = "*" if origen else " "
            de = f"  <- {_ORIGENES.get(origen, origen)}" if origen else ""
            print(f" {marca} {o.nombre_local:<26} {_mostrar_valor(o, cfg.valores.get(o.clave))}{de}")
        print()
    return comando_validar(mostrar_ok=True)


def comando_validar(mostrar_ok: bool = False) -> int:
    """Valida la configuración y devuelve 1 si hay algo para corregir."""
    from miku.ajustes import carga as config_mod
    cfg = config_mod.cargar()
    informe = validacion.validar(cfg.valores, cfg.origenes.keys(), cfg.claves_locales)
    for linea in informe.avisos():
        print("AVISO:", linea)
    for linea in informe.informativos():
        print("  -", linea)
    if not informe.hay_problemas and mostrar_ok:
        print("La configuración no tiene errores.")
    return 1 if informe.hay_problemas else 0


def comando_completar() -> int:
    """Agrega a config_local.py las opciones que faltan (comentadas) sin tocar lo existente."""
    ruta = _raiz() / "config_local.py"
    if not ruta.exists():
        print("No existe config_local.py. Copiá config_local.py.example como config_local.py primero.")
        return 1
    agregadas = ejemplo.completar(ruta)
    if not agregadas:
        print("config_local.py ya tiene todas las opciones (definidas o comentadas).")
        return 0
    print(f"Agregué {len(agregadas)} opción(es) comentadas al final de config_local.py:")
    print("  " + ", ".join(agregadas))
    print("Copia de seguridad: config_local.py.bak. Descomentá lo que quieras usar.")
    return 0


def comando_ejemplo() -> int:
    """Regenera config_local.py.example desde el esquema."""
    destino = _raiz() / "config_local.py.example"
    destino.write_text(ejemplo.generar_ejemplo(), encoding="utf-8")
    print(f"Regenerado {destino.name} ({len(OPCIONES)} opciones).")
    return 0


def main(argv: List[str] | None = None) -> int:
    """Punto de entrada del asistente."""
    analizador = argparse.ArgumentParser(prog="python -m miku.ajustes", description=__doc__)
    analizador.add_argument("comando", nargs="?", default="estado",
                            choices=("estado", "completar", "ejemplo", "validar"))
    args = analizador.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return {"estado": comando_estado, "completar": comando_completar,
            "ejemplo": comando_ejemplo, "validar": comando_validar}[args.comando]()


if __name__ == "__main__":
    raise SystemExit(main())
