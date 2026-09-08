"""
config.py - Gestor de configuración del asistente Miku.

Centraliza la lectura de config.json (rutas, API keys, modelos a usar)
y expone un objeto Config con los valores por defecto. También se puede
usar un archivo config_local.py para sobrescribir secretos sin versionarlos.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Logger específico de este módulo.
logger = logging.getLogger("miku.config")

# Ruta por defecto del archivo de configuración (relativa a la raíz).
RUTA_DEFAULT = Path(__file__).resolve().parent / "config.json"


def _defaults() -> Dict[str, Any]:
    """Devuelve un dict con los valores por defecto de configuración."""
    return {
        # --- API / modelo de texto (Groq) ---
        "groq_api_key": "",
        "groq_api_key_stt": "",  # Si vacío, usa groq_api_key.
        "modelo_api_externa": "openai/gpt-oss-20b",

        # --- Voz (RVC + Kokoro) ---
        "modelo_miku": "codigo_viejo/modelos_voz/infamous_miku_v2.pth",
        "microfono_index": None,

        # --- Modelos STT (whisper de Groq) ---
        "modelo_stt_wake": "whisper-large-v3",
        "modelo_stt_comando": "whisper-large-v3",

        # --- Modo de entrada por defecto ("voz" | "texto" | "push") ---
        "modo_entrada": "voz",
        #: Nivel de logging por defecto: "INFO", "DEBUG" o "ERROR".
        "log_level": "INFO",

        # --- Navegador (Brave) ---
        "brave_debug_port": 9222,
        "brave_ruta_exe": "",
        "brave_perfil_dir": "",

        # --- TIDAL (app de escritorio experimental) ---
        "tidal_debug_port": 9223,
        "tidal_ruta_exe": "",

        # --- Búsqueda de archivos ---
        "ruta_everything_es": "",
        "discos_buscar": ["C", "D", "R"],

        # --- Cálculo de precios ---
        "dolar_actual": 1300,
        "recargo_tarjeta": 1.60,
        "ganancia": 1.10,

        # --- Interpolación de video ---
        "carpeta_videos": "",
        "ruta_bat_interpolar": "",
    }


@dataclass
class Config:
    """Contenedor tipado de la configuración del asistente."""

    valores: Dict[str, Any] = field(default_factory=_defaults)
    ruta_archivo: Path = field(default_factory=lambda: RUTA_DEFAULT)

    # ---------- API / texto ----------
    @property
    def groq_api_key(self) -> str:
        """Clave principal del cerebro (Groq)."""
        return str(self.valores.get("groq_api_key", ""))

    @property
    def groq_api_key_stt(self) -> str:
        """Clave de transcripción. Si no está definida usa la principal."""
        clave = str(self.valores.get("groq_api_key_stt", "")).strip()
        return clave or self.groq_api_key

    @property
    def modelo_api_externa(self) -> str:
        return str(self.valores.get("modelo_api_externa", "openai/gpt-oss-20b"))

    @property
    def log_level(self) -> str:
        return str(self.valores.get("log_level", "INFO")).upper()

    # ---------- Voz ----------
    @property
    def modelo_miku(self) -> str:
        return str(self.valores.get("modelo_miku", ""))

    @property
    def microfono_index(self) -> Optional[int]:
        valor = self.valores.get("microfono_index")
        return int(valor) if valor is not None else None

    # ---------- STT ----------
    @property
    def modelo_stt_wake(self) -> str:
        return str(self.valores.get("modelo_stt_wake", "whisper-large-v3"))

    @property
    def modelo_stt_comando(self) -> str:
        return str(self.valores.get("modelo_stt_comando", "whisper-large-v3"))

    # ---------- Entrada ----------
    @property
    def modo_entrada(self) -> str:
        return str(self.valores.get("modo_entrada", "voz")).lower()

    # ---------- Búsqueda ----------
    @property
    def ruta_everything_es(self) -> str:
        return str(self.valores.get("ruta_everything_es", ""))

    @property
    def discos_buscar(self) -> List[str]:
        discos = self.valores.get("discos_buscar") or ["C", "D", "R"]
        return [str(d) for d in discos]

    # ---------- Acceso genérico ----------
    def get(self, clave: str, por_defecto: Any = None) -> Any:
        """Alias dict-like para claves sueltas."""
        return self.valores.get(clave, por_defecto)

    def actualizar(self, nuevos: Dict[str, Any]) -> None:
        """Fusiona un dict de valores sobre la config actual."""
        if nuevos:
            self.valores.update(nuevos)

    # ---------- Persistencia ----------
    def cargar(self) -> None:
        """Carga config.json (y opcionalmente config_local.py)."""
        if self.ruta_archivo.exists():
            try:
                with open(self.ruta_archivo, "r", encoding="utf-8") as f:
                    datos = json.load(f)
                self.valores.update(datos)
                logger.debug("Config cargada desde %s", self.ruta_archivo)
            except json.JSONDecodeError as e:
                logger.error(
                    "config.json tiene un error de sintaxis (línea %s): %s",
                    e.lineno, e.msg,
                )
                raise
        else:
            logger.warning("No se encontró %s. Valores por defecto.", self.ruta_archivo)

        self._cargar_local()

    def _cargar_local(self) -> None:
        """Sobrescribe claves con config_local.py si existe (secretos)."""
        try:
            import config_local  # type: ignore  # noqa
        except ImportError:
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("config_local.py tiene un error: %s", e)
            return

        for k in dir(config_local):
            if k.isupper() and not k.startswith("_"):
                self.valores.setdefault(k.lower(), getattr(config_local, k))
        logger.debug("Aplicadas sobrescrituras de config_local.")


# Instancia global reutilizable (singleton de conveniencia).
config = Config()


def cargar() -> Config:
    """Carga y devuelve la instancia global lista para usar."""
    config.cargar()
    return config
