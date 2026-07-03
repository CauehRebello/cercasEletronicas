# Decisões extras — Implementação Blocos A e B v4 (Claude Code)

> Documento de handoff para a sessão de gestão RASTRO. Lista as decisões de
> implementação tomadas durante a sessão de código que **não estavam
> cobertas explicitamente** pelo `Briefing_Implementacao_BlocoA_BlocoB_v4.md`
> (item 5 das regras da sessão). Números de FAT/DEC **não foram atribuídos**
> aqui — cabe à gestão do projeto decidir se cada item vira um Fato/Decisão
> formal e com qual numeração.
>
> Data da implementação: 2026-07-03. Commit: `5ee8f3c` (`origin/main`).

## 1. `--historico` (SQLite) mantido intacto e independente

O histórico local existente (alerta não bloqueante, por instalação) não foi
alterado nem substituído. O novo mecanismo central PostgreSQL do Bloco A
(`--pg-dsn`) roda em paralelo, como uma checagem adicional — nenhuma flag
ou comportamento antigo foi removido.

## 2. Confirmação de substituição como dois flags, não prompt interativo

O "mecanismo de flag + confirmação" (FAT-182) foi implementado como
`--substituir` + `--confirmar-substituicao` (ambos exigidos), em vez de um
prompt interativo (`input()`). Motivo: o mesmo caminho de código é usado no
modo `--batch`, que roda sem TTY.

## 3. "Próximo SEQ livre" = menor inteiro não usado, preenchendo lacunas

`sugerir_proximo_seq_livre()` retorna o menor SEQ entre 1 e 999 ainda não
utilizado pela combinação rodovia/cidade/UF/velocidade — não
necessariamente "o maior SEQ já usado + 1". Ex.: se os SEQs 1, 2 e 4 já
existem, a sugestão é 3, não 5.

## 4. Justificativa obrigatória só no Bloco B, opcional no Bloco A

`--override-sobreposicao` (Bloco B) exige justificativa não vazia (rejeitada
pelo parser se ausente) — conforme FAT-186. Já `--motivo-substituicao`
(Bloco A) é opcional, pois o briefing só exige "flag + confirmação"
(FAT-182), sem menção a texto obrigatório.

## 5. "Quem confirmou" o override é capturado automaticamente do SO

Em vez de exigir um campo manual de nome/usuário, `--override-sobreposicao`
usa `getpass.getuser()` (usuário do sistema operacional que executa o
comando) para preencher "confirmado_por" na rastreabilidade exigida pelo
FAT-186.

## 6. Bloqueio de sobreposição (Bloco B) só se aplica ao modo `--batch`

Uma execução single-cerca gera apenas o PRI/PRE de um único SEQ por vez —
não há par possível para avaliar sobreposição de área dentro da mesma
execução (a exclusão de "mesmo SEQ" já existente no Módulo 7 sempre se
aplicaria). Por isso `avaliar_bloqueio_sobreposicao()` só é chamada no
fluxo `--batch`.

## 7. Dois novos exit codes dedicados

- `sys.exit(3)`: bloqueio de duplicidade de CÓDIGO na base central (Bloco A).
- `sys.exit(4)`: bloqueio de sobreposição geométrica acima do limiar (Bloco B).

Distintos dos já existentes (`1` = erro de processamento, `2` = falha de
validação do Módulo 6).

## 8. Falha de conexão com o PostgreSQL tratada com mensagem limpa

`psycopg2.OperationalError` (ex.: servidor inacessível) é convertida em
`RuntimeError` com mensagem explicando a falha de conexão, em vez de deixar
vazar um traceback bruto do driver. Não estava especificado no briefing.

## 9. Validação do Bloco A contra PostgreSQL real não foi feita

Não há servidor PostgreSQL disponível no ambiente desta sessão de
implementação. A lógica de negócio (checagem de duplicidade, sugestão de
SEQ, substituição) foi testada com uma conexão/cursor "fake" em memória que
imita a API do psycopg2 (mesmas queries parametrizadas) — cobre 100% da
lógica Python, mas **não** valida contra um servidor Postgres real. Essa
opção foi confirmada explicitamente com o responsável do projeto antes de
prosseguir (ver AskUserQuestion registrado na sessão), em vez de aguardar
acesso a um servidor de desenvolvimento.
