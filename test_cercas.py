"""
Testes — cercas_v2.py
Cobre parsing, geometria (sem OSM), formatação SASCAR e validação.
"""
import tempfile
import os
import pytest
import requests

import cercas_v2
from cercas_v2 import (
    _COSTURA_DIST_MAX_M,
    _costura_ways,
    _haversine_m,
    _montar_codigo,
    _overpass_query,
    _utm_epsg,
    _validar_rodovia,
    detectar_sobreposicao,
    exportar_csv,
    gerar_cercas,
    gerar_relatorio,
    ler_lote,
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
    assert _costura_ways(ways, nodes) == [[1, 2, 3]]

def test_costura_reverso():
    nodes = {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0)}
    ways  = [{"nodes": [1, 2]}, {"nodes": [3, 2]}]
    assert _costura_ways(ways, nodes) == [[1, 2, 3]]

def test_costura_no_ausente():
    nodes = {1: (0.0, 0.0)}
    ways  = [{"nodes": [1, 99]}]  # nó 99 não existe
    assert _costura_ways(ways, nodes) == []

def test_costura_componentes_desconexos():
    # Dois trechos sem nó em comum: a via está fragmentada no bbox. [FAT-68]
    nodes = {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0),
             10: (50.0, 50.0), 11: (51.0, 50.0)}
    ways  = [{"nodes": [10, 11]}, {"nodes": [1, 2]}, {"nodes": [2, 3]}]
    componentes = _costura_ways(ways, nodes)
    assert {tuple(c) for c in componentes} == {(10, 11), (1, 2, 3)}

def test_costura_escolhe_componente_mais_proximo():
    # Simula a seleção feita em buscar_geometria_osm: entre dois componentes,
    # vence o que fica perto de início/fim, mesmo não sendo o primeiro da
    # lista original de ways.  [FAT-68, DEC-6]
    nodes = {1: (0.0, 0.0), 2: (0.01, 0.0),           # componente longe
             10: (-25.38, -49.19), 11: (-25.39, -49.19)}  # componente perto
    ways  = [{"nodes": [1, 2]}, {"nodes": [10, 11]}]
    ponto_inicio = (-25.38, -49.19)
    ponto_fim    = (-25.39, -49.19)

    componentes = _costura_ways(ways, nodes)
    melhor = None
    for nos in componentes:
        coords = [nodes[n] for n in nos]
        d_ini = min(_haversine_m(ponto_inicio, c) for c in coords)
        d_fim = min(_haversine_m(ponto_fim, c) for c in coords)
        if melhor is None or (d_ini + d_fim) < melhor[0]:
            melhor = (d_ini + d_fim, nos, d_ini, d_fim)

    _, nos_escolhidos, d_ini, d_fim = melhor
    assert nos_escolhidos == [10, 11]
    assert d_ini < _COSTURA_DIST_MAX_M and d_fim < _COSTURA_DIST_MAX_M


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


# ── Módulo 4: guarda de comprimento no Modo B [FAT-81] ───────────────────────

def test_gerar_cercas_modo_b_comprimento_maior_que_via():
    # _POLY tem ~7,8 km; pedir 50 km a partir de _INICIO deve falhar,
    # em vez de truncar silenciosamente. [FAT-81]
    with pytest.raises(ValueError, match="FAT-81"):
        gerar_cercas(_POLY, "B", _INICIO, None, 50_000.0, 0.0, 0.0, verbose=False)


# ── Módulo 1B: ler_lote — validação de 'modo' [FAT-85] ───────────────────────

_CABECALHO_LOTE = (
    "via,polilinha,modo,inicio,fim,comprimento,pre,pos,buffer,"
    "rodovia,cidade,uf,velocidade,seq\n"
)

def _escrever_lote(linhas: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w", encoding="utf-8", newline=""
    ) as f:
        f.write(_CABECALHO_LOTE)
        f.write(linhas)
        caminho = f.name
    return caminho

def test_ler_lote_modo_valido():
    caminho = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.39,-49.19",,0,0,50,BR-116,LUZ,MG,60,1\n'
    )
    try:
        linhas = ler_lote(caminho)
        assert linhas[0]["modo"] == "A"
    finally:
        os.unlink(caminho)

