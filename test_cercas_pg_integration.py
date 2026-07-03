"""
Testes de integração — Bloco A v4 contra um PostgreSQL REAL.  [FAT-181/182/183/202]

Diferente de `test_cercas.py` (que usa `_FakeCentralConn` em memória), estes
testes abrem uma conexão de verdade via `psycopg2` (através de
`cercas_v2._pg_conectar`) contra a instância apontada por `CERCAS_TEST_PG_DSN`.

Pulados automaticamente (sem falhar a suíte) se `CERCAS_TEST_PG_DSN` não
estiver definida — ver `.env.example`. Nunca rodar contra uma base central
de produção: use uma instância de desenvolvimento/teste descartável.
"""
import os

import pytest

from cercas_v2 import (
    DuplicidadeCodigoCentralError,
    _checar_duplicidade_central,
    _pg_conectar,
    _pg_criar_tabela,
    registrar_cerca_central,
    substituir_cerca_central,
    sugerir_proximo_seq_livre,
    verificar_codigo_central,
)

_DSN = os.environ.get("CERCAS_TEST_PG_DSN")

pytestmark = pytest.mark.skipif(
    not _DSN,
    reason="CERCAS_TEST_PG_DSN não definida — pulando testes de integração "
           "com PostgreSQL real (ver .env.example).",
)

_RODOVIA_TESTE = "TESTE_INTEGRACAO"


@pytest.fixture
def pg_conn():
    conn = _pg_conectar(_DSN)
    _pg_criar_tabela(conn)
    try:
        yield conn
    finally:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cercas_central WHERE rodovia = %s", (_RODOVIA_TESTE,))
        conn.commit()
        conn.close()


def _registro(cidade, seq=1):
    return {
        "codigo": f"PRI - {_RODOVIA_TESTE} - {cidade}_MG - 60 KmH - {seq:03d}",
        "tipo": "PRI", "seq": seq,
        "vertices": [(-25.0, -49.0), (-25.1, -49.1)], "extensao_m": 1000,
        "rodovia": _RODOVIA_TESTE, "cidade": cidade, "uf": "MG", "velocidade": 60,
    }


def test_conexao_e_realmente_psycopg2(pg_conn):
    assert type(pg_conn).__module__.startswith("psycopg2")


def test_registrar_e_verificar_codigo_central_real(pg_conn):
    registrar_cerca_central(pg_conn, _registro("REGISTRAR", seq=1), execucao_id="teste_integracao")
    existente = verificar_codigo_central(pg_conn, _RODOVIA_TESTE, "REGISTRAR", "MG", 60, 1)
    assert existente is not None
    assert existente["codigo"] == _registro("REGISTRAR", seq=1)["codigo"]


def test_verificar_codigo_central_none_quando_nao_existe_real(pg_conn):
    assert verificar_codigo_central(pg_conn, _RODOVIA_TESTE, "INEXISTENTE", "MG", 60, 1) is None


def test_sugerir_proximo_seq_livre_preenche_lacuna_real(pg_conn):
    for seq in (1, 2, 4):
        registrar_cerca_central(pg_conn, _registro("SEQLIVRE", seq=seq), execucao_id="teste_integracao")
    assert sugerir_proximo_seq_livre(pg_conn, _RODOVIA_TESTE, "SEQLIVRE", "MG", 60) == 3


def test_substituir_cerca_central_marca_superado_nao_apaga_real(pg_conn):
    registrar_cerca_central(pg_conn, _registro("SUBSTITUIR", seq=1), execucao_id="teste_integracao")
    n = substituir_cerca_central(pg_conn, _RODOVIA_TESTE, "SUBSTITUIR", "MG", 60, 1, motivo="reemissao")
    assert n == 1

    cursor = pg_conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM cercas_central WHERE rodovia = %s AND cidade = %s",
        (_RODOVIA_TESTE, "SUBSTITUIR"),
    )
    assert cursor.fetchone()[0] == 1  # nunca apaga — só marca como superado [FAT-182]
    assert verificar_codigo_central(pg_conn, _RODOVIA_TESTE, "SUBSTITUIR", "MG", 60, 1) is None


def test_checar_duplicidade_central_bloqueia_sem_substituir_real(pg_conn):
    registrar_cerca_central(pg_conn, _registro("BLOQUEIA", seq=1), execucao_id="teste_integracao")
    with pytest.raises(DuplicidadeCodigoCentralError) as exc:
        _checar_duplicidade_central(pg_conn, _RODOVIA_TESTE, "BLOQUEIA", "MG", 60, 1)
    assert "SEQ livre sugerido: 002" in str(exc.value)


def test_checar_duplicidade_central_permite_com_substituir_e_confirmar_real(pg_conn):
    registrar_cerca_central(pg_conn, _registro("PERMITE", seq=1), execucao_id="teste_integracao")
    _checar_duplicidade_central(
        pg_conn, _RODOVIA_TESTE, "PERMITE", "MG", 60, 1,
        substituir=True, confirmar_substituicao=True, motivo_substituicao="reemissao",
    )
    assert verificar_codigo_central(pg_conn, _RODOVIA_TESTE, "PERMITE", "MG", 60, 1) is None
