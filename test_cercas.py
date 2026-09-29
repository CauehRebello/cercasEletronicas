"""
Testes — cercas_v2.py
Cobre parsing, geometria (sem OSM), formatação SASCAR e validação.
"""
import json
import sqlite3
import tempfile
import os
import pytest
import requests

import cercas_v2
from cercas_v2 import (
    _COSTURA_DIST_MAX_M,
    LIMIAR_SOBREPOSICAO_BLOQUEIO_PADRAO,
    DuplicidadeCodigoCentralError,
    _FakeCentralConn,
    _cache_get,
    _cache_key,
    _cache_set,
    _checar_duplicidade_central,
    _costura_ways,
    _dist_ponto_segmento_m,
    _haversine_m,
    _montar_codigo,
    _overpass_query,
    _utm_epsg,
    _validar_rodovia,
    _validar_rota_osrm,
    avaliar_bloqueio_sobreposicao,
    buscar_geometria_osm,
    buscar_vias_alternativas_osm,
    consultar_historico,
    detectar_sobreposicao,
    exportar_csv,
    gerar_cercas,
    gerar_relatorio,
    ler_lote,
    parse_coord,
    parse_polilinha_manual,
    processar_linha_lote,
    processar_lote,
    registrar_cerca_central,
    salvar_no_historico,
    substituir_cerca_central,
    sugerir_proximo_seq_livre,
    validar_csv,
    verificar_codigo_central,
    ViaAlternativaPendenteError,
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

def test_dist_ponto_segmento_ponto_no_meio_de_nos_espacados():
    # FAT-333: nós a ~250 m um do outro (comum em rodovias multiplexadas).
    # Ponto a poucos metros do segmento, mas a mais de 100 m de cada nó
    # individual — o bug antigo (distância ao nó mais próximo) rejeitaria
    # essa coordenada; a distância ponto-segmento não deve.
    a = (0.0, 0.0)
    b = (0.002252, 0.0)  # ~250 m ao norte de `a`
    ponto = (0.001126, 0.000027)  # meio do segmento, ~3 m a leste dele

    d_no_a = _haversine_m(ponto, a)
    d_no_b = _haversine_m(ponto, b)
    assert d_no_a > _COSTURA_DIST_MAX_M and d_no_b > _COSTURA_DIST_MAX_M

    d_segmento = _dist_ponto_segmento_m(ponto, a, b)
    assert d_segmento < _COSTURA_DIST_MAX_M
    assert d_segmento == pytest.approx(3.0, abs=1.0)

def test_dist_ponto_segmento_projecao_fora_do_segmento_usa_extremo():
    a = (0.0, 0.0)
    b = (0.001, 0.0)
    ponto = (0.01, 0.0)  # muito além de `b`, fora do segmento
    assert _dist_ponto_segmento_m(ponto, a, b) == pytest.approx(_haversine_m(ponto, b))

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


# ── Módulo 1B: ler_lote — validação de 'uf' [FAT-82] ─────────────────────────

def test_ler_lote_uf_invalida():
    caminho = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.39,-49.19",,0,0,50,BR-116,LUZ,MGX,60,1\n'
    )
    try:
        with pytest.raises(ValueError, match="FAT-82"):
            ler_lote(caminho)
    finally:
        os.unlink(caminho)


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


# ── Módulo 1B: ler_lote — validação de 'velocidade'/'seq' [FAT-86] ──────────

def test_ler_lote_velocidade_nao_numerica():
    caminho = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.39,-49.19",,0,0,50,BR-116,LUZ,MG,rapido,1\n'
    )
    try:
        with pytest.raises(ValueError, match="FAT-86"):
            ler_lote(caminho)
    finally:
        os.unlink(caminho)

def test_ler_lote_velocidade_zero():
    caminho = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.39,-49.19",,0,0,50,BR-116,LUZ,MG,0,1\n'
    )
    try:
        with pytest.raises(ValueError, match="FAT-86"):
            ler_lote(caminho)
    finally:
        os.unlink(caminho)

def test_ler_lote_seq_nao_numerico():
    caminho = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.39,-49.19",,0,0,50,BR-116,LUZ,MG,60,abc\n'
    )
    try:
        with pytest.raises(ValueError, match="FAT-86"):
            ler_lote(caminho)
    finally:
        os.unlink(caminho)

def test_ler_lote_seq_fora_da_faixa():
    caminho = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.39,-49.19",,0,0,50,BR-116,LUZ,MG,60,1000\n'
    )
    try:
        with pytest.raises(ValueError, match="FAT-86"):
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

def test_overpass_query_respeita_max_tentativas_customizado(monkeypatch):
    chamadas = {"n": 0}

    def post_falso(*args, **kwargs):
        chamadas["n"] += 1
        raise requests.exceptions.ConnectionError("falha simulada")

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)
    monkeypatch.setattr(cercas_v2.time, "sleep", lambda s: None)

    with pytest.raises(Exception, match="FAT-84"):
        _overpass_query("query fake", max_tentativas=5)
    assert chamadas["n"] == 5

