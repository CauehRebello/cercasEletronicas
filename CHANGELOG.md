# Changelog

## [Não lançado]

### Adicionado
- **FAT-119/S7**: retry de rede configurável ao Overpass API — `_overpass_query()` ganhou `max_tentativas`, `espera_base_s` e `timeout_s` (repassados por `buscar_geometria_osm()`, `processar_linha_lote()` e `processar_lote()`); novos flags de CLI `--retry-tentativas` (padrão 3), `--retry-espera` (padrão 3.0) e `--retry-timeout` (padrão 30), aplicáveis tanto no fluxo single-cerca quanto no `--batch`. Sem os novos flags, comportamento idêntico ao anterior.
- **FAT-154/S4**: cache local opcional de geometrias OSM — `buscar_geometria_osm()` ganhou `usar_cache`/`cache_ttl_s`, reaproveitando buscas anteriores para a mesma via/bbox em um arquivo JSON local (`.osm_cache.json`, chave por hash, TTL configurável). Novos flags `--cache` (desabilitado por padrão) e `--cache-ttl` (padrão 86400s/24h). Não substitui o fallback `--polilinha`.
- **FAT-155/S5**: histórico persistente das cercas geradas entre execuções — novas funções `salvar_no_historico()`/`consultar_historico()` (SQLite, tabela `cercas`), acionadas pelo novo flag opcional `--historico <caminho.db>`. Checa duplicidade de CÓDIGO entre execuções e emite alerta não bloqueante (mesmo padrão do Módulo 7/S3), sem bloquear a execução. Consulta suporta filtro por rodovia, UF ou código. Não altera o formato de exportação SASCAR.
- **FAT-153/S1**: interface gráfica opcional (`cercas_gui.py`, novo arquivo, apenas `tkinter` da stdlib) espelhando os campos da CLI, incluindo as novas opções de retry/cache/histórico. Não duplica lógica de negócio — invoca `cercas_v2.main()` diretamente; validado manualmente com paridade byte-a-byte de saída entre GUI e CLI. `cercas_v2.py` e sua CLI permanecem inalterados e independentes da GUI.

### Corrigido
- **FAT-68/DEC-6**: `_costura_ways()` agora monta todos os componentes conexos possíveis a partir dos ways retornados pelo Overpass, em vez de parar na primeira cadeia que travar. `buscar_geometria_osm()` escolhe o componente mais próximo de início/fim (limite de 100 m); corrige cercas com extensão 0 m quando a via pesquisada aparece fragmentada em trechos desconexos dentro da bbox de busca (confirmado em teste real no SASCAR).
- **FAT-67/DEC-5**: `ler_lote()` agora valida que os campos `inicio`/`fim` do CSV de lote estejam no formato `"lat,lon"` entre aspas duplas, levantando `ValueError` com o número da linha quando o arquivo está malformado.
- **FAT-66/DEC-4**: `_linha_sascar()` remove vértices consecutivos idênticos (após arredondamento para 6 casas decimais) na exportação SASCAR, evitando pares repetidos gerados pelo buffer `cap_style=2` que podiam distorcer a importação.
