"""
biblioteca_juegos.py - Juegos de Steam (biblioteca detectada) y de Epic (mapa configurado).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from miku.plataforma.texto import clave_compacta, normalizar

logger = logging.getLogger("miku.plugins.biblioteca_juegos")


class BibliotecaJuegos:
    """Juegos instalados en Steam (se escanea la biblioteca) y Epic (mapa configurado a mano)."""

    def __init__(self) -> None:
        # {nombre_normalizado: appid}; se rellena la primera vez que se necesita.
        self._juegos_steam: Optional[Dict[str, str]] = None

    # ---------------- Biblioteca de juegos (Steam / Epic) ---------------- #
    def _rutas_steam_candidatas(self) -> List[str]:
        """Devuelve las rutas candidatas a la carpeta de instalación de Steam.

        Prioriza ``STEAM_RUTA`` de ``config_local.py`` si está definida; si
        no, prueba las ubicaciones típicas de Windows. NO hardcodea rutas de
        usuario: solo las convencionales de Steam.
        """
        candidatas: List[str] = []
        # 1) Rutas de config (config_local.py pisa con update directo).
        try:
            from miku.ajustes import carga as config_mod  # ruta segura
            config_mod.cargar()
            for clave in ("steam_ruta", "ruta_steam", "steam_install"):
                val = str(config_mod.config.get(clave, "") or "").strip()
                if val:
                    candidatas.append(val)
        except Exception:  # noqa: BLE001
            pass

        # 2) Ubicaciones típicas (no dependen del usuario).
        candidatas += [
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
            "C:\\Steam",
        ]
        return candidatas

    def _carpeta_steam(self) -> Optional[str]:
        """Primera carpeta de Steam existente entre las candidatas."""
        from pathlib import Path
        for c in self._rutas_steam_candidatas():
            try:
                p = Path(c)
                if (p / "steamapps").is_dir() or (p / "steam.exe").exists():
                    return str(p)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _escanear_biblioteca_steam(self) -> Dict[str, str]:
        """Escanea las bibliotecas de Steam y arma {nombre_norm: appid}.

        Pasos:
          1. Encontrar la instalación de Steam (config o típicas).
          2. Leer ``steamapps/libraryfolders.vdf`` para TODAS las bibliotecas
             (parseo por regex simple; el usuario puede tener juegos en varios
             discos).
          3. Recorrer ``appmanifest_*.acf`` de cada biblioteca y extraer
             ``appid`` + ``name`` (texto plano).

        Devuelve {} si Steam no está instalado (loguea y sigue, no rompe).
        """
        import re
        from pathlib import Path

        steam = self._carpeta_steam()
        if not steam:
            logger.info("No encontré instalación de Steam; sin biblioteca de "
                        "juegos.")
            return {}

        bibliotecas: List[Path] = []
        base = Path(steam)
        # La instalación principal siempre tiene su propio steamapps.
        bibliotecas.append(base / "steamapps")

        # libraryfolders.vdf lista las bibliotecas adicionales (otros discos).
        vdf = base / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                texto = vdf.read_text(encoding="utf-8", errors="ignore")
                # Nos interesan las claves "path" del VDF; tolera escapes \\.
                for ruta in re.findall(r'"path"\s+"([^"]+)"', texto):
                    ruta = ruta.replace("\\\\", "\\")
                    bibliotecas.append(Path(ruta) / "steamapps")
            except Exception as e:  # noqa: BLE001
                logger.warning("No pude leer libraryfolders.vdf: %s", e)

        juegos: Dict[str, str] = {}
        vistas = set()  # evita recorrer la misma carpeta dos veces
        for lib in bibliotecas:
            try:
                if not lib.is_dir():
                    continue
                real = str(lib.resolve()).lower()
                if real in vistas:
                    continue
                vistas.add(real)
            except Exception:  # noqa: BLE001
                continue

            try:
                acfs = list(lib.glob("appmanifest_*.acf"))
            except Exception as e:  # noqa: BLE001
                logger.debug("No pude listar appmanifest en %s: %s", lib, e)
                continue

            for acf in acfs:
                try:
                    datos = acf.read_text(encoding="utf-8", errors="ignore")
                    m_appid = re.search(r'"appid"\s+"(\d+)"', datos)
                    m_name = re.search(r'"name"\s+"([^"]+)"', datos)
                    if not m_appid or not m_name:
                        continue
                    appid = m_appid.group(1)
                    nombre = m_name.group(1)
                    # Normalizamos igual que el resto del archivo.
                    clave = normalizar(nombre)
                    if clave:
                        juegos[clave] = appid
                except Exception as e:  # noqa: BLE001
                    logger.debug("No pude leer %s: %s", acf, e)

        logger.info("Biblioteca de Steam escaneada: %d juego(s).", len(juegos))
        return juegos

    def _biblioteca_steam(self) -> Dict[str, str]:
        """Devuelve la biblioteca de Steam cacheada (la escanea si hace falta)."""
        if self._juegos_steam is None:
            try:
                self._juegos_steam = self._escanear_biblioteca_steam()
            except Exception as e:  # noqa: BLE001
                logger.error("Fallo escaneando la biblioteca de Steam: %s", e)
                self._juegos_steam = {}
        return self._juegos_steam

    def buscar_steam(self, nombre: str) -> Optional[str]:
        """Busca un juego por nombre (tolerante) en la biblioteca de Steam.

        Matching: exacto (normalizado), luego compacto (sin separadores), y por
        último substring en cualquier dirección. Devuelve el appid o None.
        """
        biblioteca = self._biblioteca_steam()
        if not biblioteca:
            return None
        objetivo = normalizar(nombre)
        objetivo_compacto = clave_compacta(nombre)

        # 1) Exacto / compacto.
        if objetivo in biblioteca:
            return biblioteca[objetivo]
        for clave, appid in biblioteca.items():
            if clave_compacta(clave) == objetivo_compacto:
                return appid
        # 2) Substring (tolerante a "el peak" -> "peak").
        for clave, appid in biblioteca.items():
            if objetivo and (objetivo in clave or clave in objetivo):
                return appid
        for clave, appid in biblioteca.items():
            ck = clave_compacta(clave)
            if objetivo_compacto and (objetivo_compacto in ck
                                      or ck in objetivo_compacto):
                return appid
        return None

    def _juegos_epic_config(self) -> Dict[str, str]:
        """Lee ``JUEGOS_EPIC`` (dict opcional) desde config_local.py."""
        try:
            from miku.ajustes import carga as config_mod  # ruta segura
            config_mod.cargar()
            valor = config_mod.config.get("juegos_epic", {}) or {}
            if isinstance(valor, dict):
                return {str(k): str(v) for k, v in valor.items()}
        except Exception as e:  # noqa: BLE001
            logger.debug("No pude leer JUEGOS_EPIC de config: %s", e)
        return {}

    def buscar_epic(self, nombre: str) -> Optional[str]:
        """Busca un juego en el dict manual de Epic (config_local)."""
        juegos = self._juegos_epic_config()
        if not juegos:
            return None
        objetivo = normalizar(nombre)
        objetivo_compacto = clave_compacta(nombre)
        for clave, item_id in juegos.items():
            ck = normalizar(clave)
            if ck == objetivo or clave_compacta(clave) == objetivo_compacto:
                return item_id
        for clave, item_id in juegos.items():
            ck = normalizar(clave)
            if objetivo and (objetivo in ck or ck in objetivo):
                return item_id
        return None

    def actualizar(self) -> int:
        """Re-escanea la biblioteca de Steam a demanda.

        Returns:
            Cantidad de juegos encontrados.

        Raises:
            Exception: si el escaneo falla (el llamador arma el mensaje).
        """
        self._juegos_steam = self._escanear_biblioteca_steam()
        return len(self._juegos_steam or {})
