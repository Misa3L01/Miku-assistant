"""Tests del motor proactivo, sus reglas, el banco de frases y el módulo de Open-Meteo.

Todo con relojes y lecturas falsas: no esperan, no hablan y no tocan la red ni el hardware.
"""
from __future__ import annotations

import random
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from miku.plataforma import hardware, openmeteo
from miku.servicios import proactivo, reglas_proactivas as rp
from miku.servicios.proactivo import Aviso, Contexto, MotorProactivo, Regla
from miku.voz.frases import catalogo_proactivo
from miku.voz.frases.banco import Banco


# --------------------------------------------------------------------------- #
# Banco de frases
# --------------------------------------------------------------------------- #
def test_banco_no_repite_la_variante_anterior():
    b = Banco(random.Random(1))
    b.registrar("x", ["a", "b", "c"])
    seguidas = [b.elegir("x") for _ in range(60)]
    assert all(p != q for p, q in zip(seguidas, seguidas[1:]))
    assert set(seguidas) == {"a", "b", "c"}


def test_banco_una_sola_variante_sirve():
    b = Banco()
    b.registrar("x", ["hola"])
    assert b.elegir("x") == b.elegir("x") == "hola"


def test_banco_datos_faltantes_no_rompen():
    b = Banco()
    b.registrar("x", ["Hola {nombre}!"])
    assert b.elegir("x") == "Hola !"
    assert b.elegir("x", nombre="Misa") == "Hola Misa!"


def test_banco_intencion_inexistente_y_vacia():
    b = Banco()
    with pytest.raises(KeyError):
        b.elegir("nada")
    with pytest.raises(ValueError):
        b.registrar("x", ["", "  "])


def test_catalogo_proactivo_es_coherente():
    """Cada intención tiene título, al menos 3 variantes y solo usa datos previstos."""
    b = Banco()
    catalogo_proactivo.registrar(b)
    assert set(catalogo_proactivo.CATALOGO) == set(catalogo_proactivo.TITULOS)
    for clave, variantes in catalogo_proactivo.CATALOGO.items():
        assert len(variantes) >= 3, clave
        # Rellenar con datos genéricos no debe dejar llaves sueltas.
        datos = {k: "1" for k in ("cuando", "prob", "sensacion", "juego", "detalle", "recurso",
                                  "valor", "temp", "porcentaje", "libre")}
        for _ in range(len(variantes) * 2):
            texto = b.elegir(clave, **datos)
            assert "{" not in texto and texto.strip()


# --------------------------------------------------------------------------- #
# Horario de silencio
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hora,esperado", [
    ("23:30", True), ("02:00", True), ("07:59", True), ("08:00", False), ("15:00", False),
    ("22:59", False),
])
def test_silencio_cruzando_medianoche(hora, esperado):
    h, m = map(int, hora.split(":"))
    assert proactivo.en_horario_de_silencio(datetime(2026, 1, 1, h, m), "23:00", "08:00") is esperado


def test_silencio_sin_configurar_o_mal_escrito():
    ahora = datetime(2026, 1, 1, 3, 0)
    assert not proactivo.en_horario_de_silencio(ahora, "", "")
    assert not proactivo.en_horario_de_silencio(ahora, "cualquiera", "08:00")
    assert not proactivo.en_horario_de_silencio(ahora, "10:00", "10:00")


# --------------------------------------------------------------------------- #
# Motor: política
# --------------------------------------------------------------------------- #
class Reloj:
    """Reloj falso: avanza a mano; ``ahora`` y ``mono`` van juntos."""

    def __init__(self, inicio: datetime = datetime(2026, 5, 1, 12, 0)) -> None:
        self.dt = inicio
        self.seg = 1000.0

    def avanzar(self, segundos: float) -> None:
        from datetime import timedelta
        self.dt += timedelta(seconds=segundos)
        self.seg += segundos

    def ahora(self) -> datetime:
        return self.dt

    def mono(self) -> float:
        return self.seg


class ReglaFija(Regla):
    """Devuelve siempre los mismos avisos."""

    def __init__(self, *avisos: Aviso, nombre: str = "fija", intervalo: float = 1.0) -> None:
        self.nombre, self.intervalo, self._avisos = nombre, intervalo, list(avisos)

    def evaluar(self, ctx: Contexto):
        return list(self._avisos)


