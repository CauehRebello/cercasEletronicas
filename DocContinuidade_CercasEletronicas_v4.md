**DOCUMENTO DE CONTINUIDADE**

*Projeto Cercas Eletrônicas — v3 → v4*

Transportadora Transleone Ltda

| **Versão** | 1.0 |
| --- | --- |
| **Data de emissão** | 03 de julho de 2026 |
| **Empresa** | Transportadora Transleone Ltda |
| **Aplicável a** | v4 do Sistema de Cercas Eletrônicas |
| **Responsável** | Caueh Emilio Rebello |

*Método RASTRO — Projeto Cercas Eletrônicas*

# **1. Propósito e Escopo**

**Este documento tem dupla função:**

- **Guia técnico:** orienta o desenvolvedor da v4 sobre quais decisões da v3 são invioláveis, quais podem ser revisadas e quais são meramente orientativas — e formaliza as decisões de escopo tomadas na sessão de brainstorming que originou este projeto.

- **Termo de compromisso:** declara formalmente, em caráter assinável, que a v4 preservará os invariantes listados na Seção 2 e seguirá as diretrizes de escopo da Seção 3.

**Público-alvo:**

- Responsável técnico pelo desenvolvimento da v4.

- Gestor de projeto que autoriza e assina o compromisso.

- Auditor ou revisor que verificar aderência da v4 à arquitetura original.

**Relação com a v3:** este documento nasce como **Produzido** no Corpus de Fontes da v3 (registro da sessão de brainstorming pós-fechamento do MVP). No projeto da v4, seu papel muda para **Normativo** — deve ser registrado no Corpus da v4 com Exigido = Sim antes do início do desenvolvimento.

**Origem:** as decisões descritas na Seção 3 nasceram de uma sessão de brainstorming (não de execução) entre os responsáveis do projeto, realizada imediatamente após o fechamento do MVP da v3 (Sessão 2, Fase "5 - Fechamento"). Não há ainda FAT-IDs formais da v4 — a numeração DEC-V4-01 a DEC-V4-10 usada abaixo é provisória e deve ser substituída pela numeração real do Registro de Fatos assim que o projeto v4 for criado no hub Projetos.

**⚠ Método RASTRO**

Toda decisão nova na v4 deve ser registrada como DEC no Registro de Fatos do projeto v4.

Qualquer alteração a um invariante 🔒 exige registro de FAT + DEC que supersede a entrada original.

Sessões da v4 seguem o mesmo protocolo C1 / C3 da v3.

# **2. Inventário de Decisões Herdadas da v3**

**Graus de vínculo:**

| **🔒 Inviolável** | Estrutural. Exige FAT + DEC de supersedência para mudar. |
| --- | --- |
| **⚙️ Parametrizável** | Pode ser alterado na v4 mediante nova DEC registrada, com justificativa e aprovação. |
| **📋 Orientativo** | Recomendado, mas não vinculante. |

**Todos os invariantes 🔒 da v1.4/v2/v3 permanecem em vigor sem alteração** — o núcleo de geometria, buffer, recorte e exportação SASCAR (Módulos 2–6 do `cercas_v2.py`) não é tocado por nenhuma das decisões desta atualização. Referência completa: `DocContinuidade_CercasEletronicas_v3.md`, Seção 2 (já registrado como Normativo no Corpus de Fontes da v3, referenciável pela v4). Em especial, seguem invioláveis sem qualquer ressalva adicional:

- Layout SASCAR — tipo POL, três tipos de registro CIR/AVD/POL (FAT-18/19/20/21, v1.4).
- Arquitetura PRI/PRE — polígono único de (início−X) a (fim+Y), POS eliminado (FAT-29/30, DEC-14/15, v1.4).
- Proibição de '/' em CÓDIGO/DESCRIÇÃO; CIDADE_UF com underscore; velocidade em KmH (FAT-35, DEC-19, v1.4).
- Buffers PRI (50 m/lado) e PRE (PRI + 5 m/lado) como padrão parametrizável, não estrutural (FAT-14/36).

