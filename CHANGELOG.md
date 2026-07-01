# Changelog

## [Não lançado]

### Corrigido
- **FAT-68/DEC-6**: `_costura_ways()` agora monta todos os componentes conexos possíveis a partir dos ways retornados pelo Overpass, em vez de parar na primeira cadeia que travar. `buscar_geometria_osm()` escolhe o componente mais próximo de início/fim (limite de 100 m); corrige cercas com extensão 0 m quando a via pesquisada aparece fragmentada em trechos desconexos dentro da bbox de busca (confirmado em teste real no SASCAR).
- **FAT-67/DEC-5**: `ler_lote()` agora valida que os campos `inicio`/`fim` do CSV de lote estejam no formato `"lat,lon"` entre aspas duplas, levantando `ValueError` com o número da linha quando o arquivo está malformado.
- **FAT-66/DEC-4**: `_linha_sascar()` remove vértices consecutivos idênticos (após arredondamento para 6 casas decimais) na exportação SASCAR, evitando pares repetidos gerados pelo buffer `cap_style=2` que podiam distorcer a importação.