def test_overpass_query_respeita_espera_customizada(monkeypatch):
    esperas = []

    def post_falso(*args, **kwargs):
        raise requests.exceptions.ConnectionError("falha simulada")

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)
    monkeypatch.setattr(cercas_v2.time, "sleep", lambda s: esperas.append(s))

    with pytest.raises(Exception, match="FAT-84"):
        _overpass_query("query fake", max_tentativas=3, espera_base_s=7.5)
    assert esperas == [7.5, 7.5]

def test_overpass_query_default_reproduz_comportamento_atual(monkeypatch):
    chamadas = {"n": 0}

    def post_falso(*args, **kwargs):
        chamadas["n"] += 1
        raise requests.exceptions.ConnectionError("falha simulada")

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)
    monkeypatch.setattr(cercas_v2.time, "sleep", lambda s: None)

    with pytest.raises(Exception, match="FAT-84"):
        _overpass_query("query fake")
    assert chamadas["n"] == 3


# ── Módulo 2: cache local de geometrias OSM [FAT-154, S4] ────────────────────

class _RespostaFalsaGeometria:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {
            "elements": [
                {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0},
                {"type": "node", "id": 2, "lat": 0.0, "lon": 1.0},
                {"type": "way", "id": 100, "nodes": [1, 2]},
            ]
        }

def test_cache_desabilitado_sempre_busca_rede(monkeypatch, tmp_path):
    monkeypatch.setattr(cercas_v2, "_CACHE_PATH", str(tmp_path / "cache.json"))
    chamadas = {"n": 0}

    def post_falso(*args, **kwargs):
        chamadas["n"] += 1
        return _RespostaFalsaGeometria()

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)

    buscar_geometria_osm("BR-116", (0.0, 0.0), (0.0, 1.0), verbose=False)
    buscar_geometria_osm("BR-116", (0.0, 0.0), (0.0, 1.0), verbose=False)
    assert chamadas["n"] == 2

def test_cache_hit_evita_segunda_chamada_de_rede(monkeypatch, tmp_path):
    monkeypatch.setattr(cercas_v2, "_CACHE_PATH", str(tmp_path / "cache.json"))
    chamadas = {"n": 0}

    def post_falso(*args, **kwargs):
        chamadas["n"] += 1
        return _RespostaFalsaGeometria()

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)

    r1 = buscar_geometria_osm("BR-116", (0.0, 0.0), (0.0, 1.0), verbose=False, usar_cache=True)
    r2 = buscar_geometria_osm("BR-116", (0.0, 0.0), (0.0, 1.0), verbose=False, usar_cache=True)
    assert chamadas["n"] == 1
    assert r1 == r2

def test_cache_expirado_busca_de_novo(monkeypatch, tmp_path):
    caminho = str(tmp_path / "cache.json")
    monkeypatch.setattr(cercas_v2, "_CACHE_PATH", caminho)
    chamadas = {"n": 0}

    def post_falso(*args, **kwargs):
        chamadas["n"] += 1
        return _RespostaFalsaGeometria()

    monkeypatch.setattr(cercas_v2.requests, "post", post_falso)

    chave = _cache_key("BR-116", (0.0, 0.0), (0.0, 1.0))
    _cache_set(chave, [[0.0, 0.0], [0.0, 1.0]], ttl_s=1, caminho=caminho)
    entrada = _cache_get(chave, caminho=caminho)
    assert entrada is not None  # sanity: gravou corretamente antes de expirar

    # Força expiração: recua o timestamp gravado para além do ttl.
    with open(caminho, "r", encoding="utf-8") as f:
        dados = json.load(f)
    dados[chave]["timestamp"] -= 1000
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f)

    buscar_geometria_osm(
        "BR-116", (0.0, 0.0), (0.0, 1.0), verbose=False,
        usar_cache=True, cache_ttl_s=1,
    )
    assert chamadas["n"] == 1


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


# ── Módulo 9: histórico persistente (SQLite) [FAT-155, S5] ───────────────────

def _registro_historico(codigo="PRI - BR-116 - LUZ_MG - 60 KmH - 001"):
    return {
        "codigo": codigo, "tipo": "PRI", "seq": 1,
        "vertices": [(-25.0, -49.0), (-25.1, -49.1)], "extensao_m": 1000,
        "rodovia": "BR-116", "cidade": "LUZ", "uf": "MG", "velocidade": 60,
    }

