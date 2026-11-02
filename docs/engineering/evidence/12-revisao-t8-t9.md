# Evidência 12: T8 (Veo) e T9 (Gemini Omni), os primeiros adaptadores pagos (só mocks e SDK real offline)

Data: 2026-11-02. As duas tarefas rodaram em paralelo, sem arquivos em comum. O supervisor instalou o google-genai 2.34.0 antes da delegação.

| Etapa | Agente | Tokens | Resultado |
|---|---|---|---|
| T8 | `verion-ai-engineer` | 139.360 | 51 testes, 5 mutações mortas |
| T9 | `verion-ai-engineer` | 187.599 | 91 testes, 5 mutações mortas |
| Revisão | `verion-code-reviewer` | 135.263 | **MUDANÇAS NECESSÁRIAS** (2 bloqueantes no Omni) |

## Achado de produto (T8, documentação oficial do Google, lida em 2026-11-02)

O `veo-3.1-fast-generate-preview` consta como **desligado em 22/10/2026**, e o substituto indicado é o `gemini-omni-1.1-flash`. Ver ADR-009.

## Bloqueantes encontrados pelo revisor (só visíveis com o SDK real sobre `httpx.MockTransport`)

1. **O SDK reenviava sozinho o POST pago do `interactions.create`.** Eram 4 POSTs em 408, 429, 500, 502, 503 e 504, por causa do `RetryConfig(max_retries=4)` interno. Um 504 depois de uma geração longa poderia faturar várias vezes.
2. **O ramo `except genai_errors.APIError` do Omni era código morto.** O `create` real levanta `compat_errors.*`, que não herdam dessa classe. Os testes passavam porque simulavam os erros com a classe errada.

## Correções (rodada 2, autores retomados com o próprio contexto)

- **Retry interno desligado:** os dois adaptadores usam `HttpRetryOptions(attempts=1, http_status_codes=[599])`.
- **Contagem de POSTs medida com o SDK real:**

  | Erro | Antes | Depois | Por quê |
  |---|---|---|---|
  | 408, 500, 502, 504, timeout de leitura | 4 | **1** | Podem ter sido aceitos: sem reenvio, faturado e não transient |
  | 429, 503, falha de conexão | 4 | até 3 | O provedor recusou e não criou job; só o `retry_before_accept` do próprio adaptador reenvia (regra da T2) |
  | 4xx (400, 401, 403, 404) | 1 | 1 | Recusado, sem custo |

- **Classificação dos erros do Omni:** passou a ser por duck typing (`status_code`, `__cause__`). Os testes de erro agora usam o client real.
- **Continuação automática:** `automatic_continuation=False`; `requires_continuation` é tratado como falha pós-aceite.
- **Client real:**
  - Fixado em `genai.Client(vertexai=False, api_key=GEMINI_API_KEY)`.
  - `GOOGLE_GENAI_USE_VERTEXAI` e `GOOGLE_API_KEY` são ignorados (testado com o ambiente manipulado).
- **Veo, `job_accepted`:** foi para dentro do `try`. Falha ao gravar o id vira erro pós-aceite com o job id.
- **Veo, download:** até 3 tentativas, sem reenviar a geração.
- **Veo, segredos:** redação de `AIza...`, `?key=`, Bearer e `x-goog-api-key`, testada em 9 cenários e também no log.
- **`units`:** `max(medido, pedido)`, com `measured_s` e aviso em `meta` quando o medido passa do pedido em mais de 1 s.

## Conferência do supervisor

- `uv run pytest -o addopts= -q` → **860 passed** (83 s).
- `ruff` limpo.
- Confirmados no código `HttpRetryOptions(attempts=1...)` e `vertexai=False` nos dois adaptadores.

**Status: T8 e T9 APROVADAS** para uso com mocks.

**Antes da primeira chamada paga:**
- confirmar o modelo e o preço oficiais;
- confirmar que o upload da Files API é gratuito;
- liberar o domínio `generativelanguage.googleapis.com` no proxy;
- autorização de gasto do usuário em `STATE.md`.