def test_ler_lote_modo_invalido():
    caminho = _escrever_lote(
        'BR-116,,C,"-25.38,-49.19",,,0,0,50,BR-116,LUZ,MG,60,1\n'
    )
    try:
        with pytest.raises(ValueError, match="FAT-85"):
            ler_lote(caminho)
    finally:
        os.unlink(caminho)

def test_ler_lote_modo_vazio():
    caminho = _escrever_lote(
        'BR-116,,,"-25.38,-49.19",,,0,0,50,BR-116,LUZ,MG,60,1\n'
    )
    try:
        with pytest.raises(ValueError, match="modo"):
            ler_lote(caminho)
    finally:
        os.unlink(caminho)


# ── Módulo 2: retry de erro de rede no Overpass [FAT-84] ─────────────────────

class _RespostaFalsa:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {"elements": []}

def test_overpass_query_recupera_de_erro_de_rede_transitorio(monkeypatch):
    chamadas = {"n": 0}

    def post_falso(*args, **kwargs):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise requests.exceptions.ConnectionError("falha simulada")
        return _RespostaFalsa()

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)
    monkeypatch.setattr(cercas_v2.time, "sleep", lambda s: None)

    resultado = _overpass_query("query fake")
    assert resultado == {"elements": []}
    assert chamadas["n"] == 2

def test_overpass_query_falha_persistente_de_rede(monkeypatch):
    def post_falso(*args, **kwargs):
        raise requests.exceptions.ConnectionError("falha simulada")

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)
    monkeypatch.setattr(cercas_v2.time, "sleep", lambda s: None)

    with pytest.raises(Exception, match="FAT-84"):
        _overpass_query("query fake")


# ── Módulo 7: detecção de sobreposição [FAT-78] ──────────────────────────────

def test_detectar_sobreposicao_encontra_par_sobreposto():
    registros = [
        {"codigo": "A1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "A2", "seq": 2, "vertices": [(0.5, 0.5), (0.5, 1.5), (1.5, 1.5), (1.5, 0.5)]},
    ]
    pares = detectar_sobreposicao(registros)
    assert pares == [("A1", "A2")]

def test_detectar_sobreposicao_ignora_mesmo_seq():
    # PRI e PRE do mesmo conjunto (mesmo seq) sempre se sobrepõem — não é anomalia. [FAT-39]
    registros = [
        {"codigo": "PRI-1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "PRE-1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
    ]
    assert detectar_sobreposicao(registros) == []

def test_detectar_sobreposicao_sem_intersecao():
    registros = [
        {"codigo": "A1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "A2", "seq": 2, "vertices": [(10, 10), (10, 11), (11, 11), (11, 10)]},
    ]
    assert detectar_sobreposicao(registros) == []


# ── Módulo 8: relatório de cercas geradas [FAT-78] ───────────────────────────

def test_gerar_relatorio_conteudo_basico():
    registros = [
        {"codigo": "PRI-1", "tipo": "PRI", "vertices": [(-25.0, -49.0), (-25.1, -49.1)], "extensao_m": 1000},
    ]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        caminho = f.name
    try:
        n = gerar_relatorio(registros, caminho, verbose=False)
        assert n == 1
        with open(caminho, encoding="utf-8") as f:
            conteudo = f.read()
        assert "PRI-1" in conteudo
        assert "ALERTA" not in conteudo
    finally:
        os.unlink(caminho)

def test_gerar_relatorio_com_sobreposicoes():
    registros = [
        {"codigo": "A1", "tipo": "PRI", "vertices": [(-25.0, -49.0), (-25.1, -49.1)], "extensao_m": 500},
    ]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        caminho = f.name
    try:
        gerar_relatorio(registros, caminho, sobreposicoes=[("A1", "A2")], verbose=False)
        with open(caminho, encoding="utf-8") as f:
            conteudo = f.read()
        assert "ALERTA DE SOBREPOSICAO" in conteudo
        assert "A1" in conteudo and "A2" in conteudo
    finally:
        os.unlink(caminho)