def test_salvar_no_historico_cria_tabela_e_grava(tmp_path):
    caminho_db = str(tmp_path / "historico.db")
    n = salvar_no_historico([_registro_historico()], caminho_db, verbose=False)
    assert n == 1

    conn = sqlite3.connect(caminho_db)
    try:
        row = conn.execute(
            "SELECT codigo, tipo, rodovia, cidade, uf, velocidade, seq, extensao_m, "
            "num_vertices FROM cercas"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("PRI - BR-116 - LUZ_MG - 60 KmH - 001", "PRI", "BR-116", "LUZ", "MG", 60, 1, 1000, 2)

def test_salvar_no_historico_codigo_duplicado_gera_alerta_nao_bloqueante(tmp_path, capsys):
    caminho_db = str(tmp_path / "historico.db")
    salvar_no_historico([_registro_historico()], caminho_db, verbose=True)
    capsys.readouterr()

    n = salvar_no_historico([_registro_historico()], caminho_db, verbose=True)
    saida = capsys.readouterr().out

    assert n == 1  # grava mesmo assim — não bloqueia
    assert "ALERTA" in saida
    assert "já existe no histórico" in saida

def test_consultar_historico_filtra_por_rodovia_uf_codigo(tmp_path):
    caminho_db = str(tmp_path / "historico.db")
    registros = [
        {**_registro_historico("PRI - BR-116 - LUZ_MG - 60 KmH - 001"),
         "rodovia": "BR-116", "uf": "MG"},
        {**_registro_historico("PRI - BR-470 - BLUMENAU_SC - 80 KmH - 002"),
         "rodovia": "BR-470", "uf": "SC"},
        {**_registro_historico("PRI - BR-470 - BLUMENAU_SC - 80 KmH - 003"),
         "rodovia": "BR-470", "uf": "SC"},
    ]
    salvar_no_historico(registros, caminho_db, verbose=False)

    por_rodovia = consultar_historico(caminho_db, {"rodovia": "BR-470"})
    assert len(por_rodovia) == 2

    por_uf = consultar_historico(caminho_db, {"uf": "MG"})
    assert len(por_uf) == 1

    por_codigo = consultar_historico(caminho_db, {"codigo": "PRI - BR-470 - BLUMENAU_SC - 80 KmH - 003"})
    assert len(por_codigo) == 1

def test_consultar_historico_sem_arquivo_retorna_lista_vazia(tmp_path):
    assert consultar_historico(str(tmp_path / "nao_existe.db")) == []


# ── Bloco B v4: bloqueio de sobreposição geométrica [FAT-185/186/203] ────────

def test_avaliar_bloqueio_sobreposicao_acima_limiar_bloqueia():
    registros = [
        {"codigo": "A1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "A2", "seq": 2, "vertices": [(-0.05, -0.05), (-0.05, 1.05), (1.05, 1.05), (1.05, -0.05)]},
    ]
    bloqueios = avaliar_bloqueio_sobreposicao(registros)
    assert len(bloqueios) == 1
    b = bloqueios[0]
    assert b["codigo_existente"] == "A1"
    assert b["codigo_novo"] == "A2"
    assert b["percentual"] == pytest.approx(1.0)
    assert b["bloqueado"] is True
    assert b["override"] is None

def test_avaliar_bloqueio_sobreposicao_abaixo_limiar_nao_retorna():
    registros = [
        {"codigo": "A1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "A2", "seq": 2, "vertices": [(0.5, 0.5), (0.5, 1.5), (1.5, 1.5), (1.5, 0.5)]},
    ]
    assert avaliar_bloqueio_sobreposicao(registros) == []

def test_avaliar_bloqueio_sobreposicao_limiar_customizado():
    # Mesmo par do teste acima (25% de sobreposição) — não bloqueia com o
    # limiar padrão (90%), mas bloqueia com um limiar customizado mais baixo,
    # confirmando que o valor provisório de 90% é parametrizável. [FAT-203, FAT-195]
    registros = [
        {"codigo": "A1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "A2", "seq": 2, "vertices": [(0.5, 0.5), (0.5, 1.5), (1.5, 1.5), (1.5, 0.5)]},
    ]
    bloqueios = avaliar_bloqueio_sobreposicao(registros, limiar_bloqueio=0.2)
    assert len(bloqueios) == 1
    assert bloqueios[0]["percentual"] == pytest.approx(0.25)

def test_avaliar_bloqueio_sobreposicao_ignora_mesmo_seq():
    # PRI/PRE do mesmo conjunto sempre se sobrepõem — não é anomalia,
    # mesma exclusão usada em detectar_sobreposicao. [FAT-39]
    registros = [
        {"codigo": "PRI-1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "PRE-1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
    ]
    assert avaliar_bloqueio_sobreposicao(registros) == []

def test_avaliar_bloqueio_sobreposicao_override_desbloqueia():
    registros = [
        {"codigo": "A1", "seq": 1, "vertices": [(0, 0), (0, 1), (1, 1), (1, 0)]},
        {"codigo": "A2", "seq": 2, "vertices": [(-0.05, -0.05), (-0.05, 1.05), (1.05, 1.05), (1.05, -0.05)]},
    ]
    overrides = {
        ("A1", "A2"): {
            "justificativa": "vias paralelas classificadas incorretamente",
            "confirmado_por": "caueh.rebello", "quando": "2026-07-03T10:00:00",
        },
    }
    bloqueios = avaliar_bloqueio_sobreposicao(registros, overrides=overrides)
    assert len(bloqueios) == 1
    assert bloqueios[0]["bloqueado"] is False
    assert bloqueios[0]["override"]["justificativa"] == "vias paralelas classificadas incorretamente"

def test_gerar_relatorio_com_bloqueios_sobreposicao():
    registros = [
        {"codigo": "A1", "tipo": "PRI", "vertices": [(-25.0, -49.0), (-25.1, -49.1)], "extensao_m": 500},
    ]
    bloqueios = [{
        "codigo_existente": "A1", "codigo_novo": "A2", "percentual": 0.95,
        "bloqueado": True, "override": None,
    }]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        caminho = f.name
    try:
        gerar_relatorio(registros, caminho, bloqueios_sobreposicao=bloqueios, verbose=False)
        with open(caminho, encoding="utf-8") as f:
            conteudo = f.read()
        assert "BLOQUEIO DE SOBREPOSICAO" in conteudo
        assert "A1" in conteudo and "A2" in conteudo
    finally:
        os.unlink(caminho)


# ── Bloco A v4: duplicidade de CÓDIGO contra base central [FAT-181/182/183] ──
#
# `psycopg2` real não é usado nestes testes unitários: as funções recebem
# uma conexão já aberta como parâmetro, então usamos `_FakeCentralConn`
# (importada de `cercas_v2` — mesma classe usada em produção pelo modo
# `--pg-fake`) para cobrir a lógica de negócio (checagem, sugestão de SEQ,
# substituição) sem depender de infraestrutura externa. A validação contra
# um PostgreSQL real está em `test_cercas_pg_integration.py` (pulado se
# `CERCAS_TEST_PG_DSN` não estiver definida).

def _registro_central(seq=1, codigo=None):
    codigo = codigo or f"PRI - BR-116 - LUZ_MG - 60 KmH - {seq:03d}"
    return {
        "codigo": codigo, "tipo": "PRI", "seq": seq,
        "vertices": [(-25.0, -49.0), (-25.1, -49.1)], "extensao_m": 1000,
        "rodovia": "BR-116", "cidade": "LUZ", "uf": "MG", "velocidade": 60,
    }

def test_registrar_cerca_central_insere_ativo():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(), execucao_id="20260703100000")
    assert len(conn.rows) == 1
    assert conn.rows[0]["status"] == "ativo"
    assert conn.rows[0]["execucao_id"] == "20260703100000"

def test_verificar_codigo_central_encontra_ativo():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    existente = verificar_codigo_central(conn, "BR-116", "LUZ", "MG", 60, 1)
    assert existente is not None
    assert existente["codigo"] == "PRI - BR-116 - LUZ_MG - 60 KmH - 001"

def test_verificar_codigo_central_none_quando_nao_existe():
    conn = _FakeCentralConn()
    assert verificar_codigo_central(conn, "BR-116", "LUZ", "MG", 60, 1) is None

def test_sugerir_proximo_seq_livre_sem_uso_retorna_1():
    conn = _FakeCentralConn()
    assert sugerir_proximo_seq_livre(conn, "BR-116", "LUZ", "MG", 60) == 1

def test_sugerir_proximo_seq_livre_preenche_lacuna():
    conn = _FakeCentralConn()
    for seq in (1, 2, 4):
        registrar_cerca_central(conn, _registro_central(seq=seq), execucao_id="x")
    assert sugerir_proximo_seq_livre(conn, "BR-116", "LUZ", "MG", 60) == 3

def test_substituir_cerca_central_marca_superado_nao_apaga():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    n = substituir_cerca_central(conn, "BR-116", "LUZ", "MG", 60, 1, motivo="reemissao")
    assert n == 1
    assert len(conn.rows) == 1  # nunca apaga — só marca como superado [FAT-182]
    assert conn.rows[0]["status"] == "superado"
    assert conn.rows[0]["superado_motivo"] == "reemissao"
    assert verificar_codigo_central(conn, "BR-116", "LUZ", "MG", 60, 1) is None

def test_checar_duplicidade_central_bloqueia_sem_substituir():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    with pytest.raises(DuplicidadeCodigoCentralError) as exc:
        _checar_duplicidade_central(conn, "BR-116", "LUZ", "MG", 60, 1)
    assert "SEQ livre sugerido: 002" in str(exc.value)

def test_checar_duplicidade_central_recusa_substituir_sem_confirmar():
    # Flag + confirmação: só --substituir não é suficiente. [FAT-182]
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    with pytest.raises(DuplicidadeCodigoCentralError):
        _checar_duplicidade_central(
            conn, "BR-116", "LUZ", "MG", 60, 1,
            substituir=True, confirmar_substituicao=False,
        )
    assert verificar_codigo_central(conn, "BR-116", "LUZ", "MG", 60, 1) is not None

def test_checar_duplicidade_central_permite_com_substituir_e_confirmar():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    _checar_duplicidade_central(
        conn, "BR-116", "LUZ", "MG", 60, 1,
        substituir=True, confirmar_substituicao=True, motivo_substituicao="reemissao",
    )
    assert conn.rows[0]["status"] == "superado"
    assert verificar_codigo_central(conn, "BR-116", "LUZ", "MG", 60, 1) is None

def test_checar_duplicidade_central_sem_duplicata_nao_faz_nada():
    conn = _FakeCentralConn()
    _checar_duplicidade_central(conn, "BR-116", "LUZ", "MG", 60, 1)
    assert conn.rows == []


# ── DEC-V4-32: via alternativa no Módulo 2 ───────────────────────────────────

class _RespostaFalsaVias:
    """Resposta Overpass fake com ways de identidades distintas, para
    `buscar_vias_alternativas_osm`. `elements` é parametrizável por teste."""
    status_code = 200
    def __init__(self, elements):
        self._elements = elements
    def raise_for_status(self):
        pass
    def json(self):
        return {"elements": self._elements}


def _no(id_, lat, lon):
    return {"type": "node", "id": id_, "lat": lat, "lon": lon}

def _way(id_, nodes, tags=None):
    el = {"type": "way", "id": id_, "nodes": nodes}
    if tags:
        el["tags"] = tags
    return el


def test_buscar_vias_alternativas_osm_encontra_candidata_dentro_do_limiar(monkeypatch):
    elements = [
        _no(1, 0.0, 0.0), _no(2, 0.0, 0.001),
        _way(100, [1, 2], {"ref": "BR-999"}),
    ]
    monkeypatch.setattr(cercas_v2.requests, "post", lambda *a, **k: _RespostaFalsaVias(elements))

    candidatos = buscar_vias_alternativas_osm((0.0, 0.0), (0.0, 0.001), "BR-116", verbose=False)
    assert len(candidatos) == 1
    assert candidatos[0]["way_id_referencia"] == 100
    assert candidatos[0]["nome_ou_ref"] == "BR-999"
    assert candidatos[0]["d_ini"] < _COSTURA_DIST_MAX_M
    assert candidatos[0]["d_fim"] < _COSTURA_DIST_MAX_M


def test_buscar_vias_alternativas_osm_ignora_candidatas_acima_do_limiar(monkeypatch):
    elements = [
        _no(1, 0.01, 0.0), _no(2, 0.01, 0.001),  # ~1.1 km de (0,0) — acima do limiar de 100 m
        _way(100, [1, 2], {"ref": "BR-999"}),
    ]
    monkeypatch.setattr(cercas_v2.requests, "post", lambda *a, **k: _RespostaFalsaVias(elements))

    candidatos = buscar_vias_alternativas_osm((0.0, 0.0), (0.0, 0.001), "BR-116", verbose=False)
    assert candidatos == []


def test_buscar_vias_alternativas_osm_exclui_a_propria_via_buscada(monkeypatch):
    elements = [
        _no(1, 0.0, 0.0), _no(2, 0.0, 0.001),
        _way(100, [1, 2], {"ref": "BR-116"}),  # mesma ref que a via já tentada
    ]
    monkeypatch.setattr(cercas_v2.requests, "post", lambda *a, **k: _RespostaFalsaVias(elements))

    candidatos = buscar_vias_alternativas_osm((0.0, 0.0), (0.0, 0.001), "BR-116", verbose=False)
    assert candidatos == []


def test_buscar_vias_alternativas_osm_multiplas_candidatas_ordenadas_por_distancia(monkeypatch):
    elements = [
        _no(1, 0.0002, 0.0), _no(2, 0.0002, 0.001),
        _way(100, [1, 2], {"ref": "BR-AAA"}),   # mais longe
        _no(3, 0.00005, 0.0), _no(4, 0.00005, 0.001),
        _way(200, [3, 4], {"ref": "BR-BBB"}),   # mais perto
    ]
    monkeypatch.setattr(cercas_v2.requests, "post", lambda *a, **k: _RespostaFalsaVias(elements))

    candidatos = buscar_vias_alternativas_osm((0.0, 0.0), (0.0, 0.001), "BR-116", verbose=False)
    assert [c["nome_ou_ref"] for c in candidatos] == ["BR-BBB", "BR-AAA"]
    assert candidatos[0]["d_ini"] + candidatos[0]["d_fim"] < candidatos[1]["d_ini"] + candidatos[1]["d_fim"]


def test_buscar_vias_alternativas_osm_nao_costura_ways_sem_nome_entre_si(monkeypatch):
    # Dois ways sem ref/name, compartilhando o nó 2 — costurariam juntos se
    # aplicássemos _costura_ways sobre o conjunto bruto; devem virar
    # candidatas SEPARADAS (agrupamento por way individual). [DEC-V4-32]
    elements = [
        _no(1, 0.0, 0.0), _no(2, 0.0, 0.0005), _no(3, 0.0, 0.001),
        _way(100, [1, 2]),
        _way(200, [2, 3]),
    ]
    monkeypatch.setattr(cercas_v2.requests, "post", lambda *a, **k: _RespostaFalsaVias(elements))

    candidatos = buscar_vias_alternativas_osm((0.0, 0.0), (0.0, 0.001), "BR-116", verbose=False)
    way_ids = {c["way_id_referencia"] for c in candidatos}
    assert way_ids == {100, 200}


# ── DEC-V4-32: processar_linha_lote / processar_lote — pendência de via alternativa ──

def _row_lote(via, rodovia, seq, inicio=_INICIO, fim=_FIM, num_linha="1"):
    return {
        "via": via, "polilinha": "", "modo": "A",
        "inicio": f"{inicio[0]},{inicio[1]}", "fim": f"{fim[0]},{fim[1]}",
        "comprimento": "", "pre": "0", "pos": "0", "buffer": "50",
        "rodovia": rodovia, "cidade": "LUZ", "uf": "MG", "velocidade": "60",
        "seq": str(seq), "_num_linha": num_linha,
    }


def test_processar_linha_lote_via_alternativa_aceita_via_override(monkeypatch):
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: None)
    monkeypatch.setattr(cercas_v2, "_validar_rota_osrm", lambda *a, **k: None)
    monkeypatch.setattr(
        cercas_v2, "buscar_vias_alternativas_osm",
        lambda *a, **k: [{"way_id_referencia": 555, "nome_ou_ref": "BR-888",
                           "d_ini": 10.0, "d_fim": 15.0, "coords": _POLY}],
    )
    row = _row_lote("BR-999", "BR-999", seq=1)
    codigo_base = _montar_codigo("PRI", "BR-999", "LUZ", "MG", 60, 1)
    overrides = {
        (codigo_base, "555"): {
            "justificativa": "conferido no mapa OSM", "confirmado_por": "caueh.rebello",
            "quando": "2026-09-28T10:00:00",
        },
    }

    linhas_sascar, registros, via_alt = processar_linha_lote(
        row, verbose=False, overrides_via_alternativa=overrides,
    )
    assert linhas_sascar
    assert via_alt["way_id"] == 555
    assert via_alt["justificativa"] == "conferido no mapa OSM"
    assert registros[0]["via_alternativa_aceita"]["nome_ou_ref"] == "BR-888"


def test_processar_linha_lote_via_alternativa_pendente_sem_override_levanta_erro(monkeypatch):
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: None)
    monkeypatch.setattr(cercas_v2, "_validar_rota_osrm", lambda *a, **k: None)
    monkeypatch.setattr(
        cercas_v2, "buscar_vias_alternativas_osm",
        lambda *a, **k: [{"way_id_referencia": 555, "nome_ou_ref": "BR-888",
                           "d_ini": 10.0, "d_fim": 15.0, "coords": _POLY}],
    )
    row = _row_lote("BR-999", "BR-999", seq=1)

    with pytest.raises(ViaAlternativaPendenteError) as exc:
        processar_linha_lote(row, verbose=False)
    assert "--aceitar-via-alternativa" in str(exc.value)
    assert "555" in str(exc.value)


def test_processar_linha_lote_sem_geometria_e_sem_candidata_levanta_runtime_error_comum(monkeypatch):
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: None)
    monkeypatch.setattr(cercas_v2, "_validar_rota_osrm", lambda *a, **k: None)
    monkeypatch.setattr(cercas_v2, "buscar_vias_alternativas_osm", lambda *a, **k: [])
    row = _row_lote("BR-999", "BR-999", seq=1)

    with pytest.raises(RuntimeError) as exc:
        processar_linha_lote(row, verbose=False)
    assert not isinstance(exc.value, ViaAlternativaPendenteError)
    assert "Nenhuma via alternativa" in str(exc.value)


def test_processar_lote_pendencia_via_alternativa_nao_aborta_demais_linhas(monkeypatch, tmp_path):
    def fake_geometria(via, *a, **k):
        return None if via == "BR-999" else _POLY
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", fake_geometria)
    monkeypatch.setattr(cercas_v2, "_validar_rota_osrm", lambda *a, **k: None)
    monkeypatch.setattr(
        cercas_v2, "buscar_vias_alternativas_osm",
        lambda *a, **k: [{"way_id_referencia": 777, "nome_ou_ref": "BR-777",
                           "d_ini": 5.0, "d_fim": 5.0, "coords": _POLY}],
    )
    caminho_entrada = _escrever_lote(
        'BR-999,,A,"-25.38,-49.19","-25.41,-49.19",,0,0,50,BR-999,LUZ,MG,60,1\n'
        'BR-116,,A,"-25.38,-49.19","-25.41,-49.19",,0,0,50,BR-116,LUZ,MG,60,2\n'
    )
    caminho_saida = str(tmp_path / "saida.csv")
    try:
        (total, registros, sobreposicoes, bloqueios,
         pendencias, aceitas) = processar_lote(caminho_entrada, caminho_saida, verbose=False)
        assert len(pendencias) == 1
        assert pendencias[0]["via_original"] == "BR-999"
        assert all(r["rodovia"] == "BR-116" for r in registros)
        assert total > 0
    finally:
        os.unlink(caminho_entrada)


def test_processar_lote_outro_erro_de_linha_continua_abortando_tudo(monkeypatch, tmp_path):
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: _POLY)
    caminho_entrada = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19",,,0,0,50,BR-116,LUZ,MG,60,1\n'  # modo A sem 'fim' — ValueError
        'BR-116,,A,"-25.38,-49.19","-25.41,-49.19",,0,0,50,BR-116,LUZ,MG,60,2\n'
    )
    caminho_saida = str(tmp_path / "saida.csv")
    try:
        with pytest.raises(ValueError, match="modo A requer"):
            processar_lote(caminho_entrada, caminho_saida, verbose=False)
        assert not os.path.exists(caminho_saida)  # tudo-ou-nada preservado (R6/R2)
    finally:
        os.unlink(caminho_entrada)


