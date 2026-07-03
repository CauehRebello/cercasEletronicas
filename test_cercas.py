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
    _cache_get,
    _cache_key,
    _cache_set,
    _checar_duplicidade_central,
    _costura_ways,
    _haversine_m,
    _montar_codigo,
    _overpass_query,
    _utm_epsg,
    _validar_rodovia,
    avaliar_bloqueio_sobreposicao,
    buscar_geometria_osm,
    consultar_historico,
    detectar_sobreposicao,
    exportar_csv,
    gerar_cercas,
    gerar_relatorio,
    ler_lote,
    parse_coord,
    parse_polilinha_manual,
    registrar_cerca_central,
    salvar_no_historico,
    substituir_cerca_central,
    sugerir_proximo_seq_livre,
    validar_csv,
    verificar_codigo_central,
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
# `psycopg2` real não é usado nestes testes: as funções recebem uma conexão
# já aberta como parâmetro, então usamos uma conexão/cursor "fake" em
# memória que imita apenas o subconjunto da API usado pelo código (cursor(),
# execute(sql, params), fetchone(), fetchall(), rowcount, commit(), close()).
# Isso cobre a lógica de negócio (checagem, sugestão de SEQ, substituição),
# mas NÃO valida a conexão real com um servidor PostgreSQL — não há
# servidor disponível neste ambiente.

class _FakeCentralCursor:
    def __init__(self, conn):
        self._conn = conn
        self._resultado = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        s = sql.strip()
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("SELECT codigo"):
            rodovia, cidade, uf, velocidade, seq = params
            self._resultado = [
                (r["codigo"], r["tipo"], r["rodovia"], r["cidade"], r["uf"],
                 r["velocidade"], r["seq"], r["execucao_id"])
                for r in self._conn.rows
                if r["rodovia"] == rodovia and r["cidade"] == cidade and r["uf"] == uf
                and r["velocidade"] == velocidade and r["seq"] == seq and r["status"] == "ativo"
            ]
            return
        if s.startswith("SELECT seq"):
            rodovia, cidade, uf, velocidade = params
            self._resultado = [
                (r["seq"],) for r in self._conn.rows
                if r["rodovia"] == rodovia and r["cidade"] == cidade and r["uf"] == uf
                and r["velocidade"] == velocidade
            ]
            return
        if s.startswith("UPDATE"):
            timestamp, motivo, rodovia, cidade, uf, velocidade, seq = params
            n = 0
            for r in self._conn.rows:
                if (r["rodovia"] == rodovia and r["cidade"] == cidade and r["uf"] == uf
                        and r["velocidade"] == velocidade and r["seq"] == seq
                        and r["status"] == "ativo"):
                    r["status"] = "superado"
                    r["superado_em"] = timestamp
                    r["superado_motivo"] = motivo
                    n += 1
            self.rowcount = n
            return
        if s.startswith("INSERT"):
            (codigo, tipo, rodovia, cidade, uf, velocidade, seq, extensao_m,
             v_ini, v_fim, num_vertices, data_criacao, execucao_id) = params
            self._conn.rows.append({
                "codigo": codigo, "tipo": tipo, "rodovia": rodovia, "cidade": cidade,
                "uf": uf, "velocidade": velocidade, "seq": seq, "extensao_m": extensao_m,
                "vertice_inicial": v_ini, "vertice_final": v_fim, "num_vertices": num_vertices,
                "data_criacao": data_criacao, "execucao_id": execucao_id,
                "status": "ativo", "superado_em": None, "superado_motivo": None,
            })
            return
        raise AssertionError(f"SQL não esperado no fake de teste: {sql}")

    def fetchone(self):
        return self._resultado[0] if self._resultado else None

    def fetchall(self):
        return self._resultado


class _FakeCentralConn:
    def __init__(self):
        self.rows = []
        self.commits = 0

    def cursor(self):
        return _FakeCentralCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


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