def _motor(cfg, reglas: List[Regla], reloj: Reloj, juego: Optional[str] = None,
           ruta_estado=None, **cambios: Any):
    for k, v in cambios.items():
        cfg.valores[k] = v
    salida: List[tuple] = []
    banco = Banco(random.Random(0))
    motor = MotorProactivo(cfg, reglas, lambda t, m: salida.append((t, m)),
                           detector_juego=lambda c: juego, ahora=reloj.ahora, reloj=reloj.mono,
                           banco=banco, ruta_estado=ruta_estado)
    return motor, salida


def _aviso(**kw: Any) -> Aviso:
    kw.setdefault("clave", "bateria_baja")
    kw.setdefault("intencion", "pc.bateria")
    kw.setdefault("datos", {"porcentaje": 10})
    return Aviso(**kw)


def test_emite_titulo_y_frase(cfg):
    motor, salida = _motor(cfg, [ReglaFija(_aviso())], Reloj())
    assert motor.tick() == 1
    titulo, mensaje = salida[0]
    assert titulo == "Batería baja" and "10" in mensaje


def test_cooldown_por_clave(cfg):
    reloj = Reloj()
    motor, salida = _motor(cfg, [ReglaFija(_aviso())], reloj, proactivo_cooldown_min=30,
                           proactivo_max_por_hora=0)
    motor.tick()
    reloj.avanzar(29 * 60)
    motor.tick()
    assert len(salida) == 1
    reloj.avanzar(2 * 60)
    motor.tick()
    assert len(salida) == 2


def test_una_vez_por_dia_y_se_recuerda_al_reiniciar(cfg, tmp_path):
    ruta = tmp_path / "estado.json"
    aviso = lambda: _aviso(clave="clima.lluvia", intencion="clima.lluvia",  # noqa: E731
                           datos={"cuando": "pronto", "prob": 80}, una_vez_por_dia=True,
                           cooldown_min=0)
    reloj = Reloj()
    motor, salida = _motor(cfg, [ReglaFija(aviso())], reloj, ruta_estado=ruta)
    motor.tick()
    reloj.avanzar(3 * 3600)
    motor.tick()
    assert len(salida) == 1

    # "Reinicio": un motor nuevo con el mismo archivo de estado no repite el aviso de hoy.
    hoy = Reloj(datetime.now().replace(hour=12, minute=0))
    m1, s1 = _motor(cfg, [ReglaFija(aviso())], hoy, ruta_estado=ruta)
    m1.tick()
    m2, s2 = _motor(cfg, [ReglaFija(aviso())], hoy, ruta_estado=ruta)
    m2.tick()
    assert (len(s1), len(s2)) == (1, 0)

    # Al día siguiente vuelve a poder avisar.
    hoy.avanzar(24 * 3600)
    m2.tick()
    assert len(s2) == 1


def test_estado_de_otro_dia_se_descarta(cfg, tmp_path):
    ruta = tmp_path / "estado.json"
    ruta.write_text('{"dia": {"clima.frio": "2001-01-01"}}', encoding="utf-8")
    motor, _ = _motor(cfg, [], Reloj(), ruta_estado=ruta)
    assert motor._dia == {}


def test_horario_de_silencio_no_avisa(cfg):
    reloj = Reloj(datetime(2026, 5, 1, 2, 0))
    motor, salida = _motor(cfg, [ReglaFija(_aviso())], reloj)
    assert motor.tick() == 0 and not salida
    reloj.avanzar(7 * 3600)  # 09:00
    assert motor.tick() == 1


def test_no_molestar_en_juego(cfg):
    motor, salida = _motor(cfg, [ReglaFija(_aviso())], Reloj(), juego="cs2")
    assert motor.tick() == 0
    motor, salida = _motor(cfg, [ReglaFija(_aviso())], Reloj(), juego="cs2",
                           proactivo_no_molestar_en_juego=False)
    assert motor.tick() == 1


def test_los_avisos_que_ignoran_juego_pasan(cfg):
    motor, salida = _motor(cfg, [ReglaFija(_aviso(ignora_juego=True))], Reloj(), juego="cs2")
    assert motor.tick() == 1


