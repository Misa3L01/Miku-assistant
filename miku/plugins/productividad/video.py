"""
video_interpolador.py - Dispara el pipeline de interpolación de video.

Publica la tool `interpolar_video`. El usuario define en config_local.py:
    - CARPETA_VIDEOS      -> carpeta donde están los videos a interpolar.
    - RUTA_BAT_INTERPOLAR -> script .bat que hace la interpolación.

Comportamiento:
    1. Lista los videos (mp4/mkv/avi/mov/wmv/webm/flv) de la carpeta.
    2. Si hay UNO solo (o el usuario especificó "el último"/un nombre), lo elige.
    3. Si hay VARIOS y el usuario no aclaró cuál -> usa el mecanismo GENÉRICO de
       desambiguación (devuelve {"desambiguar": True, "opciones": [...]}) para que
       el parser le pregunte cuál quiere.
    4. Lanza el .bat en segundo plano (SIN ventana, capturando salida) en un hilo
       aparte, para no bloquear la conversación.
    5. Al terminar, avisa por VOZ (contexto["voice"]) y/o por consola con el
       nombre del video y si salió bien o falló.

Alcance / honestidad:
    - NO inventa la salida del pipeline. Solo lo dispara y avisa cuando TERMINA.
    - Si falta `carpeta_videos` o `ruta_bat_interpolar`, responde clarito que no
      está configurado (no intenta adivinar rutas).
    - La notificación es por voz si hay motor TTS; si no, por consola.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from miku.plugins.base import Plugin
from miku.voz.frases.respuesta import falla

logger = logging.getLogger("miku.plugins.video_interpolador")

# Extensiones de video que consideramos "videos a interpolar".
_EXT_VIDEO = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv")

# Sufijo del video que genera el .bat: "<nombre>-2x.mkv" junto al original.
_SUFIJO_SALIDA = "-2x.mkv"


def _listar_videos(carpeta: str) -> List[str]:
    """Devuelve las rutas de videos en `carpeta` (ordenadas por mtime desc).

    Cada elemento es una tupla (ruta, mtime) para poder ofrecer "el último".
    Si la carpeta no existe o no se puede leer, devuelve [].
    """
    try:
        p = Path(carpeta)
        if not p.is_dir():
            return []
        videos = []
        for item in p.iterdir():
            try:
                if item.is_file() and item.suffix.lower() in _EXT_VIDEO:
                    videos.append((str(item), item.stat().st_mtime))
            except Exception:  # noqa: BLE001
                continue
        # Más recientes primero.
        videos.sort(key=lambda par: par[1], reverse=True)
        return videos
    except Exception as e:  # noqa: BLE001
        logger.error("No pude listar videos en '%s': %s", carpeta, e)
        return []


class VideoInterpolador(Plugin):
    """Dispara el .bat de interpolación de video (config_local).

    Args:
        (ninguno; lee las rutas de config en runtime para respetar cambios).
    """

    nombre = "video_interpolador"
    descripcion = ("Dispara el pipeline de interpolación de video configurado "
                   "(carpeta_videos / ruta_bat_interpolar).")

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "interpolar_video",
                "description": "Inicia la interpolación de un video de la "
                               "carpeta configurada. Si no se aclara cuál, y "
                               "hay varios, se preguntará al usuario. Ej: "
                               "'interpolá el último video', 'interpolá el "
                               "video de ayer'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Opcional: nombre o parte del "
                                           "nombre del video a interpolar. "
                                           "Si se omite, se usa el más "
                                           "reciente de la carpeta.",
                        },
                    },
                },
            },
        },
    ]

    # ---------------- Ciclo de vida ----------------
    def __init__(self) -> None:
        """Prepara el control de concurrencia del pipeline."""
        super().__init__()
        # Solo se permite UN pipeline a la vez (son procesos muy pesados).
        self._lock = threading.Lock()
        self._en_curso: Optional[str] = None

    def initialize(self, event_bus: Any = None) -> None:
        """Registra el plugin (no necesita recursos propios)."""
        super().initialize(event_bus)
        logger.info("Plugin video_interpolador listo.")

    # ---------------- Lectura de config ----------------
    def _leer_config(self) -> Dict[str, str]:
        """Devuelve {'carpeta': ..., 'bat': ...} desde config (runtime)."""
        try:
            from miku.ajustes import carga as config_mod  # ruta segura
            cfg = config_mod.cargar()
            carpeta = str(cfg.get("carpeta_videos", "") or "").strip()
            bat = str(cfg.get("ruta_bat_interpolar", "") or "").strip()
            return {"carpeta": carpeta, "bat": bat}
        except Exception as e:  # noqa: BLE001
            logger.error("No pude leer la config de video: %s", e)
            return {"carpeta": "", "bat": ""}

    # ---------------- Despacho de tools ----------------
    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        if nombre_tool == "interpolar_video":
            # Caso especial: si viene "ruta_elegida" (desambiguación ya resuelta),
            # disparamos ESO directo sin volver a listar.
            ruta_elegida = args.get("ruta_elegida")
            if ruta_elegida:
                cfg = self._leer_config()
                return self._lanzar(str(ruta_elegida), cfg["bat"],
                                    cfg["carpeta"], contexto)
            return self.interpolar_video(str(args.get("nombre", "") or ""),
                                         contexto)
        return None

    # ---------------- Lógica principal ----------------
    def interpolar_video(self, nombre: str = "",
                         contexto: Optional[Dict[str, Any]] = None) -> Any:
        """Dispara la interpolación del video pedido (o el más reciente).

        Returns:
            - str con la respuesta si todo va bien o hay un error claro.
            - dict de DESAMBIGUACIÓN si hay varios y no se pudo elegir.
        """
        contexto = contexto or {}
        cfg = self._leer_config()
        carpeta = cfg["carpeta"]
        bat = cfg["bat"]

        # 1) Config faltante -> respuesta honesta (no adivinamos rutas).
        if not carpeta or not bat:
            faltan = []
            if not carpeta:
                faltan.append("CARPETA_VIDEOS")
            if not bat:
                faltan.append("RUTA_BAT_INTERPOLAR")
            return ("No tengo configurado el pipeline de video. Definí "
                    f"{' y '.join(faltan)} en config_local.py.")

        if not os.path.exists(bat):
            return (f"No encuentro el script de interpolación en '{bat}'. "
                    f"Revisá RUTA_BAT_INTERPOLAR.")

        videos = _listar_videos(carpeta)
        if not videos:
            return falla("video.sin_videos", carpeta=carpeta)

        # 2) Elegir el video objetivo.
        nombre = (nombre or "").strip().lower()
        if nombre:
            # "el último" / "el mas reciente" -> el primero de la lista (mtime).
            if any(p in nombre for p in ("último", "ultimo", "reciente",
                                         "ultima", "última", "mas nuevo",
                                         "más nuevo")):
                elegido = videos[0][0]
            else:
                coincidencias = [
                    r for r, _ in videos
                    if nombre in os.path.basename(r).lower()
                ]
                if len(coincidencias) == 1:
                    elegido = coincidencias[0]
                elif len(coincidencias) > 1:
                    # Varios matchean el nombre -> desambiguar.
                    return self._opciones(coincidencias, bat, carpeta)
                else:
                    # Sin coincidencia por nombre -> quizá el usuario dijo algo
                    # parecido; si hay un único video, lo usamos; si hay varios,
                    # preguntamos.
                    if len(videos) == 1:
                        elegido = videos[0][0]
                    else:
                        return self._opciones([r for r, _ in videos], bat,
                                              carpeta)
        else:
            # Sin nombre: si hay UNO solo, ese; si hay varios, preguntar cuál
            # (ofreciendo "el más reciente" primero).
            if len(videos) == 1:
                elegido = videos[0][0]
            else:
                return self._opciones([r for r, _ in videos], bat, carpeta)

        # 3) Lanzar el pipeline.
        return self._lanzar(elegido, bat, carpeta, contexto)

    def _opciones(self, rutas: List[str], bat: str,
                  carpeta: str) -> Dict[str, Any]:
        """Arma el dict de desambiguación con las opciones (videos)."""
        top = rutas[:6]  # no abrumar con 50 archivos
        opciones = [
            {
                "indice": i + 1,
                "etiqueta": os.path.basename(r)
                            + ("   (más reciente)" if i == 0 else ""),
                "valor": {"ruta_elegida": r},
            }
            for i, r in enumerate(top)
        ]
        return {
            "desambiguar": True,
            "tool_origen": "interpolar_video",
            "args_origen": {},
            "opciones": opciones,
        }

    @staticmethod
    def _validar_video(ruta_video: str, carpeta: str, bat: str) -> Optional[str]:
        """Devuelve un mensaje de error si NO es seguro lanzar ``ruta_video``.

        El ``.bat`` se ejecuta vía ``cmd.exe``, que interpreta ``& % ^ ! ( )``
        aunque no haya ``shell=True``: un nombre como ``AT&T.mp4`` ejecutaría
        ``T.mp4`` como comando. Además la ruta puede venir del LLM, así que se
        exige que sea un video que esté dentro de la carpeta configurada.
        """
        if not os.path.exists(bat):
            return (f"No encuentro el script de interpolación en '{bat}'. "
                    f"Revisá RUTA_BAT_INTERPOLAR.")
        try:
            ruta = Path(ruta_video).resolve()
            base = Path(carpeta).resolve()
            if not ruta.is_file() or not ruta.is_relative_to(base):
                return "Ese video no está en la carpeta de videos configurada."
        except Exception:  # noqa: BLE001
            return "No pude validar la ruta del video."
        if ruta.suffix.lower() not in _EXT_VIDEO:
            return "Ese archivo no parece un video."
        caracteres = set(ruta.name) & set('&%^!()|<>"')
        if caracteres:
            return ("El nombre del video tiene caracteres que el script no "
                    f"soporta ({' '.join(sorted(caracteres))}). Renombralo y "
                    "probá de nuevo.")
        return None

    def _lanzar(self, ruta_video: str, bat: str, carpeta: str,
                contexto: Dict[str, Any]) -> str:
        """Lanza el .bat en segundo plano y agenda el aviso de fin.

        El .bat se ejecuta SIN ventana (CREATE_NO_WINDOW) y capturando salida.
        Un hilo aparte espera a que termine y avisa por voz/consola. Valida la
        ruta y no permite dos interpolaciones a la vez.
        """
        problema = self._validar_video(ruta_video, carpeta, bat)
        if problema:
            return problema
        nombre_video = os.path.basename(ruta_video)

        with self._lock:
            if self._en_curso is not None:
                return (f"Ya estoy interpolando '{self._en_curso}'. Esperá a "
                        f"que termine antes de arrancar otro.")
            self._en_curso = nombre_video

        # Lanzamos en un hilo para no bloquear; el hilo espera y avisa al final.
        hilo = threading.Thread(
            target=self._correr_pipeline,
            args=(ruta_video, bat, nombre_video, contexto),
            daemon=True,
            name="video_interpolador",
        )
        hilo.start()
        return (f"Dale, arranqué la interpolación de '{nombre_video}' en "
                f"segundo plano. Te aviso cuando termine.")

    def _correr_pipeline(self, ruta_video: str, bat: str, nombre_video: str,
                         contexto: Dict[str, Any]) -> None:
        """Ejecuta el .bat y, al terminar, notifica por voz/consola.

        Captura stdout/stderr para poder reportar el resultado. No crea ventana
        (CREATE_NO_WINDOW) en Windows.
        """
        creationflags = 0
        startupinfo = None
        try:
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags |= subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        except Exception:  # noqa: BLE001
            startupinfo = None

        logger.info("Lanzando interpolación: bat='%s' video='%s'",
                    bat, ruta_video)
        codigo = None
        salida = ""
        try:
            # El video se pasa como argumento al .bat (convención habitual del
            # script de interpolación: recibe la ruta del video a procesar).
            proc = subprocess.run(
                [bat, ruta_video],
                capture_output=True, text=True, shell=False,
                cwd=os.path.dirname(bat) or None,
                creationflags=creationflags, startupinfo=startupinfo,
            )
            codigo = proc.returncode
            salida = (proc.stdout or "")[-500:]  # últimos 500 chars (diagnóstico)
        except Exception:  # noqa: BLE001
            logger.exception("Error ejecutando el .bat de interpolación.")
            self._notificar(
                f"Uy, no pude ejecutar la interpolación de {nombre_video}. "
                f"Falló el script.", contexto)
            return
        finally:
            with self._lock:
                self._en_curso = None

        # El código de salida NO alcanza: el .bat original devolvía 0 aunque
        # fallara. Se comprueba además que el video final exista y no esté vacío.
        salida_video = Path(ruta_video).with_name(Path(ruta_video).stem + _SUFIJO_SALIDA)
        if codigo == 0 and not (salida_video.is_file()
                                and salida_video.stat().st_size > 0):
            logger.warning("El script terminó sin error pero no generó %s.",
                           salida_video)
            codigo = None  # se reporta como "no se generó el video"

        if codigo == 0:
            logger.info("Interpolación OK: %s", nombre_video)
            self._notificar(
                f"¡Listo! Terminé de interpolar {nombre_video}.", contexto)
        else:
            logger.warning("Interpolación falló (código %s): %s. Salida: %s",
                           codigo, nombre_video, salida)
            detalle = ("no se generó el video de salida" if codigo is None
                       else f"el script devolvió el código {codigo}")
            self._notificar(
                f"La interpolación de {nombre_video} falló: {detalle}.",
                contexto)

    def _notificar(self, frase: str, contexto: Dict[str, Any]) -> None:
        """Avisa el resultado por voz (si hay TTS) o por consola."""
        voice = (contexto or {}).get("voice")
        if voice is not None:
            try:
                voice.decir(frase)
                return
            except Exception:  # noqa: BLE001
                logger.exception("No pude avisar por voz el fin de la "
                                 "interpolación.")
        # Fallback: consola.
        print(f"[Video] {frase}")
