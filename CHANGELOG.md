# Changelog

## [Não lançado]

### Corrigido
- **FAT-67/DEC-5**: `ler_lote()` agora valida que os campos `inicio`/`fim` do CSV de lote estejam no formato `"lat,lon"` entre aspas duplas, levantando `ValueError` com o número da linha quando o arquivo está malformado.
- **FAT-66/DEC-4**: `_linha_sascar()` remove vértices consecutivos idênticos (após arredondamento para 6 casas decimais) na exportação SASCAR, evitando pares repetidos gerados pelo buffer `cap_style=2` que podiam distorcer a importação.
