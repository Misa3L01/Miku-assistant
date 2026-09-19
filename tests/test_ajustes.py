"""Ajustes: esquema único, validación, generador del .example y asistente 'completar'."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from miku.ajustes import carga as config_mod
from miku.ajustes import ejemplo, validacion
from miku.ajustes.esquema import GRUPOS, OBSOLETAS, OPCIONES, REQUISITOS, valores_por_defecto

RAIZ = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- esquema
def test_defaults_de_config_salen_del_esquema(cfg):
    assert set(cfg.valores) == set(OPCIONES)
    assert cfg.valores["voicevox_speaker_id"] == 6
    assert cfg.valores["carpeta_capturas"] == ""


def test_defaults_no_comparten_objetos_mutables():
    a, b = valores_por_defecto(), valores_por_defecto()
    a["app_whitelist"].append("x")
    assert "x" not in b["app_whitelist"]


def test_todas_las_opciones_tienen_grupo_y_descripcion():
    grupos = {g for g, _ in GRUPOS}
    for o in OPCIONES.values():
        assert o.grupo in grupos, o.clave
        assert o.descripcion.strip(), o.clave
        assert o.clave == o.clave.lower()


def test_los_requisitos_apuntan_a_opciones_reales():
    for plugin, (_, requisitos) in REQUISITOS.items():
        for alternativas in requisitos:
            for clave in alternativas:
                assert clave in OPCIONES, f"{plugin}: {clave}"


def test_las_obsoletas_no_estan_en_el_esquema():
    assert not set(OBSOLETAS) & set(OPCIONES)


def test_ninguna_clave_del_codigo_se_lee_sin_estar_en_el_esquema():
    """Las opciones que los módulos leen por nombre deben estar declaradas."""
    for clave in ("voicevox_run_exe", "steam_ruta", "carpeta_capturas", "gemini_api_key",
                  "tesseract_ruta", "brave_debug_port", "discord_guild_id"):
        assert clave in OPCIONES


# ---------------------------------------------------------------- validación
def test_detecta_clave_mal_escrita_con_sugerencia(cfg):
    informe = validacion.validar(cfg.valores, ["carpeta_captura"])
    assert informe.desconocidas == [("carpeta_captura", "carpeta_capturas")]
    assert "CARPETA_CAPTURAS" in informe.avisos()[0]


def test_detecta_obsoletas(cfg):
    informe = validacion.validar(cfg.valores, ["dolar_actual"])
    assert informe.obsoletas and informe.hay_problemas


@pytest.mark.parametrize("clave,valor", [
    ("voicevox_speaker_id", "seis"),
    ("memoria_activa", "quizás"),
    ("app_whitelist", "brave"),
    ("mensajes_juego", ["gg"]),
    ("personalidad", "pirata"),
    ("carpeta_capturas", 5),
])
def test_detecta_tipos_y_valores_invalidos(cfg, clave, valor):
    cfg.valores[clave] = valor
    assert validacion.validar(cfg.valores, [clave]).errores


@pytest.mark.parametrize("clave,valor", [
    ("voicevox_speaker_id", "6"), ("memoria_activa", "no"), ("personalidad", "Formal"),
    ("microfono_index", None), ("embeddings_umbral", "0.5"), ("modo_entrada", "texto"),
])
def test_acepta_valores_validos(cfg, clave, valor):
    cfg.valores[clave] = valor
    assert validacion.validar(cfg.valores, [clave]).errores == []


def test_plugins_incompletos(cfg):
    informe = validacion.validar(cfg.valores, [])
    faltan = {p: f for p, _, f in informe.incompletos}
    assert "vision" in faltan and faltan["vision"] == ["gemini_api_key"]
    assert faltan["clima"] == ["ciudad_clima/clima_lat"]
    cfg.valores["gemini_api_key"] = "abc"
    cfg.valores["clima_lat"] = "-34.6"
    faltan = {p for p, _, _ in validacion.validar(cfg.valores, []).incompletos}
    assert "vision" not in faltan and "clima" not in faltan


def test_origenes_de_los_valores(tmp_path):
    ruta = tmp_path / "preferences.json"
    ruta.write_text(json.dumps({"personalidad": "formal"}), encoding="utf-8")
    c = config_mod.Config(ruta_archivo=ruta)
    c._cargar_preferences()
    assert c.origenes == {"personalidad": "preferences"}


# ---------------------------------------------------------------- .example generado
def test_el_ejemplo_generado_es_python_valido_y_completo():
    texto = ejemplo.generar_ejemplo()
    with warnings.catch_warnings():
        warnings.simplefilter("error")                     # p. ej. secuencias de escape inválidas
        compile(texto, "config_local.py.example", "exec")
    comentadas = ejemplo.nombres_comentados(texto)
    definidas = ejemplo.nombres_definidos(texto)
    todas = {o.nombre_local for o in OPCIONES.values()}
    assert todas <= (comentadas | definidas), "el .example debe cubrir TODAS las opciones"


def test_el_ejemplo_versionado_esta_al_dia():
    """Si cambia el esquema hay que regenerar: python -m miku.ajustes ejemplo"""
    versionado = (RAIZ / "config_local.py.example").read_text(encoding="utf-8")
    assert versionado == ejemplo.generar_ejemplo()


def test_el_ejemplo_no_trae_secretos():
    for linea in ejemplo.generar_ejemplo().splitlines():
        if "API_KEY" in linea or "TOKEN" in linea:
            assert linea.startswith("#") or linea.endswith('= ""'), linea


# ---------------------------------------------------------------- completar
def test_completar_agrega_lo_que_falta_sin_tocar_lo_existente(tmp_path):
    original = 'GROQ_API_KEY = "gsk_secreta"\nMICROFONO_INDEX = 3\n# STEAM_RUTA = "D:/Steam"\n'
    ruta = tmp_path / "config_local.py"
    ruta.write_text(original, encoding="utf-8")

    agregadas = ejemplo.completar(ruta)

    nuevo = ruta.read_text(encoding="utf-8")
    assert nuevo.startswith(original.rstrip("\n")), "lo que ya estaba queda intacto"
    assert "GROQ_API_KEY" not in agregadas and "MICROFONO_INDEX" not in agregadas
    assert "STEAM_RUTA" not in agregadas, "una opción ya comentada no se duplica"
    assert "GEMINI_API_KEY" in agregadas and "CARPETA_CAPTURAS" in agregadas
    assert (tmp_path / "config_local.py.bak").read_text(encoding="utf-8") == original
    # todo lo agregado está comentado: no cambia el comportamiento
    definidas = ejemplo.nombres_definidos(nuevo)
    assert definidas == {"GROQ_API_KEY", "MICROFONO_INDEX"}
    compile(nuevo, "config_local.py", "exec")


def test_completar_es_idempotente(tmp_path):
    ruta = tmp_path / "config_local.py"
    ruta.write_text('GROQ_API_KEY = ""\n', encoding="utf-8")
    assert ejemplo.completar(ruta)
    assert ejemplo.completar(ruta) == []


def test_asistente_no_imprime_secretos(capsys, monkeypatch):
    from miku.ajustes import __main__ as asistente
    c = config_mod.Config()
    c.valores["groq_api_key"] = "gsk_MUY_SECRETA"
    c.origenes["groq_api_key"] = "config_local"
    monkeypatch.setattr(config_mod, "cargar", lambda: c)
    asistente.comando_estado()
    salida = capsys.readouterr().out
    assert "gsk_MUY_SECRETA" not in salida and "(configurada)" in salida


def test_claves_de_preferencias_no_se_marcan_como_desconocidas(cfg):
    """preferences.json puede tener datos propios; solo config_local.py es estricto."""
    informe = validacion.validar(cfg.valores, ["notas", "carpeta_captura"], claves_estrictas=["carpeta_captura"])
    assert [n for n, _ in informe.desconocidas] == ["carpeta_captura"]
