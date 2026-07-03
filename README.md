# Cercas Eletrônicas — Transportadora Transleone Ltda

Ferramenta para geração de cercas eletrônicas (POL) no formato de importação
SASCAR, a partir de geometria de vias obtida no OpenStreetMap (OSM) ou
informada manualmente.

## Instalação

```bash
pip install -r requirements.txt
```

Requer Python 3.9+.

**Windows:** os prints de progresso usam caracteres Unicode (✓, ⚠). Se o
console mostrar `UnicodeEncodeError`, rode com:

```powershell
$env:PYTHONIOENCODING = "utf-8"
python cercas_v2.py ...
```

## Uso — cerca única

### Modo A (par de coordenadas), via buscada no OSM

```bash
python cercas_v2.py --via BR-116 --modo A \
  --inicio -25.38,-49.19 --fim -25.40,-49.16 \
  --pre 300 --pos 300 \
  --rodovia BR-116 --cidade LUZ --uf MG --velocidade 60 --seq 1
```

### Modo B (início + comprimento), polilinha manual, saída TXT

```bash
python cercas_v2.py --polilinha '-25.38,-49.19;-25.39,-49.18;-25.40,-49.16' \
  --modo B --inicio -25.38,-49.19 --comprimento 2000 \
  --pre 500 --pos 500 \
  --rodovia 'Av Brasil' --cidade BLUMENAU --uf SC \
  --velocidade 40 --seq 1 --formato txt
```

`--via` e `--polilinha` são mutuamente exclusivos: `--via` busca a geometria
no Overpass API (OSM); `--polilinha` é o fallback manual (`lat1,lon1;lat2,lon2;...`),
usado quando a via não é encontrada no OSM ou a rede está indisponível.

## Uso — lote (múltiplas cercas)

```bash
python cercas_v2.py --batch lote_modelo.csv --saida saida_lote.csv --relatorio relatorio.csv
```

O CSV de lote (ver `lote_modelo.csv`) tem uma linha por cerca, com as colunas
`via, polilinha, modo, inicio, fim, comprimento, pre, pos, buffer, rodovia,
cidade, uf, velocidade, seq`. `--batch` é mutuamente exclusivo com os
argumentos de cerca única (`--via`, `--modo`, `--rodovia` etc.).

## Interface gráfica (opcional)

```bash
python cercas_gui.py
```

Formulário `tkinter` com os mesmos campos da CLI (incluindo as opções de
retry/cache/histórico abaixo). Não duplica lógica de negócio — invoca
`cercas_v2.main()` internamente, então produz exatamente a mesma saída que a
chamada equivalente da CLI. A CLI continua funcionando de forma independente,
sem qualquer dependência da GUI.

## Opções gerais

| Flag | Descrição | Padrão |
|---|---|---|
| `--formato {csv,txt}` | Extensão do arquivo de saída | `csv` |
| `--saida <caminho>` | Caminho do arquivo de saída | `cercas.<formato>` / `cercas_lote.<formato>` |
| `--buffer <m>` | Meio-largura do buffer (m/lado) | `50.0` |
| `--silencioso` | Suprime mensagens de progresso | — |
| `--relatorio <caminho.csv>` | Relatório com resumo das cercas geradas e alertas de sobreposição entre polígonos (não bloqueante) | nenhum relatório |

## Retry de rede configurável (Overpass API)

Controla as tentativas de acesso ao Overpass API ao buscar geometria via `--via`:

| Flag | Descrição | Padrão |
|---|---|---|
| `--retry-tentativas <n>` | Número de tentativas | `3` |
| `--retry-espera <s>` | Espera entre tentativas (segundos) | `3.0` |
| `--retry-timeout <s>` | Timeout por requisição (segundos) | `30` |

```bash
python cercas_v2.py --via BR-116 --modo A --inicio -25.38,-49.19 --fim -25.40,-49.16 \
  --rodovia BR-116 --cidade LUZ --uf MG --velocidade 60 --seq 1 \
  --retry-tentativas 6 --retry-espera 5
```

Útil quando o Overpass público (`lz4.overpass-api.de`) está sobrecarregado
(erros HTTP 429/502/503/504) — aumentar tentativas/espera reduz falhas
transitórias sem alterar o resultado final.

## Cache local de geometrias OSM

Evita buscar no Overpass API novamente a mesma via/bbox já consultada
recentemente. Desabilitado por padrão — sem `--cache`, o comportamento é
idêntico ao anterior (sempre busca na rede).

