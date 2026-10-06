"""Registro de uso (minutos por programa), juego seguido y aviso de horas de juego. Todo con relojes falsos."""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

import pytest

from miku.plugins import registro
from miku.servicios import reglas_proactivas as rp, uso
from miku.servicios.proactivo import MotorProactivo
from miku.voz.frases.banco import Banco


class Reloj:
    """Reloj falso: ``ahora`` (pared) y ``mono`` avanzan juntos, a mano."""

    def __init__(self) -> None:
        self.dt = datetime(2026, 5, 1, 20, 0)
        self.seg = 1000.0

    def avanzar(self, segundos: float) -> None:
        self.dt += timedelta(seconds=segundos)
        self.seg += segundos

    def ahora(self) -> datetime:
        return self.dt

    def mono(self) -> float:
        return self.seg


# --------------------------------------------------------------------------- #
# Frases de duración
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("segundos,texto", [
    (10, "menos de un minuto"), (60, "1 minuto"), (45 * 60, "45 minutos"), (3600, "1 hora"),
    (2 * 3600, "2 horas"), (3600 + 20 * 60, "1 hora y 20 minutos"), (5 * 3600 + 60, "5 horas y 1 minuto"),
])
def test_duracion_en_texto(segundos, texto):
    assert uso.duracion_en_texto(segundos) == texto


def test_nombres_para_decir_en_voz_alta():
    assert uso.nombre_amigable("code") == "Visual Studio Code"
    assert uso.nombre_amigable("cs2") == "Counter-Strike 2"
    assert uso.nombre_amigable("algo-raro") == "Algo"


# --------------------------------------------------------------------------- #
# Registro en disco
# --------------------------------------------------------------------------- #
def test_suma_y_totales_por_dia():
    hoy = {"d": date(2026, 5, 1)}
    r = uso.RegistroUso(hoy=lambda: hoy["d"])
    r.sumar("brave", 600)
    r.sumar("brave", 60)
    r.sumar("cs2", 3600)
    assert r.totales(1) == {"brave": 660, "cs2": 3600}
    hoy["d"] = date(2026, 5, 2)
    r.sumar("brave", 100)
    assert r.totales(1) == {"brave": 100}, "cada día tiene su cuenta"
    assert r.totales(7) == {"brave": 760, "cs2": 3600}
    assert r.totales(1, hasta=date(2026, 5, 1)) == {"brave": 660, "cs2": 3600}


def test_no_suma_vacio_ni_negativo():
    r = uso.RegistroUso()
    r.sumar("", 10)
    r.sumar("brave", 0)
    r.sumar("brave", -5)
    assert r.totales(7) == {}


def test_se_guarda_y_se_vuelve_a_leer(tmp_path):
    ruta = tmp_path / "data" / "uso.json"
    r = uso.RegistroUso(ruta, hoy=lambda: date(2026, 5, 1))
    r.sumar("brave", 125.46)
    r.guardar()
    assert not list(ruta.parent.glob("*.tmp")), "no deja temporales"
    otro = uso.RegistroUso(ruta, hoy=lambda: date(2026, 5, 1))
    assert otro.totales(1) == {"brave": 125.5}


def test_solo_guarda_nombres_y_segundos(tmp_path):
    """Privacidad: el archivo no tiene títulos de ventana ni nada más que programa -> segundos."""
    ruta = tmp_path / "uso.json"
    r = uso.RegistroUso(ruta, hoy=lambda: date(2026, 5, 1))
    r.sumar("brave", 60)
    r.guardar()
    assert json.loads(ruta.read_text(encoding="utf-8")) == {"dias": {"2026-05-01": {"brave": 60.0}}}


def test_sin_cambios_no_reescribe(tmp_path):
    ruta = tmp_path / "uso.json"
    r = uso.RegistroUso(ruta)
    r.guardar()
    assert not ruta.exists()


