# Evidência 10: T3 (roteiro e harness) e T5 (ffmpeg e Ken Burns): implementação, revisão e correções

Data: 2026-11-02. As duas tarefas rodaram em paralelo, sem arquivos em comum.

| Etapa | Agente | Tokens | Resultado |
|---|---|---|---|
| T3 | `verion-backend` | 135.593 | 126 testes novos; 5 mutações no harness, todas detectadas |
| T5 | `verion-media-engineer` | 100.754 | 71 testes; formato de entrega e 5,000 s conferidos por ffprobe |
| Revisão | `verion-code-reviewer` | 116.575 | **APROVADO**, sem bloqueantes |

## O que o revisor verificou

- 19 casos de injeção de falhas em estimate, reserve, generate, settle_ok e settle_error (`RuntimeError`, `KeyboardInterrupt`, `SystemExit`, `MemoryError`).
  - Nenhuma chamada paga ocorreu sem reserva.
  - Nenhuma tentativa `error` ficou com custo abaixo da reserva.
- Zoom medido quadro a quadro com ffmpeg real:

  | Quadro | 1 | 40 | 75 | 110 | 150 |
  |---|---|---|---|---|---|
  | Zoom medido | 1,000 | 1,018 | 1,032 | 1,044 | 1,060 |
  | Zoom esperado | 1,000 | 1,016 | 1,030 | 1,044 | 1,060 |

- O teste de fidelidade é significativo: ele pega desvios de zoom a partir de 1,07.
- Nomes de arquivo com `-`, `;`, `$`, `:` e `[]` foram testados sem injeção de argumentos.
- Timeout: o processo filho é morto e não sobra ffmpeg órfão.

## Correções aplicadas pelo supervisor (TDD: os 3 testes novos falharam antes)

1. **`harness.py`:** `attempted` e `clock()` entraram no `try` depois da reserva.
   - Um relógio que falha não deixa mais `pending` órfã.
   - Teste: `test_clock_failure_right_after_reserve_does_not_leave_pending`.
2. **`harness.py`:** toda exceção sai com `verion_run_id` e com a nota `run_id=N`.
   - A CLI (T4) consegue reportar e liquidar o run.
   - Teste: `test_exception_carries_run_id`.
3. **`harness.py`:** a nota "use `verion ledger settle`" só aparece se a tentativa ainda estiver `pending`.
4. **`local/ffmpeg.py`:** `stdin=subprocess.DEVNULL`, porque o ffmpeg interativo consumia teclas do terminal.
   - Teste: `test_run_never_inherits_stdin`.

**Suíte:** `uv run pytest -o addopts= -q` → **544 passed** (40 s). `ruff` limpo.

## Pendências para as próximas tarefas

- **T4:**
  - ignorar runs com 0 tentativas no relatório;
  - usar `exc.verion_run_id`;
  - mapear exceções para exit codes (ProviderError = 4, SpendBlocked = 3).
- **T6 e T7:**
  - usar `ffmpeg.run` com caminhos absolutos;
  - acrescentar um teste de foto com EXIF orientação 6 e foto em retrato (ver ADR-007);
  - nomes no padrão `%d` falham por design do image2.
- **Formatação:** o `ruff format` não é imposto (sem `[tool.ruff]`); decisão adiada.
