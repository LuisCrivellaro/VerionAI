# Estado do projeto

Atualizado: 2026-10-31

## Fase atual
**Fase 0 — validação audiovisual** (ainda não iniciada). Ambiente de agentes configurado nesta data.

## Em andamento
- [x] Setup da equipe de agentes, skills e memória (branch `setup/agent-team`)
- [x] Spec do harness da Fase 0 (`docs/engineering/FASE0_HARNESS.md`), escrita pelo verion-architect; **revisão pelo verion-code-reviewer pendente**
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
