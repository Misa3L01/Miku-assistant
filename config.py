"""
config.py - Gestor de configuración del asistente Miku.

Centraliza la lectura de la configuración en un único lugar (con prioridad):
    1. Defaults definidos acá mismo (dict ``_defaults``).
    2. ``data/preferences.json``  (JSON persistente editable, opcional).
    3. ``config_local.py``      (secretos/override NO versionados).

Las rutas por defecto son RELATIVAS a la raíz del proyecto, calculadas con
``Path(__file__).parent`` para no depender del directorio de ejecución.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Logger específico de este módulo.
logger = logging.getLogger("miku.config")

# ==== Raíz del proyecto (siempre igual, sin importar CWD) ====
BASE_DIR: Path = Path(__file__).resolve().parent

# Carpeta de modelos de voz (por compatibilidad futura).
MODELOS_DIR: Path = BASE_DIR / "modelos_voz"
# Carpeta de binarios externos (es.exe, etc.).
BIN_DIR: Path = BASE_DIR / "bin"

# URL base de VOICEVOX (motor local de síntesis de voz).
VOICEVOX_URL: str = "http://localhost:50021"


def _defaults() -> Dict[str, Any]:
    """Devuelve el dict con los valores por defecto de configuración."""
    return {
        # --- API / modelo de texto (Groq) ---
        # Las claves quedan vacías acá: se leen de variables de entorno o de
        # ``config_local.py`` (NUNCA se versionan valores reales).
        "groq_api_key": "",
        "groq_api_key_stt": "",  # Si vacío, usa groq_api_key.
        "modelo_api_externa": "openai/gpt-oss-20b",

        # --- Voz: VOICEVOX ---
        "voicevox_url": VOICEVOX_URL,
        "voicevox_speaker_id": 1,
        "microfono_index": None,

        # --- Modelos STT (whisper de Groq) ---
        "modelo_stt_wake": "whisper-large-v3",
        "modelo_stt_comando": "whisper-large-v3",

        # --- Modo de entrada por defecto ("voz" | "texto" | "push") ---
        "modo_entrada": "voz",
        #: Nivel de logging por defecto: "INFO", "DEBUG" o "ERROR".
        "log_level": "INFO",

        # --- Memoria persistente (SQLite en data/miku_memoria.db) ---
        #: True para activar la memoria (guardar/consultar recuerdos).
        #: Se puede forzar a False desde config_local.py para desactivarla.
        "memoria_activa": True,

        # --- Navegador (Brave) ---
        "brave_debug_port": 9222,
        # Las rutas de tu instalación van en config_local.py (NO en defaults).
        "brave_ruta_exe": "",
        "brave_perfil_dir": "",
        # --- TIDAL ---
        # Ruta de instalación en config_local.py.
        "tidal_ruta_exe": "",
        "tidal_debug_port": 9223,

        # --- Sistema / búsqueda / seguridad ---
        "ruta_everything_es": "bin/es.exe",
        "discos_buscar": ["C", "D", "R"],
        "app_whitelist": [
            "brave", "discord", "tidal", "vscode", "code", "chrome", "edge",
            "spotify", "steam", "obs", "notepad", "explorer", "calculadora",
        ],

        # --- Precios (cálculo ARS) ---
        "dolar_actual": 1500,
        "recargo_tarjeta": 1.60,
        "ganancia": 1.10,

        # --- Interpolación de video ---
        # Rutas privadas (carpeta_videos / ruta_bat_interpolar): vacías en
        # defaults. Se definen únicamente en config_local.py (NO versionado).
        "carpeta_videos": "",
        "ruta_bat_interpolar": "",

        # --- Traductor para juegos ---
        # Idioma destino y mensajes preconfigurados. Se definen típicamente en
        # config_local.py. Ejemplo:
        #   IDIOMA_JUEGO = "inglés"
        #   MENSAJES_JUEGO = {"gg": "buena partida", "gracias": "gracias"}
        "idioma_juego": "",
        "mensajes_juego": {},
    }


@dataclass
class Config:
    """Contenedor tipado de la configuración del asistente."""

    valores: Dict[str, Any] = field(default_factory=_defaults)
    #: Archivo de preferencias persistente (JSON) en data/.
    ruta_archivo: Path = field(
        default_factory=lambda: BASE_DIR / "data" / "preferences.json"
    )

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

    # ---------- Voz / VOICEVOX ----------
    @property
    def voicevox_url(self) -> str:
        """URL base del servidor VOICEVOX."""
        return str(self.valores.get("voicevox_url", VOICEVOX_URL))

    @property
    def voicevox_speaker_id(self) -> int:
        """ID de hablante por defecto de VOICEVOX."""
        try:
            return int(self.valores.get("voicevox_speaker_id", 1))
        except (TypeError, ValueError):
            return 1

    @property
    def modelo_miku(self) -> str:
        """Modelo de voz (ruta relativa). Por ahora VOICEVOX no lo necesita."""
        return str(self.valores.get("modelo_miku", "modelos_voz/infamous_miku_v2.pth"))

    @property
    def microfono_index(self) -> Optional[int]:
        valor = self.valores.get("microfono_index")
        return int(valor) if valor is not None else None

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

    # ---------- Memoria ----------
    @property
    def memoria_activa(self) -> bool:
        """True si la memoria persistente debe estar activa."""
        valor = self.valores.get("memoria_activa", True)
        if isinstance(valor, str):
            return valor.strip().lower() in ("1", "true", "sí", "si", "yes", "on")
        return bool(valor)

    # ---------- Sistema / apps ----------
    @property
    def app_whitelist(self) -> List[str]:
        """Lista blanca de procesos que podemos cerrar (seguridad)."""
        lista = self.valores.get("app_whitelist") or []
        return [str(a).lower() for a in lista]

    @property
    def ruta_everything_es(self) -> str:
        """Ruta ABSOLUTA al es.exe de Everything.

        Se resuelve siempre contra ``BASE_DIR`` (la raíz real del proyecto),
        sin importar desde qué directorio de trabajo se ejecute ``main.py``.
        Admite override por config_local (p. ej. una ruta absoluta) o una
        ruta relativa como ``bin/es.exe`` → se convierte en absoluta.
        """
        crudo = str(self.valores.get("ruta_everything_es", "")).strip()
        if not crudo:
            return ""
        # Expandimos variables de entorno (~, %VAR% del estilo de config).
        crudo = os.path.expandvars(os.path.expanduser(crudo))
        p = Path(crudo)
        if not p.is_absolute():
            # Relativa => la asumimos desde la raíz del proyecto.
            p = Path(BASE_DIR) / p
        return str(p.resolve())

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
        """Carga config con prioridad de mayor especificidad a menor:
        env -> config_local.py -> preferences.json -> defaults.

        Es decir, se aplican en orden creciente de prioridad:
            1) defaults, 2) preferences.json, 3) config_local.py,
            4) variables de entorno (más alta, pensadas para secretos).
        """
        # 1) Preferences JSON (EDITA sobre los defaults).
        self._cargar_preferences()
        # 2) config_local.py (sobrescribe SIEMPRE, con update).
        self._cargar_local()
        # 3) Variables de entorno (secretos; precedencia máxima).
        self._cargar_entorno()

    def _cargar_entorno(self) -> None:
        """Sobrescribe claves con variables de entorno (opcional).

        Se mapean en MAYÚSCULAS: para la clave ``groq_api_key`` se mira
        ``GROQ_API_KEY``, etc. Si la env existe, su valor reemplaza.
        """
        for clave in list(self.valores.keys()):
            env_nombre = clave.upper()
            valor_env = os.environ.get(env_nombre)
            if valor_env is not None and str(valor_env).strip() != "":
                self.valores[clave] = valor_env
                logger.debug("Clave '%s' venida de la variable de entorno '%s'.",
                             clave, env_nombre)

    def _cargar_preferences(self) -> None:
        """Lee data/preferences.json (si existe dentro de la carpeta data/)
        y lo funde sobre los defaults.

        Nota: el archivo real debe vivir en ``<raíz>/data/preferences.json``.
        """
        if not self.ruta_archivo.exists():
            logger.warning(
                "No se encontró el archivo de preferencias (%s). "
                "Se usan los valores por defecto. Asegurate de que "
                "preferences.json esté dentro de la carpeta data/ del proyecto.",
                self.ruta_archivo)
            return
        try:
            with open(self.ruta_archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
            if isinstance(datos, dict):
                for k in ("__comentario__", "_version"):
                    datos.pop(k, None)
                self.valores.update(datos)
            logger.info("Preferencias cargadas desde %s", self.ruta_archivo)
        except FileNotFoundError:
            logger.warning(
                "No se pudo abrir %s (¿está en data/?). Uso defaults.",
                self.ruta_archivo)
        except json.JSONDecodeError as e:
            logger.error(
                "%s tiene un error de sintaxis (línea %s): %s",
                self.ruta_archivo, e.lineno, e.msg,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("No se pudo leer %s: %s", self.ruta_archivo, e)

    def _cargar_local(self) -> None:
        """Sobrescribe claves con config_local.py (secretos, sin versionar).

        A diferencia de los viejos ``setdefault``, acá se hace un ``update``
        directo: lo que defina ``config_local.py`` REEMPLAZA al valor default.
        """
        ruta_local = BASE_DIR / "config_local.py"
        if not ruta_local.exists():
            return
        try:
            import config_local  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.warning("config_local.py tiene un error: %s", e)
            return

        configuracion_nueva: Dict[str, Any] = {}
        for k in dir(config_local):
            if k.isupper() and not k.startswith("_"):
                configuracion_nueva[k.lower()] = getattr(config_local, k)
        # Sobrescritura directa (no setdefault).
        self.valores.update(configuracion_nueva)
        logger.debug("Aplicados overrides de config_local.py (%d claves).",
                     len(configuracion_nueva))


# Instancia global reutilizable (singleton de conveniencia).
config = Config()


def cargar() -> Config:
    """Carga y devuelve la instancia global lista para usar."""
    config.cargar()
    return config
