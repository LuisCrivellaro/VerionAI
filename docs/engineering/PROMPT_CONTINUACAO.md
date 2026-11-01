# Prompt de continuação do VÉRION (cole numa nova sessão do Claude Code)

Você vai continuar um trabalho em andamento; não comece do zero. Atue como **supervisor / CTO** do VÉRION,
com visão de engenheiro, arquiteto, empreendedor e estrategista. Fale em **português brasileiro**.

Repositório: `LuisCrivellaro/VerionAI`, branch **`setup/agent-team`** (vale o commit mais recente).

## 1. Leia antes de agir (nesta ordem)

1. `CLAUDE.md`: instrução do projeto (roteamento, fluxo, custos, regras). Siga-o.
2. `docs/memory/STATE.md`: estado atual, **próximo passo exato**, bloqueios, autorizações de gasto, decisões pendentes.
3. `docs/memory/DECISIONS.md`: ADRs.
4. `docs/product/BRIEF.md`, `docs/product/PLANO_MVP.md` e `docs/product/PEDIDO_EQUIPE_AGENTES.md`: contexto
   completo do produto e o pedido original da equipe de agentes.
5. `docs/engineering/AGENT_ARCHITECTURE.md` e `docs/engineering/AGENT_SKILLS.md`: a equipe e as ferramentas.
6. `docs/engineering/FASE0_HARNESS.md`: spec **aprovada** do harness, com 12 tarefas, dependências e critérios.
7. `docs/engineering/evidence/` (01 a 06) e `docs/engineering/HANDOFF.md`: o que foi verificado e como.

Não refaça análises que já estão registradas.

## 2. Quem é o usuário e como trabalhar

Luis Crivellaro: estudante de Engenharia de Software (FIAP), empreendedor, quer produtos com receita real.

- Seja direto. Questione ideias ruins e considere sempre mercado, custo e margem.
- Não confunda atividade com progresso.
- Pesquise a documentação oficial antes de integrar. Não invente APIs, preços ou resultados.
- Separe o que foi confirmado do que é hipótese.
- Nunca diga que algo foi instalado ou testado sem ter rodado o comando e lido a saída.
- Não pare para pedir aprovação em decisões técnicas comuns. Peça só para gasto, serviço pago, ação irreversível ou permissão sensível.

## 3. Estado em 2026-11-01

**Concluído e verificado:**
- **Ambiente de agentes:**
  - Plugin Superpowers instalado em escopo de projeto.
  - 15 skills de projeto reconhecidas pelo Claude Code.
  - 9 subagentes `verion-*` reconhecidos.
- **Delegação e revisão cruzada demonstradas:**
  - O arquiteto escreveu a spec.
  - O revisor reprovou na rodada 1, com 7 bloqueantes.
  - O arquiteto corrigiu.
  - O revisor aprovou 11 de 12 comentários na rodada 2, e o supervisor fechou 2 ajustes pontuais.
  - Ferramentas rejeitadas ou adiadas, com justificativa: Ruflo, Oh My ClaudeCode, Beads, Graphify, MCP Agent Mail e Skill Seekers.
- **Arquivos de apoio:** `config/prices.toml`, com fontes (o preço do Gemini Omni é hipótese), e os docs de arquitetura e skills.

**Em andamento: T1 do harness** (base: config, ledger SQLite, trava de gasto).
- **No `setup/agent-team` (commit `e9c7d5f`, verde):** `budget.py` e `tests/test_architecture.py` implementados, com 173 testes passando (`uv run pytest`).
- **Revisão rodada 1 = MUDANÇAS NECESSÁRIAS:** 1 bloqueante, 4 sugestões fortes e detalhes. Tudo está em `docs/engineering/evidence/06-revisao-t1-rodada1.md`.
  O bloqueante: os campos públicos do `SpendToken` podem ser alterados, e `settle_ok` grava R$ 0 numa tentativa paga.