**Novas / qualificadas nesta atualização (v3 → v4), originadas do brainstorming pós-fechamento do MVP:**

| **DEC** | **Grau** | **Decisão** | **Condição para alterar na v5+** |
| --- | --- | --- | --- |
| DEC-V4-01 | ⚙️ Parametrizável | Ao detectar CÓDIGO já existente no histórico (S5), o sistema recusa o SEQ pedido e sugere automaticamente o próximo SEQ livre para a mesma combinação rodovia/cidade/UF/velocidade. | Nova DEC se a v5 adotar outra política (ex.: bloqueio rígido sem sugestão). |
| DEC-V4-02 | ⚙️ Parametrizável | Existe um mecanismo explícito de substituição intencional (flag + confirmação) para reemitir uma cerca sob o mesmo CÓDIGO quando a anterior estiver incorreta. A cerca antiga é marcada como superada no histórico, nunca apagada (princípio C2 aplicado ao S5). | Nova DEC se a v5 mudar o mecanismo de confirmação ou o tratamento do registro superado. |
| DEC-V4-03 | ⚙️ Parametrizável (dependente de DEC-V4-10) | A checagem de duplicidade de CÓDIGO roda contra uma base histórica **central**, não mais local por instalação. | Nova DEC se a v5 voltar a checagem local ou mudar a topologia da base. |
| DEC-V4-04 | ⚙️ Parametrizável — **valor provisório, sujeito a validação empírica** | Sobreposição geométrica entre polígonos de SEQ diferentes é tratada como anomalia real quando o novo polígono sobrepõe mais de 80% da área de um polígono pré-existente. | Ajuste do limiar mediante nova DEC, após validação com casos reais de vias paralelas (ver Seção 4). |
| DEC-V4-05 | ⚙️ Parametrizável | O bloqueio de sobreposição é conservador na primeira versão: bloqueia apenas os casos acima do limiar da DEC-V4-04; os demais permanecem apenas como alerta (comportamento herdado do Módulo 7 da v2/v3, FAT-78/145). | Nova DEC para expandir o critério de bloqueio. |
| DEC-V4-06 | ⚙️ Parametrizável | Existe um mecanismo de override manual com justificativa obrigatória para os casos em que o critério da DEC-V4-04 classificar incorretamente uma sobreposição legítima (ex.: vias paralelas, cf. Premissa P7 do Módulo 7) como anomalia. | Nova DEC se a v5 mudar o mecanismo de override ou seu registro de auditoria. |
| DEC-V4-07 | ⚙️ Parametrizável | A primeira fase da aplicação web é a Opção A: API REST fina sobre o núcleo atual (Módulos 2–8 intocados), com front-end simples por cima. Opção B (banco compartilhado) fica para uma etapa seguinte; Opção C (produto multiusuário completo, com autenticação/perfis) fica fora do escopo desta v4. | Nova DEC caso se decida avançar para Opção B ou C em versão futura. |
| DEC-V4-08 | 🔒 Inviolável (reconfirmação) | O sistema segue para uso interno da Transportadora Transleone, sem comercialização nem distribuição a terceiros — reconfirmação formal de FAT-100/DEC-7 (v2/v3) no novo contexto de aplicação web. | Reconfirmação obrigatória caso a política de comercialização/distribuição mude no futuro, com nova análise de exposição de segurança antes de prosseguir. |
| DEC-V4-09 | 🔒 Inviolável (condição de bloqueio de fase) | A S8 (verificação automatizada de cobertura de testes) é tratada como pré-requisito bloqueante: o Bloco C (aplicação web, DEC-V4-07) não avança para exposição da API sem a S8 implementada e validada. | Só pode ser revista por nova DEC explícita, com justificativa de risco aceita pelo responsável de projeto. |
| DEC-V4-10 | 🔒 Inviolável (ligado a DEC-V4-08) | A base histórica central (DEC-V4-03) é um banco compartilhado hospedado em servidor da própria Transportadora Transleone, administrado pela empresa — sem uso de infraestrutura de nuvem de terceiros. | Reconfirmação obrigatória junto com DEC-V4-08 caso a política de hospedagem mude. |