def test_presupuesto_por_hora_y_urgentes(cfg):
    reloj = Reloj()
    reglas = [ReglaFija(_aviso(clave=f"k{i}", cooldown_min=0), nombre=f"r{i}") for i in range(5)]
    reglas.append(ReglaFija(_aviso(clave="urgente", cooldown_min=0, urgente=True), nombre="u"))
    motor, salida = _motor(cfg, reglas, reloj, proactivo_max_por_hora=3)
    motor.tick()
    # 3 normales + el urgente, que no cuenta contra el límite.
    assert len(salida) == 4
    reloj.avanzar(3601)
    assert motor.tick() >= 3  # pasó la hora: el presupuesto se renovó


def test_como_mucho_un_aviso_por_regla_y_vuelta(cfg):
    regla = ReglaFija(_aviso(clave="a"), _aviso(clave="b"))
    motor, salida = _motor(cfg, [regla], Reloj())
    assert motor.tick() == 1


def test_intervalo_de_cada_regla(cfg):
    reloj = Reloj()
    regla = ReglaFija(_aviso(cooldown_min=0), intervalo=60)
    motor, salida = _motor(cfg, [regla], reloj, proactivo_max_por_hora=0)
    motor.tick()
    reloj.avanzar(30)
    motor.tick()
    assert len(salida) == 1
    reloj.avanzar(31)
    motor.tick()
    assert len(salida) == 2


def test_una_regla_que_falla_no_frena_a_las_demas(cfg):
    class Mala(Regla):
        nombre = "mala"

        def evaluar(self, ctx):
            raise RuntimeError("boom")

    motor, salida = _motor(cfg, [Mala(), ReglaFija(_aviso())], Reloj())
    assert motor.tick() == 1


def test_una_salida_que_falla_no_rompe_el_motor(cfg):
    motor = MotorProactivo(cfg, [ReglaFija(_aviso())], lambda t, m: 1 / 0,
                           detector_juego=lambda c: None, banco=Banco(),
                           ahora=lambda: datetime(2026, 5, 1, 12, 0))
    assert motor.tick() == 1


def test_juego_con_gracia_de_alt_tab(cfg):
    reloj = Reloj()
    visto = {"juego": "cs2"}
    motor = MotorProactivo(cfg, [], lambda t, m: None, detector_juego=lambda c: visto["juego"],
                           ahora=reloj.ahora, reloj=reloj.mono, banco=Banco())
    assert motor.juego_activo() == "cs2"
    visto["juego"] = None
    reloj.avanzar(30)
    assert motor.juego_activo() == "cs2"   # alt-tab corto: sigue "en juego"
    reloj.avanzar(proactivo.GRACIA_JUEGO_SEG)
    assert motor.juego_activo() is None


def test_hilo_arranca_y_se_detiene(cfg):
    motor, _ = _motor(cfg, [], Reloj())
    motor.iniciar()
    assert motor._hilo is not None and motor._hilo.is_alive()
    motor.detener()
    assert motor._hilo is None


# --------------------------------------------------------------------------- #
# Reglas
# --------------------------------------------------------------------------- #
def _ctx(cfg, juego=None, mono=0.0, hora=12):
    return Contexto(cfg, datetime(2026, 5, 1, hora, 0), mono, juego)


def _pron(**kw: Any) -> openmeteo.Pronostico:
    return openmeteo.Pronostico(nombre="Casa", **{"temperatura": 20, "sensacion": 20, "codigo": 0,
                                                  **kw})


def test_clima_lluvia_proxima(cfg):
    regla = rp.ClimaAvisos(cfg, obtener=lambda: _pron(proxima_lluvia=(80.0, 3)))
    (aviso,) = regla.evaluar(_ctx(cfg))
    assert aviso.intencion == "clima.lluvia" and aviso.una_vez_por_dia
    assert aviso.datos == {"cuando": "en unas 3 horas", "prob": 80}


def test_clima_lluvia_debajo_del_umbral_no_avisa(cfg):
    regla = rp.ClimaAvisos(cfg, obtener=lambda: _pron(proxima_lluvia=(40.0, 2)))
    assert list(regla.evaluar(_ctx(cfg))) == []


