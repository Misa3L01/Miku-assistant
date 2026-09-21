"""Configuración común de los tests.

Regla de oro: los tests NO leen ``config_local.py`` ni ``data/`` del usuario. Usan una
``Config()`` de fábrica (solo defaults) y directorios temporales, así que se pueden correr
en cualquier PC sin claves y sin tocar datos reales.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Qt sin pantalla (bandeja/subtítulos se prueban "offscreen").
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from miku.ajustes import carga as config_mod  # noqa: E402
from miku.servicios.eventos import EventBus  # noqa: E402
from miku.cerebro.parser import CommandParser  # noqa: E402


@pytest.fixture
def cfg() -> "config_mod.Config":
    """Config de fábrica (defaults), sin claves y sin leer archivos del usuario."""
    return config_mod.Config()


class PluginFalso:
    """Plugin mínimo para probar el parser sin depender de los plugins reales."""

    nombre = "falso"
    peligrosas = frozenset({"control_energia"})
    tools = [
        {"type": "function", "function": {"name": "control_energia", "parameters": {}}},
        {"type": "function", "function": {"name": "abrir_programa", "parameters": {}}},
    ]

    def __init__(self) -> None:
        self.llamadas: List[tuple] = []

    def manejar_tool(self, nombre: str, args: Dict[str, Any], contexto: Dict[str, Any]) -> str:
        self.llamadas.append((nombre, args))
        return f"hice {nombre}"


class MemoriaFalsa:
    """Memoria en una lista (sin SQLite)."""

    def __init__(self) -> None:
        self.datos: List[str] = []

    def guardar_recuerdo(self, texto: str) -> None:
        self.datos.append(texto)

    def buscar_recuerdos(self, consulta: str) -> str:
        return ""


class CerebroFalso:
    """Cerebro cuyo resultado se fija desde el test (``resp``).

    Con ``partir_en`` simula el *streaming*: habla esas oraciones por ``al_fragmento`` antes de
    devolver el resultado, igual que el LLM real cuando responde de a poco.
    """

    def __init__(self) -> None:
        self.resp: Dict[str, Any] = {"respuesta": "", "tools_call": []}
        self.partir_en: List[str] = []
        self.recibio_emisor = False

    def consultar(self, texto: str, contexto: Dict[str, Any], tools: List[dict],
                  al_fragmento: Any = None) -> Dict[str, Any]:
        self.recibio_emisor = al_fragmento is not None
        if al_fragmento is not None and self.partir_en:
            for frase in self.partir_en:
                al_fragmento(frase)
            return {**self.resp, "dicho": " ".join(self.partir_en)}
        return self.resp


@pytest.fixture
def plugin() -> PluginFalso:
    return PluginFalso()


@pytest.fixture
def memoria() -> MemoriaFalsa:
    return MemoriaFalsa()


@pytest.fixture
def cerebro() -> CerebroFalso:
    return CerebroFalso()


@pytest.fixture
def parser(cfg, plugin, memoria, cerebro) -> CommandParser:
    bus = EventBus()
    bus.plugins = [plugin]
    return CommandParser(cfg, cerebro, bus, memoria=memoria)


@pytest.fixture
def espacio_de_nombres():
    """Atajo para crear objetos con atributos (``types.SimpleNamespace``)."""
    return types.SimpleNamespace


@pytest.fixture(autouse=True)
def _snapshot_de_modos_aislado(tmp_path, monkeypatch):
    """El snapshot de "salir del modo" se guarda en disco: que ningún test toque ``data/`` real."""
    from miku.servicios import modos
    monkeypatch.setattr(modos, "RUTA_SNAPSHOT", tmp_path / "modo_snapshot.json")
    monkeypatch.setattr(modos, "_snapshot", None)
    monkeypatch.setattr(modos, "_cargado", False)


@pytest.fixture(autouse=True)
def _cache_de_audio_aislada(tmp_path, monkeypatch):
    """La caché de audio del TTS vive en disco: que ningún test escriba en ``data/`` real."""
    from miku.voz.salida import tts
    monkeypatch.setattr(tts, "CARPETA_CACHE", tmp_path / "tts_cache")
