# Evidência 09: T2, rodada 2 (correções do autor e conferência do supervisor)

Data: 2026-11-02. O autor `verion-ai-engineer` foi retomado com o próprio contexto (SendMessage).

## Correções (relato do autor, com TDD)

Os testes novos rodaram primeiro contra o código antigo: **29 falharam**. Os 18 mutantes passaram pelo helper antigo (`DID NOT RAISE`); os outros 11 testes cobrem comportamento novo.

1. **`SpyClient` recursivo.** Todos os níveis compartilham os contadores, e a recusa de token exige `calls == 0` e `accesses == 0`.
   Os mutantes `NestedCallBeforeConsume` e `NestedAccessBeforeConsume` são reprovados.
2. **Em método pago, o `provider_job_id` gravado no ledger precisa existir e ser igual ao do resultado.**
   `NoJobAccepted`, `JobIdMismatch` e `ResultWithoutJobId` são reprovados. Remover `job_accepted` do `fake` também passou a reprovar.
3. **`retry_before_accept` só repete erros comprovadamente anteriores ao envio:**
   - `ProviderError(transient=True, billed_units == 0, provider_job_id is None)`;
   - `ConnectionRefusedError`;
   - `socket.gaierror`.

   `ConnectionError` genérico, `ConnectionResetError`, `BrokenPipeError`, `TimeoutError` e `OSError` sobem sem retry. Seis testes que fixavam o comportamento antigo foram substituídos por testes de regressão.
4. **O helper recusa banco com autorização, run ou tentativa prévia** (banco descartável obrigatório).
5. **Novos limites no helper:**
   - `max_client_calls` (padrão 1);
   - `units_tolerance` (padrão 0,5x a 2x);
   - metadados finitos e maiores que 0.

   `SubmitsTwice`, `UnitsTiny`, `UnitsHuge` e os metadados zerados são reprovados.
7. **Testes negativos** para todos os casos acima.

## Conferência do supervisor (sem nova chamada ao revisor, para economizar tokens)

- `uv run pytest -o addopts= -q` → **344 passed** (2,9 s).
- `ruff check src tests` → limpo.
- Código conferido:
  - `base.py:113-158`: lista `_SAFE_BEFORE_SEND` restrita.
  - `contract.py:236-238`: recusa com zero acessos e zero chamadas.
  - `contract.py:302-311`: teto de chamadas e `provider_job_id` gravado.

**Status: T2 APROVADA** pelo supervisor. Os critérios do revisor (bloqueantes 1–3 e testes do item 7) foram atendidos.

## Pendências registradas para as próximas tarefas

- **T8 a T11:**
  - testar falhas `transient` e pós-aceite (item 6);
  - passar `max_client_calls` exato do próprio mock (polling);
  - o construtor do adaptador não pode ler atributos do client.
- **T3:** usar um adaptador por run (a sequência de `VERION_FAKE_MODE` vale por instância).
- **`mypy`:** o erro em `ledger/repo.py:349` continua aberto.
