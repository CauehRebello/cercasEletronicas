# Relatório — Feature de Mapa/Visualização v4

Referência: `Briefing_MapaVisualizacao_CercasV4.md` (DEC-V4-17/FAT-325, DEC-V4-18/21-27, Sessão 8).

## ⚠️ Alerta de transparência (Cláusula VII) — antes de qualquer outra coisa

`git diff --stat cercas_v2.py` **não retorna vazio**, mas essa modificação **não foi feita por mim** e não
tem relação com esta entrega:

```
cercas_v2.py | 7 ++++++-
1 file changed, 6 insertions(+), 1 deletion(-)
```

Esse diff (dois trechos: `sys.stdout.reconfigure(encoding="utf-8")`/`sys.stderr.reconfigure(...)` logo após
os imports, e um ajuste de regex em `buscar_geometria_osm` para casar `ref` com múltiplos valores separados
por `;`) já constava como `M cercas_v2.py` no `git status` **no início desta sessão**, antes de eu tocar em
qualquer arquivo — não foi introduzido por este trabalho. Não revertei essa alteração por não saber se é
trabalho em progresso seu; recomendo que você confira e decida se commita, descarta ou trata em separado.
**Nenhuma linha de `cercas_v2.py` foi adicionada, removida ou alterada por esta entrega.** Rode
`git diff -- cercas_v2.py` você mesmo para conferir o conteúdo exato antes de aceitar esta afirmação.

## 1. Endpoints do Bloco C alterados

Nenhuma rota nova — apenas os 3 já previstos no Briefing (Seção 4.1), em `api_cercas.py`:

| Endpoint | Alteração |
|---|---|
| `GET /cercas/historico` | `geometria_wkt` incluído na tupla `_COLUNAS_CERCAS_CENTRAL` — propaga para o `SELECT` real e para o filtro do `_FakeCentralConn`. |
| `POST /cercas` | Após o `INSERT` do núcleo (`cv.registrar_cerca_central`), chama `_gravar_geometria_wkt(conn, registros)` — grava o WKT do polígono já calculado (`registro["vertices"]`), mesma conexão. |
| `POST /cercas/lote` e `POST /cercas/override` | Ambos passam por `_processar_lote_arquivo`; após `cv.processar_lote` retornar (conexão interna já commitada e fechada), abre uma nova conexão e chama `_gravar_geometria_wkt` sobre os `registros` retornados. |

`POST /cercas/override` não precisou de código extra: compartilha `_processar_lote_arquivo` com `/lote`.

**Decisão de design (validada com o responsável antes da implementação):** `geometria_wkt` = WKT
`LINESTRING` da lista completa de vértices do polígono com buffer (`registro["vertices"]`) — a mesma lista
já usada hoje para `vertice_inicial`/`vertice_final` (primeiro/último ponto dela). Zero cálculo novo, zero
alteração de `cercas_v2.py`.

## 2. Schema

Novo `migracao_geometria_wkt.sql`:
```sql
ALTER TABLE public.cercas_central ADD COLUMN IF NOT EXISTS geometria_wkt TEXT;
```
`schema_cercas_v4.sql` (dump de referência) atualizado para incluir a coluna, mantendo-o fiel ao schema real.

**Pendente de você:** aplicar no servidor 10.2.15.3 (`psql -h 10.2.15.3 -U <usuario> -d <banco> -f
migracao_geometria_wkt.sql`) e colar aqui a saída de `\d cercas_central` — não tenho acesso a esse servidor
nesta sessão, então não posso executar nem confirmar essa etapa por conta própria (exigência da Seção 6 do
Briefing).

## 3. Backfill (`backfill_geometria_wkt.py`, novo, standalone)

Reaproveita `cv.buscar_geometria_osm` (retry S7) e `cv.parse_coord`. Loga sucesso/falha por cerca, não
aborta o lote em falha individual, idempotente (só processa `geometria_wkt IS NULL`).

