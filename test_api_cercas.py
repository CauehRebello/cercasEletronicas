"""
Testes do Bloco C — API REST fina (api_cercas.py).

Usa `_FakeCentralConn` (mesma conexão em memória já usada por
`test_cercas.py`/`--pg-fake`) para /cercas e /health via override da
dependency `_get_raw_conn`. Para /cercas/lote e /cercas/override, que NÃO
usam essa dependency (repassam `pg_dsn`/`pg_fake` direto para
`processar_lote`, que abre sua própria conexão), o mecanismo de teste é
`CERCAS_API_PG_FAKE=1` + monkeypatch de `cercas_v2._obter_conexao_central`
quando o teste precisa que o estado "central" persista entre a montagem do
cenário e a chamada da API (senão cada `_obter_conexao_central(usar_fake=True)`
cria uma conexão fake nova e vazia).

Nenhum teste aqui depende de rede real (Overpass/OSM) — usa `polilinha`
manual ou monkeypatch de `cercas_v2.buscar_geometria_osm`.
"""
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

import api_cercas
import cercas_v2 as cv
from api_cercas import app, get_pg_conn, _get_raw_conn


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _limpar_overrides():
    yield
    app.dependency_overrides.clear()


def _usar_fake_conn(conn: cv._FakeCentralConn):
    """Faz /cercas e /health usarem `conn` (via override da dependency)."""
    def _override():
        yield conn
    app.dependency_overrides[_get_raw_conn] = _override


def _linha_lote_dict(**overrides):
    base = {
        "via": "", "polilinha": "-25.000000,-49.000000;-25.001000,-49.000000",
        "modo": "B", "inicio": "-25.000000,-49.000000", "fim": "",
        "comprimento": "10", "pre": "0", "pos": "0", "buffer": "50",
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG",
        "velocidade": "60", "seq": "1",
    }
    base.update(overrides)
    return base


def _csv_lote(linhas):
    colunas = ["via", "polilinha", "modo", "inicio", "fim", "comprimento",
               "pre", "pos", "buffer", "rodovia", "cidade", "uf", "velocidade", "seq"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=colunas)
    writer.writeheader()
    for linha in linhas:
        writer.writerow(linha)
    return buf.getvalue().encode("utf-8")