def test_clima_ya_esta_lloviendo(cfg):
    regla = rp.ClimaAvisos(cfg, obtener=lambda: _pron(codigo=63))
    (aviso,) = regla.evaluar(_ctx(cfg))
    assert aviso.intencion == "clima.lloviendo" and aviso.clave == "clima.lluvia"


def test_clima_frio_y_calor(cfg):
    frio = rp.ClimaAvisos(cfg, obtener=lambda: _pron(sensacion=4.4))
    (a,) = frio.evaluar(_ctx(cfg))
    assert (a.intencion, a.datos["sensacion"]) == ("clima.frio", 4)
    calor = rp.ClimaAvisos(cfg, obtener=lambda: _pron(sensacion=35.2))
    (a,) = calor.evaluar(_ctx(cfg))
    assert a.intencion == "clima.calor"


def test_clima_normal_no_avisa_y_prioriza_lluvia(cfg):
    assert list(rp.ClimaAvisos(cfg, obtener=lambda: _pron()).evaluar(_ctx(cfg))) == []
    mixto = rp.ClimaAvisos(cfg, obtener=lambda: _pron(sensacion=3, proxima_lluvia=(90.0, 0)))
    avisos = list(mixto.evaluar(_ctx(cfg)))
    assert [a.intencion for a in avisos] == ["clima.lluvia", "clima.frio"]
    assert avisos[0].datos["cuando"] == "en la próxima hora"


def test_clima_sin_datos_o_desactivado(cfg):
    assert list(rp.ClimaAvisos(cfg, obtener=lambda: None).evaluar(_ctx(cfg))) == []
    cfg.valores["proactivo_clima"] = False
    assert list(rp.ClimaAvisos(cfg, obtener=lambda: _pron(sensacion=0)).evaluar(_ctx(cfg))) == []


def test_clima_una_vez_al_dia_de_punta_a_punta(cfg):
    reloj = Reloj()
    regla = rp.ClimaAvisos(cfg, obtener=lambda: _pron(sensacion=3, proxima_lluvia=(90.0, 2)))
    motor, salida = _motor(cfg, [regla], reloj, proactivo_max_por_hora=0)
    motor.tick()                      # lluvia
    reloj.avanzar(regla.intervalo + 1)
    motor.tick()                      # la lluvia ya salió hoy: la regla ofrece el frío...
    reloj.avanzar(regla.intervalo + 1)
    motor.tick()                      # ...y después ya no queda nada por decir hoy
    assert len(salida) == 2
    assert "lluvia" in salida[0][0].lower() or "llover" in salida[0][0].lower()
    assert salida[1][0] == "Hace frío"


def test_bateria_y_disco(cfg):
    bat = SimpleNamespace(percent=15, power_plugged=False)
    (a,) = rp.BateriaBaja(cfg, leer=lambda: bat).evaluar(_ctx(cfg))
    assert a.datos["porcentaje"] == 15
    assert list(rp.BateriaBaja(cfg, leer=lambda: SimpleNamespace(percent=5, power_plugged=True))
                .evaluar(_ctx(cfg))) == []
    assert list(rp.BateriaBaja(cfg, leer=lambda: None).evaluar(_ctx(cfg))) == []
    (a,) = rp.DiscoLleno(cfg, leer=lambda: 3.2).evaluar(_ctx(cfg))
    assert a.datos["libre"] == 3
    assert list(rp.DiscoLleno(cfg, leer=lambda: 50.0).evaluar(_ctx(cfg))) == []
    assert list(rp.DiscoLleno(cfg, leer=lambda: None).evaluar(_ctx(cfg))) == []