def test_un_registro_danado_no_rompe_y_se_arranca_de_cero(tmp_path):
    ruta = tmp_path / "uso.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    r = uso.RegistroUso(ruta)
    assert r.totales(7) == {}
    r.sumar("brave", 60)
    r.guardar()
    assert uso.RegistroUso(ruta).totales(1) == {"brave": 60}


def test_se_descarta_la_historia_vieja(tmp_path):
    ruta = tmp_path / "uso.json"
    hoy = date(2026, 5, 1)
    viejo = (hoy - timedelta(days=uso.DIAS_A_GUARDAR + 5)).isoformat()
    ruta.write_text(json.dumps({"dias": {viejo: {"brave": 10}, hoy.isoformat(): {"cs2": 20}}}), encoding="utf-8")
    r = uso.RegistroUso(ruta, hoy=lambda: hoy)
    r.sumar("cs2", 1)
    r.guardar()
    assert list(json.loads(ruta.read_text(encoding="utf-8"))["dias"]) == [hoy.isoformat()]


# --------------------------------------------------------------------------- #
# Juego seguido
# --------------------------------------------------------------------------- #
def test_la_sesion_suma_solo_el_tiempo_con_el_juego_en_primer_plano():
    s = uso.SesionJuego(pausa_seg=900)
    for i in range(10):
        s.registrar("cs2", 1000 + i * 5, 5)
    assert s.actual() == ("cs2", 50, 1)


def test_un_alt_tab_corto_no_corta_la_sesion():
    s = uso.SesionJuego(pausa_seg=900)
    s.registrar("cs2", 1000, 5)
    s.registrar(None, 1300, 5)                      # 5 min en el navegador
    s.registrar("cs2", 1305, 5)
    assert s.actual() == ("cs2", 10, 1), "sigue la misma sesión, sin sumar el rato fuera"


def test_una_pausa_larga_corta_la_sesion_y_la_siguiente_empieza_de_cero():
    s = uso.SesionJuego(pausa_seg=900)
    s.registrar("cs2", 1000, 5)
    s.registrar(None, 2000, 5)                      # más de 15 minutos sin jugar
    assert s.actual() is None
    s.registrar("cs2", 2100, 5)
    assert s.actual() == ("cs2", 5, 2), "otra sesión, con otro número"


def test_volver_tras_una_pausa_larga_sin_pasar_por_none_tambien_es_otra_sesion():
    s = uso.SesionJuego(pausa_seg=900)
    s.registrar("cs2", 1000, 5)
    s.registrar("genshinimpact", 3000, 5)
    assert s.actual() == ("genshinimpact", 5, 2)


def test_cambiar_de_juego_seguido_sigue_la_misma_sesion():
    s = uso.SesionJuego(pausa_seg=900)
    s.registrar("cs2", 1000, 5)
    s.registrar("genshinimpact", 1005, 5)
    assert s.actual() == ("genshinimpact", 10, 1)


# --------------------------------------------------------------------------- #
# El vigía
# --------------------------------------------------------------------------- #
class Entorno:
    """Lo que el vigía lee del sistema, controlado desde el test."""

    def __init__(self) -> None:
        self.reloj = Reloj()
        self.programa: Optional[str] = "brave"
        self.inactivo = 0.0
        self.juegos: List[str] = ["cs2"]
        self.registro = uso.RegistroUso(hoy=lambda: self.reloj.dt.date())
        self.sesion = uso.SesionJuego(900)
        self.vigia = uso.Vigia(self.registro, self.sesion, primer_plano=lambda: self.programa,
                               inactivo=lambda: self.inactivo, juegos=lambda: self.juegos, reloj=self.reloj.mono)

    def pasar(self, segundos: float, paso: float = 5.0) -> None:
        for _ in range(int(segundos / paso)):
            self.reloj.avanzar(paso)
            self.vigia.muestrear()


