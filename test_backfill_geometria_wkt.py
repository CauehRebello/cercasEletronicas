"""
Testes do script de backfill de geometria_wkt (DEC-V4-22).

Usa `_FakeCentralConn` (mesma conexão em memória do Bloco A) e monkeypatch de
`cercas_v2.buscar_geometria_osm` — sem rede real, mesmo padrão de
`test_api_cercas.py`.
"""
import cercas_v2 as cv
import backfill_geometria_wkt as bf


def _linha(codigo, geometria_wkt=None, vertice_inicial="-25.000000,-49.000000",
           vertice_final="-25.001000,-49.000000"):
    return {
        "codigo": codigo, "tipo": "PRI", "rodovia": "BR-116", "cidade": "TESTE",
        "uf": "MG", "velocidade": 60, "seq": 1, "extensao_m": 10,
        "vertice_inicial": vertice_inicial, "vertice_final": vertice_final,
        "num_vertices": 4, "data_criacao": "", "execucao_id": "x", "status": "ativo",
        "superado_em": None, "superado_motivo": None, "geometria_wkt": geometria_wkt,
    }


def test_wkt_linestring_formato():
    wkt = bf._wkt_linestring([(-25.0, -49.0), (-25.001, -49.0)])
    assert wkt == "LINESTRING(-49.000000 -25.000000, -49.000000 -25.001000)"


def test_wkt_linestring_pontos_insuficientes():
    assert bf._wkt_linestring([(-25.0, -49.0)]) is None
    assert bf._wkt_linestring([]) is None


def test_backfill_sucesso(monkeypatch):
    conn = cv._FakeCentralConn()
    conn.rows.append(_linha("A"))
    conn.rows.append(_linha("B", geometria_wkt="LINESTRING(1 2, 3 4)"))  # já migrada

    monkeypatch.setattr(
        cv, "buscar_geometria_osm",
        lambda *a, **kw: [(-25.0, -49.0), (-25.001, -49.0)],
    )

    sucesso, falha = bf.backfill(conn, verbose=False)
    assert sucesso == 1
    assert falha == 0
    assert conn.rows[0]["geometria_wkt"] == "LINESTRING(-49.000000 -25.000000, -49.000000 -25.001000)"
    assert conn.rows[1]["geometria_wkt"] == "LINESTRING(1 2, 3 4)"  # não reprocessada


def test_backfill_falha_individual_nao_aborta_lote(monkeypatch, capsys):
    conn = cv._FakeCentralConn()
    conn.rows.append(_linha("FALHA"))
    conn.rows.append(_linha("OK"))

    respostas = iter([None, [(-25.0, -49.0), (-25.001, -49.0)]])
    monkeypatch.setattr(cv, "buscar_geometria_osm", lambda *a, **kw: next(respostas))

    sucesso, falha = bf.backfill(conn, verbose=False)
    assert sucesso == 1
    assert falha == 1
    assert conn.rows[0]["geometria_wkt"] is None
    assert conn.rows[1]["geometria_wkt"] is not None
    assert "[FALHA] FALHA" in capsys.readouterr().out


def test_backfill_vertice_invalido_conta_como_falha(monkeypatch):
    conn = cv._FakeCentralConn()
    conn.rows.append(_linha("RUIM", vertice_inicial="", vertice_final=""))

    sucesso, falha = bf.backfill(conn, verbose=False)
    assert sucesso == 0
    assert falha == 1
    assert conn.rows[0]["geometria_wkt"] is None