# ── DEC-V4-34: validação de rota OSRM no Módulo 2 ────────────────────────────

def _osrm_route(steps, geometry_coords):
    return {"routes": [{"legs": [{"steps": steps}], "geometry": {"coordinates": geometry_coords}}]}


class _RespostaFalsaOSRM:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
    def json(self):
        return self._payload


def test_validar_rota_osrm_aceita_rota_acima_do_limiar_mesmo_com_nome_diferente_do_via(monkeypatch):
    rota = _osrm_route(
        steps=[{"name": "Rua das Flores", "distance": 1000.0}],
        geometry_coords=[[-49.19, -25.38], [-49.19, -25.41]],  # [lon, lat]
    )
    monkeypatch.setattr(cercas_v2.requests, "get", lambda *a, **k: _RespostaFalsaOSRM(200, rota))

    elements = [
        _no(1, -25.38, -49.19), _no(2, -25.41, -49.19),
        _way(100, [1, 2], {"name": "Rua das Flores"}),
    ]
    monkeypatch.setattr(cercas_v2.requests, "post", lambda *a, **k: _RespostaFalsaVias(elements))

    coords = _validar_rota_osrm(_INICIO, _FIM, verbose=False)
    assert coords

    # Integração: cai no caminho automático, sem pendência de via alternativa.
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: None)
    row = _row_lote("BR-999", "BR-999", seq=1)
    linhas_sascar, registros, via_alt = processar_linha_lote(row, verbose=False)
    assert linhas_sascar
    assert via_alt is None


