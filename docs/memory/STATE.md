# Estado do projeto

Atualizado: 2026-11-02

## Fase atual
**Fase 0 — validação audiovisual**: ferramenta (harness) pronta e testada sem gasto; validação com imóveis reais aguarda decisões do usuário.

## Em andamento
- [x] Setup da equipe de agentes, skills e memória (branch `setup/agent-team`)
- [x] Spec do harness da Fase 0 escrita pelo verion-architect (rodada 1)
- [x] Revisão rodada 1 pelo verion-code-reviewer: MUDANÇAS NECESSÁRIAS, 7 bloqueantes (evidência 04)
- [x] Spec corrigida pelo verion-architect (rodada 2, 364 linhas, changelog na seção 7)
- [x] Revisão rodada 2: 11/12 resolvidos + 2 bloqueantes novos corrigidos pelo supervisor → **spec aprovada** (evidência 05)
- [x] Skill de QC com gemini-omni; `config/prices.toml` (Omni = hipótese); AGENT_ARCHITECTURE.md e AGENT_SKILLS.md
- [x] **T1 concluída e aprovada** (evidências 06 e 07): config, errors, ledger SQLite e trava de gasto (`SpendToken`, reserva `pending` em `BEGIN IMMEDIATE`, triggers, AST). **219 testes verdes**, ruff limpo. ADR-006.
- [x] **T2 concluída e aprovada** (evidências 08 e 09): contrato dos adaptadores, registry, helper de contrato endurecido e `fake`. **344 testes verdes**.
- [x] **T3 (roteiro + harness) e T5 (ffmpeg + Ken Burns) concluídas e aprovadas** (evidência 10, ADR-007). **544 testes verdes**.
- [x] **T6 (parallax 2,5D) e T7 (montagem + QC) concluídas e aprovadas** (evidência 11, ADR-008). **655 testes verdes** (~90 s).
- [x] **T8 (Veo) e T9 (Gemini Omni) concluídas e aprovadas com mocks** (evidência 12, ADR-009). **860 testes verdes** (~83 s). Veo preview descontinuado → Omni é o provedor pago principal.
- [x] **T4 (CLI `verion`, relatório, export) concluída e aprovada** após revisão de código + segurança (evidência 13, ADR-010). **1111 testes verdes** (~93 s), ruff e mypy limpos.
- [x] **T12 (aceitação ponta a ponta) concluída** (evidência 14): nível padrão sempre (1124 passed, 13 skipped, ~107 s); fluxo completo opt-in com `VERION_E2E_FULL=1` (26 passed, ~122 s). Nenhum bug de produção.
- [x] **HARNESS DA FASE 0 CONCLUÍDO SEM CHAMADAS PAGAS** (T1–T9, T12; T10 Kling e T11 Wan bloqueadas por decisão do usuário).
- [ ] **PRÓXIMO PASSO: decisões do usuário** (lista abaixo) para iniciar a Fase 0 real: autorizar orçamento, fornecer 3 imóveis com termo de fotos, liberar `generativelanguage.googleapis.com` no proxy, colocar `GEMINI_API_KEY` no ambiente (pelo próprio usuário), confirmar preço oficial do Omni em 1080p. Antes da 1ª chamada paga: rodar `VERION_E2E_FULL=1` e anexar a saída como evidência. Opcional: merge de `setup/agent-team` em `main`.
- [ ] Depois: {T3, T5, T8, T9} → {T6, T7} → T4 → T12. T10/T11 bloqueadas por decisão do usuário.
- [ ] Implementação do harness (registro de custos, Ken Burns e 2,5D locais, adaptadores com mock)

## Autorizações de gasto
Nenhuma. Chamadas pagas (Gemini Omni, Veo, Kling, GPU) estão **bloqueadas** até o usuário autorizar o orçamento da Fase 0 (proposta: até R$ 350).

## Decisões de produto pendentes (usuário)
0. Aplicar ou não a regra `permissions.deny` proposta no ADR-010 (agentes não autorizam gasto nem aprovam QC como humano)
1. Cliente-alvo inicial (proposta: gestoras de temporada e imobiliárias de alto padrão)
2. Orçamento da Fase 0 (até R$ 350)
3. 3 imóveis de teste com autorização escrita para uso das fotos
4. Hipótese de preço (R$ 600–1.200 por vídeo de 60 s)
5. Retenção de arquivos (proposta: 12 meses)

## Bloqueios e limitações conhecidas
- Proxy das sessões em nuvem bloqueia sites fora da allowlist (ex.: ffmpeg.org); APIs pagas precisam de liberação de domínio.
- Sessões em nuvem são efêmeras: tudo que importa precisa ser commitado.