# **3. Termo de Compromisso Formal**

Pelo presente termo, o responsável pelo desenvolvimento da v4 do Sistema de Cercas Eletrônicas declara que:

**I.** preservará todos os invariantes 🔒 herdados da v1.4/v2/v3 (Seção 2) sem exceção, e os novos invariantes 🔒 desta atualização (DEC-V4-08, DEC-V4-09, DEC-V4-10), salvo alteração documentada com FAT + DEC de supersedência registrados no Método RASTRO;

**II.** registrará toda decisão nova como DEC no Registro de Fatos do projeto v4, com justificativa e referência à DEC provisória correspondente desta lista (DEC-V4-01 a 10);

**III.** aplicará o protocolo de abertura e fechamento de sessão (C1 / C3) do Método RASTRO em todas as sessões de desenvolvimento da v4;

**IV.** não implementará o Bloco C (aplicação web, DEC-V4-07) antes de resolver a S8 (DEC-V4-09) — ordem de dependência tratada como bloqueante, não apenas recomendada;

**V.** tratará o limiar de 80% (DEC-V4-04) como valor provisório até validação com dados reais de sobreposição entre vias paralelas, registrando o resultado dessa validação como novo FAT antes de considerar o valor definitivo;

**VI.** comunicará à Transportadora Transleone qualquer lacuna que venha a bloquear o desenvolvimento da v4, conforme Seção 4 deste documento;

**VII.** tratará qualquer alegação de ferramenta externa (ex. Claude Code) sobre Fatos, FAT-IDs ou métricas do projeto como não verificada até cotejamento direto com a fonte real, mantendo a prática já estabelecida na v3 (FAT-96/97).

**📌 Validade**

Este termo é válido pelo tempo de vida do projeto v4.

Em caso de fusão, aquisição ou mudança de responsável técnico, o novo responsável deve assinar uma aditiva.

**Assinaturas:**

| **Responsável pelo Desenvolvimento da v4** ___________________________________ Nome: _________________________ Data: _____ / _____ / __________ | **Responsável de Projeto — Transleone** ___________________________________ Nome: _________________________ Data: _____ / _____ / __________ |
| --- | --- |

# **4. Lacunas Identificadas**

| **#** | **Premissa** | **Descrição** | **Status** | **Ação requerida na v4** |
| --- | --- | --- | --- | --- |
| P1 (v4) | Limiar de sobreposição [DEC-V4-04] | O valor de 80% foi definido sem validação empírica prévia; não se sabe ainda a distribuição real de sobreposição entre vias paralelas legítimas na base OSM usada pela Transleone. | Aberta — não bloqueante para início do desenvolvimento | Validar o limiar com casos reais assim que houver volume suficiente de execuções pós-implementação do Módulo 7 revisado; registrar novo FAT com o resultado. |
| P2 (v4) | Especificação técnica do servidor [DEC-V4-10] | Decidido que a base central roda em servidor da própria Transleone, mas ainda não há especificação técnica (SO, capacidade, responsável de infraestrutura, política de backup). | Aberta — bloqueante para Bloco A/C em produção | Levantar especificação técnica do servidor junto à Transleone antes de iniciar a implementação da base compartilhada (DEC-V4-03). |
| P3 (v4) | Escolha do motor de banco [DEC-V4-03/10] | "Base compartilhada" foi decidido, mas o motor (SQLite em servidor único vs. Postgres para concorrência) ainda não foi definido. | Aberta — não bloqueante para início (Bloco A/B podem avançar sem isso) | Definir motor de banco antes de iniciar a migração do S5 local para a base central. |
| P4 (v4) | Fragmentação de dados OSM em pontos de cerca [DEC-V4-34] | Cotejamento real (2026-09-29) com SP-148/BR-158 (mesmas coordenadas, Jurubatuba/SP) mostrou que a via está mapeada no OSM como dois grupos de ways fisicamente desconectados nesse trecho (~120 m de gap). Nem o Módulo 2 clássico nem a validação de rota OSRM (DEC-V4-34) conseguem costurar automaticamente — ambos caem, corretamente, no fallback herdado sem candidata de via alternativa (comportamento seguro por design, não é falha do código). O caminho de sucesso do DEC-V4-34 (aceitação automática com `--via` divergente do OSM) foi validado com sucesso no mesmo cotejamento usando BR-101/Ubaitaba-BA (via física bem conectada). | Aberta — não bloqueante (recusa segura já é o comportamento correto nesses casos) | Para trechos com gap de conectividade OSM conhecido, seguir usando a coluna `polilinha` manual (fallback já existente); considerar reportar a lacuna de mapeamento à comunidade OSM se o caso se repetir com frequência. |