def test_validar_rota_osrm_abaixo_do_limiar_cai_no_herdado(monkeypatch):
    rota = _osrm_route(
        steps=[
            {"name": "Rua das Flores", "distance": 400.0},
            {"name": "", "distance": 600.0},
        ],
        geometry_coords=[[-49.19, -25.38], [-49.19, -25.41]],
    )
    monkeypatch.setattr(cercas_v2.requests, "get", lambda *a, **k: _RespostaFalsaOSRM(200, rota))
    # Não mockar requests.post: se _validar_rota_osrm chegar a consultar o
    # Overpass, o teste falha por não haver resposta fake configurada.

    assert _validar_rota_osrm(_INICIO, _FIM, verbose=False) is None

    # Integração: sem OSRM válido, mantém o fallback de via alternativa.
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: None)
    monkeypatch.setattr(
        cercas_v2, "buscar_vias_alternativas_osm",
        lambda *a, **k: [{"way_id_referencia": 555, "nome_ou_ref": "BR-888",
                           "d_ini": 10.0, "d_fim": 15.0, "coords": _POLY}],
    )
    row = _row_lote("BR-999", "BR-999", seq=1)
    with pytest.raises(ViaAlternativaPendenteError):
        processar_linha_lote(row, verbose=False)


def test_validar_rota_osrm_falha_de_rede_cai_no_herdado_sem_excecao(monkeypatch):
    def _levanta_timeout(*a, **k):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(cercas_v2.requests, "get", _levanta_timeout)
    assert _validar_rota_osrm(_INICIO, _FIM, verbose=False) is None

    monkeypatch.setattr(cercas_v2.requests, "get", lambda *a, **k: _RespostaFalsaOSRM(500, None))
    assert _validar_rota_osrm(_INICIO, _FIM, verbose=False) is None


