# Evidência 08: revisão da T2, rodada 1

Data: 2026-11-02. Autor: `verion-ai-engineer` (153.781 tokens, 34 chamadas; 315 testes verdes; ruff limpo).
Revisor: `verion-code-reviewer` (79.728 tokens, 18 chamadas, 169 s). Alvo: commit `fb07e1a`.

**Veredito: MUDANÇAS NECESSÁRIAS.** O revisor testou as afirmações com mutações reais em /tmp.

## Bloqueantes

1. **`SpyClient` não embrulha clientes aninhados** (`client.models.generate_videos`), o formato do SDK google-genai.
   Uma mutação que chama o SDK antes de `token.consume` foi aprovada pelo helper. Isso afeta `contract.py:50-64,182-185`.
2. **Adaptador sem `token.job_accepted`, ou com job id divergente, passa no helper** (`contract.py:214-247`).
   Se o processo cair entre submit e liquidação, o job faturado fica sem id no ledger.
3. **`retry_before_accept` repete `ConnectionError` e `TimeoutError`**, que são ambíguos (o POST pode ter sido aceito).
   Foi demonstrada uma submissão dupla. Isso pode furar o teto de gasto (`base.py:112,150-157`).

## Sugestões fortes

4. O helper grava R$ 1.000 de autorização em qualquer `conn`. Ele deve exigir banco descartável.
5. O helper não detecta submissão duplicada, `units` absurdo nem metadados zerados.
6. O helper cobre só o caminho feliz e a recusa de token. As falhas (transient, pós-aceite) ficam para os testes de T8 e T9.
7. Faltam testes negativos para os itens 1, 2 e 5.

## Detalhes

8. A sequência do `VERION_FAKE_MODE` vale por instância. A T3 deve usar um adaptador por run.
9. Os extras (`latency_s`/`sleep` e o conftest de providers) não trazem risco.
10. O erro de mypy em `repo.py:349` persiste e virou tarefa à parte.

## Trava de gasto da T1

O fake e o helper não abrem caminho para liquidar sem token nem contornar o AST. As regras 1 a 6 continuam verdes.
