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
    "juego.lanzador_iniciado": [
        "Dale, abro {nombre}. Primero el lanzador; cuando esté listo abro el juego.",
        "Voy con {nombre}: abro el lanzador y después el juego.",
    ],
    "juego.abriendo_juego": [
        "Listo, abriendo {nombre}.",
        "Ya pedí abrir {nombre}. Aceptá el aviso de administrador si aparece.",
    ],
    "juego.ya_abierto": [
        "{nombre} ya está abierto.",
        "Ya tenés {nombre} corriendo.",
    ],
    "juego.lanzador_no_abre": [
        "No pude abrir el lanzador de {nombre}. Revisá la ruta en JUEGOS_LANZADOR.",
        "El lanzador de {nombre} no se abrió; revisá JUEGOS_LANZADOR en config_local.py.",
    ],
    "juego.lanzador_no_aparece": [
        "El lanzador de {nombre} no llegó a abrirse. ¿Aceptaste el aviso de administrador? Probá de nuevo.",
        "Esperé y el lanzador de {nombre} no apareció. Decime de nuevo cuando quieras.",
    ],
    "juego.juego_no_abre": [
        "No pude abrir {nombre}. Revisá la ruta del juego en JUEGOS_LANZADOR.",
        "El juego {nombre} no se abrió; puede que la ruta esté mal o no aceptaste el aviso de administrador.",
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
    "ventana.lista": [
        "Tenés {cantidad} ventanas abiertas: {nombres}{mas}.",
        "Ahora mismo hay abiertas: {nombres}{mas}.",
    ],
    "ventana.ninguna_abierta": [
        "No veo ninguna ventana abierta.",
        "No hay ninguna ventana abierta que yo pueda ver.",
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

    # -------------------------------------------------------------------- recuerdos
    "memoria.guardado": [
        "Anotado, no me olvido.",
        "Listo, lo recuerdo.",
        "Dale, queda guardado.",
    ],
    "memoria.olvidado": [
        "Listo, olvidé: {recuerdo}",
        "Ya está, borré este recuerdo: {recuerdo}",
    ],
    "memoria.varios": [
        "Tengo varios recuerdos que coinciden: {listado}. Decime cuál con más detalle.",
        "Hay más de uno que coincide: {listado}. ¿Cuál querés que olvide?",
    ],
    "memoria.olvidar_sin_texto": [
        "¿Qué recuerdo querés que olvide?",
        "Decime de qué trata el recuerdo que tengo que olvidar.",
    ],
    "memoria.guardar_sin_texto": [
        "¿Qué querés que recuerde?",
        "Decime qué anoto.",
    ],
    "memoria.inactiva": [
        "La memoria no está activa ahora.",
        "No tengo la memoria disponible en este momento.",
    ],
    "memoria.error_olvidar": [
        "No pude olvidar ese recuerdo.",
        "Algo falló al borrar ese recuerdo.",
    ],
    "memoria.error_guardar": [
        "No pude guardar eso ahora mismo.",
        "Algo falló al anotarlo; probá de nuevo.",
    ],
    "memoria.vacia": [
        "Todavía no tengo ningún recuerdo guardado.",
        "No tengo nada anotado por ahora.",
    ],
    "memoria.lista": [
        "Tengo {total} recuerdos: {listado}.",
        "Esto es lo que recuerdo ({total}): {listado}.",
    ],
    "memoria.lista_parcial": [
        "Tengo {total} recuerdos. Los últimos {cantidad}: {listado}.",
        "Recuerdo {total} cosas; las más recientes son: {listado}.",
    ],
    "memoria.charla_olvidada": [
        "Listo, empezamos de cero.",
        "Dale, olvidé lo que veníamos hablando.",
    ],

    # ---------------------------------------------------------------------- Brave
    "brave.sin_cdp": [
        "No puedo controlar Brave ahora: falta el puerto de depuración. Configurá BRAVE_RUTA_EXE para "
        "que lo lance yo, o abrí Brave con --remote-debugging-port.",
        "No logro conectarme a Brave (falta el puerto de depuración). Con BRAVE_RUTA_EXE configurado "
        "lo abro yo con ese puerto.",
    ],
    "brave.pestana_no_encontrada": [
        "No veo ninguna pestaña que diga '{titulo}'.",
        "No encuentro una pestaña con '{titulo}' abierta.",
    ],
    "brave.varias_pestanas": [
        "Hay {cantidad} pestañas con '{titulo}': {nombres}. Decime cuál con más detalle y la cierro.",
        "Encontré {cantidad} pestañas que coinciden con '{titulo}': {nombres}. ¿Cuál cierro?",
    ],
    "brave.pestana_sin_id": [
        "No pude identificar esa pestaña.",
        "Esa pestaña no me dio su identificador, no pude cerrarla.",
    ],
    "brave.no_pude_cerrar": [
        "No pude cerrar la pestaña.",
        "Intenté cerrar la pestaña pero no respondió.",
    ],
    "brave.pestanas": [
        "Tenés {cantidad} pestañas abiertas en Brave: {titulos}{mas}.",
        "En Brave hay {cantidad} pestañas: {titulos}{mas}.",
    ],
    "brave.pestana_cerrada": [
        "Listo, cerré la pestaña.",
        "Ya cerré la pestaña.",
        "Pestaña cerrada.",
    ],
    "brave.buscar_sin_consulta": [
        "¿Qué querés que busque?",
        "Decime qué busco.",
    ],
    "brave.buscado_en_actual": [
        "Listo, busqué {consulta} en la pestaña actual.",
        "Ya está, {consulta} en la pestaña que tenías abierta.",
    ],
    "brave.buscado_en_nueva": [
        "Listo, abrí una pestaña nueva buscando {consulta}.",
        "Busqué {consulta} en una pestaña nueva.",
    ],

    "app.arranque": [
        "Ya estoy lista.",
        "Acá estoy.",
        "Lista para lo que necesites.",
        "Ya arranqué.",
        "Presente.",
    ],

    # ------------------------------------------------------------------ portapapeles
    "portapapeles.traducir": [
        "Listo, lo traduje al {idioma} y quedó en el portapapeles{recortado}. Pegalo con Ctrl+V.",
        "Traducido al {idioma}{recortado}. Ya está copiado, pegalo cuando quieras.",
    ],
    "portapapeles.corregir": [
        "Listo, corregí el texto y lo dejé en el portapapeles. Si no te gusta, decime 'deshacé'.",
        "Texto corregido y copiado. Pegalo con Ctrl+V; con 'deshacé' vuelve el original.",
    ],
    "portapapeles.reescribir": [
        "Listo, lo reescribí y quedó en el portapapeles. Con 'deshacé' vuelve el original.",
        "Reescrito y copiado. Pegalo con Ctrl+V.",
    ],
    "portapapeles.dicho": ["{texto}"],
    "portapapeles.dicho_y_copiado": [
        "{texto} Además lo dejé copiado en el portapapeles.",
    ],
    "portapapeles.deshecho": [
        "Listo, el portapapeles volvió a como estaba antes.",
        "Deshecho: recuperé el texto original.",
    ],
    "portapapeles.nada_que_deshacer": [
        "No tengo nada para deshacer del portapapeles.",
        "No hice ninguna transformación reciente que pueda deshacer.",
    ],
    "portapapeles.vacio": [
        "No hay nada copiado en el portapapeles.",
        "El portapapeles está vacío. Copiá un texto y volvé a pedírmelo.",
    ],
    "portapapeles.no_es_texto": [
        "Lo que copiaste no es texto (es {contenido}). Copiá un texto y probamos.",
        "En el portapapeles hay {contenido}, no texto. Por ahora solo trabajo con texto.",
    ],
    "portapapeles.sin_idioma": [
        "¿A qué idioma lo traduzco?",
        "Decime a qué idioma traducir lo que copiaste.",
    ],
    "portapapeles.sin_instruccion": [
        "¿Cómo lo reescribo? Por ejemplo: más formal, más corto o más simple.",
        "Decime cómo querés el texto: más formal, más corto, más simple...",
    ],
    "portapapeles.accion_desconocida": [
        "No sé hacer eso con el portapapeles. Puedo traducir, corregir, reescribir, resumir, explicar o leer.",
        "Con lo copiado puedo traducir, corregir, reescribir, resumir, explicar o leer. ¿Cuál querés?",
    ],
    "portapapeles.sin_respuesta": [
        "No pude procesar el texto ahora mismo. Revisá la conexión o la clave de Groq.",
        "El modelo no me respondió; probá de nuevo en un rato.",
    ],
    "portapapeles.no_pude_copiar": [
        "Tengo el resultado pero no pude copiarlo al portapapeles. Probá de nuevo.",
        "No logré escribir en el portapapeles; puede que otro programa lo esté usando.",
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

    # -------------------------------------------------------------------- comedor
    "comedor.iniciando": [
        "Dale, me pongo con el comedor. Se va a abrir una ventana del navegador: no la toques y te aviso cuando termine.",
        "Voy con el comedor. Se abre una ventana aparte; dejala trabajar y te cuento.",
    ],
    "comedor.inscripto": [
        "Listo, quedaste inscripto al comedor para {dia}.",
        "Ya está: te inscribí al comedor para {dia}.",
        "Inscripción hecha para {dia}. A comer.",
    ],
    "comedor.ya_inscripto": [
        "Ya estabas inscripto al comedor para {dia}, no hizo falta hacer nada.",
        "Para {dia} ya figurás inscripto en el comedor.",
    ],
    "comedor.sin_comidas": [
        "No aparece ninguna comida para {dia}, así que no había nada para inscribirse.",
        "El comedor no tiene comidas cargadas para {dia} todavía.",
    ],
    "comedor.no_habilitado": [
        "Hay comida para {dia} pero la inscripción no está habilitada ({detalle}).",
        "No pude inscribirte para {dia}: {detalle}.",
    ],
    "comedor.desconocido": [
        "Entré al comedor pero no reconocí la página o no pude confirmar la inscripción para {dia}. Revisalo a mano.",
        "No estoy segura de haberte inscripto para {dia}: la página no se parece a lo que espero. Fijate vos.",
    ],
    "comedor.listo_para_inscribir": [
        "Simulacro: para {dia} se puede inscribir ({detalle}).",
    ],
    "comedor.login_fallo": [
        "No pude iniciar sesión en el comedor: {detalle}. Revisá el usuario en config_local.py y guardá la contraseña de nuevo con guardar-clave.",
        "El comedor no me dejó entrar ({detalle}). Revisá usuario y contraseña.",
    ],
    "comedor.usuario_invalido": [
        "Tu COMEDOR_USUARIO tiene caracteres que la página no acepta: solo letras, números y guion bajo. Revisalo en config_local.py.",
        "El usuario del comedor no es válido (solo letras, números y _). Fijate que no tenga comillas o símbolos de más.",
    ],
    "comedor.sin_usuario": [
        "Falta tu usuario del comedor. Agregá COMEDOR_USUARIO en config_local.py.",
        "No tengo tu usuario del comedor: ponelo en COMEDOR_USUARIO.",
    ],
    "comedor.sin_clave": [
        "No tengo guardada tu contraseña del comedor. Guardala con: python -m miku.plugins.productividad.comedor guardar-clave.",
        "Me falta la contraseña del comedor; guardala con el comando guardar-clave.",
    ],
    "comedor.sin_navegador": [
        "No pude abrir el navegador para el comedor. Revisá BRAVE_RUTA_EXE en config_local.py.",
        "El navegador no arrancó, así que no pude hacer el trámite del comedor.",
    ],
    "comedor.error": [
        "Algo falló haciendo el trámite del comedor ({detalle}). Probá de nuevo o hacelo a mano.",
        "No pude completar lo del comedor: {detalle}.",
    ],
    "comedor.en_curso": [
        "Ya estoy con el trámite del comedor, esperá un momento.",
        "El comedor ya está en marcha; te aviso cuando termine.",
    ],

    # -------------------------------------------------------------------- whatsapp
    "whatsapp.enviando": [
        "Dale, se lo mando por WhatsApp{a}. Tarda unos segundos; te aviso cuando salga.",
        "Voy con el WhatsApp{a}. Te cuento cuando esté enviado.",
    ],
    "whatsapp.enviado": [
        "Listo, ya salió por WhatsApp{a}.",
        "Enviado por WhatsApp{a}.",
        "Ya lo mandé por WhatsApp{a}.",
    ],
    "whatsapp.sin_sesion": [
        "WhatsApp todavía no está vinculado (o se cerró la sesión). Decime 'conectá WhatsApp' y escaneás el código QR.",
        "Me pide el código QR: falta vincular WhatsApp. Decime 'conectá WhatsApp'.",
    ],
    "whatsapp.numero_invalido": [
        "WhatsApp dice que el número {detalle} no existe o no tiene WhatsApp. Revisá el número en config_local.py.",
        "No pude enviarlo: {detalle} no figura en WhatsApp.",
    ],
    "whatsapp.no_cargo": [
        "WhatsApp Web no terminó de cargar. Revisá la conexión y probá de nuevo.",
        "La página de WhatsApp no cargó a tiempo; probá otra vez en un rato.",
    ],
    "whatsapp.no_envio": [
        "Abrí el chat pero no logré enviar {detalle}. Puede que WhatsApp haya cambiado su página.",
        "No pude confirmar que {detalle} haya salido. Fijate en el chat.",
    ],
    "whatsapp.archivo_grande": [
        "No lo mando: {detalle} es demasiado grande para enviarlo así.",
        "El archivo es muy pesado ({detalle}). Probá con uno más chico.",
    ],
    "whatsapp.archivo_no_encontrado": [
        "No encuentro el archivo '{archivo}' para mandarlo por WhatsApp.",
        "No di con '{archivo}'. Decime la ruta o el nombre exacto.",
    ],
    "whatsapp.sin_navegador": [
        "No pude abrir el navegador para WhatsApp. Revisá BRAVE_RUTA_EXE en config_local.py.",
        "Sin el navegador no puedo usar WhatsApp: revisá BRAVE_RUTA_EXE.",
    ],
    "whatsapp.sin_numero": [
        "No tengo el número por defecto de WhatsApp. Agregá WHATSAPP_CONTACTO_DEFAULT en config_local.py.",
        "Me falta WHATSAPP_CONTACTO_DEFAULT para saber a quién mandárselo.",
    ],
    "whatsapp.sin_contacto": [
        "No tengo a '{contacto}' entre los contactos de WhatsApp. Agregalo en WHATSAPP_CONTACTOS con su número.",
        "No conozco a '{contacto}' en WhatsApp; sumalo a WHATSAPP_CONTACTOS.",
    ],
    "whatsapp.sin_contenido": [
        "¿Qué querés que mande por WhatsApp? Decime un mensaje o un archivo.",
        "No me dijiste qué enviar: un mensaje o un archivo.",
    ],
    "whatsapp.en_curso": [
        "Ya estoy con un envío de WhatsApp, esperá un momento.",
        "Hay otro WhatsApp en marcha; te aviso cuando termine.",
    ],
    "whatsapp.error": [
        "Algo falló con WhatsApp ({detalle}). Probá de nuevo.",
        "No pude completar el envío por WhatsApp: {detalle}.",
    ],
    "whatsapp.conectando": [
        "Te abro WhatsApp Web: escaneá el código QR con el teléfono del número que va a enviar. Tenés unos minutos.",
        "Abrí WhatsApp Web. Escaneá el QR con el teléfono (Ajustes, Dispositivos vinculados) y te aviso.",
    ],
    "whatsapp.conectado": [
        "Listo, WhatsApp quedó vinculado. Ya puedo mandar mensajes.",
        "WhatsApp vinculado. Pedime lo que quieras enviar.",
    ],
    "whatsapp.conexion_fallida": [
        "No se completó la vinculación de WhatsApp (venció el código o no se escaneó). Decime 'conectá WhatsApp' otra vez.",
        "La vinculación de WhatsApp no terminó. Podemos intentar de nuevo cuando quieras.",
    ],

    # -------------------------------------------------------------------- telegram
    "telegram.enviado": [
        "Listo, te mandé {nombre}{a} por Telegram.",
        "Enviado: {nombre}{a}, por Telegram.",
        "Ya está, {nombre} salió por Telegram{a}.",
    ],
    "telegram.sin_token": [
        "No tengo el bot de Telegram configurado. Falta TELEGRAM_BOT_TOKEN en config_local.py.",
        "Sin TELEGRAM_BOT_TOKEN no puedo mandar nada por Telegram.",
    ],
    "telegram.sin_chat": [
        "No sé a qué chat mandarlo: falta TELEGRAM_CHAT_ID en config_local.py.",
        "Me falta tu TELEGRAM_CHAT_ID para saber a quién enviárselo.",
    ],
    "telegram.sin_contacto": [
        "No tengo a '{contacto}' entre los contactos de Telegram. Agregalo en TELEGRAM_CONTACTOS.",
        "No conozco a '{contacto}' en Telegram; sumalo a TELEGRAM_CONTACTOS con su ID.",
    ],
    "telegram.archivo_no_encontrado": [
        "No encuentro el archivo '{archivo}' para mandarlo.",
        "No di con '{archivo}'. Decime la ruta o el nombre exacto.",
    ],
    "telegram.archivo_grande": [
        "{nombre} pesa {mb} MB y Telegram solo deja mandar hasta 50 MB con un bot.",
        "Es demasiado grande ({mb} MB): el límite de Telegram para bots es de 50 MB.",
    ],
    "telegram.error_red": [
        "No pude enviarlo por Telegram ahora mismo. Revisá la conexión.",
        "Falló el envío por Telegram; probá de nuevo en un rato.",
    ],
    "telegram.rechazado": [
        "Telegram rechazó el envío: {motivo}.",
        "No se pudo enviar por Telegram ({motivo}).",
    ],

    # ------------------------------------------------------------------------ TIDAL
    "tidal.reproduciendo": [
        "Dale, pongo {titulo}{de}{modo}.",
        "Ahí va {titulo}{de}{modo}.",
        "Poniendo {titulo}{de}{modo}.",
    ],
    "tidal.volumen": [
        "Listo, la música quedó al {nivel}%.",
        "Volumen de TIDAL en {nivel}%.",
        "Dale, TIDAL al {nivel}%.",
    ],
    "tidal.volumen_sin_control": [
        "Para tocar el volumen de TIDAL necesito tenerlo abierto con el control activo. Poné algo con Miku primero.",
        "No puedo manejar el volumen de TIDAL ahora: falta el control. Pedime que ponga algo y lo activo.",
    ],
    "tidal.volumen_sin_valor": [
        "¿A qué nivel querés la música? Decime un número de 0 a 100.",
        "No entendí qué hacer con el volumen. Decime subir, bajar o un nivel.",
    ],
    "tidal.volumen_error": [
        "No pude cambiar el volumen de TIDAL.",
        "Toqué el volumen de TIDAL pero no logré confirmar el cambio.",
    ],
    "tidal.aleatorio_on": [
        "Listo, activé el aleatorio.",
        "Dale, ahora suena en aleatorio.",
    ],
    "tidal.aleatorio_off": [
        "Listo, saqué el aleatorio.",
        "Dale, ahora suena en orden.",
    ],
    "tidal.aleatorio_error": [
        "No pude cambiar el aleatorio de TIDAL. ¿Está abierto con el control activo?",
        "No logré tocar el aleatorio; probá poniendo primero algo en TIDAL.",
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
    "tidal.no_reprodujo": [
        "Encontré {titulo}{de}, pero no logré ponerlo a sonar en TIDAL. Probá de nuevo en un momento.",
        "No pude darle play a {titulo}{de} en TIDAL. Puede que la app todavía esté arrancando.",
    ],
    "tidal.sin_control": [
        "Encontré {titulo}{de}, pero no puedo manejar TIDAL: revisá TIDAL_RUTA_EXE en config_local.py. Abrí la ficha del tema, pero no lo reproduje.",
        "Encontré {titulo}{de} y abrí su ficha en TIDAL, pero sin TIDAL_RUTA_EXE bien configurado no puedo darle play.",
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