def test_gerar_relatorio_secao_pendencias_via_alternativa():
    pendencias = [{
        "num_linha": "1", "codigo_base": "PRI - BR-999 - LUZ_MG - 60 KmH - 001",
        "via_original": "BR-999",
        "candidatos": [{"way_id_referencia": 777, "nome_ou_ref": "BR-777",
                         "d_ini": 5.0, "d_fim": 5.0}],
    }]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        caminho = f.name
    try:
        gerar_relatorio([], caminho, pendencias_via_alternativa=pendencias, verbose=False)
        with open(caminho, encoding="utf-8") as f:
            conteudo = f.read()
        assert "PENDENCIA DE VIA ALTERNATIVA" in conteudo
        assert "--aceitar-via-alternativa" in conteudo
        assert "777" in conteudo
    finally:
        os.unlink(caminho)


def test_gerar_relatorio_secao_vias_alternativas_aceitas():
    aceitas = [{
        "codigo_base": "PRI - BR-999 - LUZ_MG - 60 KmH - 001", "via_original": "BR-999",
        "way_id": 777, "nome_ou_ref": "BR-777", "justificativa": "conferido no mapa",
        "confirmado_por": "caueh.rebello", "quando": "2026-09-28T10:00:00",
    }]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        caminho = f.name
    try:
        gerar_relatorio([], caminho, vias_alternativas_aceitas=aceitas, verbose=False)
        with open(caminho, encoding="utf-8") as f:
            conteudo = f.read()
        assert "VIA ALTERNATIVA ACEITA" in conteudo
        assert "conferido no mapa" in conteudo and "caueh.rebello" in conteudo
    finally:
        os.unlink(caminho)


