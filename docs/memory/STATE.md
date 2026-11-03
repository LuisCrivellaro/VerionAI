# Estado do projeto

Atualizado: 2026-11-03

## Fase atual
**Fase 0 — validação audiovisual**: ferramenta (harness) pronta e testada sem gasto; validação com imóveis reais aguarda decisões do usuário.

**Ferramenta principal de vídeo (ADR-013, 03/11):** Open-Higgsfield-AI (fork Autom8AI) com kie.ai (Seedance 2.0 / Kling 3.0), usada manualmente na máquina do usuário. Ken Burns e parallax continuam como fallback gratuito.
**Próximo passo imediato:** usuário envia as fotos da acomodação (com termo) para o teste. Gerar com kie.ai exige chave própria e autorização de gasto registrada abaixo.

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
- [x] Decisões do usuário registradas (ADR-011): sem gasto por ora; só Gemini Omni (T10/T11 fora); trava de permissões aplicada.
- [x] **Sessão 2026-11-03 (revalidação em ambiente limpo):** `uv sync --frozen --extra gemini` + pytest → **1124 passed, 13 skipped (99 s)**; mypy limpo. Sem o extra `gemini`, a coleta quebra (`httpx`): agora documentado no README. `ruff check .` acusava 31 erros só em scripts de terceiros da skill gemini-omni-flash-api → `.claude` excluído do ruff (ADR-012).
- [ ] **Trava de permissões ainda NÃO confirmada:** a sessão de 03/11 começou fora do repo (clone no meio da sessão), então o `echo budget authorize` passou sem valer como teste; e o `claude -p` headless falhou com erro de autenticação neste ambiente. Confirmar numa sessão aberta **já dentro do repo** (ou no Claude Code local do usuário).
- [ ] **PRÓXIMO PASSO (depende do usuário):**
  1. Numa sessão nova, confirmar que a trava bloqueia (ex.: `echo budget authorize` deve ser negado).
  2. Usuário anexa fotos de 1–3 imóveis + código do termo; supervisor monta o roteiro (`property_id` tipo `imv-001`, fotos fora do git em `data/`).
  3. Rodar `ken-burns` nas fotos reais, gerar quadros de QC e montar o vídeo; usuário aprova no terminal (`APROVO tentativa N`).
  4. Parallax em fotos reais precisa de mapa de profundidade: avaliar modelo com licença comercial (Depth Anything V2 Small, Apache-2.0 — hipótese; Hugging Face bloqueado pelo proxy).
  5. Chamada paga do Omni só após autorização futura do usuário. **Pré-requisitos extras da 1ª chamada paga:** teto do `settle_ok` contra a reserva (hoje um clipe mais longo que o pedido pode estourar a reserva em até a duração máxima do modelo; o custo real continua gravado e a próxima `reserve` bloqueia, mas o excesso de uma tentativa não é impedido) e preço oficial do Omni em 1080p.
  6. Depth Anything (pendência 4) continua inviável aqui: em 03/11, Hugging Face (timeout) e github.com/DepthAnything (403) bloqueados pelo proxy; só o PyPI passa. Gerar os `.depth.png` na máquina do usuário ou adiar o parallax real.

## Autorizações de gasto
Nenhuma. Chamadas pagas (Gemini Omni, Veo, Kling, GPU) estão **bloqueadas** até o usuário autorizar o orçamento da Fase 0 (proposta: até R$ 350).

## Decisões de produto pendentes (usuário)
1. Cliente-alvo inicial (proposta: gestoras de temporada e imobiliárias de alto padrão)
2. Orçamento da Fase 0 (até R$ 350)
3. 3 imóveis de teste com autorização escrita para uso das fotos
4. Hipótese de preço (R$ 600–1.200 por vídeo de 60 s)
5. Retenção de arquivos (proposta: 12 meses)

## Bloqueios e limitações conhecidas
- Proxy das sessões em nuvem bloqueia sites fora da allowlist (ex.: ffmpeg.org); APIs pagas precisam de liberação de domínio.
- Sessões em nuvem são efêmeras: tudo que importa precisa ser commitado.