@pytest.fixture
def entorno() -> Entorno:
    e = Entorno()
    e.vigia.muestrear()                              # la primera muestra solo fija el punto de partida
    return e


def test_el_vigia_suma_el_tiempo_del_programa_en_primer_plano(entorno):
    entorno.pasar(60)
    assert entorno.registro.totales(1) == {"brave": 60}
    entorno.programa = "code"
    entorno.pasar(30)
    assert entorno.registro.totales(1) == {"brave": 60, "code": 30}


def test_la_primera_muestra_no_suma_tiempo_inventado():
    e = Entorno()
    e.vigia.muestrear()
    assert e.registro.totales(1) == {}


def test_si_la_pc_estuvo_suspendida_ese_tiempo_no_cuenta(entorno):
    entorno.pasar(10)
    entorno.reloj.avanzar(3 * 3600)                   # suspendida 3 horas
    entorno.vigia.muestrear()
    assert entorno.registro.totales(1) == {"brave": 10}


def test_sin_tocar_nada_la_pc_sola_no_cuenta_como_uso(entorno):
    entorno.inactivo = uso.INACTIVO_SEG + 1
    entorno.pasar(60)
    assert entorno.registro.totales(1) == {}


def test_jugando_no_cuenta_la_inactividad(entorno):
    """Un joystick no mueve el mouse: con el juego en primer plano se cuenta igual."""
    entorno.programa, entorno.inactivo = "cs2", 3600
    entorno.pasar(60)
    assert entorno.registro.totales(1) == {"cs2": 60} and entorno.sesion.actual()[1] == 60


def test_no_cuenta_pantallas_del_sistema(entorno):
    entorno.programa = "lockapp"
    entorno.pasar(30)
    assert entorno.registro.totales(1) == {}


def test_sin_ventana_en_primer_plano_no_suma(entorno):
    entorno.programa = None
    entorno.pasar(30)
    assert entorno.registro.totales(1) == {}


def test_si_falla_la_lectura_del_sistema_el_vigia_sigue(entorno):
    def falla():
        raise OSError("sin win32")

    entorno.vigia._primer_plano = falla
    entorno.pasar(10)
    assert entorno.registro.totales(1) == {}


def test_la_sesion_de_juego_sigue_los_juegos_de_la_config(entorno):
    entorno.programa = "genshinimpact"
    entorno.pasar(30)
    assert entorno.sesion.actual() is None, "no es un juego configurado"
    entorno.juegos.append("genshinimpact")
    entorno.pasar(30)
    assert entorno.sesion.actual()[0] == "genshinimpact"


# --------------------------------------------------------------------------- #
# Resumen ("¿en qué gasté el tiempo?")
# --------------------------------------------------------------------------- #
def test_resumen_de_hoy_con_lo_que_mas_usaste():
    r = uso.RegistroUso(hoy=lambda: date(2026, 5, 1))
    r.sumar("brave", 2 * 3600 + 600)
    r.sumar("code", 3600)
    r.sumar("cs2", 30 * 60)
    r.sumar("steam", 20)                             # menos de un minuto: no se menciona
    texto = uso.resumen(r, "hoy")
    assert texto.startswith("Hoy usaste la PC 3 horas y 40 minutos.")
    assert "Brave, 2 horas y 10 minutos; Visual Studio Code, 1 hora; Counter-Strike 2, 30 minutos." in texto
    assert "Steam" not in texto


def test_resumen_de_ayer_y_de_la_semana():
    hoy = {"d": date(2026, 5, 1)}
    r = uso.RegistroUso(hoy=lambda: hoy["d"])
    r.sumar("cs2", 7200)
    hoy["d"] = date(2026, 5, 2)
    r.sumar("brave", 600)
    assert uso.resumen(r, "ayer").startswith("Ayer usaste la PC 2 horas.")
    semana = uso.resumen(r, "semana")
    assert semana.startswith("En los últimos 7 días usaste la PC 2 horas y 10 minutos.")
    assert "promedio de 19 minutos por día" in semana


