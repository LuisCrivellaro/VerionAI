# Evidência 05: revisão cruzada da spec da Fase 0 (rodada 2) e aprovação

Data: 2026-11-01. Revisor: `verion-code-reviewer` (50.817 tokens, 6 chamadas de ferramenta, 106 s).

## Resultado da rodada 2

- **Comentários da rodada 1:** dos 12, 11 estão RESOLVIDOS e 1 está PARCIAL (o nº 3: `UnitKind` ainda vinha da T2).
- **Checagem mecânica (script do revisor):** nenhum arquivo compartilhado entre as 12 tarefas e nenhum ciclo de dependências. Ordem topológica: T1, T2, T3, T5, T8, T9, T6, T7, T4, T10, T11, T12.
- **Problemas novos:**
  - **A [bloqueante]:** `UnitKind` estava na T2.
  - **B [bloqueante]:** o teste AST da T1 quebraria quando a T2 entrasse.
  - **C:** faltavam leituras no repo.
  - **D:** o `SpendToken` contrariava a regra `frozen`.
  - **E:** premissas do SDK do Gemini não verificadas.
- **Veredito:** MUDANÇAS NECESSÁRIAS. O revisor declarou que, com A e B corrigidos e C a E registrados, aprovaria sem nova rodada, desde que o supervisor conferisse as alterações.

## Correções aplicadas pelo supervisor

Por serem pontuais, o supervisor aplicou as correções diretamente, sem nova rodada do arquiteto. O registro está ao final de `FASE0_HARNESS.md`.

- **A:** `UnitKind` agora fica em `config.py`, e `base.py` o importa de lá.
- **B:** o teste AST cobre só `src/verion/**`, com allow-list `cli.py` + `providers/contract.py`. A regra de `token.consume` vale só para classes `*Adapter`.
- **C:** foram acrescentados `get_attempt`, `list_runs` e `list_authorizations`.
- **D:** o `SpendToken` virou exceção declarada à regra `frozen`, com `_conn` e `_used`.
- **E:** as capacidades do SDK ficam como hipótese a confirmar na T9.
- **Detalhes:** fotos sintéticas próprias na T12; `examples/fotos/` atribuído à T3.

**Status: spec APROVADA pelo critério do revisor (A e B corrigidos; C a E registrados).**
A conferência foi feita pelo supervisor; não houve terceira chamada ao revisor.
