"""
everything.py - Búsqueda con Everything (es.exe) y ranking de resultados.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

from miku.plataforma.texto import clave_compacta, normalizar

logger = logging.getLogger("miku.plugins.everything")


class Everything:
    """Búsqueda de archivos con Everything (``es.exe``) y su ranking.

    Lo comparten ``abrir_programa`` (fallback por nombre de ejecutable) y ``buscar_archivo``.
    """

    def ejecutar(self, es: str, consulta: str,
                     max_resultados: int) -> List[str]:
        """Ejecuta es.exe con `consulta` y devuelve la lista de rutas.

        Devuelve [] ante error/timeout (el caller decide el mensaje). No usa
        ``shell=True`` para evitar inyección de comandos.

        Sin ``-s``: esa opción ordena por RUTA COMPLETA y ``-n`` recorta esa
        lista, así que un ``.exe`` bueno en ``D:/Steam`` quedaba fuera detrás
        del ruido alfabético de ``C:/``. El orden por defecto (por nombre)
        deja arriba los nombres que coinciden.
        """
        try:
            resp = subprocess.run(
                [str(es), "-n", str(max_resultados), consulta],
                capture_output=True, text=True, timeout=15, shell=False)
        except subprocess.TimeoutExpired:
            logger.warning("es.exe agotó el timeout para '%s'.", consulta)
            return []
        except Exception as e:  # noqa: BLE001
            logger.error("Error buscando con Everything: %s", e)
            return []
        return [l.strip().strip('"') for l in (resp.stdout or "").splitlines()
                if l.strip()]

    # Extensiones de "accesos directos" que son ruido: casi nunca es lo que
    # el usuario quiere abrir cuando busca un archivo real.
    _EXT_RUIDO = {"lnk", "url", "tmp", "crdownload", "part"}

    def puntaje(self, ruta: str, terminos: List[str]) -> int:
        """Puntúa un resultado priorizando coincidencias en el NOMBRE.

        Más puntos = mejor. Comparaciones case-insensitive Y sin acentos.
        Además:
          - Penaliza accesos directos (.lnk) y archivos temporales, que suelen
            ensuciar la lista (p. ej. los .LNK en AppData\\...\\Recent).
          - Tolera variantes compactas: "lista animes" puntúa alto contra
            "ListaAnimes" (sin separadores).
        """
        nombre_arch = os.path.basename(ruta).lower()
        nombre_sin_ext = os.path.splitext(nombre_arch)[0]
        carpeta = os.path.dirname(ruta).lower()
        ext = os.path.splitext(nombre_arch)[1].lstrip(".")

        # Versiones normalizadas (sin acentos).
        nombre_arch_n = normalizar(nombre_arch)
        nombre_sin_ext_n = normalizar(nombre_sin_ext)
        carpeta_n = normalizar(carpeta)
        nombre_compacto = clave_compacta(nombre_sin_ext)
        consulta_compacta = clave_compacta(" ".join(terminos))

        puntos = 0
        for t in terminos:
            tn = normalizar(t)
            if not tn:
                continue
            if tn in nombre_arch_n:
                puntos += 10
            if tn in nombre_sin_ext_n:
                puntos += 5   # coincidencia en el nombre sin extensión
            elif tn in carpeta_n:
                puntos += 1   # solo aparece en carpetas padre: casi nada

        # Bonus fuerte por coincidencia COMPACTA del nombre completo.
        # "lista animes" -> "listaanimes" == "ListaAnimes" -> match casi exacto.
        if consulta_compacta and consulta_compacta == nombre_compacto:
            puntos += 50
        elif consulta_compacta and consulta_compacta in nombre_compacto:
            puntos += 20

        # Penalizar accesos directos y temporales (ruido). Se usa un valor alto
        # para que un .lnk NUNCA gane por sobre un archivo real aunque el
        # nombre del archivo real solo contenga el término embebido.
        if ext in self._EXT_RUIDO:
            puntos -= 60

        return puntos

    def coincide_nombre(self, ruta: str, nombre: str) -> bool:
        """¿El nombre del archivo coincide (compacto) con lo buscado?

        Se usa para decidir un ganador CLARO: si el nombre sin extensión,
        compactado, es igual al término buscado compactado, no hace falta
        preguntar aunque haya otros resultados (p. ej. los .lnk).
        """
        nombre_arch = os.path.basename(ruta)
        sin_ext = os.path.splitext(nombre_arch)[0]
        buscado = clave_compacta(nombre)
        return bool(buscado) and clave_compacta(sin_ext) == buscado

    def elegir_mejor(self, rutas: List[str],
                      puntajes: Optional[List[int]] = None,
                      nombre: str = "") -> Optional[str]:
        """Devuelve la mejor ruta si hay ganador CLARO; si no, None.

        Criterios (en orden):
          1. Si el mejor resultado tiene coincidencia EXACTA de nombre (compacto)
             mientras los demás no, se elige aunque el margen sea chico. Esto
             resuelve el caso típico "ListaAnimes.xlsm" vs dos .LNK de acceso.
          2. Si no, se exige un margen >= 5 puntos (una coincidencia de nombre)
             sobre el segundo. Si empatan, devolvemos None para que el usuario
             elija y no abramos al azar.
        """
        if not rutas:
            return None
        if len(rutas) == 1:
            return rutas[0]
        if not puntajes:
            # Sin puntajes no podemos juzgar claridad: pedimos que elija.
            return None

        # 1) Coincidencia exacta de nombre en el TOP (y no en el segundo).
        if nombre:
            if (self.coincide_nombre(rutas[0], nombre)
                    and not self.coincide_nombre(rutas[1], nombre)):
                return rutas[0]

        # 2) Margen de puntaje clásico.
        margen = puntajes[0] - puntajes[1]
        return rutas[0] if margen >= 5 else None

    def ruta_es(self) -> Optional[str]:
        """Devuelve la ruta al es.exe si existe (bin/ o la de config)."""
        from miku.ajustes import carga as config_mod  # ruta segura, sin deps pesadas
        base = config_mod.BASE_DIR

        try:
            ruta_conf = str(config_mod.config.get("ruta_everything_es", "") or "").strip()
        except Exception:  # noqa: BLE001
            ruta_conf = ""

        candidatos: List[str] = []
        if ruta_conf:
            candidatos.append(ruta_conf)
        # Fallback por convención: <raíz>/bin/es.exe
        candidatos.append(str(base / "bin" / "es.exe"))

        from pathlib import Path
        for c in candidatos:
            p = Path(c)
            if not p.is_absolute():
                p = base / p
            if p.exists():
                return str(p)
        return None
