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

## Processamento paralelo do lote

Processa as linhas do `--batch` concorrentemente (útil quando a maior parte
do tempo é gasta esperando o Overpass API responder). Desabilitado por
padrão — sem `--paralelo`, o processamento é sequencial, idêntico ao
comportamento anterior. A ordem de saída é sempre a do arquivo de lote,
independente da ordem em que as threads terminam.

| Flag | Descrição | Padrão |
|---|---|---|
| `--paralelo` | Habilita o processamento concorrente (`ThreadPoolExecutor`) | desabilitado |
| `--paralelo-workers <n>` | Número máximo de threads | `4` |

```bash
python cercas_v2.py --batch lote_modelo.csv --saida saida_lote.csv --paralelo --paralelo-workers 4
```

Combine com `--cache` para reduzir chamadas de rede duplicadas quando várias
linhas do lote referenciam a mesma via/bbox.

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