def test_estado_al_jugar_espera_y_cuenta_una_vez(cfg):
    gpu = hardware.UsoGpu(70, 65, 3000, 6000)
    regla = rp.EstadoAlJugar(leer_cpu=lambda: 35.0, leer_ram=lambda: 60.0, leer_gpu=lambda: gpu)
    assert list(regla.evaluar(_ctx(cfg, juego=None, mono=0))) == []
    assert list(regla.evaluar(_ctx(cfg, juego="cs2", mono=100))) == []   # recién arrancó
    assert list(regla.evaluar(_ctx(cfg, juego="cs2", mono=120))) == []   # todavía no pasó la espera
    (a,) = regla.evaluar(_ctx(cfg, juego="cs2", mono=150))
    assert a.intencion == "pc.juego_tranquilo" and a.ignora_juego
    assert a.datos["juego"] == "Counter-Strike 2"
    assert a.datos["detalle"] == "CPU al 35%, RAM al 60% y GPU al 70% a 65 grados"
    assert list(regla.evaluar(_ctx(cfg, juego="cs2", mono=400))) == []   # una vez por sesión
    # Sale del juego y vuelve a entrar: cuenta de nuevo.
    list(regla.evaluar(_ctx(cfg, juego=None, mono=500)))
    list(regla.evaluar(_ctx(cfg, juego="cs2", mono=510)))
    assert len(list(regla.evaluar(_ctx(cfg, juego="cs2", mono=560)))) == 1


def test_estado_al_jugar_exigente_y_sin_gpu(cfg):
    regla = rp.EstadoAlJugar(leer_cpu=lambda: 97.0, leer_ram=lambda: 50.0, leer_gpu=lambda: None)
    list(regla.evaluar(_ctx(cfg, juego="genshinimpact", mono=0)))
    (a,) = regla.evaluar(_ctx(cfg, juego="genshinimpact", mono=60))
    assert a.intencion == "pc.juego_exigente"
    assert a.datos["juego"] == "Genshin Impact"
    assert a.datos["detalle"] == "CPU al 97% y RAM al 50%"


def test_estado_al_jugar_desactivable(cfg):
    cfg.valores["proactivo_estado_juego"] = False
    regla = rp.EstadoAlJugar(leer_cpu=lambda: 1.0, leer_ram=lambda: 1.0, leer_gpu=lambda: None)
    list(regla.evaluar(_ctx(cfg, juego="cs2", mono=0)))
    assert list(regla.evaluar(_ctx(cfg, juego="cs2", mono=999))) == []


def test_carga_sostenida_no_avisa_por_picos(cfg):
    valores = iter([95.0] * 4 + [10.0] + [95.0] * 20)
    regla = rp.CargaSostenida(leer_cpu=lambda: next(valores), leer_ram=lambda: 10.0)
    avisos = [a for _ in range(24) for a in regla.evaluar(_ctx(cfg))]
    # Los 4 primeros picos se descartan por el corte; después hacen falta LECTURAS seguidas.
    assert len(avisos) == 1 and avisos[0].datos["recurso"] == "CPU"


def test_gpu_caliente_es_urgente_e_ignora_juego(cfg):
    caliente = hardware.UsoGpu(99, 90, 1, 2)
    (a,) = rp.GpuCaliente(lambda: caliente).evaluar(_ctx(cfg, juego="cs2"))
    assert a.urgente and a.ignora_juego and a.datos["temp"] == 90
    assert list(rp.GpuCaliente(lambda: hardware.UsoGpu(99, 70, 1, 2)).evaluar(_ctx(cfg))) == []
    assert list(rp.GpuCaliente(lambda: None).evaluar(_ctx(cfg))) == []


def test_nombres_de_juego():
    assert rp.nombre_de_juego("fortniteclient-win64-shipping") == "Fortnite"
    assert rp.nombre_de_juego("hollowknight") == "Hollowknight"
    assert rp.nombre_de_juego("some_game-win64") == "Some"


def test_reglas_por_defecto_segun_config(cfg):
    nombres = {r.nombre for r in rp.reglas_por_defecto(cfg)}
    assert {"bateria", "disco", "estado_juego", "carga", "gpu", "clima"} <= nombres
    cfg.valores["proactivo_clima"] = False
    assert "clima" not in {r.nombre for r in rp.reglas_por_defecto(cfg)}


# --------------------------------------------------------------------------- #
# Open-Meteo (con la red simulada)
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, datos):
        self._d = datos

    def json(self):
        return self._d


def _falso_get(datos):
    return lambda url, params=None, timeout=None: _Resp(datos)


