# Estado do projeto

Atualizado: 2026-11-02

## Fase atual
**Fase 0 — validação audiovisual** (ainda não iniciada). Ambiente de agentes configurado nesta data.

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
- [ ] **PRÓXIMO PASSO: {T6, T7} (dependem de T5) e {T8, T9}**, depois T4 e T12. Pendências: evidência 10 (T4: runs vazios, `exc.verion_run_id`; T6/T7: teste EXIF/retrato); evidência 09 (T8/T9: falhas transient/pós-aceite, `max_client_calls`); mypy em `ledger/repo.py:349`.
- [ ] Depois: {T3, T5, T8, T9} → {T6, T7} → T4 → T12. T10/T11 bloqueadas por decisão do usuário.
- [ ] Implementação do harness (registro de custos, Ken Burns e 2,5D locais, adaptadores com mock)

## Autorizações de gasto
Nenhuma. Chamadas pagas (Veo, Kling, GPU) estão **bloqueadas** até o usuário autorizar o orçamento da Fase 0 (proposta: até R$ 350).

## Decisões de produto pendentes (usuário)
1. Cliente-alvo inicial (proposta: gestoras de temporada e imobiliárias de alto padrão)
2. Orçamento da Fase 0 (até R$ 350)
3. 3 imóveis de teste com autorização escrita para uso das fotos
4. Hipótese de preço (R$ 600–1.200 por vídeo de 60 s)
5. Retenção de arquivos (proposta: 12 meses)

## Bloqueios e limitações conhecidas
- Proxy das sessões em nuvem bloqueia sites fora da allowlist (ex.: ffmpeg.org); APIs pagas precisam de liberação de domínio.
- Sessões em nuvem são efêmeras: tudo que importa precisa ser commitado.