| Flag | Descrição | Padrão |
|---|---|---|
| `--cache` | Habilita o cache (arquivo local `.osm_cache.json`) | desabilitado |
| `--cache-ttl <s>` | Tempo de vida do cache em segundos | `86400` (24h) |

```bash
python cercas_v2.py --batch lote_modelo.csv --saida saida_lote.csv --cache --cache-ttl 3600
```

Não substitui o fallback `--polilinha` — é apenas uma camada antes da
chamada de rede no fluxo via OSM.

## Histórico persistente entre execuções

Registra as cercas geradas em um banco SQLite, permitindo consultar
execuções anteriores e detectar CÓDIGOs repetidos entre execuções distintas.

| Flag | Descrição | Padrão |
|---|---|---|
| `--historico <caminho.db>` | Banco SQLite onde as cercas geradas são registradas | nenhuma persistência |

```bash
python cercas_v2.py --batch lote_modelo.csv --saida saida_lote.csv --historico cercas.db
```

Se um CÓDIGO já existir no histórico (de uma execução anterior), é emitido
um alerta não bloqueante — a execução **não** é interrompida. Consultas
programáticas por rodovia, UF ou código: `cercas_v2.consultar_historico(caminho_db, filtros)`.

## Bloco A v4 — duplicidade de CÓDIGO contra base central (PostgreSQL)

Independente do histórico local acima. Quando `--pg-dsn` é informado, o
CÓDIGO (combinação rodovia/cidade/UF/velocidade/SEQ) é checado contra uma
base central PostgreSQL — se já existir ativo, a execução é **recusada**
(não apenas alertada), com sugestão automática do próximo SEQ livre.

| Flag | Descrição | Padrão |
|---|---|---|
| `--pg-dsn <dsn>` | String de conexão (formato libpq) da base central | nenhuma checagem central |
| `--substituir` | Declara intenção de reemitir sob o mesmo CÓDIGO/SEQ | desabilitado |
| `--confirmar-substituicao` | Confirma a substituição declarada em `--substituir` | desabilitado |
| `--motivo-substituicao <texto>` | Motivo opcional registrado com a substituição | nenhum |

```bash
python cercas_v2.py --batch lote_modelo.csv --saida saida_lote.csv \
  --pg-dsn "host=meuservidor dbname=cercas user=cercas_app password=***"
```

Para reemitir intencionalmente sob o mesmo CÓDIGO (ex.: correção de uma
cerca já publicada), use `--substituir` **junto com** `--confirmar-substituicao`
— o registro antigo é marcado como `superado` na base central, nunca
apagado. Requer a dependência `psycopg2-binary` (em `requirements.txt`),
carregada apenas quando `--pg-dsn` é usado.

## Bloco B v4 — bloqueio de sobreposição geométrica

Extensão do Módulo 7 (`--relatorio`/sobreposição), aplicável ao modo
`--batch`: quando o novo polígono cobre mais de um limiar de área de um
polígono já gerado na mesma execução, a execução é bloqueada em vez de só
alertada. O limiar padrão (90%) é **provisório**, sem validação empírica —
por isso é ajustável.

| Flag | Descrição | Padrão |
|---|---|---|
| `--limiar-sobreposicao <0-1>` | Percentual de área a partir do qual bloqueia | `0.90` |
| `--override-sobreposicao CODIGO:CODIGO:justificativa` | Libera um par bloqueado (ex.: vias paralelas); repetível | nenhum |

```bash
python cercas_v2.py --batch lote_modelo.csv --saida saida_lote.csv --relatorio relatorio.csv \
  --limiar-sobreposicao 0.85 \
  --override-sobreposicao "PRI - BR-116 - LUZ_MG - 60 KmH - 001:PRI - BR-116 - LUZ_MG - 60 KmH - 002:vias paralelas confirmadas em campo"
```

Sobreposições abaixo do limiar continuam apenas como alerta (comportamento
herdado). "Quem confirmou" o override é capturado automaticamente do
usuário do sistema operacional; a rastreabilidade completa (percentual,
justificativa, confirmado por, quando) fica registrada no `--relatorio`.

## Testes

```bash
pytest test_cercas.py -v
```

## Estrutura do projeto

- `cercas_v2.py` — motor principal (CLI, geometria OSM, buffer/recorte, exportação SASCAR, relatório, histórico)
- `cercas_gui.py` — interface gráfica opcional (tkinter), consumidora de `cercas_v2.py`
- `test_cercas.py` — suíte de testes unitários (pytest)
- `lote_modelo.csv` — exemplo de arquivo de entrada para o modo `--batch`
- `CHANGELOG.md` — histórico de mudanças
