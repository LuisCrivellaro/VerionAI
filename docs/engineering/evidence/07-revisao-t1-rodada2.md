# Evidência 07: revisão da T1, rodada 2, e aprovação

Data: 2026-11-02. Revisor: `verion-code-reviewer` nativo (63.596 tokens, 13 chamadas de ferramenta, 125 s).
Alvo: commit `9f66466`, com as correções 1–7 da evidência 06 feitas pelo `verion-backend` (114.628 tokens, 31 chamadas).

## Resultado

- **Achados da rodada 1:** 1 a 6 RESOLVIDOS; o 7 ficou PARCIAL, só nos itens que dependem da T3.
- **Tentativas de burla da trava de gasto (experimentos reais do revisor).** Todas falharam e o gasto ficou intacto:

  | Tentativa | Resultado | Gasto |
  |---|---|---|
  | Atribuir atributo direto | `AttributeError` | R$ 16 |
  | `vars()` | `TokenInvalid` | R$ 16 |
  | `object.__setattr__` | `TokenInvalid` | R$ 16 |
  | Trocar `_conn` ou `_prices` | `TokenInvalid` | R$ 16 |
  | SQL `UPDATE` em `reserved_brl`, `method` ou `cost_brl` | `IntegrityError` (trigger) | R$ 16 |
  | Token apontado para outra cena | `TokenInvalid` | R$ 32 (as duas reservas intactas) |
  | `copy.copy` | `TokenInvalid` | R$ 16 |

- **Testes alterados pelo autor:** nenhum enfraquecido.
- **Suíte:** 215 passed.
- **Veredito: APROVADO**, com ressalvas.

## Ressalvas corrigidas pelo supervisor (commit `d7509e8`)

1. **[strong] Subclasse com `_require_issued` vazio e tabela "grátis" liquidava com custo 0.**
   - **Correção:** `settle_*` passam a usar `_verified_grant`, que exige `type(token) is SpendToken`, chama as checagens pela classe e lê preços, conexão e id do `_Grant` imutável, nunca do token.
   - **Testes novos:** `test_subclass_token_with_noop_checks_cannot_settle_for_free` e `test_instance_level_method_override_does_not_bypass_settle`. **Ambos falham na versão anterior** (2 failed) e passam na nova.
2. **[strong] `repo.finish_attempt` chamado direto zerava o custo.**
   - **Correção:** regra 6 do AST: `finish_attempt` só em `budget.py` e `ledger/repo.py`, com teste próprio.
3. **Lint:** os 6 avisos do ruff foram corrigidos. `ruff check src tests` passa limpo.

**Suíte final:** `uv run pytest -o addopts= -q` → **219 passed** (2,4 s), verificado pelo supervisor após o merge em `setup/agent-team`.

## Limites aceitos (a spec já os declara)

- Reflexão em `_used`, SQL bruto `UPDATE status='ok'` e `DROP TRIGGER` passam: quem grava direto no SQLite ou usa reflexão contorna a trava.
- A regra 5 do AST é heurística: SQL montado por f-string ou concatenação não é detectado.
- As barreiras reais seguem sendo a chave de API, que só o usuário põe no ambiente, e a autorização de gasto.

## Pendências para as próximas tarefas

- **T2:** a regra 4 deve conferir os caminhos de `METHODS` contra as classes verificadas.
- **T3:** exceção em `settle_ok` cai em `settle_error` com a reserva; tratar o método do run.
- **`mypy src`:** 1 erro anterior em `repo.py:349` (`list_attempts`); corrigir na próxima tarefa do backend.