**Limitação registrada e aceita (não é omissão):** o polígono completo das cercas históricas nunca foi
persistido (só os 2 extremos) e os parâmetros originais de geração (buffer/modo/pré/pós) também não — logo
é impossível reconstruir o polígono exato. O backfill grava a **linha da via** (Módulo 2, rebuscada no
Overpass), não o polígono. `geometria_wkt` de cercas migradas por backfill é, portanto, geometricamente
diferente do que é gravado para cercas novas (polígono). Isso está documentado no topo do próprio script e
seria repetido no console a cada execução real.

**Pendente de você:** rodar contra o servidor real e colar a contagem real de sucesso/falha (não posso
executar contra 10.2.15.3 nesta sessão — sem acesso de rede a esse servidor a partir daqui).

## 4. GUI (`cercas_gui.py`)

- `ttk.Notebook` com 2 abas: "Gerar Cerca" (formulário existente, sem alteração de lógica) e "Mapa" (nova).
- Aba "Mapa": `GET {CERCAS_GUI_API_URL:-http://localhost:8000}/cercas/historico` via `requests` (timeout 5s),
  parse de `geometria_wkt` (`_parse_wkt_linestring`, função pura testável) e desenho com
  `tkintermapview.set_path`.
- Fallback: qualquer falha de rede (requisição HTTP ou construção do widget/tiles) exibe um `ttk.Label` de
  aviso na própria aba em vez de propagar exceção.
- `tkintermapview>=1.29` adicionado a `requirements.txt`; **instalado e validado nesta máquina** contra
  Python 3.14.6/Windows (`import tkintermapview` limpo, confirmado via `pip install` + import direto).

**Pendente de você:** abrir a GUI (`python cercas_gui.py`) e validar visualmente com e sem rede — não tenho
como operar a interface gráfica interativamente nesta sessão de terminal.

## 5. Suíte de testes — saída literal

```
$ pytest -q -rs
........................................................................ [ 70%]
......................sssssss.                                           [100%]
=========================== short test summary info ===========================
SKIPPED [1] test_cercas_pg_integration.py:60: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
SKIPPED [1] test_cercas_pg_integration.py:64: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
SKIPPED [1] test_cercas_pg_integration.py:71: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
SKIPPED [1] test_cercas_pg_integration.py:75: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
SKIPPED [1] test_cercas_pg_integration.py:81: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
SKIPPED [1] test_cercas_pg_integration.py:95: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
SKIPPED [1] test_cercas_pg_integration.py:102: CERCAS_TEST_PG_DSN não definida — pulando testes de integração com PostgreSQL real (ver .env.example).
95 passed, 7 skipped, 4 warnings in 1.77s
```

Os 7 `skipped` são pré-existentes (exigem PostgreSQL real via `CERCAS_TEST_PG_DSN`, não relacionados a esta
entrega — confirmado lendo `test_cercas_pg_integration.py:60-102`). Testes novos desta entrega (12, todos
`passed` acima): 3 em `test_api_cercas.py` (grava WKT em `/cercas`, em `/cercas/lote`, retorna WKT em
`/historico`), 5 em `test_backfill_geometria_wkt.py`, 3 em `test_cercas_gui_mapa.py`, 1 em
`test_migracao_geometria_wkt.py`.

## 6. Arquivos alterados/criados

```
$ git diff --stat
 api_cercas.py        |  49 +++++++++++++++++++++-
 cercas_gui.py        | 113 +++++++++++++++++++++++++++++++++++++++++++++++++--
 cercas_v2.py         |   7 +++-   <- pré-existente, não é desta entrega (ver alerta acima)
 requirements.txt     |   1 +
 schema_cercas_v4.sql |   3 +-
 test_api_cercas.py   |  66 ++++++++++++++++++++++++++++++

Novos (git status ??):
 backfill_geometria_wkt.py
 migracao_geometria_wkt.sql
 test_backfill_geometria_wkt.py
 test_cercas_gui_mapa.py
 test_migracao_geometria_wkt.py
```

## 7. Fora de escopo (confirmado não implementado)

FAT-309 (Bloco B cross-execução), FAT-333 (nó-vs-linha Módulo 2), índice espacial GiST/PostGIS, Opção B/C
web — nenhum desses foi tocado, conforme Seção 5 do Briefing.