def test_registrar_cerca_central_grava_via_alternativa_aceita():
    conn = _FakeCentralConn()
    registro = _registro_central(seq=1)
    registro["via_alternativa_aceita"] = {
        "via_original": "BR-999", "way_id": 777, "nome_ou_ref": "BR-777",
        "justificativa": "conferido no mapa", "confirmado_por": "caueh.rebello",
        "quando": "2026-09-28T10:00:00",
    }
    registrar_cerca_central(conn, registro, execucao_id="x")
    assert conn.rows[0]["via_alternativa_original"] == "BR-999"
    assert conn.rows[0]["via_alternativa_way_id"] == 777
    assert conn.rows[0]["via_alternativa_justificativa"] == "conferido no mapa"


def test_registrar_cerca_central_sem_via_alternativa_grava_none():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    assert conn.rows[0]["via_alternativa_original"] is None
    assert conn.rows[0]["via_alternativa_way_id"] is None


# ── FAT-351 / DEC-V4-31: fluxo direto (CLI/GUI) grava geometria_wkt no INSERT ──

def test_registrar_cerca_central_grava_geometria_wkt_linestring():
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, _registro_central(seq=1), execucao_id="x")
    assert conn.rows[0]["geometria_wkt"] == (
        "LINESTRING(-49.000000 -25.000000, -49.100000 -25.100000)"
    )


