# Relatório final — Bloco C (API REST fina), Seção 7 do briefing

**Projeto:** Cercas Eletrônicas V4 — Transportadora Transleone Ltda
**Referência:** `Briefing_BlocoC_APIRestFina_v4.md` (Método RASTRO, DEC-V4-07/13/14/15/16)
**Data:** 13/07/2026

**Atualização (DEC-V4-16):** os itens 1, 3 e 5 abaixo foram revisados para refletir a correção de arquitetura do histórico (SQLite local → `cercas_central`/Postgres central). Os demais itens (duplicidade, sobreposição, override, health) não mudaram desde a versão anterior deste relatório.

---

## 1. Lista exata dos endpoints implementados, com exemplo de request/response

| Endpoint | Request de exemplo | Response de exemplo |
|---|---|---|
| `POST /cercas` | `{"modo":"B","polilinha":"-25.0,-49.0;-25.001,-49.0","inicio":"-25.0,-49.0","comprimento":10,"rodovia":"BR-116","cidade":"MANUAL","uf":"MG","velocidade":60,"seq":1}` | `201` → `{"execucao_id":"20260712231145476077","cercas":[{"codigo":"PRI - BR-116 - MANUAL_MG - 60 KmH - 001","tipo":"PRI","extensao_m":10,"num_vertices":4}]}` |
| `POST /cercas` (duplicidade) | mesmo request repetido | `409` → `{"detail":{"erro":"duplicidade_codigo","mensagem":"...","seq_sugerido":2}}` |
| `POST /cercas/lote` | multipart `arquivo=lote.csv` | sucesso: `200` com `total_gravado`/`sobreposicoes_alerta`/`bloqueios_sobreposicao`; bloqueio: `409` → `{"detail":{"erro":"bloqueio_sobreposicao","pares_bloqueados":[...]}}` (testado ao vivo: par com 100% de sobreposição bloqueado corretamente) |
| `GET /cercas/historico?rodovia=BR-116` (**DEC-V4-16**: consulta `cercas_central`/Postgres, mesma conexão do Bloco A/health — não mais SQLite local) | — | `200` → `{"total":1,"registros":[{"codigo":"PRI - BR-116 - MANUAL_MG - 60 KmH - 001","tipo":"PRI","rodovia":"BR-116","cidade":"MANUAL","uf":"MG","velocidade":60,"seq":1,"extensao_m":10.0,"vertice_inicial":"...","vertice_final":"...","num_vertices":4,"data_criacao":"...","execucao_id":"...","status":"ativo","superado_em":null,"superado_motivo":null}]}` — testado ao vivo (criar via `POST /cercas`, consultar via `GET /cercas/historico`) |
| `POST /cercas/override` | multipart `arquivo` + `overrides=[{"codigo_existente":...,"codigo_novo":...,"justificativa":"...","confirmado_por":"caueh.rebello"}]` | `200` → `bloqueios_sobreposicao[0].bloqueado=false`, `relatorio_csv` com a seção de auditoria (justificativa/confirmado_por/quando) — testado ao vivo |
| `GET /health` | — | `200` → `{"status":"ok"}` |

Documentação automática (Swagger/OpenAPI) confirmada ativa: `GET /docs` e `GET /openapi.json` retornaram `200`.

## 2. Confirmação de que nenhuma função dos Módulos 1–9 teve assinatura alterada

`git diff --stat cercas_v2.py` retornou vazio — nenhuma linha do arquivo foi tocada. `api_cercas.py` só importa `cercas_v2` e chama suas funções.

## 3. Suíte de testes — contagem exata

