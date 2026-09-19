"""
catalogo_respuestas.py - Frases de las respuestas de las tools (con variantes que rotan).

Regla de honestidad: cuando Miku **no encuentra** algo lo dice como una búsqueda que no dio
resultado ("no veo", "no lo encuentro"), nunca como un hecho que no puede saber ("no está
instalado", "no existe"). Hay un test que lo vigila.

Importar el módulo registra el catálogo (``registrar()`` es idempotente).
"""
from __future__ import annotations

from typing import Dict, List

from miku.voz.frases.banco import Banco, frases

CATALOGO: Dict[str, List[str]] = {
    # ------------------------------------------------------------------ programas
    "programa.no_encontrado": [
        "No encuentro '{nombre}' ni entre los programas, ni en Steam o Epic, ni como ejecutable en el "
        "disco. Si lo usás seguido, agregalo a _APPS en miku/plugins/sistema/programas.py "
        "(o configurá JUEGOS_EPIC / STEAM_RUTA) y la próxima lo abro al instante.",
        "Busqué '{nombre}' en los programas, en Steam, en Epic y en el disco y no lo veo por ningún "
        "lado. Si lo usás seguido, sumalo a _APPS en miku/plugins/sistema/programas.py "
        "(o configurá JUEGOS_EPIC / STEAM_RUTA) y lo abro directo la próxima.",
        "No di con '{nombre}': ni entre los programas, ni en Steam, ni en Epic, ni en el disco. "
        "Podés agregarlo a _APPS en miku/plugins/sistema/programas.py (o configurar JUEGOS_EPIC / "
        "STEAM_RUTA) para que lo abra al instante.",
    ],
    "programa.abriendo": [
        "Abriendo {nombre}.",
        "Dale, abriendo {nombre}.",
        "Ahí abro {nombre}.",
        "Listo, ya abro {nombre}.",
    ],
    "programa.cerrado": [
        "Cerré {nombre}.",
        "Listo, {nombre} cerrado.",
        "Ya cerré {nombre}.",
    ],
    "programa.juegos_steam_vacio": [
        "No encuentro juegos en tu biblioteca de Steam. ¿Está instalado y con la sesión iniciada?",
        "No veo juegos en la biblioteca de Steam. Fijate que Steam esté instalado y con tu cuenta.",
    ],

    # -------------------------------------------------------------------- ventanas
    "ventana.no_encontrada": [
        "No veo ninguna ventana de {app} abierta.",
        "No encuentro una ventana de {app}. ¿Está abierta?",
        "No hay ninguna ventana de {app} que yo pueda ver.",
    ],
    "ventana.monitor_invalido": [
        "No encuentro ese monitor.",
        "No veo ese monitor conectado.",
        "Ese monitor no me aparece. Revisá el número.",
    ],
    "ventana.ninguna_de_dos": [
        "No veo ninguna ventana ni de {a} ni de {b}.",
        "No encuentro ni {a} ni {b} abiertas.",
    ],
    "ventana.falta_una": [
        "No veo ninguna ventana de {falta}, pero acomodé {puesta}.",
        "{falta} no me aparece abierta; igual acomodé {puesta}.",
        "Acomodé {puesta}, pero no encuentro ninguna ventana de {falta}.",
    ],
    "ventana.acoplada": [
        "Listo, acoplé {a} y {b} {orientacion}.",
        "Ya quedaron {a} y {b} {orientacion}.",
        "Dale, {a} y {b} {orientacion}.",
    ],
    "ventana.organizada": [
        "Listo, {izq} a la izquierda y {der} a la derecha.",
        "Ya está: {izq} a la izquierda y {der} a la derecha.",
        "Dale, {izq} de un lado y {der} del otro.",
    ],
    "ventana.organizada_parcial": [
        "Puse {puesta} {lado}, pero no encuentro ninguna ventana de {falta}.",
        "Acomodé {puesta} {lado}; de {falta} no veo ninguna ventana.",
    ],
    "ventana.posicionada": [
        "Puse {app} {lugar}.",
        "Listo, {app} {lugar}.",
        "Ya quedó {app} {lugar}.",
    ],
    "ventana.posicion_invalida": [
        "No entendí la posición. Usá izquierda, derecha, arriba, abajo o completa.",
        "No me quedó clara la posición. Puede ser izquierda, derecha, arriba, abajo o completa.",
    ],
    "ventana.error": [
        "No pude {accion} {app}.",
        "Intenté {accion} {app} pero no me dejó.",
    ],
    "ventana.minimizada": [
        "Minimicé {app}.",
        "Listo, {app} minimizada.",
        "{app} minimizada.",
    ],

    # ----------------------------------------------------------------------- audio
    "audio.app_no_suena": [
        "No encuentro ninguna app sonando que se llame '{app}'. ¿Está abierta y reproduciendo audio?",
        "No veo que '{app}' esté reproduciendo audio ahora. ¿Está abierta?",
        "Ninguna app sonando se llama '{app}'. Fijate que esté abierta y con sonido.",
    ],

    "audio.volumen": [
        "Volumen en {pct}%.",
        "Listo, volumen al {pct}%.",
        "Ya quedó en {pct}%.",
    ],
    "energia.brillo": [
        "Brillo en {pct}%.",
        "Listo, brillo al {pct}%.",
        "Ya quedó el brillo en {pct}%.",
    ],

    # -------------------------------------------------------------------- archivos
    "archivos.sin_resultados": [
        "No encontré archivos{etiqueta} para '{nombre}'.",
        "Busqué '{nombre}'{etiqueta} y no me apareció nada.",
        "No hay resultados{etiqueta} para '{nombre}'.",
    ],

    # ------------------------------------------------------------------ otros plugins
    "captura.monitor_invalido": [
        "No encuentro el monitor {monitor}.",
        "No veo el monitor {monitor} conectado.",
    ],
    "todoist.tarea_no_encontrada": [
        "No encontré ninguna tarea que diga '{descripcion}'.",
        "No veo ninguna tarea con '{descripcion}'.",
        "Ninguna tarea coincide con '{descripcion}'.",
    ],
    "video.sin_videos": [
        "No encontré videos para interpolar en '{carpeta}'.",
        "No veo videos para interpolar en '{carpeta}'.",
    ],
    "brave.sin_pestanas": [
        "No encuentro pestañas para cerrar.",
        "No veo pestañas abiertas para cerrar.",
    ],
    "memoria.sin_recuerdos": [
        "No encontré ningún recuerdo sobre '{consulta}'.",
        "No tengo nada guardado sobre '{consulta}'.",
        "No encuentro recuerdos que hablen de '{consulta}'.",
    ],
    "energia.accion_no_encontrada": [
        "No encuentro esa acción programada.",
        "No veo ninguna acción programada con ese número.",
    ],

    # ------------------------------------------------------------------ traductor
    "traductor.copiado": [
        "Listo, dejé en el portapapeles la traducción al {idioma}: {traduccion}. Pegala con Ctrl+V.",
        "Ya está en el portapapeles, en {idioma}: {traduccion}. Pegala con Ctrl+V.",
        "Traducido al {idioma} y copiado: {traduccion}. Solo falta pegarlo con Ctrl+V.",
    ],
    "traductor.sin_idioma": [
        "¿A qué idioma lo traduzco? Decímelo, o configurá IDIOMA_JUEGO en config_local.py.",
        "Me falta el idioma. Decime a cuál traducir, o dejá uno por defecto en IDIOMA_JUEGO.",
    ],
    "traductor.sin_texto": [
        "No me dijiste qué traducir.",
        "¿Qué querés que traduzca?",
    ],
    "traductor.error": [
        "No pude traducirlo ahora mismo. Revisá la clave de Groq o la conexión.",
        "La traducción no salió; puede ser la conexión o la clave de Groq.",
    ],
    "traductor.sin_portapapeles": [
        "Lo traduje ({traduccion}), pero no pude copiarlo al portapapeles.",
        "La traducción es: {traduccion}. Igual no logré copiarla al portapapeles.",
    ],

    # ---------------------------------------------------------------------- macros
    "macros.lista": [
        "Tengo {cantidad} macros: {nombres}.",
        "Estas son mis {cantidad} macros: {nombres}.",
    ],
    "macros.lista_larga": [
        "Tengo {cantidad} macros, por ejemplo {nombres}. La lista completa la dejé en el registro.",
        "Hay {cantidad} macros. Algunas: {nombres}. El resto está en el registro.",
    ],
    "macros.sin_macros": [
        "Todavía no hay macros configuradas.",
        "No tengo macros configuradas por ahora.",
    ],
    "macros.sin_nombre": [
        "¿Qué macro querés que ejecute?",
        "Decime el nombre de la macro que querés.",
    ],
    "macros.desconocida": [
        "No conozco la macro '{nombre}'. Algunas que tengo: {nombres}.",
        "No encuentro una macro llamada '{nombre}'. Tengo, por ejemplo: {nombres}.",
    ],

    # ----------------------------------------------------------------------- modos
    "modo.sin_modo": [
        "No tengo ningún modo activo del que salir. No toqué nada.",
        "No hay ningún modo activo, así que no cambié nada.",
    ],
    "modo.restaurado": [
        "Listo, salí del modo y dejé todo como estaba ({restaurado}).",
        "Ya está: salí del modo y volví {restaurado} a como estaban.",
    ],
    "modo.nada_que_restaurar": [
        "Salí del modo, pero no había nada para restaurar.",
        "Salí del modo; no había nada que devolver a su estado anterior.",
    ],
    "modo.restauracion_parcial": [
        "Salí del modo. Restauré: {restaurado}. No pude restaurar: {fallos}.",
        "Salí del modo, pero no todo salió bien. Volvió: {restaurado}. Falló: {fallos}.",
    ],
    "modo.ya_nativa": [
        "La pantalla ya está en su resolución nativa, {ancho} por {alto}.",
        "Ya estás en la resolución nativa ({ancho} por {alto}).",
    ],
    "modo.nativa_aplicada": [
        "Listo, puse la pantalla en su resolución nativa: {ancho} por {alto}.",
        "Volví a la resolución nativa, {ancho} por {alto}.",
    ],
    "modo.nativa_error": [
        "No pude poner la resolución nativa ({ancho} por {alto}). Probá desde la configuración de pantalla de Windows.",
        "Intenté volver a {ancho} por {alto} pero no me dejó. Podés hacerlo desde la configuración de pantalla.",
    ],
    "modo.sin_resolucion_nativa": [
        "No pude averiguar la resolución nativa del monitor.",
        "No logro leer las resoluciones que soporta el monitor.",
    ],

    # ------------------------------------------------------------------------ TIDAL
    "tidal.reproduciendo": [
        "Dale, pongo {titulo}{de}.",
        "Ahí va {titulo}{de}.",
        "Poniendo {titulo}{de}.",
    ],
    "tidal.sin_consulta": [
        "¿Qué querés que ponga en TIDAL?",
        "Decime qué canción, álbum o artista busco en TIDAL.",
    ],
    "tidal.sin_resultados": [
        "No encuentro '{consulta}' en TIDAL.",
        "Busqué '{consulta}' en TIDAL y no me apareció nada. ¿Probamos con otro nombre?",
    ],
    "tidal.sin_sesion": [
        "Para buscar música por nombre primero tengo que conectarme a tu cuenta de TIDAL. Decime 'conectá TIDAL'.",
        "Todavía no estoy conectada a tu cuenta de TIDAL. Decime 'conectá TIDAL' y lo hacemos.",
    ],
    "tidal.sin_libreria": [
        "Para poner música por nombre necesito la librería tidalapi. Instalala con pip install tidalapi y probamos.",
        "Me falta tidalapi para buscar en TIDAL. Se instala con pip install tidalapi.",
    ],
    "tidal.no_abre": [
        "Encontré el tema pero no pude abrirlo en TIDAL. ¿Está instalada la app de escritorio?",
        "No logré abrir TIDAL con ese tema. Revisá que la app esté instalada.",
    ],
    "tidal.conectando": [
        "Te abrí el navegador: iniciá sesión en TIDAL y aprobá el acceso. Yo espero.",
        "Abrí la página de TIDAL en el navegador. Aprobá el acceso y te aviso cuando termine.",
    ],
    "tidal.ya_conectado": [
        "Ya estoy conectada a tu cuenta de TIDAL.",
        "TIDAL ya está conectado, podés pedirme música.",
    ],
    "tidal.conexion_error": [
        "No pude empezar la conexión con TIDAL ahora. Probá de nuevo en un rato.",
        "No logré iniciar el acceso a TIDAL. Puede ser la conexión o que ya haya uno en curso.",
    ],
    "tidal.conectado": [
        "Listo, ya estoy conectada a TIDAL. Ahora podés pedirme música por nombre.",
        "TIDAL conectado. Pedime lo que quieras escuchar.",
    ],
    "tidal.conexion_fallida": [
        "No se completó la conexión con TIDAL. Cuando quieras, decime 'conectá TIDAL' de nuevo.",
        "La conexión con TIDAL no terminó (venció o se canceló). Podemos intentar otra vez.",
    ],

    # ----------------------------------------------------------------------- clima
    "clima.sin_ciudad": [
        "No sé de qué ciudad me hablás. Configurá CIUDAD_CLIMA en config_local.py o decime una ciudad.",
        "No sé qué ciudad querés. Decime una, o configurá CIUDAD_CLIMA en config_local.py.",
    ],
    "clima.sin_datos": [
        "No pude consultar el clima ahora mismo.",
        "Ahora no me respondió el servicio del clima. Probá de nuevo en un rato.",
        "No logro traer el clima en este momento.",
    ],
}

#: Intenciones de "no lo encuentro": no pueden afirmar hechos que Miku no puede saber.
INTENCIONES_NO_ENCONTRADO = tuple(k for k in CATALOGO
                                  if k.endswith(("no_encontrado", "no_encontrada", "sin_resultados",
                                                 "sin_recuerdos", "no_suena", "monitor_invalido",
                                                 "sin_pestanas", "sin_videos", "vacio")))

#: Fragmentos que afirmarían algo que Miku no sabe (prohibidos en las frases de arriba).
AFIRMACIONES_PROHIBIDAS = ("no está instalad", "no tenés instalad", "no existe", "no hay ningún programa")


def registrar(banco: Banco = frases) -> None:
    """Carga este catálogo en el banco (idempotente: no pisa la rotación si ya está cargado)."""
    if banco.existe("programa.no_encontrado"):
        return
    banco.registrar_varias(CATALOGO)
