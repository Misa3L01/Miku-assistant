"""
configuracion.py - Ventana de configuración (bandeja > Configuración…).

Dos pestañas:

    **Voz**      Motor (VOICEVOX / AivisSpeech / voz de Windows / propio), qué voz usar (la lista sale del
                 motor en uso), velocidad, tono, entonación, volumen y ritmo, con un botón para probar
                 cómo suena antes de guardar y otro para instalar voces de AivisSpeech (``.aivmx``).
                 Al guardar otro motor, Miku pasa a ese sin reiniciar (y es con el que arranca después).
    **Ajustes**  Cada opción de sí/no y cada una de "elegí entre estas", agrupadas como en
                 ``config_local.py``. El "!" al lado de cada una explica qué hace al pasarle el mouse.

La ventana **no inventa un lugar nuevo para guardar**: escribe en tu ``config_local.py`` (solo la línea de
cada opción que cambiaste, ver ``miku/ajustes/escritura.py``), así la ventana y el archivo dicen siempre
lo mismo. Casi todo vale al instante; lo que no (el VAD, la palabra clave...) queda marcado "al reiniciar".

Las opciones salen del esquema (``miku/ajustes/esquema.py``): una opción nueva aparece sola, con su
descripción como ayuda, sin tocar este archivo.

Corre en el hilo de Qt compartido (``ui/qt_hilo``), como la ventana del modo texto.
"""
from __future__ import annotations

import html
import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from miku.ajustes import escritura, esquema
from miku.ui import qt_hilo

logger = logging.getLogger("miku.configuracion")

try:
    from PyQt5 import QtCore, QtWidgets
except Exception:  # noqa: BLE001
    QtCore = QtWidgets = None

#: Opciones que no se muestran en "Ajustes": las maneja Miku sola (se guardan en preferences.json y
#: escribirlas en config_local.py las dejaría fijas), tienen su propio lugar, o son de compatibilidad.
EXCLUIDAS = frozenset({"modo_entrada", "personalidad", "tecla_invocar", "tts_motor", "comedor_ver"})

#: Opciones que recién se aplican al reiniciar Miku (el resto vale al instante).
REQUIEREN_REINICIO = frozenset({
    "tts_cache", "voicevox_gpu", "stt_vad", "vad_proveedor", "wake_proveedor",
    "stt_proveedor", "memoria_activa", "embeddings_activos", "proactivo_activo", "log_level", "uso_registro",
})

#: Motores de voz para el desplegable: (valor de TTS_MOTOR, cómo se muestra).
MOTORES: Tuple[Tuple[str, str], ...] = (
    ("voicevox", "VOICEVOX"),
    ("aivisspeech", "AivisSpeech"),
    ("sistema", "Voz de Windows (en español)"),
    ("comando", "Motor propio (TTS_COMANDO)"),
)
#: Los motores que tienen voces para elegir (y que hay que arrancar).
MOTORES_CON_VOCES = ("voicevox", "aivisspeech")

#: Deslizadores de la voz: (opción, rótulo, mínimo, máximo, por defecto).
DESLIZADORES: Tuple[Tuple[str, str, float, float, float], ...] = (
    ("voz_velocidad", "Velocidad", 0.5, 2.0, 1.0),
    ("voz_tono", "Tono", -0.15, 0.15, 0.0),
    ("voz_entonacion", "Entonación / emoción", 0.0, 2.0, 1.0),
    ("voz_volumen", "Volumen", 0.0, 2.0, 1.0),
    ("voz_ritmo", "Ritmo (solo AivisSpeech)", 0.0, 2.0, 1.0),
)

#: Frase del botón "Probar voz".
FRASE_PRUEBA = "Hola, así voy a sonar."


def opciones_de_ajustes() -> List[Tuple[str, List[esquema.Opcion]]]:
    """Las opciones de la pestaña "Ajustes" (sí/no y de elegir), agrupadas y en el orden del esquema."""
    grupos: List[Tuple[str, List[esquema.Opcion]]] = []
    for clave_grupo, titulo in esquema.GRUPOS:
        opciones = [o for o in esquema.OPCIONES.values()
                    if o.grupo == clave_grupo and o.clave not in EXCLUIDAS
                    and (o.tipo == "booleano" or o.permitidos)]
        if opciones:
            grupos.append((titulo, opciones))
    return grupos


