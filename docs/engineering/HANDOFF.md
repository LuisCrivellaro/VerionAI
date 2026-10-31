# Handoff — setup da equipe de agentes (interrompido em 31/10/2026)

## Já feito (verificado)

- `.claude/settings.json`: plugin **superpowers@superpowers-dev 7.0.1** instalado em escopo de projeto
  via `claude plugin install` (confirmado por `claude plugin list`; custo fixo ~706 tokens/sessão).
- `.claude/skills/` com 15 skills (nome do frontmatter igual ao da pasta, verificado):
  - lude-kit (Apache-2.0, commit b41c7a6): senior-cv-engineer, senior-eval-engineer, senior-code-reviewer,
    principal-security-engineer, senior-product-manager, python-expert, staff-software-architect
  - wshobson/agents (MIT, commit 7e2fb55): uv-package-manager, python-resilience, python-testing-patterns, llm-evaluation
  - google-gemini/gemini-skills (Apache-2.0, commit 39d81f4): gemini-api-dev, gemini-omni-flash-api
  - Próprias: verion-ffmpeg-assembly (receitas testadas com ffmpeg 6.1.1; o teste pegou e corrigiu
    um bug de `yuv444p`), verion-fidelity-qc (rubrica de QC por cena)
- Licenças em `docs/engineering/third-party/`.

## Decisões tomadas

- Orquestrador: **subagentes nativos do Claude Code + metodologia Superpowers**.
  - Ruflo foi rejeitado: 239 MB, 26 dependências, servidor MCP próprio e sobreposição com os recursos nativos.
  - Oh My ClaudeCode foi rejeitado: hooks em 11 eventos, incluindo PreToolUse e UserPromptSubmit, o que é intrusivo e caro.
- Memória: arquivos versionados (`CLAUDE.md` + `docs/memory/DECISIONS.md` + `docs/memory/STATE.md`).
  - Beads foi rejeitado por enquanto: exige o binário `bd` e Dolt em cada sessão efêmera, e a sincronização depende de push. Reavaliar com mais de 30 tarefas abertas.
- Graphify: adiado. O repositório tem 1 arquivo; reavaliar com mais de 50 arquivos de código.
- MCP Agent Mail: rejeitado. A versão Python foi substituída pela versão em Rust e não tem mais manutenção; o retorno nativo dos subagentes basta.
- Skill Seekers: o repositório certo é yusufkaraaslan/Skill_Seekers. Não foi possível rodar porque o proxy desta sessão bloqueia ffmpeg.org. A skill de ffmpeg foi escrita à mão e testada.
- wshobson: foram copiadas só 4 skills. Os plugins completos trazem agentes com `model: opus` e 16 skills ou mais cada.
- Skills da conta claude.ai (frontend-design, ui-ux-pro-max, webapp-testing, vercel-react-best-practices, skill-creator) **não** foram duplicadas.

## Falta fazer

1. Criar `.claude/agents/` com 9 agentes VÉRION (frontmatter: name, description, tools, model, skills, color):
   - verion-product (PM + Finanças/custos) · sonnet · senior-product-manager
   - verion-architect (Arquitetura + DevOps) · opus · staff-software-architect
   - verion-media-engineer (Visão computacional + vídeo/ffmpeg) · sonnet · senior-cv-engineer, verion-ffmpeg-assembly, verion-fidelity-qc
   - verion-ai-engineer (LLM, adaptadores Veo/Kling/Wan, avaliações) · sonnet · gemini-api-dev, gemini-omni-flash-api, llm-evaluation, senior-eval-engineer, python-resilience
   - verion-backend (Backend + Banco + Automação) · sonnet · python-expert, uv-package-manager
   - verion-frontend (Frontend + UI/UX, dormente até a Fase 3) · sonnet
   - verion-qa · sonnet · python-testing-patterns
   - verion-security (somente leitura, LGPD) · sonnet · principal-security-engineer
   - verion-code-reviewer (somente leitura: Read, Grep, Glob, Bash) · sonnet · senior-code-reviewer
2. `CLAUDE.md` na raiz: papel de CTO, tabela de roteamento papel → agente, regras de delegação
   (tarefa simples = sem delegação; nunca dois agentes no mesmo arquivo; revisão obrigatória pelo
   code-reviewer antes de commit), controle de custo (teto de R$ 500/mês; registrar custo de toda chamada paga).
3. `docs/memory/DECISIONS.md` e `docs/memory/STATE.md`.
4. Verificar o reconhecimento: abrir o Claude Code no repo, rodar `/agents` e `/skills` (ou `claude -p` com
   `--output-format stream-json` e ler a lista de agents/skills no evento `init`).
5. Testar a delegação real: (a) verion-architect escreve `docs/engineering/FASE0_HARNESS.md`;
   (b) verion-code-reviewer revisa; (c) o supervisor integra. Guardar as evidências em `docs/engineering/evidence/`.
6. Escrever `docs/engineering/AGENT_SKILLS.md` e `docs/engineering/AGENT_ARCHITECTURE.md`.
7. Próximo passo de produto: Fase 0 (ver o documento "VÉRION — Análise crítica e plano do MVP").