# **5. Orientações Técnicas para o Desenvolvedor da v4**

## **5.1 Estrutura de blocos e dependências**

- **Bloco A — Bloqueio de duplicidade de CÓDIGO** (DEC-V4-01, 02, 03): pode começar imediatamente sobre o S5 existente; checagem local é aceitável como primeira iteração, migração para base central (DEC-V4-03) depende de P2/P3 acima.
- **Bloco B — Bloqueio de sobreposição geométrica** (DEC-V4-04, 05, 06): independente do Bloco A, pode avançar em paralelo. Extensão direta do Módulo 7 existente (`detectar_sobreposicao`), sem alterar geometria/buffer.
- **Bloco C — Aplicação Web** (DEC-V4-07, 08, 09, 10): só inicia a exposição da API depois de:
  1. Pelo menos DEC-V4-01/02 (Bloco A) estarem implementadas — a API já deve nascer sabendo a política de duplicidade;
  2. S8 (DEC-V4-09) implementada e validada.

## **5.2 O que pode ser estendido livremente**

- Ajustes de UX na sugestão automática de SEQ (DEC-V4-01) — forma de exibir a sugestão ao operador, desde que a regra de negócio (próximo SEQ livre) não mude.
- Formato do relatório de override (DEC-V4-06) — desde que preserve rastreabilidade (quem confirmou, quando, justificativa).

## **5.3 O que exige nova DEC antes de implementar**

- **Qualquer mudança no limiar de 80%** (DEC-V4-04) sem passar pela validação empírica da Seção 4, P1.
- **Qualquer avanço para Opção B ou C** da aplicação web (DEC-V4-07) sem nova DEC explícita.
- **Qualquer exposição da API antes da S8 estar pronta** (DEC-V4-09) — violação direta do Termo de Compromisso, cláusula IV.
- **Qualquer mudança na política de hospedagem** (DEC-V4-10) para fora da infraestrutura própria da Transleone.

## **5.4 Obrigações do Método RASTRO na v4**

Idênticas às da v3 (C1/C2/C3, R1–R7) — ver Seção 5.3 do `DocContinuidade_CercasEletronicas_v3.md` para o texto completo, aplicável sem alteração.

**ℹ Herança do Registro de Fatos**

Os FAT-IDs da v1.4, v2 e v3 (1 a 180) permanecem válidos como referência.

A v4 inicia seu próprio Registro de Fatos a partir do próximo número disponível.

*Documento produzido sob o Método RASTRO — todas as afirmações factuais citam um FAT-ID verificado no Registro de Fatos do projeto de origem (v3), até que a v4 tenha seu próprio Registro.*

*Cercas Eletrônicas — Transportadora Transleone Ltda | Documento de Continuidade v4 | v1.0*