def _upload_lote(client, linhas, **kwargs):
    conteudo = _csv_lote(linhas)
    return client.post(
        "/cercas/lote",
        files={"arquivo": ("lote.csv", conteudo, "text/csv")},
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /health
# ─────────────────────────────────────────────────────────────────────────────

def test_health_ok(client):
    _usar_fake_conn(cv._FakeCentralConn())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_falha_conexao(client):
    from fastapi import HTTPException

    def _override_falha():
        raise HTTPException(status_code=503, detail="Falha ao conectar à base central PostgreSQL (--pg-dsn): boom")
        yield  # pragma: no cover - nunca alcançado, mantém a função geradora

    app.dependency_overrides[_get_raw_conn] = _override_falha
    resp = client.get("/health")
    assert resp.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# POST /cercas
# ─────────────────────────────────────────────────────────────────────────────

def test_criar_cerca_sucesso(client):
    _usar_fake_conn(cv._FakeCentralConn())
    body = {
        "modo": "B",
        "polilinha": "-25.000000,-49.000000;-25.001000,-49.000000",
        "inicio": "-25.000000,-49.000000",
        "comprimento": 10,
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG",
        "velocidade": 60, "seq": 1,
    }
    resp = client.post("/cercas", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["execucao_id"]
    codigos = [c["codigo"] for c in data["cercas"]]
    assert any(c.startswith("PRI - BR-116 - TESTE_MG - 60 KmH - 001") for c in codigos)


def test_criar_cerca_duplicidade_retorna_seq_sugerido(client):
    conn = cv._FakeCentralConn()
    conn.rows.append({
        "codigo": "PRI - BR-116 - TESTE_MG - 60 KmH - 001", "tipo": "PRI",
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG", "velocidade": 60, "seq": 1,
        "extensao_m": 10, "vertice_inicial": "", "vertice_final": "", "num_vertices": 4,
        "data_criacao": "", "execucao_id": "x", "status": "ativo",
        "superado_em": None, "superado_motivo": None,
    })
    _usar_fake_conn(conn)
    body = {
        "modo": "B",
        "polilinha": "-25.000000,-49.000000;-25.001000,-49.000000",
        "inicio": "-25.000000,-49.000000",
        "comprimento": 10,
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG",
        "velocidade": 60, "seq": 1,
    }
    resp = client.post("/cercas", json=body)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["erro"] == "duplicidade_codigo"
    assert detail["seq_sugerido"] == 2


def test_criar_cerca_modo_b_comprimento_maior_que_via(client):
    _usar_fake_conn(cv._FakeCentralConn())
    body = {
        "modo": "B",
        "polilinha": "-25.000000,-49.000000;-25.001000,-49.000000",  # ~111 m
        "inicio": "-25.000000,-49.000000",
        "comprimento": 100_000,  # bem maior que a via disponível
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG",
        "velocidade": 60, "seq": 1,
    }
    resp = client.post("/cercas", json=body)
    assert resp.status_code == 422


def test_criar_cerca_osm_nao_encontrado(client, monkeypatch):
    _usar_fake_conn(cv._FakeCentralConn())
    monkeypatch.setattr(cv, "buscar_geometria_osm", lambda *a, **kw: None)
    body = {
        "modo": "A",
        "via": "BR-116",
        "inicio": "-25.000000,-49.000000",
        "fim": "-25.001000,-49.000000",
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG",
        "velocidade": 60, "seq": 1,
    }
    resp = client.post("/cercas", json=body)
    assert resp.status_code == 502


def test_criar_cerca_via_e_polilinha_mutuamente_exclusivos(client):
    _usar_fake_conn(cv._FakeCentralConn())
    body = {
        "modo": "B",
        "via": "BR-116",
        "polilinha": "-25.000000,-49.000000;-25.001000,-49.000000",
        "inicio": "-25.000000,-49.000000",
        "comprimento": 10,
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG",
        "velocidade": 60, "seq": 1,
    }
    resp = client.post("/cercas", json=body)
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# POST /cercas/lote
# ─────────────────────────────────────────────────────────────────────────────

def test_lote_sucesso(client, monkeypatch):
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")
    resp = _upload_lote(client, [_linha_lote_dict()])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_gravado"] == 1  # pre/pos=0 => só PRI é gerada
    assert data["bloqueios_sobreposicao"] == []


def test_lote_duplicidade(client, monkeypatch):
    conn_compartilhada = cv._FakeCentralConn()
    conn_compartilhada.rows.append({
        "codigo": "PRI - BR-116 - TESTE_MG - 60 KmH - 001", "tipo": "PRI",
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG", "velocidade": 60, "seq": 1,
        "extensao_m": 10, "vertice_inicial": "", "vertice_final": "", "num_vertices": 4,
        "data_criacao": "", "execucao_id": "x", "status": "ativo",
        "superado_em": None, "superado_motivo": None,
    })
    monkeypatch.setattr(cv, "_obter_conexao_central", lambda dsn, usar_fake=False, verbose=True: conn_compartilhada)
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")

    resp = _upload_lote(client, [_linha_lote_dict(seq="1")])
    assert resp.status_code == 409
    assert resp.json()["detail"]["erro"] == "duplicidade_codigo"


def test_lote_bloqueio_sobreposicao(client, monkeypatch):
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")
    linhas = [
        _linha_lote_dict(seq="1"),
        _linha_lote_dict(seq="2"),  # mesma polilinha/posição => ~100% sobreposição
    ]
    resp = _upload_lote(client, linhas)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["erro"] == "bloqueio_sobreposicao"
    assert len(detail["pares_bloqueados"]) == 1


def test_lote_nao_usa_dependency_get_pg_conn(client, monkeypatch):
    """/cercas/lote não deve depender de `get_pg_conn` — ele repassa
    pg_dsn/pg_fake direto para `processar_lote`. Se alguém acoplar essa
    rota à dependency por engano, este teste passaria a falhar (a
    dependency não está sobrescrita aqui, então exigiria banco real)."""
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")
    resp = _upload_lote(client, [_linha_lote_dict()])
    assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# POST /cercas/override
# ─────────────────────────────────────────────────────────────────────────────

def test_override_resolve_bloqueio(client, monkeypatch):
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")
    linhas = [
        _linha_lote_dict(seq="1"),
        _linha_lote_dict(seq="2"),
    ]
    overrides = json.dumps([
        {
            "codigo_existente": "PRI - BR-116 - TESTE_MG - 60 KmH - 001",
            "codigo_novo": "PRI - BR-116 - TESTE_MG - 60 KmH - 002",
            "justificativa": "Vias paralelas confirmadas manualmente.",
            "confirmado_por": "caueh.rebello",
        }
    ])
    conteudo = _csv_lote(linhas)
    resp = client.post(
        "/cercas/override",
        files={"arquivo": ("lote.csv", conteudo, "text/csv")},
        data={"overrides": overrides},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["bloqueios_sobreposicao"][0]["bloqueado"] is False
    assert "Vias paralelas confirmadas manualmente." in data["relatorio_csv"]
    assert "caueh.rebello" in data["relatorio_csv"]


def test_override_justificativa_vazia_e_invalida(client, monkeypatch):
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")
    linhas = [_linha_lote_dict(seq="1"), _linha_lote_dict(seq="2")]
    overrides = json.dumps([
        {
            "codigo_existente": "PRI - BR-116 - TESTE_MG - 60 KmH - 001",
            "codigo_novo": "PRI - BR-116 - TESTE_MG - 60 KmH - 002",
            "justificativa": "   ",
            "confirmado_por": "caueh.rebello",
        }
    ])
    conteudo = _csv_lote(linhas)
    resp = client.post(
        "/cercas/override",
        files={"arquivo": ("lote.csv", conteudo, "text/csv")},
        data={"overrides": overrides},
    )
    assert resp.status_code == 422


def test_override_par_desalinhado_do_lote_reenviado(client, monkeypatch):
    """Se o override se refere a um par que não está mais bloqueado no lote
    reenviado (ex.: o cliente mudou o lote entre a tentativa original e o
    override), o bloqueio remanescente (se houver) continua sendo reportado
    — o endpoint não assume silenciosamente que o override cobre tudo."""
    monkeypatch.setenv("CERCAS_API_PG_FAKE", "1")
    linhas = [_linha_lote_dict(seq="1"), _linha_lote_dict(seq="2")]
    overrides = json.dumps([
        {
            "codigo_existente": "PRI - BR-116 - TESTE_MG - 60 KmH - 999",
            "codigo_novo": "PRI - BR-116 - TESTE_MG - 60 KmH - 998",
            "justificativa": "Par que não existe neste lote.",
            "confirmado_por": "caueh.rebello",
        }
    ])
    conteudo = _csv_lote(linhas)
    resp = client.post(
        "/cercas/override",
        files={"arquivo": ("lote.csv", conteudo, "text/csv")},
        data={"overrides": overrides},
    )
    # O par real (seq 001 x 002) continua bloqueado — override não bateu.
    assert resp.status_code == 409
    assert resp.json()["detail"]["erro"] == "bloqueio_sobreposicao"


# ─────────────────────────────────────────────────────────────────────────────
# GET /cercas/historico — agora consulta cercas_central (Postgres), DEC-V4-16
#
# `_consultar_cercas_central` (api_cercas.py) detecta `_FakeCentralConn` e
# filtra `conn.rows` diretamente em memória, sem emitir SQL — por isso os
# testes abaixo usam `_FakeCentralConn` normalmente, sem subclasse alguma.
# ─────────────────────────────────────────────────────────────────────────────

def test_historico_sem_banco_configurado(client, monkeypatch):
    monkeypatch.delenv("CERCAS_API_PG_DSN", raising=False)
    monkeypatch.delenv("CERCAS_API_PG_FAKE", raising=False)
    resp = client.get("/cercas/historico")
    assert resp.status_code == 503


def test_historico_sem_registros(client):
    _usar_fake_conn(cv._FakeCentralConn())
    resp = client.get("/cercas/historico")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "registros": []}


def test_historico_com_dados_filtro_e_status_superado(client):
    conn = cv._FakeCentralConn()
    conn.rows.append({
        "codigo": "PRI - BR-116 - TESTE_MG - 60 KmH - 001", "tipo": "PRI",
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG", "velocidade": 60, "seq": 1,
        "extensao_m": 10, "vertice_inicial": "", "vertice_final": "", "num_vertices": 4,
        "data_criacao": "", "execucao_id": "x", "status": "ativo",
        "superado_em": None, "superado_motivo": None,
    })
    conn.rows.append({
        "codigo": "PRI - BR-116 - TESTE_MG - 60 KmH - 002", "tipo": "PRI",
        "rodovia": "BR-116", "cidade": "TESTE", "uf": "MG", "velocidade": 60, "seq": 2,
        "extensao_m": 10, "vertice_inicial": "", "vertice_final": "", "num_vertices": 4,
        "data_criacao": "", "execucao_id": "y", "status": "superado",
        "superado_em": "2026-07-13T10:00:00", "superado_motivo": "reemissao",
    })
    conn.rows.append({
        "codigo": "PRI - SC-470 - OUTRA_SC - 60 KmH - 001", "tipo": "PRI",
        "rodovia": "SC-470", "cidade": "OUTRA", "uf": "SC", "velocidade": 60, "seq": 1,
        "extensao_m": 10, "vertice_inicial": "", "vertice_final": "", "num_vertices": 4,
        "data_criacao": "", "execucao_id": "z", "status": "ativo",
        "superado_em": None, "superado_motivo": None,
    })
    _usar_fake_conn(conn)

    resp = client.get("/cercas/historico", params={"rodovia": "BR-116"})
    assert resp.status_code == 200
    data = resp.json()
    # Ambos os registros de BR-116 aparecem, inclusive o 'superado' — histórico
    # não se restringe a cercas atualmente ativas.
    assert data["total"] == 2
    status_encontrados = {r["status"] for r in data["registros"]}
    assert status_encontrados == {"ativo", "superado"}

    resp_sem_match = client.get("/cercas/historico", params={"rodovia": "RS-135"})
    assert resp_sem_match.json() == {"total": 0, "registros": []}
