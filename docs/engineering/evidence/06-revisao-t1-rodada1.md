# Evidência 06 · Revisão da T1, rodada 1 (2026-11-01)

Revisor: papel `verion-code-reviewer`, rodado por um agente `general-purpose` (os `verion-*` não carregaram nesta sessão).
Alvo: commit `e9c7d5f` (`src/verion/budget.py` e `tests/test_architecture.py`), 173 testes verdes (`uv run pytest`).

**Veredito: MUDANÇAS NECESSÁRIAS** (1 bloqueante).

## Confirmado como correto (experimentos do revisor em /tmp, que já não existem)
- **Ordem do `reserve`:** segue a seção 3.3.
- **Saldo:** é lido dentro do `BEGIN IMMEDIATE`. O rollback cobre qualquer exceção, inclusive `KeyboardInterrupt`.
- **Transação já aberta ou `autocommit=False`:** gera `OperationalError` e nada é gravado (falha fechado).
- **O teste de concorrência é sensível de verdade.** Com variantes fracas trocadas manualmente:

  | Variante | Falhas |
  |---|---|
  | Sem transação | 50/50 |
  | `BEGIN` deferred | 50/50 |
  | `IMMEDIATE` real | 0/50 |

- **Desvios aceitos (registrar em DECISIONS.md):**
  - `_MIN_COST_BRL`;
  - `_BALANCE_EPS = 1e-9`;
  - `WeakSet _ISSUED`;
  - token guarda `prices`;
  - `settle_error` aceita token não consumido;
  - a definição de `add_authorization` em `repo.py` não conta como uso.

## Achados
1. **Bloqueante.** Os campos públicos do `SpendToken` (`attempt_id`, `method`, `reserved_brl`) podem ser alterados.
   - Reprodução: reserva `veo` de R$ 16, depois `t.method = "ken-burns"` e `settle_ok(units=0)`. Resultado: `cost_brl = 0` e o gasto total vira R$ 0.
   - Correção: campos somente leitura; `_require_pending` confere `method`, `attempt_no` e `reserved_brl` contra a linha; o preço sai de `row.method`.
2. **Sugestão forte.** `settle_ok` aceita token nunca consumido.
3. **Sugestão forte.** Em `settle_error`, um `billed_units` do tipo `numpy.int64`, `numpy.float32` ou `Decimal` vira 0. O valor faturado se perde.
4. **Sugestão forte.** A regra 4 do AST não pega `generate` herdado, atribuído (`generate = _gen`) nem decorado.
5. **Sugestão forte.** O AST não pega:
   - `_ISSUED.add`;
   - `._used = False`;
   - SQL bruto com `INSERT INTO authorizations`, `INSERT INTO attempts` ou `UPDATE attempts`.

   Correção: falta também um trigger que impeça alterar `reserved_brl`.
6. **Detalhe.** Em `repo.py:150-157`, o `COMMIT` fica fora do `try`; se falhar, falta o `ROLLBACK`.
7. **Detalhes.**
   - `scene_index=1.5` é aceito;
   - custo não finito não gera erro;
   - `conn` pode ser diferente de `token._conn`;
   - o método do run é ignorado (fica para o harness da T3);
   - `_job_id` não é usado;
   - o teste de diretórios é tautológico.

## Pendências registradas
- **T2:** a regra 4 deve conferir os caminhos de `METHODS` contra as classes verificadas.
- **T3:** uma exceção em `settle_ok` deve cair em `settle_error` com a reserva.
- **Limite:** o uso único do token vale só em memória. Reflexão proposital passa, como a spec já declara.

## Estado da correção
O autor começou as correções 1–7 e foi interrompido. Elas estão no branch `wip/t1-revisao` (commit `17a22f8`), com 9 testes vermelhos.