def test_registrar_cerca_central_sem_vertices_grava_geometria_wkt_none():
    conn = _FakeCentralConn()
    registro = _registro_central(seq=1)
    registro["vertices"] = []
    registrar_cerca_central(conn, registro, execucao_id="x")
    assert conn.rows[0]["geometria_wkt"] is None


def test_registrar_cerca_central_geometria_wkt_e_poligono_com_buffer_nao_linha_crua():
    """geometria_wkt vem dos vértices do polígono com buffer (Módulo 4), nunca
    da polilinha crua da via — evita reintroduzir a inconsistência
    polígono-vs-linha do backfill."""
    cercas = gerar_cercas(_POLY, "A", _INICIO, _FIM, None, pre_m=0, pos_m=0,
                          buffer_m=50, verbose=False)
    vertices_poligono = cercas["PRI"]["vertices"]
    registro = _registro_central(seq=1)
    registro["vertices"] = vertices_poligono
    conn = _FakeCentralConn()
    registrar_cerca_central(conn, registro, execucao_id="x")

    wkt = conn.rows[0]["geometria_wkt"]
    pontos = wkt[len("LINESTRING("):-1].split(", ")
    assert len(pontos) == len(vertices_poligono)
    # nenhum vértice do polígono coincide com a linha crua da via (buffer 50 m)
    linha_crua = {f"{lon:.6f} {lat:.6f}" for lat, lon in _POLY}
    assert not linha_crua.intersection(pontos)


def test_registrar_cerca_central_geometria_wkt_identica_ao_caminho_da_api():
    """Paridade com o caminho de referência (`api_cercas._gravar_geometria_wkt`)."""
    import api_cercas
    cercas = gerar_cercas(_POLY, "A", _INICIO, _FIM, None, pre_m=100, pos_m=100,
                          buffer_m=50, verbose=False)
    for tipo in ("PRI", "PRE"):
        vertices = cercas[tipo]["vertices"]
        registro = _registro_central(seq=1)
        registro["vertices"] = vertices
        conn = _FakeCentralConn()
        registrar_cerca_central(conn, registro, execucao_id="x")
        assert conn.rows[0]["geometria_wkt"] == api_cercas._wkt_linestring(vertices)


def test_processar_lote_fluxo_direto_grava_geometria_wkt_na_base_central(monkeypatch, tmp_path):
    monkeypatch.setattr(cercas_v2, "buscar_geometria_osm", lambda *a, **k: _POLY)
    conn = _FakeCentralConn()
    monkeypatch.setattr(cercas_v2, "_obter_conexao_central", lambda *a, **k: conn)
    caminho_entrada = _escrever_lote(
        'BR-116,,A,"-25.38,-49.19","-25.41,-49.19",,0,0,50,BR-116,LUZ,MG,60,1\n'
    )
    caminho_saida = str(tmp_path / "saida.csv")
    try:
        _, registros, *_ = processar_lote(caminho_entrada, caminho_saida,
                                          verbose=False, pg_fake=True)
        assert conn.rows
        por_codigo = {r["codigo"]: r for r in registros}
        for row in conn.rows:
            assert row["geometria_wkt"].startswith("LINESTRING(")
            esperado = cercas_v2._wkt_linestring(por_codigo[row["codigo"]]["vertices"])
            assert row["geometria_wkt"] == esperado
    finally:
        os.unlink(caminho_entrada)