def test_pronostico_calcula_proxima_lluvia(monkeypatch):
    horas = [f"2026-05-01T{h:02d}:00" for h in range(24)]
    probs = [0] * 24
    probs[15], probs[17] = 30, 75
    datos = {"current": {"time": "2026-05-01T14:20", "temperature_2m": 18.2,
                         "apparent_temperature": 16.0, "weather_code": 3},
             "daily": {"temperature_2m_max": [21], "temperature_2m_min": [11],
                       "precipitation_probability_max": [80]},
             "hourly": {"time": horas, "precipitation_probability": probs}}
    monkeypatch.setattr(openmeteo.requests, "get", _falso_get(datos))
    p = openmeteo.obtener_pronostico(1.0, 2.0, "Casa")
    assert p.proxima_lluvia == (75.0, 3)        # a las 17 (desde las 14)
    assert (p.temp_min, p.temp_max, p.prob_lluvia_dia) == (11, 21, 80)
    assert not p.esta_lloviendo and p.descripcion == "está nublado"


def test_pronostico_sin_datos_o_con_error_de_red(monkeypatch):
    monkeypatch.setattr(openmeteo.requests, "get", _falso_get({}))
    assert openmeteo.obtener_pronostico(1, 2) is None

    def falla(*a, **k):
        raise OSError("sin red")

    monkeypatch.setattr(openmeteo.requests, "get", falla)
    assert openmeteo.obtener_pronostico(1, 2) is None
    assert openmeteo.geocodificar("Rosario") is None


def test_resolver_ubicacion(monkeypatch, cfg):
    assert openmeteo.resolver_ubicacion(cfg) == (None, None, "")
    cfg.valores.update(clima_lat="-32.9", clima_lon="-60.6")
    assert openmeteo.resolver_ubicacion(cfg) == (-32.9, -60.6, "tu zona")
    monkeypatch.setattr(openmeteo.requests, "get", _falso_get(
        {"results": [{"name": "Rosario", "country": "Argentina", "latitude": -32.9,
                      "longitude": -60.7}]}))
    assert openmeteo.resolver_ubicacion(cfg, "Rosario") == (-32.9, -60.7, "Rosario, Argentina")


# --------------------------------------------------------------------------- #
# Plugin y config
# --------------------------------------------------------------------------- #
def test_plugin_proactivo_arma_y_cierra_el_motor(cfg, monkeypatch, tmp_path):
    from miku.ajustes import carga as config_mod
    from miku.plugins.asistente import proactivo as plugin_mod

    monkeypatch.setattr(config_mod, "config", cfg)
    monkeypatch.setattr(plugin_mod, "BASE_DIR", tmp_path)
    plugin = plugin_mod.AsistenteProactivo()
    dichas: List[str] = []
    bus = SimpleNamespace(voice=SimpleNamespace(decir=dichas.append))
    plugin.initialize(bus)
    try:
        assert plugin._motor is not None and plugin._motor._hilo.is_alive()
        plugin._emitir("Titulo", "Hola")
        assert dichas == ["Hola"]
    finally:
        plugin.cerrar()
    assert plugin._motor is None


def test_plugin_proactivo_desactivado_no_arranca(cfg, monkeypatch):
    from miku.ajustes import carga as config_mod
    from miku.plugins.asistente import proactivo as plugin_mod

    cfg.valores["proactivo_activo"] = False
    monkeypatch.setattr(config_mod, "config", cfg)
    plugin = plugin_mod.AsistenteProactivo()
    plugin.initialize(None)
    assert plugin._motor is None


def test_opciones_proactivas_tienen_default_valido(cfg):
    from miku.ajustes import esquema
    for clave in ("proactivo_max_por_hora", "proactivo_silencio_desde", "proactivo_silencio_hasta",
                  "proactivo_no_molestar_en_juego", "proactivo_clima", "proactivo_lluvia_prob",
                  "proactivo_frio_c", "proactivo_calor_c", "proactivo_estado_juego",
                  "proactivo_carga_pct", "proactivo_gpu_temp_c", "proactivo_clima_intervalo_min"):
        assert clave in esquema.OPCIONES, clave
        assert cfg.get(clave) == esquema.OPCIONES[clave].default
    assert proactivo.parsear_hora(cfg.get("proactivo_silencio_desde")) is not None
