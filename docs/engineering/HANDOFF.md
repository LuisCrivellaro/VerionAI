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

## Andamento após o handoff inicial

- Os 9 agentes foram criados, reconhecidos e testados (evidências 01 a 03).
- `CLAUDE.md` e a memória (`docs/memory/`) foram criados.
- A delegação foi testada: o arquiteto escreveu a spec da Fase 0.
- A revisão cruzada da rodada 1 reprovou a spec com 7 bloqueantes (evidência 04).
- O arquiteto corrigiu a spec na rodada 2 (changelog na seção 7 de `FASE0_HARNESS.md`).
- Contexto do produto: `docs/product/` (BRIEF, PLANO_MVP, PEDIDO_EQUIPE_AGENTES).

## Falta fazer (em ordem)

1. **Garantir os agentes:** confirme que os 9 `verion-*` estão disponíveis para a ferramenta Agent. Se não
   estiverem, copie `.claude/agents/verion-*.md` para `~/.claude/agents/` e confirme.
2. **Revisão cruzada, rodada 2 (próximo passo; a rodada 1 já foi feita):** delegue ao `verion-code-reviewer`
   a revisão da spec corrigida `docs/engineering/FASE0_HARNESS.md`. Ele deve conferir se cada um dos 12 comentários
   de `docs/engineering/evidence/04-revisao-fase0-rodada1.md` foi realmente resolvido (o changelog está na seção 7
   da spec), refazer a checagem mecânica de arquivos exclusivos por tarefa e verificar as novas dependências
   ("Depende de"). Veredito APROVADO ou MUDANÇAS NECESSÁRIAS. Se ainda houver bloqueantes, o supervisor decide:
   corrige pontualmente ou devolve ao arquiteto uma última vez. Registre o resultado em
   `docs/engineering/evidence/05-revisao-fase0-rodada2.md` e atualize o ADR-005 se a decisão mudou.
3. Atualize `verion-fidelity-qc` com o método `gemini-omni-1.1-flash`.
4. Delegue ao `verion-product` a criação de `config/prices.toml` a partir dos preços do plano (seção 2), com fonte e data.
5. Escreva `docs/engineering/AGENT_ARCHITECTURE.md`: hierarquia, responsabilidades, skills por papel, regras
   de delegação, comunicação, memória, controle de custos, critérios de revisão e fluxos de execução.
6. Escreva `docs/engineering/AGENT_SKILLS.md`: cada skill com origem, commit, licença e agente que usa; o
   plugin; ferramentas rejeitadas com justificativa; como reinstalar numa máquina nova.
7. Atualize `STATE.md` e `HANDOFF.md`, faça commit e push.
8. **Comece a Fase 0 usando a equipe**, pelas tarefas de `FASE0_HARNESS.md` que não dependem de chamada paga.
   Cada tarefa vai ao agente responsável com apenas os seus arquivos, em TDD (`superpowers:test-driven-development`),
   com QA e revisão do `verion-code-reviewer` antes de cada commit. Paralelize só tarefas independentes.
9. Ao final, apresente ao usuário um relatório objetivo com: skills instaladas, subagentes disponíveis,
   orquestrador, ferramentas rejeitadas e por quê, arquitetura da equipe, fluxo de delegação, memória,
   custos e dependências, testes realizados com evidências e próximos passos.

## Regras

Chamadas pagas estão bloqueadas até o usuário autorizar em `STATE.md`. Não reinstale o que já existe e não adicione orquestradores rejeitados. Faça commit e push com frequência.
