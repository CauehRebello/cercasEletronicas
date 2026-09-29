"""
Testes de integração — DEC-V4-34 (validação de rota OSRM no Módulo 2) contra
o OSRM público e o Overpass REAIS.  [FAT-368/376/377/378/379]

Diferente de `test_cercas.py` (que mocka `requests.get`/`requests.post`),
estes testes fazem chamadas de rede de verdade e reproduzem os 4 casos reais
cotejados manualmente com Caueh em 2026-09-29:

  1. SP-148 / BR-158 (mesmas coords, Jurubatuba/SP): a via física está
     fragmentada em dois grupos de ways desconectados no OSM perto desse
     ponto (gap de ~120 m) — nem o Módulo 2 clássico nem o DEC-V4-34
     conseguem costurar; cai no herdado sem candidata (RuntimeError).
  2. BR-365 (Pirapora/MG, ~180 m): rota OSRM entre os pontos tem 0% em vias
     nomeadas (provável via de acesso local sem tag); cai no herdado, que
     encontra 2 candidatas de via alternativa mas não aceita automaticamente
     (ViaAlternativaPendenteError).
  3. BR-101 (Ubaitaba/BA): via nomeada corretamente encontrada pelo Módulo 2
     clássico — sucesso sem passar pelo DEC-V4-34 (`via_alternativa_aceita`
     None, sem chamar OSRM).
  4. BR-101 com `--via` inventado (mesmas coords do caso 3): força o
     DEC-V4-34 — OSRM confirma ≥95% nomeado, Overpass acha os ways reais
     (ref BR-101 / name "Rodovia Governador Mário Covas"), costura
     conectada, aceito automaticamente. Mesma polilinha do caso 3.

Como esses resultados dependem do estado atual do OSM/OSRM público (dados
podem mudar), estes testes NÃO rodam na suíte normal — só com
`CERCAS_TEST_OSRM_REAL=1` explícito. Se o OSM for editado nesses pontos, os
asserts abaixo podem precisar de ajuste; o objetivo é documentar/reproduzir
o comportamento real observado, não travar o dado externo.
"""
import os

import pytest

from cercas_v2 import ViaAlternativaPendenteError, processar_linha_lote

pytestmark = pytest.mark.skipif(
    os.environ.get("CERCAS_TEST_OSRM_REAL") != "1",
    reason="CERCAS_TEST_OSRM_REAL != 1 — pulando testes reais contra "
           "OSRM/Overpass públicos (rede real, lento, dados externos).",
)


def _row(via, rodovia, cidade, uf, inicio, fim, velocidade=40, seq=1):
    return {
        "via": via, "polilinha": "", "modo": "A",
        "inicio": inicio, "fim": fim,
        "comprimento": "", "pre": "0", "pos": "0", "buffer": "50",
        "rodovia": rodovia, "cidade": cidade, "uf": uf, "velocidade": str(velocidade),
        "seq": str(seq), "_num_linha": "1",
    }


def test_real_sp148_br158_gap_conectividade_cai_no_herdado():
    row = _row("SP-148", "SP-148", "JURUBATUBA", "SP",
                "-23.84858,-46.46915", "-23.85191,-46.46608")
    with pytest.raises(RuntimeError) as exc:
        processar_linha_lote(row, verbose=False)
    assert not isinstance(exc.value, ViaAlternativaPendenteError)
    assert "Nenhuma via alternativa" in str(exc.value)


def test_real_br365_rota_sem_nome_cai_no_herdado():
    row = _row("BR-365", "BR-365", "PIRAPORA", "MG",
                "-17.36876,-44.94222", "-17.36771,-44.94074")
    with pytest.raises(ViaAlternativaPendenteError):
        processar_linha_lote(row, verbose=False)


def test_real_br101_via_correta_sucesso_classico():
    row = _row("BR-101", "BR-101", "UBAITABA", "BA",
                "-14.31221,-39.31464", "-14.31401,-39.31362")
    linhas, registros, via_alt = processar_linha_lote(row, verbose=False)
    assert linhas
    assert via_alt is None


def test_real_br101_via_inventada_aceita_automaticamente_dec_v4_34():
    row = _row("RODOVIA-INEXISTENTE-XYZ", "BR-101", "UBAITABA", "BA",
                "-14.31221,-39.31464", "-14.31401,-39.31362")
    linhas, registros, via_alt = processar_linha_lote(row, verbose=False)
    assert linhas
    assert via_alt is None  # aceitação automática, sem pendência/rastreabilidade
    extensao_m = registros[0]["extensao_m"]
    assert 200 < extensao_m < 300  # ~248 m observados no cotejamento real