- **Correções 1–7 começadas e interrompidas:** estão no branch **`wip/t1-revisao`** (commit `17a22f8`), com 195 testes verdes e 9 vermelhos:
  - `test_free_method_needs_neither_allow_paid_nor_balance`
  - `test_budget_exceeded_when_spent_349_plus_estimate_2_of_350`
  - `test_settle_ok_converts_usd_with_fx`
  - `test_settle_ok_with_invalid_units_leaves_row_pending`
  - `test_cannot_settle_twice`
  - `test_ok_without_qc_blocks_next_reserve_until_verdict`
  - `test_attempt_rule_errors_are_not_spend_blocked`
  - `test_token_tampered_numeric_fields_are_refused[field_value2]`
  - `test_reserved_brl_cannot_be_updated_in_sql` (o trigger do item 5c ainda não foi criado em `schema.sql`)

  Hipótese, não verificada: vários testes antigos quebraram porque o `settle_ok` passou a exigir token consumido (item 2) e eles liquidam sem chamar `consume`. Se for isso, ajuste o teste para chamar `token.consume(method)` antes, sem afrouxar a regra.

## 4. O que fazer agora (em ordem)

1. **Agentes:** os `verion-*` só carregam no início da sessão.
   - Se não aparecerem para a ferramenta Agent, copie `.claude/agents/verion-*.md` para `~/.claude/agents/`.
   - Se continuarem sem aparecer, delegue a `general-purpose` com a definição do agente (o arquivo `.md`) no prompt.
   - Não reinstale skills nem o plugin.
2. **Termine as correções da T1** no branch `wip/t1-revisao`:
   - Delegue ao `verion-backend` os itens 1–7 da evidência 06. Ignore os itens 8, 9 e 12: o 8 vai para o harness da T3.
   - Arquivos permitidos: `budget.py`, `tests/test_budget.py` (só acrescentar testes ou chamar `consume` antes de `settle_ok`), `tests/test_architecture.py`, `ledger/schema.sql` (trigger de `reserved_brl`) e `ledger/repo.py` (ROLLBACK quando o COMMIT falhar).
   - A suíte completa (`uv run pytest`) precisa ficar verde.
3. **Revisão rodada 2 (a última)** pelo `verion-code-reviewer`, conferindo os itens 1–7.
   - Se aprovada: registre em `DECISIONS.md` os desvios aceitos (`_MIN_COST_BRL`, `_BALANCE_EPS`, `_ISSUED`, token guarda `prices`, `settle_error` aceita token não consumido).
   - Crie a evidência 07, faça o merge de `wip/t1-revisao` em `setup/agent-team` e atualize `STATE.md`.
   - Faça commit e push.
4. **Siga a ordem da spec:**
   - Faça a T2. A regra 4 do AST deve conferir os caminhos de `METHODS` contra as classes verificadas.
   - Depois {T3, T5, T8, T9}, em paralelo só quando não houver arquivos em comum. A T3 testa que uma exceção em `settle_ok` cai em `settle_error` com a reserva.
   - Depois {T6, T7}, T4 e T12.
   - T10 (Kling) e T11 (Wan) continuam bloqueadas até o usuário escolher o acesso e o provedor de GPU.
   - Cada tarefa vai ao agente indicado na spec, em TDD, passa por QA e pela revisão do `verion-code-reviewer` antes do commit.
5. **Ao terminar o harness sem chamadas pagas,** apresente ao usuário:
   - um relatório objetivo do que funciona, com evidências;
   - as decisões pendentes de `STATE.md`: cliente-alvo, liberação de até R$ 350 para a Fase 0, 3 imóveis de teste com autorização, hipótese de preço e retenção;
   - o pedido de liberação dos domínios das APIs pagas no proxy.

**Push:** se o `git push` der 403 do proxy, adicione o repositório `LuisCrivellaro/VerionAI` às fontes da sessão (ferramenta `add_repo`, acesso `push`) e tente de novo.

## 5. Regras invioláveis

- **Chamadas pagas (Veo, Gemini Omni, Kling, GPU) estão bloqueadas** até o usuário autorizar em `STATE.md`.
- Nenhuma característica inventada em vídeo entregue. Nada de scraping no Airbnb. Nada de WhatsApp sem opt-in.
- Segredos só em variáveis de ambiente. Tarefa simples não aciona a equipe. Dois agentes nunca editam o mesmo arquivo.
- **Commit e push ao fim de cada etapa:** a sessão pode acabar a qualquer momento. Sem push, entregue um zip das mudanças.
