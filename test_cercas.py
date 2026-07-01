"""
Testes — cercas_v2.py
Cobre parsing, geometria (sem OSM), formatação SASCAR e validação.
"""
import tempfile
import os
import pytest

from cercas_v2 import (
    _costura_ways,
    _haversine_m,
    _montar_codigo,
    _utm_epsg,
    _validar_rodovia,
    exportar_csv,
    gerar_cercas,
    parse_coord,
    parse_polilinha_manual,
    validar_csv,
)

# Polilinha manual: trecho reto N-S próximo a Curitiba (~5,5 km)
# Início e fim ficam no meio da polilinha para que pre/pos tenham espaço para expandir
_POLY = [
    (-25.36, -49.19), (-25.37, -49.19),
    (-25.38, -49.19), (-25.39, -49.19), (-25.40, -49.19), (-25.41, -49.19),
    (-25.42, -49.19), (-25.43, -49.19),
]
_INICIO = (-25.38, -49.19)
_FIM    = (-25.41, -49.19)


# ── Módulo 1: parsing ──────────────────────────────────────────────────────────

def test_parse_coord_valido():
    assert parse_coord("-25.38,-49.19") == (-25.38, -49.19)

def test_parse_coord_invalido():
    with pytest.raises(ValueError):
        parse_coord("-25.38")

def test_parse_polilinha_valida():
    pts = parse_polilinha_manual("-25.38,-49.19;-25.39,-49.18")
    assert len(pts) == 2

def test_parse_polilinha_curta():
    with pytest.raises(ValueError):
        parse_polilinha_manual("-25.38,-49.19")


# ── Módulo 2: geometria auxiliar ──────────────────────────────────────────────

def test_haversine_zero():
    assert _haversine_m((-25.0, -49.0), (-25.0, -49.0)) == pytest.approx(0.0)

def test_haversine_valor_conhecido():
    # 1 grau de latitude ≈ 111 km
    d = _haversine_m((0.0, 0.0), (1.0, 0.0))
    assert 110_000 < d < 112_000

def test_utm_epsg_sul():
    # Curitiba está no hemisfério sul, zona 22S → EPSG:32722
    assert _utm_epsg(-25.38, -49.19) == "EPSG:32722"

def test_utm_epsg_norte():
    assert _utm_epsg(1.0, -49.19).startswith("EPSG:326")


# ── Módulo 2: costura de ways ─────────────────────────────────────────────────

def test_costura_sequencial():
    nodes = {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0)}
    ways  = [{"nodes": [1, 2]}, {"nodes": [2, 3]}]
    assert _costura_ways(ways, nodes) == [1, 2, 3]

def test_costura_reverso():
    nodes = {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0)}
    ways  = [{"nodes": [1, 2]}, {"nodes": [3, 2]}]
    assert _costura_ways(ways, nodes) == [1, 2, 3]

def test_costura_no_ausente():
    nodes = {1: (0.0, 0.0)}
    ways  = [{"nodes": [1, 99]}]  # nó 99 não existe
    assert _costura_ways(ways, nodes) == []


# ── Módulo 4: gerar_cercas ────────────────────────────────────────────────────

def test_gerar_cercas_modo_a_pri():
    result = gerar_cercas(_POLY, "A", _INICIO, _FIM, None, 0.0, 0.0, verbose=False)
    pri = result["PRI"]
    assert len(pri["vertices"]) > 0
    assert pri["extensao_m"] > 0

def test_gerar_cercas_modo_b_pri():
    result = gerar_cercas(_POLY, "B", _INICIO, None, 1000.0, 0.0, 0.0, verbose=False)
    pri = result["PRI"]
    assert 900 < pri["extensao_m"] < 1100

def test_gerar_cercas_pre_gerada():
    result = gerar_cercas(_POLY, "A", _INICIO, _FIM, None, 300.0, 300.0, verbose=False)
    assert len(result["PRE"]["vertices"]) > 0
    assert result["PRE"]["extensao_m"] > result["PRI"]["extensao_m"]

def test_gerar_cercas_pre_vazia_sem_extensao():
    result = gerar_cercas(_POLY, "A", _INICIO, _FIM, None, 0.0, 0.0, verbose=False)
    assert result["PRE"]["vertices"] == []


# ── Módulo 5: formatação SASCAR ───────────────────────────────────────────────

def test_montar_codigo_formato():
    c = _montar_codigo("PRI", "BR-116", "LUZ", "MG", 60, 1)
    assert c == "PRI - BR-116 - LUZ_MG - 60 KmH - 001"

def test_montar_codigo_uf_minuscula():
    c = _montar_codigo("PRE", "SC-470", "BLUMENAU", "sc", 80, 12)
    assert "_SC" in c

def test_validar_rodovia_valida():
    _validar_rodovia("BR-116")   # não levanta

def test_validar_rodovia_invalida():
    with pytest.raises(ValueError):
        _validar_rodovia("BR116")

def test_validar_rodovia_rua_livre():
    _validar_rodovia("Av Brasil")   # sem dígito → aceito livre


# ── Módulos 5+6: exportar e validar ──────────────────────────────────────────

def _cercas_fixture():
    return gerar_cercas(_POLY, "A", _INICIO, _FIM, None, 300.0, 300.0, verbose=False)

def test_exportar_e_validar_sem_erros():
    cercas = _cercas_fixture()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        caminho = f.name
    try:
        n = exportar_csv(cercas, "BR-116", "LUZ", "MG", 60, 1, caminho, verbose=False)
        assert n == 2   # PRI + PRE
        erros = validar_csv(caminho, verbose=False)
        assert erros == []
    finally:
        os.unlink(caminho)

def test_validar_linha_invalida():
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w", encoding="utf-8") as f:
        f.write('"POL";"CODIGO_ERRADO";"desc";-25.0,-49.0\n')
        caminho = f.name
    try:
        erros = validar_csv(caminho, verbose=False)
        assert any("CÓDIGO" in e for e in erros)
    finally:
        os.unlink(caminho)