def test_resumen_sin_datos():
    r = uso.RegistroUso(hoy=lambda: date(2026, 5, 1))
    assert "casi no tengo registro" in uso.resumen(r, "hoy")
    assert "Empecé a anotar hace poco" in uso.resumen(r, "ayer")


# --------------------------------------------------------------------------- #
# Aviso de horas de juego
# --------------------------------------------------------------------------- #
def _motor(cfg, sesion, reloj, juego="cs2", **cambios: Any):
    for k, v in cambios.items():
        cfg.valores[k] = v
    salida: List[tuple] = []
    motor = MotorProactivo(cfg, [rp.HorasDeJuego(lambda: sesion["actual"])], lambda t, m: salida.append((t, m)),
                           detector_juego=lambda c: juego, ahora=reloj.ahora, reloj=reloj.mono,
                           banco=Banco(random.Random(0)))
    return motor, salida


def test_avisa_a_las_dos_horas_aunque_este_el_juego_en_primer_plano(cfg):
    reloj, sesion = Reloj(), {"actual": ("cs2", 2 * 3600, 1)}
    motor, salida = _motor(cfg, sesion, reloj, juego="cs2")
    assert motor.tick() == 1
    titulo, mensaje = salida[0]
    assert titulo == "A descansar un rato"
    assert "2 horas" in mensaje and "Counter-Strike 2" in mensaje


def test_no_avisa_antes_de_tiempo(cfg):
    reloj, sesion = Reloj(), {"actual": ("cs2", 2 * 3600 - 60, 1)}
    motor, salida = _motor(cfg, sesion, reloj)
    assert motor.tick() == 0 and salida == []


def test_no_repite_el_aviso_del_mismo_hito(cfg):
    reloj, sesion = Reloj(), {"actual": ("cs2", 2 * 3600, 1)}
    motor, salida = _motor(cfg, sesion, reloj)
    motor.tick()
    for _ in range(10):
        reloj.avanzar(60)
        sesion["actual"] = ("cs2", sesion["actual"][1] + 60, 1)
        motor.tick()
    assert len(salida) == 1


def test_avisa_en_cada_hito_diciendo_el_tiempo_total(cfg):
    reloj, sesion = Reloj(), {"actual": ("cs2", 2 * 3600, 1)}
    motor, salida = _motor(cfg, sesion, reloj)
    motor.tick()
    reloj.avanzar(2 * 3600)
    sesion["actual"] = ("cs2", 4 * 3600, 1)
    assert motor.tick() == 1
    assert "4 horas" in salida[1][1]


def test_si_te_saltaste_un_hito_solo_dice_el_ultimo(cfg):
    """Si Miku estuvo en silencio y llegó al hito de 4 h sin haber dicho el de 2 h, no dice los dos."""
    reloj, sesion = Reloj(), {"actual": ("cs2", 4 * 3600 + 5, 1)}
    motor, salida = _motor(cfg, sesion, reloj)
    assert motor.tick() == 1 and "4 horas" in salida[0][1]


def test_una_sesion_nueva_vuelve_a_avisar(cfg):
    reloj, sesion = Reloj(), {"actual": ("cs2", 2 * 3600, 1)}
    motor, salida = _motor(cfg, sesion, reloj)
    motor.tick()
    reloj.avanzar(3600)
    sesion["actual"] = ("cs2", 2 * 3600, 2)           # otra sesión, otra noche
    assert motor.tick() == 1 and len(salida) == 2


def test_se_puede_cambiar_cada_cuanto_avisa_y_apagar(cfg):
    reloj, sesion = Reloj(), {"actual": ("cs2", 3600, 1)}
    motor, salida = _motor(cfg, sesion, reloj, uso_aviso_horas=1)
    assert motor.tick() == 1 and "1 hora" in salida[0][1]
    motor, salida = _motor(cfg, {"actual": ("cs2", 99 * 3600, 1)}, Reloj(), uso_aviso_horas=0)
    assert motor.tick() == 0