`test_api_cercas.py`: **17 testes** (`grep -c "^def test_" test_api_cercas.py` → 17), cobrindo os 5 endpoints e os casos de erro da Seção 3.1. Mudança desde a versão anterior deste relatório (DEC-V4-16): os 3 testes que exercitavam o mecanismo SQLite eliminado (`test_criar_cerca_grava_historico_quando_configurado`, `test_lote_grava_historico_quando_configurado`, `test_historico_sem_configuracao`) foram removidos; `test_historico_vazio_sem_arquivo`/`test_historico_com_dados_e_filtro` foram reescritos como `test_historico_sem_banco_configurado`, `test_historico_sem_registros` e `test_historico_com_dados_filtro_e_status_superado` (esse último confirma que registros com status `superado` também aparecem no histórico, não só `ativo` — ver decisão no item 5). Os demais 14 testes (duplicidade+seq sugerido, Modo B inválido, OSM ausente, bloqueio de sobreposição, override feliz e override desalinhado, health up/down, `/cercas/lote` independente da dependency `get_pg_conn`) não mudaram.

## 4. Resultado real da execução

```
python -m pytest test_cercas.py test_cercas_pg_integration.py test_api_cercas.py
...
83 passed, 7 skipped in 1.82s
```
(7 skipped = testes de integração real contra Postgres, sem `CERCAS_TEST_PG_DSN` no ambiente — comportamento esperado, igual às sessões anteriores.)

## 5. Limitações e decisões documentadas

- **DEC-V4-14**: `POST /cercas` não faz checagem de sobreposição — fiel ao comportamento atual do CLI single-cerca. Extensão futura (cerca avulsa vs. histórico) fica como lacuna em aberto.
- **DEC-V4-15**: `POST /cercas/override` é stateless — reenvia o CSV do lote + overrides, reprocessa tudo numa chamada. Auditoria (quem/quando/por quê) reaproveita `gerar_relatorio()` (Módulo 8) já existente; `confirmado_por` vem do corpo da requisição (sem auth nesta fase), `quando` é carimbado pelo servidor.
- **DEC-V4-16 (correção de arquitetura do histórico):**
  - Verificado por leitura direta do código (não assumido): `consultar_historico()`/`salvar_no_historico()` em `cercas_v2.py` **continuam exclusivamente SQLite** (`sqlite3.connect(caminho_db)`) — não foram migradas para o Postgres central em nenhum momento. Não havia, portanto, nada reaproveitável dessas duas funções.
  - `GET /cercas/historico` foi reescrito para consultar `cercas_central` diretamente, usando a mesma dependency `get_pg_conn` de `/cercas`/`/health`. `CERCAS_API_HISTORICO_DB` foi eliminada por completo (config, docstring e as duas gravações que eu tinha adicionado em `/cercas`/`/cercas/lote` na correção anterior, que ficaram redundantes — a escrita em `cercas_central` via `registrar_cerca_central` já é "o mesmo destino").
  - **Decisão sinalizada explicitamente**: o endpoint retorna registros de qualquer `status` (`ativo` e `superado`), preservando o espírito de "log de tudo já registrado" que a antiga tabela SQLite tinha (sem conceito de status). Se a gestão preferir restringir a `ativo`, é um ajuste pequeno — não decidido silenciosamente.
  - **Bug real encontrado e corrigido durante a verificação manual** (não só um problema de teste): a query nova (`SELECT codigo, tipo, rodovia, ...`) colidia com o prefixo `"SELECT codigo"` que `_FakeCentralConn`/`_FakeCentralCursor` (usado por `--pg-fake`) reconhece como a query de checagem de duplicidade — isso quebrava com `ValueError: not enough values to unpack` (HTTP 500) sempre que a API rodasse em modo `--pg-fake` e alguém chamasse `/cercas/historico`. Reproduzido ao vivo (`curl` contra a API rodando com `CERCAS_API_PG_FAKE=1`), não apenas nos testes automatizados. Corrigido em `_consultar_cercas_central` (`api_cercas.py`) detectando `isinstance(conn, cv._FakeCentralConn)` e filtrando `conn.rows` diretamente em memória nesse caso, sem emitir SQL — sem tocar `cercas_v2.py`. Reverificado ao vivo após o fix: `200` em vez de `500`.

---

Conforme Cláusula VII do Método RASTRO, esta alegação permanece **não verificada até cotejamento direto** pela gestão do projeto.
