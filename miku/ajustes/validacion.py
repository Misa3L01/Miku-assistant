"""
validacion.py - Revisa la configuración del usuario contra el esquema.

Detecta, sin lanzar excepciones (es solo un informe):
    - claves mal escritas ("CARPETA_CAPTURA" en vez de "CARPETA_CAPTURAS") con sugerencia,
    - claves obsoletas,
    - valores del tipo equivocado o fuera de los permitidos,
    - plugins a los que les falta configuración para funcionar.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from miku.ajustes.esquema import OBSOLETAS, OPCIONES, REQUISITOS, Opcion

_VERDADEROS = {"1", "true", "sí", "si", "yes", "on", "0", "false", "no", "off"}


@dataclass
class Informe:
    """Resultado de validar la configuración."""

    desconocidas: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    obsoletas: List[Tuple[str, str]] = field(default_factory=list)
    errores: List[str] = field(default_factory=list)
    incompletos: List[Tuple[str, str, List[str]]] = field(default_factory=list)
    #: (para qué, paquete de pip) de las librerías opcionales que faltan y que la config necesita.
    dependencias: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def hay_problemas(self) -> bool:
        """True si hay algo que el usuario debería corregir (no cuenta 'incompletos')."""
        return bool(self.desconocidas or self.obsoletas or self.errores)

    def avisos(self) -> List[str]:
        """Mensajes de advertencia (algo está mal en el archivo del usuario)."""
        lineas: List[str] = []
        for nombre, sugerencia in self.desconocidas:
            extra = f" ¿Quisiste decir {sugerencia.upper()}?" if sugerencia else ""
            lineas.append(f"Opción desconocida en config_local.py: {nombre.upper()}.{extra}")
        for nombre, motivo in self.obsoletas:
            lineas.append(f"Opción obsoleta {nombre.upper()}: {motivo}.")
        lineas.extend(self.errores)
        for para_que, paquete in self.dependencias:
            lineas.append(f"Configuraste {para_que} pero falta la librería: pip install {paquete}")
        return lineas

    def informativos(self) -> List[str]:
        """Mensajes informativos (funciones que quedan sin usar por falta de datos)."""
        lineas = []
        for plugin, para_que, faltan in self.incompletos:
            claves = " / ".join(k.upper() for k in faltan)
            lineas.append(f"Sin configurar '{plugin}' ({para_que}): falta {claves}.")
        return lineas


#: Opciones que activan una función y la librería opcional que esa función necesita:
#: (opción, módulo a importar, paquete de pip, para qué).
_DEPENDENCIAS = (
    ("telegram_bot_token", "telegram", "python-telegram-bot", "el control por Telegram"),
    ("discord_bot_token", "discord", "discord.py", "el bot de Discord"),
    ("tidal_ruta_exe", "tidalapi", "tidalapi", "TIDAL (poné X por nombre)"),
    ("tidal_ruta_exe", "websocket", "websocket-client", "TIDAL (poné X por nombre)"),
    ("brave_ruta_exe", "websocket", "websocket-client", "Brave (buscar en la pestaña actual)"),
)


def dependencias_faltantes(valores: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Librerías opcionales que faltan para las funciones que el usuario configuró."""
    from importlib.util import find_spec
    faltan: List[Tuple[str, str]] = []
    for opcion, modulo, paquete, para_que in _DEPENDENCIAS:
        if _valor_configurado(valores.get(opcion)) and find_spec(modulo) is None                 and (para_que, paquete) not in faltan:
            faltan.append((para_que, paquete))
    return faltan


def _valor_configurado(valor: Any) -> bool:
    """True si el valor cuenta como 'configurado' (no vacío)."""
    if valor is None:
        return False
    if isinstance(valor, str):
        return bool(valor.strip())
    if isinstance(valor, (list, tuple, dict, set)):
        return len(valor) > 0
    return True


def error_de_valor(opcion: Opcion, valor: Any) -> Optional[str]:
    """Devuelve un mensaje si ``valor`` no es válido para ``opcion`` (o None)."""
    nombre = opcion.nombre_local
    if valor is None:
        return None if opcion.default is None else f"{nombre} no puede estar vacío (None)."
    tipo = opcion.tipo
    if tipo in ("entero", "decimal"):
        if isinstance(valor, bool):
            return f"{nombre} debe ser un número, no True/False."
        try:
            float(valor) if tipo == "decimal" else int(float(valor))
        except (TypeError, ValueError):
            return f"{nombre} debe ser un número (tiene {valor!r})."
    elif tipo == "booleano":
        if not isinstance(valor, bool) and str(valor).strip().lower() not in _VERDADEROS:
            return f"{nombre} debe ser True o False (tiene {valor!r})."
    elif tipo == "lista":
        if not isinstance(valor, (list, tuple)):
            return f"{nombre} debe ser una lista [..] (tiene {type(valor).__name__})."
    elif tipo == "mapa":
        if not isinstance(valor, dict):
            return f"{nombre} debe ser un diccionario {{..}} (tiene {type(valor).__name__})."
    elif not isinstance(valor, str):
        return f"{nombre} debe ser texto entre comillas (tiene {type(valor).__name__})."
    if opcion.permitidos and str(valor).strip().lower() not in {str(p).lower() for p in opcion.permitidos}:
        validos = ", ".join(str(p) for p in opcion.permitidos)
        return f"{nombre} debe ser uno de: {validos} (tiene {valor!r})."
    return None


def validar(valores: Dict[str, Any], claves_definidas: Iterable[str],
            claves_estrictas: Optional[Iterable[str]] = None) -> Informe:
    """Valida la configuración del usuario.

    Args:
        valores: Configuración ya combinada (defaults + preferencias + config_local).
        claves_definidas: Claves (minúsculas) que el USUARIO definió (config_local/preferencias).
        claves_estrictas: Claves donde una opción DESCONOCIDA es un error de tipeo (las de
            ``config_local.py``). ``data/preferences.json`` puede guardar datos propios del
            usuario que no son opciones, así que ahí no se avisa. None = todas estrictas.
    """
    informe = Informe()
    estrictas = None if claves_estrictas is None else set(claves_estrictas)
    for clave in sorted(set(claves_definidas)):
        if clave in OBSOLETAS:
            informe.obsoletas.append((clave, OBSOLETAS[clave]))
        elif clave not in OPCIONES:
            if estrictas is not None and clave not in estrictas:
                continue
            cercanas = difflib.get_close_matches(clave, OPCIONES.keys(), n=1, cutoff=0.7)
            informe.desconocidas.append((clave, cercanas[0] if cercanas else None))
        else:
            error = error_de_valor(OPCIONES[clave], valores.get(clave))
            if error:
                informe.errores.append(error)

    for plugin, (para_que, requisitos) in REQUISITOS.items():
        faltan = [alternativas[0] if len(alternativas) == 1 else "/".join(alternativas)
                  for alternativas in requisitos
                  if not any(_valor_configurado(valores.get(k)) for k in alternativas)]
        if faltan:
            informe.incompletos.append((plugin, para_que, faltan))
    informe.dependencias = dependencias_faltantes(valores)
    return informe
