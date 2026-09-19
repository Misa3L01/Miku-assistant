"""
archivos.py - Búsqueda de archivos con Everything.

Nunca lanza ejecutables (.exe, .bat, .ps1...): los muestra en su carpeta.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from miku.plataforma.texto import clave_compacta, normalizar
from miku.plugins.base import Plugin
from miku.plataforma.everything import Everything
from miku.plugins.utiles import RutasOfrecidas, abrir_resultado
from miku.voz.frases.respuesta import falla

logger = logging.getLogger("miku.plugins.archivos")


def _normalizar_extension(extension: Optional[str]) -> Optional[str]:
    """Normaliza una extensión escrita por el usuario.

    Acepta ``"pdf"``, ``".pdf"``, ``"*.pdf"``, ``" PDF "`` -> ``"pdf"``.
    Devuelve None si queda vacía.
    """
    if not extension:
        return None
    ext = str(extension).strip().lower()
    ext = ext.lstrip("*").lstrip(".").strip()
    return ext or None


class Archivos(Plugin):
    """Busca archivos con Everything (ranking + reordenado semántico opcional)."""

    nombre = "archivos"
    descripcion = "Busca archivos con Everything (ranking + reordenado semántico opcional)."

    tools: List[dict] = [
        {
            "type": "function",
            "function": {
                "name": "buscar_archivo",
                "description": "Busca un archivo/carpeta en la PC con el "
                               "indexador Everything. Permite filtrar por "
                               "tipo de archivo y opcionalmente ABRIR el "
                               "resultado. Ej: 'buscá un pdf de física', "
                               "'buscá el informe y abrilo', 'buscá capturas "
                               "png'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "Palabra o nombre a buscar "
                                           "(sin la extensión).",
                        },
                        "extension": {
                            "type": "string",
                            "description": "Opcional: filtra por extensión "
                                           "(ej: 'pdf', 'docx', 'mp4').",
                        },
                        "abrir_carpeta": {
                            "type": "boolean",
                            "description": "Si True, abre el Explorador con "
                                           "el archivo seleccionado; si False "
                                           "(default), abre el archivo "
                                           "directo.",
                        },
                    },
                    "required": ["nombre"],
                },
            },
        },
    ]

    def __init__(self) -> None:
        super().__init__()
        self._everything = Everything()
        self._ofrecidas = RutasOfrecidas()

    def initialize(self, event_bus: Any = None) -> None:
        """Deja el plugin listo."""
        super().initialize(event_bus)
        logger.info("Plugin archivos listo.")

    def manejar_tool(self, nombre_tool: str, args: Dict[str, Any],
                     contexto: Dict[str, Any]) -> Any:
        """Resuelve la tool ``buscar_archivo``."""
        if nombre_tool == "buscar_archivo":
            # Si viene "ruta_elegida" (desambiguación ya resuelta) se abre ESO directo,
            # pero solo si es una de las rutas que nosotros ofrecimos.
            ruta_elegida = args.get("ruta_elegida")
            if ruta_elegida:
                if not self._ofrecidas.contiene(str(ruta_elegida)):
                    return "Esa opción no es una de las que te ofrecí."
                return self._abrir_archivo_buscado(
                    str(ruta_elegida), bool(args.get("abrir_carpeta", False)))
            return self.buscar_archivo(
                str(args.get("nombre", "")),
                args.get("extension"),
                bool(args.get("abrir_carpeta", False)))
        return None

    # ---------------- Búsqueda con Everything (es.exe) ---------------- #
    def buscar_archivo(self, nombre: str, extension: Optional[str] = None,
                       abrir_carpeta: bool = False,
                       max_resultados: int = 20) -> str:
        """Busca un archivo con Everything y opcionalmente lo abre.

        Args:
            nombre: término a buscar (sin la extensión).
            extension: opcional, filtra por tipo (ej. "pdf").
            abrir_carpeta: si True, abre el Explorador con el archivo
                seleccionado; si False, abre el archivo directo.
            max_resultados: máximo a pedir/mostrar (interno).

        Comportamiento:
            - Filtra por extensión (primero con ``ext:`` nativo de es.exe y,
              además, en Python para asegurar el resultado).
            - Ordena priorizando coincidencias en el NOMBRE del archivo (no en
              la ruta de carpetas).
            - Si hay varios resultados razonables y ninguno destaca, NO abre el
              primero: lista los nombres y pregunta cuál abrir.
            - Si hay un ganador claro, lo abre y confirma por NOMBRE.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            return "¿Qué archivo querés que busque?"

        ext = _normalizar_extension(extension)

        es = self._everything.ruta_es()
        if not es:
            return ("No tengo el indexador Everything (es.exe) configurado. "
                    "Copiá es.exe a la carpeta bin/ o definí su ruta y reintentá.")

        # Construcción de la query de es.exe: la extensión va DENTRO del string
        # de búsqueda (operador `ext:`), no como flag separado.
        consulta = f"ext:{ext} {nombre}" if ext else nombre

        rutas = self._everything.ejecutar(es, consulta, max_resultados)
        print(f"[buscar_archivo] término='{nombre}' ext={ext} "
              f"-> {len(rutas)} resultado(s) crudos.")

        # IMPORTANTE: comprobamos que combinar `ext:` con el término funciona
        # en ESTA versión de es.exe (v1.1.0.37 no siempre matchea bien). Si no
        # trajo nada y hay extensión, reintentamos solo con el término y
        # filtramos por extensión en Python (fallback robusto).
        if ext and not rutas:
            logger.info("es.exe no dio resultados con 'ext:'; reintento solo "
                        "con el término y filtro en Python.")
            rutas = self._everything.ejecutar(es, nombre, max_resultados)
            print(f"[buscar_archivo] fallback sin 'ext:' -> "
                  f"{len(rutas)} resultado(s) crudos.")

        # Fuzzy/tolerancia: si el término tal cual no trajo NADA, probamos
        # variantes. Lo más útil: quitar espacios ("lista animes" -> "listaanimes")
        # y buscar tokenizado. Esto cubre transcripciones de voz con espacios
        # o separadores que no coinciden con el nombre real del archivo.
        if not rutas and " " in nombre:
            for variante in self._variantes_busqueda(nombre):
                rutas = self._everything.ejecutar(es, variante, max_resultados)
                if rutas:
                    print(f"[buscar_archivo] sin coincidencia directa; "
                          f"variante '{variante}' dio {len(rutas)} resultado(s).")
                    break

        # Refuerzo en Python del filtro por extensión (siempre).
        if ext:
            filtradas = [r for r in rutas
                         if os.path.splitext(r)[1].lower().lstrip(".") == ext]
            if filtradas:
                rutas = filtradas
            elif rutas:
                logger.info("Nada con extensión .%s; descarto los %d "
                            "resultados de otras extensiones.", ext, len(rutas))
                rutas = []

        if not rutas:
            etiqueta = f" .{ext}" if ext else ""
            print(f"[buscar_archivo] SIN resultados para '{nombre}'{etiqueta}.")
            return falla("archivos.sin_resultados", etiqueta=etiqueta, nombre=nombre)

        # Ordenamos por coincidencia en el NOMBRE del archivo (no en carpetas).
        terminos = [t for t in normalizar(nombre).split() if t]
        # Preservamos el orden original como desempate estable.
        rankeadas = sorted(
            ((self._everything.puntaje(r, terminos), i, r)
             for i, r in enumerate(rutas)),
            key=lambda par: (-par[0], par[1]))
        rutas_ord = [r for _, _, r in rankeadas]
        puntajes_ord = [p for p, _, _ in rankeadas]

        # Z2 — Re-rank SEMÁNTICO (capa EXTRA, opcional): "PDF de termodinámica"
        # encuentra el archivo correcto aunque el nombre no coincida palabra a
        # palabra. NO reemplaza Everything: suma un bonus por similitud de
        # SIGNIFICADO entre la consulta y el nombre+ruta, y reordena. Si no hay
        # motor de embeddings, devuelve todo igual (sin cambios).
        rutas_ord, puntajes_ord = self._rerank_semantico(
            nombre, rutas_ord, puntajes_ord)

        # ¿Hay ganador claro o hay que preguntar?
        mejor = self._everything.elegir_mejor(rutas_ord, puntajes_ord, nombre=nombre)
        if mejor is None:
            # Varios razonables y ninguno destaca: NO abrimos. Devolvemos un
            # dict de DESAMBIGUACIÓN para que el parser guarde el estado y le
            # pregunte al usuario cuál quiere (así el siguiente "el tercero"
            # tiene contexto). El parser lo convierte en pregunta + opciones.
            print("[buscar_archivo] Varios resultados, preguntando al usuario.")
            top = rutas_ord[:5]
            self._ofrecidas.registrar(top)
            opciones = [
                {
                    "indice": i + 1,
                    "etiqueta": os.path.basename(r),
                    # El "valor" es lo que completa la tool al elegir.
                    "valor": {"nombre": nombre, "extension": ext,
                              "abrir_carpeta": abrir_carpeta,
                              "ruta_elegida": r},
                }
                for i, r in enumerate(top)
            ]
            return {
                "desambiguar": True,
                "tool_origen": "buscar_archivo",
                "args_origen": {"nombre": nombre, "extension": ext,
                                "abrir_carpeta": abrir_carpeta},
                "opciones": opciones,
            }

        print(f"[buscar_archivo] ganador claro: {os.path.basename(mejor)} "
              f"(puntaje={puntajes_ord[0]}) -> abriendo.")
        return self._abrir_archivo_buscado(mejor, abrir_carpeta)

    @staticmethod
    def _variantes_busqueda(nombre: str) -> List[str]:
        """Variantes de búsqueda para tolerar espacios/separadores de más.

        Ej: ``"lista animes"`` -> ``["listaanimes", "lista", "animes"]``.
        Se probarán en orden hasta que una dé resultados.
        """
        variantes: List[str] = []
        compacto = clave_compacta(nombre)
        if compacto and compacto != normalizar(nombre):
            variantes.append(compacto)
        # Último recurso: buscar por tokens sueltos (el más largo primero).
        tokens = sorted((t for t in normalizar(nombre).split() if len(t) >= 3),
                        key=len, reverse=True)
        variantes.extend(tokens)
        return variantes

    def _rerank_semantico(self, nombre: str, rutas: List[str],
                          puntajes: List[int], max_candidatos: int = 12
                          ) -> tuple:
        """Re-rankea los resultados por similitud semántica (opcional).

        Toma hasta ``max_candidatos`` resultados y les suma un bonus de
        similitud coseno (consulta vs "nombre + carpeta"). Es best-effort: si
        el módulo de embeddings no está disponible, devuelve los mismos
        valores SIN cambios.

        El bonus se escala a la misma magnitud que ``_puntaje_resultado`` (que
        usa decenas de puntos) para no pisar coincidencias exactas de nombre.
        """
        try:
            from miku.cerebro.memoria import embeddings  # import tardío
            if embeddings is None or not embeddings.disponible():
                return rutas, puntajes
            vec_consulta = embeddings.embeber(nombre)
            if vec_consulta is None:
                return rutas, puntajes
        except Exception:  # noqa: BLE001
            return rutas, puntajes

        limite = min(len(rutas), int(max_candidatos))
        # Re-puntuamos los primeros `limite` (el resto queda con su puntaje).
        nuevos: List[tuple] = []
        for idx, ruta in enumerate(rutas):
            base = puntajes[idx] if idx < len(puntajes) else 0
            if idx < limite:
                try:
                    etiqueta = f"{os.path.basename(ruta)} " \
                               f"{os.path.dirname(ruta)}"
                    vec = embeddings.embeber(etiqueta)
                    sim = embeddings.similitud(vec_consulta, vec)
                    # El bonus va de 0 a ~20 puntos (no supera una coincidencia
                    # exacta de nombre, que vale 50): es un desempate, no un piso.
                    base = base + int(round(max(0.0, sim) * 20))
                except Exception:  # noqa: BLE001
                    pass
            nuevos.append((base, idx, ruta))

        nuevos.sort(key=lambda par: (-par[0], par[1]))
        return ([r for _, _, r in nuevos], [p for p, _, _ in nuevos])

    # Extensiones que EJECUTAN código: ``buscar_archivo`` nunca las lanza
    # directo (buscar "el instalador" no debe correrlo), solo muestra la carpeta.
    _EXT_EJECUTABLES = frozenset({
        ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse",
        ".wsf", ".scr", ".reg", ".com", ".hta", ".cpl",
    })

    def _abrir_archivo_buscado(self, ruta: str, abrir_carpeta: bool) -> str:
        """Abre el resultado de ``buscar_archivo``; los ejecutables solo se muestran."""
        if (not abrir_carpeta
                and os.path.splitext(ruta)[1].lower() in self._EXT_EJECUTABLES):
            logger.info("'%s' es ejecutable: lo muestro en su carpeta.", ruta)
            abrir_carpeta = True
        return abrir_resultado(ruta, abrir_carpeta)
