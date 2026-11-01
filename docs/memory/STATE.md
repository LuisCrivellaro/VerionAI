# Estado do projeto

Atualizado: 2026-11-01

## Fase atual
**Fase 0 — validação audiovisual** (ainda não iniciada). Ambiente de agentes configurado nesta data.

## Em andamento
- [x] Setup da equipe de agentes, skills e memória (branch `setup/agent-team`)
- [x] Spec do harness da Fase 0 escrita pelo verion-architect (rodada 1)
- [x] Revisão rodada 1 pelo verion-code-reviewer: MUDANÇAS NECESSÁRIAS, 7 bloqueantes (evidência 04)
- [x] Spec corrigida pelo verion-architect (rodada 2, 364 linhas, changelog na seção 7)
- [x] Revisão rodada 2: 11/12 resolvidos + 2 bloqueantes novos corrigidos pelo supervisor → **spec aprovada** (evidência 05)
- [x] Skill de QC com gemini-omni; `config/prices.toml` (Omni = hipótese); AGENT_ARCHITECTURE.md e AGENT_SKILLS.md
- [ ] **T1: REVISÃO RODADA 1 = MUDANÇAS NECESSÁRIAS (evidência 06) — PRÓXIMO PASSO: terminar as correções no branch `wip/t1-revisao`**
  - Correções 1–7 da evidência 06 foram iniciadas e interrompidas. Estão em `wip/t1-revisao` (commit `17a22f8`), com 195 testes verdes e 9 vermelhos. `setup/agent-team` continua verde (173).
  - Feito: `pyproject.toml`, `uv.lock`, `.gitignore`, `config/prices.example.toml`, `src/verion/{__init__,errors,config,budget}.py`, `src/verion/ledger/{__init__.py,repo.py,schema.sql}`, `tests/{conftest,test_config,test_ledger,test_budget,test_architecture}.py` → **173 testes passando** (`uv run pytest`, 0,96 s, verificado pelo supervisor).
  - Desvios declarados pelo autor (a revisar): `_MIN_COST_BRL` (custo arredondado a 6 casas < 1e-6 → `EstimateInvalid`), `_BALANCE_EPS = 1e-9` no saldo, `WeakSet _ISSUED` além do `_SEAL`, token guarda `prices`; regra 4 do AST é vácua até T2/T5 criarem adaptadores.
  - Nota de ambiente: os agentes `verion-*` só carregam no início da sessão; nesta sessão a delegação usou `general-purpose` com a definição do agente no prompt.
  - Falta: concluir correções, rodada 2 de revisão (última), registrar desvios aceitos em DECISIONS.md, merge do wip em setup/agent-team, commit e push.
  - Depois: T2 e então {T3, T5, T8, T9} conforme FASE0_HARNESS.md.
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