def test_sin_juego_en_curso_no_avisa(cfg):
    motor, salida = _motor(cfg, {"actual": None}, Reloj())
    assert motor.tick() == 0


def test_en_horario_de_silencio_espera_y_avisa_cuando_termina(cfg):
    """No se pierde: se reintenta y sale apenas se puede."""
    reloj = Reloj()
    reloj.dt = datetime(2026, 5, 1, 23, 30)           # ya en silencio (23:00 a 08:00)
    sesion = {"actual": ("cs2", 2 * 3600, 1)}
    motor, salida = _motor(cfg, sesion, reloj, proactivo_silencio_desde="23:00", proactivo_silencio_hasta="08:00")
    assert motor.tick() == 0 and salida == []
    reloj.avanzar(3600)                               # 00:30, sigue en silencio
    assert motor.tick() == 0 and salida == []
    reloj.avanzar(8 * 3600)                           # 08:30
    assert motor.tick() == 1, "el aviso pendiente sale cuando termina el silencio"


def test_la_regla_esta_en_las_reglas_por_defecto(cfg):
    class UsoFalso:
        def sesion_juego(self):
            return ("cs2", 2 * 3600, 1)

    reglas = rp.reglas_por_defecto(cfg, plugin=lambda nombre: UsoFalso() if nombre == "uso_pc" else None)
    horas = next(r for r in reglas if isinstance(r, rp.HorasDeJuego))
    assert horas._sesion() == ("cs2", 2 * 3600, 1)
    sin_plugin = rp.reglas_por_defecto(cfg)
    assert next(r for r in sin_plugin if isinstance(r, rp.HorasDeJuego))._sesion() is None


# --------------------------------------------------------------------------- #
# El plugin
# --------------------------------------------------------------------------- #
def test_el_plugin_responde_la_tool_con_el_resumen(cfg, tmp_path, monkeypatch):
    from miku.ajustes import carga as config_mod
    from miku.plugins.sistema import uso_pc
    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(uso_pc, "RUTA_REGISTRO", tmp_path / "uso.json")
    monkeypatch.setattr(uso.Vigia, "iniciar", lambda self: None)       # sin hilo ni Windows
    p = uso_pc.UsoPC()
    assert "Todavía no empecé" in p.manejar_tool("tiempo_de_uso", {}, {})
    p.initialize()
    p._registro.sumar("brave", 1800)
    assert p.manejar_tool("tiempo_de_uso", {"periodo": "hoy"}, {}).startswith("Hoy usaste la PC 30 minutos.")
    assert p.manejar_tool("otra_cosa", {}, {}) is None
    assert p.sesion_juego() is None
    p.cerrar()
    assert (tmp_path / "uso.json").exists(), "al cerrar se guarda lo pendiente"


def test_el_plugin_se_carga_por_defecto_y_se_apaga_con_la_opcion(cfg):
    entrada = next(e for e in registro._CATALOGO if e.modulo == "sistema.uso_pc")
    assert entrada.condicion(cfg) is True
    cfg.valores["uso_registro"] = False
    assert entrada.condicion(cfg) is False


def test_la_tool_se_elige_cuando_preguntas_en_que_gastaste_el_tiempo():
    from miku.cerebro import enrutador
    from miku.plugins.sistema.uso_pc import UsoPC
    from miku.plugins.sistema.estado_pc import SystemStatus
    from miku.plugins.sistema.audio import Audio
    tools = UsoPC.tools + SystemStatus.tools + Audio.tools
    for frase in ("¿en qué gasté el tiempo hoy?", "cuánto jugué ayer", "resumen de uso de la semana"):
        elegidas = [t["function"]["name"] for t in enrutador.seleccionar(frase, tools, maximo=1)]
        assert elegidas == ["tiempo_de_uso"], frase