def ayuda(opcion: esquema.Opcion) -> str:
    """El texto del "!" de una opción (su descripción, y si hace falta reiniciar)."""
    texto = opcion.descripcion
    if opcion.clave in REQUIEREN_REINICIO:
        texto += " (Se aplica al reiniciar Miku.)"
    return texto


def _html(texto: str) -> str:
    """Rich text: así Qt parte las líneas largas del cartelito en vez de hacer una tira infinita."""
    return f"<p>{html.escape(texto)}</p>"


class _PanelConfiguracion:
    """La ventana y sus controles. Sus métodos corren en el hilo de Qt."""

    def __init__(self, cfg: Any, obtener_voz: Callable[[], Any], hilo: Any) -> None:
        self.cfg = cfg
        self._obtener_voz = obtener_voz
        self._hilo = hilo
        self.ventana: Any = None
        self.controles: Dict[str, Any] = {}       # opción -> control (casilla, desplegable, deslizador)
        self._originales: Dict[str, Any] = {}
        self._combo_voz: Any = None
        self._nota_voz: Any = None
        self._boton_agregar: Any = None
        self._estado: Any = None
        #: El motor que suena ahora. Cambia al guardar otro (Miku pasa a ese sin reiniciar).
        self._motor_activo = str(cfg.get("tts_motor", "voicevox") or "voicevox")
        #: Voz recién instalada, para dejarla elegida cuando llegue la lista (ver ``instalar_voz``).
        self._voz_a_elegir: Optional[int] = None

    # ------------------------------------------------------------------ construcción
    def _crear(self) -> None:
        ventana = QtWidgets.QWidget()
        ventana.setWindowTitle("Miku - Configuración")
        ventana.resize(600, 640)
        capas = QtWidgets.QVBoxLayout(ventana)
        pestanas = QtWidgets.QTabWidget()
        pestanas.addTab(self._pestana_voz(), "Voz")
        pestanas.addTab(self._pestana_ajustes(), "Ajustes")
        capas.addWidget(pestanas)

        self._estado = QtWidgets.QLabel("Los cambios se guardan en config_local.py.")
        self._estado.setWordWrap(True)
        capas.addWidget(self._estado)
        botones = QtWidgets.QHBoxLayout()
        botones.addStretch(1)
        cancelar = QtWidgets.QPushButton("Cancelar")
        cancelar.clicked.connect(self.cancelar)
        guardar = QtWidgets.QPushButton("Guardar")
        guardar.setDefault(True)
        guardar.clicked.connect(self.guardar)
        botones.addWidget(cancelar)
        botones.addWidget(guardar)
        capas.addLayout(botones)
        self.ventana = ventana
        self._tomar_originales()

    @staticmethod
    def _signo_ayuda(texto: str) -> Any:
        """El "!" que explica la opción al pasarle el mouse."""
        signo = QtWidgets.QLabel("!")
        signo.setFixedSize(16, 16)
        signo.setAlignment(QtCore.Qt.AlignCenter)
        signo.setStyleSheet("QLabel { background: #e8a33d; color: white; border-radius: 8px; "
                            "font-weight: bold; font-size: 11px; }")
        signo.setToolTip(_html(texto))
        signo.setCursor(QtCore.Qt.WhatsThisCursor)
        return signo

    def _fila(self, grilla: Any, control: Any, texto_ayuda: str, rotulo: Optional[str] = None,
              extra: Any = None) -> None:
        """Agrega una fila a la grilla: [rótulo] [control] [extra] [!], con las columnas alineadas.

        Sin rótulo (las casillas, que ya dicen su nombre), el control ocupa también su columna.
        """
        fila = grilla.rowCount()
        if rotulo:
            grilla.addWidget(QtWidgets.QLabel(rotulo), fila, 0)
            grilla.addWidget(control, fila, 1)
        else:
            grilla.addWidget(control, fila, 0, 1, 2)
        if extra is not None:
            grilla.addWidget(extra, fila, 2)
        grilla.addWidget(self._signo_ayuda(texto_ayuda), fila, 3)

    @staticmethod
    def _grilla() -> Any:
        grilla = QtWidgets.QGridLayout()
        grilla.setColumnStretch(1, 1)                     # el control es el que se estira
        grilla.setHorizontalSpacing(10)
        return grilla

    def _pestana_voz(self) -> Any:
        pagina = QtWidgets.QWidget()
        capas = QtWidgets.QVBoxLayout(pagina)
        grilla = self._grilla()
        capas.addLayout(grilla)

        motor = QtWidgets.QComboBox()
        for valor, nombre in MOTORES:
            motor.addItem(nombre, valor)
        motor.currentIndexChanged.connect(self._al_cambiar_motor)
        self.controles["tts_motor"] = motor
        self._fila(grilla, motor, ayuda(esquema.OPCIONES["tts_motor"]), "Motor de voz")

        combo_voz = QtWidgets.QComboBox()
        combo_voz.addItem("Cargando voces…", None)
        refrescar = QtWidgets.QPushButton("↻")
        refrescar.setFixedWidth(32)
        refrescar.setToolTip("Volver a pedirle las voces al motor (por ejemplo, si lo acabás de abrir).")
        refrescar.clicked.connect(self.cargar_voces)
        self._fila(grilla, combo_voz, "La voz (personaje y estilo) que usa Miku. La lista sale del motor en "
                                      "uso; se aplica al instante.", "Voz", extra=refrescar)
        self._combo_voz = combo_voz
        self._nota_voz = QtWidgets.QLabel("")
        self._nota_voz.setWordWrap(True)
        self._nota_voz.setStyleSheet("color: #888;")
        grilla.addWidget(self._nota_voz, grilla.rowCount(), 1, 1, 3)

        for clave, rotulo, minimo, maximo, _ in DESLIZADORES:
            self._deslizador(grilla, clave, rotulo, minimo, maximo)

        botones = QtWidgets.QHBoxLayout()
        probar = QtWidgets.QPushButton("Probar voz")
        probar.setToolTip(_html("Dice una frase con lo que elegiste arriba, sin guardarlo todavía."))
        probar.clicked.connect(self.probar_voz)
        botones.addWidget(probar)
        agregar = QtWidgets.QPushButton("Agregar voz (.aivmx)…")
        agregar.setToolTip(_html("Instala en AivisSpeech un modelo de voz que descargaste (por ejemplo de "
                                 "AivisHub) y lo deja elegido en la lista. Solo con el motor AivisSpeech."))
        agregar.clicked.connect(self.agregar_voz)
        botones.addWidget(agregar)
        botones.addStretch(1)
        self._boton_agregar = agregar
        capas.addLayout(botones)
        capas.addStretch(1)
        return pagina

    def _deslizador(self, grilla: Any, clave: str, rotulo: str, minimo: float, maximo: float) -> None:
        """Un deslizador de 0,01 en 0,01 con el valor escrito al lado."""
        deslizador = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        deslizador.setRange(round(minimo * 100), round(maximo * 100))
        valor = QtWidgets.QLabel("")
        valor.setMinimumWidth(40)
        valor.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        deslizador.valueChanged.connect(lambda n, e=valor: e.setText(f"{n / 100:.2f}"))
        # Qt no avisa si el valor no cambia (el tono arranca y queda en 0): el número se escribe ya.
        valor.setText(f"{deslizador.value() / 100:.2f}")
        self.controles[clave] = deslizador
        self._fila(grilla, deslizador, esquema.OPCIONES[clave].descripcion, rotulo, extra=valor)

    def _pestana_ajustes(self) -> Any:
        contenido = QtWidgets.QWidget()
        capas = QtWidgets.QVBoxLayout(contenido)
        for titulo, opciones in opciones_de_ajustes():
            caja = QtWidgets.QGroupBox(titulo)
            grilla = self._grilla()
            caja.setLayout(grilla)
            for opcion in opciones:
                if opcion.tipo == "booleano":
                    control = QtWidgets.QCheckBox(opcion.nombre_local)
                    self._fila(grilla, control, ayuda(opcion))
                else:
                    control = QtWidgets.QComboBox()
                    for permitido in opcion.permitidos:
                        control.addItem(str(permitido), permitido)
                    self._fila(grilla, control, ayuda(opcion), opcion.nombre_local)
                self.controles[opcion.clave] = control
            capas.addWidget(caja)
        capas.addStretch(1)
        desplazable = QtWidgets.QScrollArea()
        desplazable.setWidgetResizable(True)
        desplazable.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)   # solo se baja, nunca de costado
        desplazable.setWidget(contenido)
        return desplazable

    # ------------------------------------------------------------------ valores
    def _valor_en_config(self, clave: str) -> Any:
        opcion = esquema.OPCIONES.get(clave)
        return self.cfg.get(clave, opcion.default if opcion else None)

    def _tomar_originales(self) -> None:
        """Pone en los controles lo que dice la config y lo recuerda (para saber qué cambió)."""
        self._originales = {}
        for clave, control in self.controles.items():
            self._escribir_control(control, self._valor_en_config(clave))
            # Lo que quedó a la vista (un valor fuera de rango ya se ve acotado): así una opción que no
            # tocaste nunca cuenta como cambio, ni se reescribe en config_local.py.
            self._originales[clave] = self._leer_control(control)
        self._originales[self._clave_voz()] = self._voz_en_config()
        self._al_cambiar_motor()

    @staticmethod
    def _escribir_control(control: Any, valor: Any) -> None:
        if isinstance(control, QtWidgets.QCheckBox):
            control.setChecked(bool(valor))
        elif isinstance(control, QtWidgets.QComboBox):
            indice = control.findData(valor)
            control.setCurrentIndex(max(indice, 0))
        elif isinstance(control, QtWidgets.QSlider):
            try:
                control.setValue(round(float(valor) * 100))
            except (TypeError, ValueError):
                pass

    @staticmethod
    def _leer_control(control: Any) -> Any:
        """El valor de un control con el tipo que va en config_local.py."""
        if isinstance(control, QtWidgets.QCheckBox):
            return bool(control.isChecked())
        if isinstance(control, QtWidgets.QComboBox):
            return control.currentData()
        if isinstance(control, QtWidgets.QSlider):
            return round(control.value() / 100, 2)
        return None

    def valores_actuales(self) -> Dict[str, Any]:
        """Lo que muestra la ventana ahora, listo para guardar."""
        valores = {clave: self._leer_control(control) for clave, control in self.controles.items()}
        voz = self._combo_voz.currentData() if self._combo_voz is not None else None
        if voz is not None:
            valores[self._clave_voz()] = int(voz)
        return valores

    def cambios(self) -> Dict[str, Any]:
        """Solo lo que cambió: una opción que no tocaste no se escribe en config_local.py."""
        return {clave: valor for clave, valor in self.valores_actuales().items()
                if valor != self._originales.get(clave)}

    # ------------------------------------------------------------------ voces
    def _motor_en_uso(self) -> str:
        """El motor que está sonando ahora."""
        return self._motor_activo

    def _clave_voz(self) -> str:
        motor = self._motor_en_uso()
        return f"{motor if motor in MOTORES_CON_VOCES else 'voicevox'}_speaker_id"

    def _voz_en_config(self) -> Optional[int]:
        try:
            return int(self.cfg.get(self._clave_voz(), 6 if self._clave_voz().startswith("voicevox") else 0))
        except (TypeError, ValueError):
            return None

    def cargar_voces(self) -> None:
        """Pide la lista de voces al motor en otro hilo (es una consulta HTTP) y la muestra al llegar."""
        self._combo_voz.clear()
        self._combo_voz.addItem("Cargando voces…", None)
        self._combo_voz.setEnabled(False)

        def pedir() -> None:
            try:
                voces = self._obtener_voz().voces()
            except Exception:  # noqa: BLE001
                logger.debug("No pude pedir las voces.", exc_info=True)
                voces = []
            self._hilo.ejecutar(lambda: self.mostrar_voces(voces))

        threading.Thread(target=pedir, name="miku_config_voces", daemon=True).start()

    def mostrar_voces(self, voces: List[Tuple[int, str]]) -> None:
        """Llena el desplegable de voces y deja elegida la que se usa ahora."""
        self._combo_voz.clear()
        if not voces:
            self._combo_voz.addItem("El motor no responde: abrilo y tocá ↻", None)
            self._combo_voz.setEnabled(False)
            return
        for identificador, nombre in voces:
            self._combo_voz.addItem(nombre, identificador)
        # Recién instalada, queda elegida (sin guardar todavía); si no, la que se usa ahora.
        actual = self._voz_a_elegir if self._voz_a_elegir is not None else self._originales.get(self._clave_voz())
        self._voz_a_elegir = None
        indice = self._combo_voz.findData(actual)
        self._combo_voz.setCurrentIndex(max(indice, 0))
        if indice < 0 and actual == 0 and self._motor_en_uso() == "aivisspeech":
            # En AivisSpeech, 0 es "la primera voz que tengas": la que quedó elegida es la que ya suena,
            # no un cambio tuyo (si no, se escribiría en config_local.py sin que la hayas tocado).
            self._originales[self._clave_voz()] = self._combo_voz.currentData()
        self._combo_voz.setEnabled(self.controles["tts_motor"].currentData() == self._motor_en_uso())

    def _al_cambiar_motor(self, *_: Any) -> None:
        """Si elegís otro motor, se avisa que Miku pasa a ese al guardar (y que ahí se ven sus voces)."""
        if self._nota_voz is None or self._boton_agregar is None:
            return
        elegido = self.controles["tts_motor"].currentData()
        en_uso = self._motor_en_uso()
        nombres = dict(MOTORES)
        self._boton_agregar.setEnabled(elegido == en_uso == "aivisspeech")
        if elegido != en_uso:
            self._nota_voz.setText(f"Tocá Guardar y Miku pasa a {nombres.get(elegido, elegido)} sin reiniciar; "
                                   "después vas a ver acá sus voces.")
            self._combo_voz.setEnabled(False)
        elif en_uso in MOTORES_CON_VOCES:
            self._nota_voz.setText(f"Voces de {nombres[en_uso]}. La voz y los ajustes de abajo se aplican al "
                                   "instante.")
            self._combo_voz.setEnabled(self._combo_voz.currentData() is not None)
        else:
            self._nota_voz.setText("Este motor no tiene voces para elegir ni estos ajustes.")
            self._combo_voz.setEnabled(False)

    # ------------------------------------------------------------------ acciones
    def _aplicar_en_memoria(self, valores: Dict[str, Any]) -> None:
        for clave, valor in valores.items():
            self.cfg.valores[clave] = valor

    def probar_voz(self) -> None:
        """Dice una frase con la voz y los ajustes elegidos (aplicados en memoria, sin guardar)."""
        claves_voz = {clave for clave, *_ in DESLIZADORES} | {self._clave_voz()}
        self._aplicar_en_memoria({k: v for k, v in self.valores_actuales().items() if k in claves_voz})

        def decir() -> None:
            try:
                self._obtener_voz().decir(FRASE_PRUEBA)
            except Exception:  # noqa: BLE001
                logger.exception("No pude probar la voz.")

        threading.Thread(target=decir, name="miku_config_probar", daemon=True).start()

    def guardar(self) -> Dict[str, Any]:
        """Escribe lo cambiado en config_local.py y lo aplica. Devuelve lo que guardó."""
        cambios = self.cambios()
        if not cambios:
            self._estado.setText("No hay cambios para guardar.")
            return {}
        try:
            ruta = escritura.guardar(cambios, config=self.cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("No pude guardar la configuración.")
            self._estado.setText(f"No pude guardar: {e}")
            return {}
        self._originales.update(cambios)
        reiniciar = sorted(k.upper() for k in cambios if k in REQUIEREN_REINICIO)
        texto = f"Guardado en {ruta.name}: {', '.join(k.upper() for k in cambios)}."
        if reiniciar:
            texto += f" Reiniciá Miku para que tome: {', '.join(reiniciar)}."
        if "tts_motor" in cambios:
            texto += " " + self._activar_motor(str(cambios["tts_motor"]))
        self._estado.setText(texto)
        return cambios

    def _activar_motor(self, motor: str) -> str:
        """Pasa Miku a ``motor`` sin reiniciar. Devuelve qué contarle al usuario.

        La voz ya habla con el motor nuevo desde la próxima frase (la config se actualizó al guardar). Si
        tiene voces, además se lo arranca ya, en segundo plano, y al terminar se muestran sus voces.
        """
        self._motor_activo = motor
        nombre = dict(MOTORES).get(motor, motor)
        if motor not in MOTORES_CON_VOCES:
            self._al_cambiar_motor()
            return f"Miku ya habla con {nombre}."
        self._originales[self._clave_voz()] = self._voz_en_config()
        self._combo_voz.clear()
        self._combo_voz.addItem(f"Arrancando {nombre}…", None)
        self._al_cambiar_motor()

        def arrancar() -> None:
            try:
                self._obtener_voz().precalentar()
            except Exception:  # noqa: BLE001
                logger.exception("No pude arrancar %s.", nombre)
            self._hilo.ejecutar(self.cargar_voces)

        threading.Thread(target=arrancar, name="miku_config_motor", daemon=True).start()
        return f"Pasando a {nombre}: puede tardar hasta un minuto en arrancar."

    def agregar_voz(self) -> None:
        """Botón "Agregar voz": elegís el archivo .aivmx y se instala en AivisSpeech."""
        ruta, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.ventana, "Elegí el modelo de voz de AivisSpeech", "", "Modelos de AivisSpeech (*.aivmx)")
        if ruta:
            self.instalar_voz(ruta)

    def instalar_voz(self, ruta: str) -> None:
        """Instala el modelo en otro hilo (son unos segundos) y después deja elegida la voz nueva."""
        nombre = os.path.basename(ruta)
        self._estado.setText(f"Instalando {nombre} en AivisSpeech… (unos segundos)")
        self._boton_agregar.setEnabled(False)

        def instalar() -> None:
            try:
                nuevas, error = self._obtener_voz().instalar_voz(ruta), ""
            except Exception as e:  # noqa: BLE001
                logger.warning("No pude instalar %s: %s", nombre, e)
                nuevas, error = [], str(e)
            self._hilo.ejecutar(lambda: self._voz_instalada(nombre, nuevas, error))

        threading.Thread(target=instalar, name="miku_config_instalar", daemon=True).start()

    def _voz_instalada(self, nombre: str, nuevas: List[Tuple[int, str]], error: str) -> None:
        self._al_cambiar_motor()                    # vuelve a habilitar el botón
        if error:
            self._estado.setText(f"No pude instalar {nombre}: {error}")
            return
        if nuevas:
            self._voz_a_elegir = nuevas[0][0]
            self._estado.setText(f"Instalé {', '.join(voz for _, voz in nuevas)} y la dejé elegida: "
                                 "probala y tocá Guardar.")
        else:
            self._estado.setText(f"{nombre} ya estaba instalada: elegila en la lista.")
        self.cargar_voces()

    def cancelar(self) -> None:
        """Descarta lo no guardado (también lo que "Probar voz" había aplicado) y cierra."""
        claves_voz = {clave for clave, *_ in DESLIZADORES} | {self._clave_voz()}
        self._aplicar_en_memoria({k: v for k, v in self._originales.items() if k in claves_voz and v is not None})
        for clave, control in self.controles.items():
            self._escribir_control(control, self._originales.get(clave))
        self._estado.setText("Los cambios se guardan en config_local.py.")
        if self.ventana is not None:
            self.ventana.hide()

    def mostrar(self) -> None:
        if self.ventana is None:
            self._crear()
        else:
            self._tomar_originales()             # puede haber cambiado el archivo desde afuera
        self.cargar_voces()
        self.ventana.show()
        self.ventana.raise_()
        self.ventana.activateWindow()


class VentanaConfiguracion:
    """Fachada thread-safe de la ventana de configuración.

    Args:
        cfg: La ``Config`` en uso.
        obtener_voz: Devuelve el motor de voz (``TextoAVoz``); se llama recién al usarlo.

    Raises:
        RuntimeError: si PyQt5 no está disponible.
    """

    def __init__(self, cfg: Any, obtener_voz: Callable[[], Any]) -> None:
        hilo = qt_hilo.obtener() if QtWidgets is not None else None
        if hilo is None:
            raise RuntimeError("PyQt5 es necesario para la ventana de configuración.")
        self._hilo = hilo
        self._panel = _PanelConfiguracion(cfg, obtener_voz, hilo)

    def mostrar(self) -> None:
        """Abre (o trae al frente) la ventana."""
        self._hilo.ejecutar(self._panel.mostrar)
