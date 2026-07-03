# Decisões extras — Validação do Bloco A contra PostgreSQL real (FAT-214)

> Handoff para a gestão RASTRO, referente ao `Briefing_ValidacaoBlocoA_PostgreSQLReal_v4.md`
> (item 5, nota técnica sem formalização de FAT/DEC por esta sessão). Números
> de FAT/DEC não foram atribuídos aqui — a gestão decide se algum item vira
> Decisão Extra formal (seguindo o padrão de FAT-206 a FAT-213).
>
> Data: 2026-07-03.

## 1. Driver mantido: `psycopg2` (não `psycopg3`)

Não houve motivo técnico para trocar de driver — `psycopg2-binary` já estava
instalado e testado (sessão anterior) e a conexão real funcionou sem
ajustes de driver. Migrar para `psycopg3` teria sido uma mudança de escopo
não pedida.

## 2. Modo fake movido de `test_cercas.py` para `cercas_v2.py` (produção)

Por pedido explícito do usuário (contrariando minha recomendação inicial de
mantê-lo isolado nos testes): `_FakeCentralConn`/`_FakeCentralCursor` agora
vivem em `cercas_v2.py` e são acionáveis em produção via novo flag
`--pg-fake` (mutuamente exclusivo com `--pg-dsn`). Um aviso explícito é
impresso sempre que usado, deixando claro que não é uma base central real
(não persiste, não é compartilhado entre processos) — mitigação para o
risco de alguém usar `--pg-fake` pensando que está com checagem central de
verdade.

## 3. Senha via `.env`, nunca em `--pg-dsn`

`_pg_conectar()` agora completa a DSN com `password=<CERCAS_DB_PASSWORD>`
quando a env var está definida e a DSN não já tiver senha — evita senha
visível em `ps`/histórico de shell. Não foram criados flags separados para
host/porta/banco/usuário; `--pg-dsn` continua sendo a única forma de passar
esses parâmetros (formato `chave=valor` do libpq já é suficiente).

## 4. Achado real durante a validação: permissão insuficiente no PostgreSQL de teste

Ao rodar a suíte de integração (`test_cercas_pg_integration.py`) contra o
PostgreSQL local fornecido pelo usuário (`localhost:5432`, banco
`cercas_v4`, usuário `cercas_app`), a primeira execução falhou com:

```
psycopg2.errors.InsufficientPrivilege: ERRO: permissão negada para esquema public
```

**Causa:** desde o PostgreSQL 15, o schema `public` não concede mais
`CREATE` a `PUBLIC` por padrão; `cercas_app` não é superusuário nem dono do
banco (`cercas_v4` pertence a `postgres`), então não conseguia criar a
tabela `cercas_central`. **Não é um bug do Bloco A nem da suíte de testes**
— é uma questão de provisionamento do banco. Corrigido pelo usuário (via
credencial de superusuário) com:
```sql
GRANT CREATE ON SCHEMA public TO cercas_app;
```
Após o `GRANT`, a suíte completa passou (73/73, ver relatório da sessão).

**Recomendação para a topologia central real (fora do escopo desta tarefa,
nota apenas):** quando o Bloco A migrar da instância local de teste para a
base central real (Windows Server, FAT-198/199/200), confirmar que o role
de aplicação usado pela CLI (equivalente a `cercas_app`) recebe esse mesmo
`GRANT CREATE ON SCHEMA public` (ou um schema dedicado) durante o
provisionamento — caso contrário, o mesmo erro vai se repetir em produção.

## 5. Um segundo achado técnico não relacionado à lógica de negócio

Uma tentativa inicial de rodar os testes com a DSN de teste sem aspas em
`.env` fez o `bash source` truncar a DSN (só `host=localhost` chegava à
aplicação, perdendo porta/banco/usuário), o que por sua vez fez o
`psycopg2.connect()` cair em algum caminho interno que lançava
`UnicodeDecodeError`. Corrigido colocando a DSN entre aspas no `.env` — o
erro de Unicode não voltou a aparecer depois disso. Não foi necessária
nenhuma mudança de código por causa disso; é um cuidado de uso do
`CERCAS_TEST_PG_DSN` (documentado no `.env.example`), não uma falha do
Bloco A.
