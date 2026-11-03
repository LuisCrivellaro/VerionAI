# Evidência 14: T12, aceitação ponta a ponta. Harness da Fase 0 concluído (sem chamadas pagas)

Data: 2026-11-02. Agente: `verion-qa` (124.046 tokens, 33 chamadas). Todos os testes usam a CLI real `verion` em subprocesso, com dados em `tmp_path`.

## Dois níveis de teste

- **Padrão:** 13 testes, sempre rodam. Inclui um teste de fumaça com render real: 2 runs de `ken-burns`, aprovação em pty, `assemble` com fallback, `report` e `ledger export`.
- **Completo (`VERION_E2E_FULL=1`):** 13 testes com o fluxo da spec.
  - 12 cenas de `ken-burns`: 23,4 s.
  - 3 cenas de `parallax-2.5d` com `.depth.png` de 16 bits e aprovação humana em pty: 28,2 s.
  - Montagem de 12 clipes com fallback: 37,7 s, gerando 30,50 s de vídeo em formato de entrega.
  - Medido: **26 passed em 122 s** rodando sozinho.

## Critérios verificados

| Critério | Resultado |
|---|---|
| `fake` sem `--allow-paid` | código 3, com 0 runs |
| `fake` com autorização semeada e `billed_error` | código 4; gasto R$ 3,00 e reserva liquidada em `budget show` |
| `authorize` sem TTY | código 2 e 0 linhas |
| Recusas de aprovação (sem TTY, frase errada, `agente`) | código 2 e 0 QC gravado |
| Omni sem `photo_release_ref` | código 2 antes de qualquer reserva |
| `report` | traz os 2 métodos e omite o `fake` |
| `ledger export` com chave sintética no ambiente e caminhos plantados em notas | nenhum caminho nem chave no JSONL |

**Nenhum bug de produção encontrado.**

## Medições do supervisor

- `uv run pytest -o addopts= -q` → **1124 passed, 13 skipped em 107 s**. A meta de 120 s vale para o nível padrão.
- `ruff` limpo.

## Gargalos de desempenho (não são bugs; máquina com 2 núcleos)

| Etapa | Tempo |
|---|---|
| `assemble` de 12 clipes de 3 s | ~38 s |
| `parallax` por cena de 3 s | ~9 s |
| `ken-burns` por cena | ~2 s |

O `ken-burns` amplia cada quadro para 3840x2160 antes do zoom. Otimizar isso fica para quando houver volume real.
