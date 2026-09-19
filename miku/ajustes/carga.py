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
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from miku.ajustes.esquema import valores_por_defecto

# Logger específico de este módulo.
logger = logging.getLogger("miku.config")

# ==== Raíz del proyecto (siempre igual, sin importar CWD) ====
# En un ejecutable empaquetado (PyInstaller, ``sys.frozen``) ``__file__`` apunta
# a la carpeta interna de recursos: la raíz real (donde el usuario deja
# ``config_local.py`` y ``data/``) es la carpeta del ``.exe``.
if getattr(sys, "frozen", False):
    BASE_DIR: Path = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parents[2]   # miku/ajustes/carga.py -> raíz del proyecto

# Lock para escribir data/preferences.json desde varios hilos/plugins.
_LOCK_PREFERENCIAS = threading.Lock()

# URL base de VOICEVOX (motor local de síntesis de voz).
VOICEVOX_URL: str = "http://localhost:50021"


def _defaults() -> Dict[str, Any]:
    """Valores por defecto de todas las opciones.

    Salen del esquema único (``miku/ajustes/esquema.py``), donde cada opción está
    declarada con su tipo y su descripción.
    """
    return valores_por_defecto()


@dataclass
class Config:
    """Contenedor tipado de la configuración del asistente."""

    valores: Dict[str, Any] = field(default_factory=_defaults)
    #: Archivo de preferencias persistente (JSON) en data/.
    ruta_archivo: Path = field(
        default_factory=lambda: BASE_DIR / "data" / "preferences.json"
    )
    #: De dónde vino cada valor que NO es el default: "preferences", "config_local" o "entorno".
    origenes: Dict[str, str] = field(default_factory=dict)
    #: Claves (minúsculas) definidas en config_local.py (para validar y para el asistente).
    claves_locales: Set[str] = field(default_factory=set)

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
            return int(self.valores.get("voicevox_speaker_id", 6))
        except (TypeError, ValueError):
            return 6

    @property
    def microfono_index(self) -> Optional[int]:
        """Índice del micrófono (None = el de por defecto del sistema)."""
        valor = self.valores.get("microfono_index")
        if valor is None or str(valor).strip() == "":
            return None
        try:
            return int(valor)
        except (TypeError, ValueError):
            logger.warning("MICROFONO_INDEX inválido (%r); uso el default.", valor)
            return None

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

    # ---------- Carpetas favoritas ----------
    @property
    def carpetas_favoritas(self) -> Dict[str, str]:
        """Mapa nombre -> ruta de las carpetas favoritas configuradas.

        Normaliza a str y descarta entradas vacías. NO incluye las que el
        usuario haya "enseñado" por voz (esas viven en preferences.json y las
        fusiona miku/plugins/productividad/favoritos.py).
        """
        valor = self.valores.get("carpetas_favoritas") or {}
        if not isinstance(valor, dict):
            return {}
        return {str(k): str(v) for k, v in valor.items()
                if str(k).strip() and str(v).strip()}

    # ---------- Captura de pantalla ----------
    @property
    def carpeta_capturas(self) -> str:
        """Carpeta donde guardar capturas (absoluta).

        Si no se configura, usa ``<raíz>/data/capturas``. Una ruta relativa se
        resuelve contra la raíz del proyecto.
        """
        crudo = str(self.valores.get("carpeta_capturas", "") or "").strip()
        if not crudo:
            return str(Path(BASE_DIR) / "data" / "capturas")
        crudo = os.path.expandvars(os.path.expanduser(crudo))
        p = Path(crudo)
        if not p.is_absolute():
            p = Path(BASE_DIR) / p
        return str(p.resolve())

    # ---------- Clima ----------
    @property
    def ciudad_clima(self) -> str:
        return str(self.valores.get("ciudad_clima", "") or "").strip()

    @property
    def clima_lat(self) -> str:
        return str(self.valores.get("clima_lat", "") or "").strip()

    @property
    def clima_lon(self) -> str:
        return str(self.valores.get("clima_lon", "") or "").strip()

    # ---------- Personalidad ----------
    @property
    def personalidad(self) -> str:
        return str(self.valores.get("personalidad", "neutral") or "neutral").strip().lower()

    # ---------- Auto-Game Booster ----------
    @property
    def juegos_booster(self) -> List[str]:
        lista = self.valores.get("juegos_booster") or []
        return [str(x).lower().strip() for x in lista if str(x).strip()]

    @property
    def booster_apps_volumen(self) -> List[str]:
        lista = self.valores.get("booster_apps_volumen") or []
        return [str(x).lower().strip() for x in lista if str(x).strip()]

    @property
    def booster_volumen_objetivo(self) -> int:
        try:
            return max(0, min(100, int(self.valores.get("booster_volumen_objetivo", 20))))
        except (TypeError, ValueError):
            return 20

    @property
    def booster_aviso(self) -> bool:
        valor = self.valores.get("booster_aviso", True)
        if isinstance(valor, str):
            return valor.strip().lower() in ("1", "true", "sí", "si", "yes", "on")
        return bool(valor)

    # ---------- OCR ----------
    @property
    def tesseract_ruta(self) -> str:
        return str(self.valores.get("tesseract_ruta", "") or "").strip()

    @property
    def ocr_idioma(self) -> str:
        return str(self.valores.get("ocr_idioma", "spa") or "spa").strip()

    # ---------- Memoria / embeddings ----------
    @property
    def embeddings_activos(self) -> bool:
        valor = self.valores.get("embeddings_activos", True)
        if isinstance(valor, str):
            return valor.strip().lower() in ("1", "true", "sí", "si", "yes", "on")
        return bool(valor)

    @property
    def embeddings_umbral(self) -> float:
        try:
            return float(self.valores.get("embeddings_umbral", 0.35))
        except (TypeError, ValueError):
            return 0.35

    # ---------- Visión (Gemini) ----------
    @property
    def gemini_api_key(self) -> str:
        return str(self.valores.get("gemini_api_key", "") or "").strip()

    @property
    def gemini_modelo(self) -> str:
        return str(self.valores.get("gemini_modelo", "gemini-2.0-flash") or
                   "gemini-2.0-flash").strip()

    # ---------- Todoist ----------
    @property
    def todoist_api_token(self) -> str:
        return str(self.valores.get("todoist_api_token", "") or "").strip()

    # ---------- Telegram ----------
    @property
    def telegram_bot_token(self) -> str:
        return str(self.valores.get("telegram_bot_token", "") or "").strip()

    @property
    def telegram_chat_id(self) -> str:
        return str(self.valores.get("telegram_chat_id", "") or "").strip()

    # ---------- Asistente proactivo ----------
    @property
    def proactivo_activo(self) -> bool:
        valor = self.valores.get("proactivo_activo", True)
        if isinstance(valor, str):
            return valor.strip().lower() in ("1", "true", "sí", "si", "yes", "on")
        return bool(valor)

    @property
    def proactivo_bateria_min(self) -> int:
        try:
            return int(self.valores.get("proactivo_bateria_min", 20))
        except (TypeError, ValueError):
            return 20

    @property
    def proactivo_disco_gb(self) -> float:
        try:
            return float(self.valores.get("proactivo_disco_gb", 5.0))
        except (TypeError, ValueError):
            return 5.0

    @property
    def proactivo_intervalo_min(self) -> int:
        try:
            return max(1, int(self.valores.get("proactivo_intervalo_min", 5)))
        except (TypeError, ValueError):
            return 5

    @property
    def proactivo_cooldown_min(self) -> int:
        try:
            return max(1, int(self.valores.get("proactivo_cooldown_min", 30)))
        except (TypeError, ValueError):
            return 30

    # ---------- Discord ----------
    @property
    def discord_bot_token(self) -> str:
        """Token del bot de Discord (secreto). Vacío => plugin inactivo."""
        return str(self.valores.get("discord_bot_token", "") or "").strip()

    @property
    def discord_guild_id(self) -> str:
        """ID del servidor (guild) por defecto para resolver usuarios/canales."""
        return str(self.valores.get("discord_guild_id", "") or "").strip()

    # ---------- Acceso genérico ----------
    def get(self, clave: str, por_defecto: Any = None) -> Any:
        """Alias dict-like para claves sueltas."""
        return self.valores.get(clave, por_defecto)

    def actualizar(self, nuevos: Dict[str, Any]) -> None:
        """Fusiona un dict de valores sobre la config actual."""
        if nuevos:
            self.valores.update(nuevos)

    def guardar_preferencias(self, nuevos: Dict[str, Any]) -> bool:
        """Persiste ``nuevos`` en data/preferences.json y en la config viva.

        Hace merge (conserva el resto de las claves del JSON) y escribe de forma
        atómica (archivo temporal + ``os.replace``): un corte a mitad de escritura
        no deja el JSON truncado. Está protegido con un lock porque lo usan varios
        plugins (favoritos, personalidad, modo de entrada).

        Returns:
            True si se pudo guardar.
        """
        with _LOCK_PREFERENCIAS:
            datos: Dict[str, Any] = {}
            if self.ruta_archivo.exists():
                try:
                    with open(self.ruta_archivo, "r", encoding="utf-8") as f:
                        cargado = json.load(f)
                    if isinstance(cargado, dict):
                        datos = cargado
                except Exception as e:  # noqa: BLE001
                    logger.warning("preferences.json ilegible (%s); lo reescribo.", e)
            datos.update(nuevos)
            try:
                self.ruta_archivo.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=str(self.ruta_archivo.parent),
                                           suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(datos, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, self.ruta_archivo)
                except Exception:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    raise
            except Exception:  # noqa: BLE001
                logger.exception("No pude guardar %s.", self.ruta_archivo)
                return False
            self.valores.update(nuevos)
            return True

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
                self.origenes[clave] = "entorno"
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
                for k in datos:
                    self.origenes[k] = "preferences"
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
        # En un .exe empaquetado la carpeta del ejecutable no está en sys.path.
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
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
        self.claves_locales = set(configuracion_nueva)
        for k in configuracion_nueva:
            self.origenes[k] = "config_local"
        logger.debug("Aplicados overrides de config_local.py (%d claves).",
                     len(configuracion_nueva))


# Instancia global reutilizable (singleton de conveniencia).
config = Config()
_cargada = False


def cargar() -> Config:
    """Devuelve la instancia global, cargándola SOLO la primera vez.

    Es idempotente: varios plugins la llaman "por las dudas", y recargar cada
    vez releía preferences.json y pisaba cambios hechos en runtime (p. ej. la
    personalidad cambiada por voz). Para releer a propósito usar ``recargar()``.
    """
    global _cargada
    if not _cargada:
        config.cargar()
        _cargada = True
    return config


def recargar() -> Config:
    """Vuelve a leer preferences.json, config_local.py y el entorno."""
    global _cargada
    config.cargar()
    _cargada = True
    return config
