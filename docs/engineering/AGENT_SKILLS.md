# Skills, plugin e ferramentas do ambiente de agentes

Atualizado: 2026-11-01. Reconhecimento verificado no Claude Code 2.1.316 (evidência 02).

## Plugin

| Plugin | Versão | Escopo | Como foi instalado | Custo fixo |
|---|---|---|---|---|
| `superpowers@superpowers-dev` (obra/superpowers, MIT) | 7.0.1 | projeto (`.claude/settings.json`) | `claude plugin marketplace add obra/superpowers --scope project` + `claude plugin install superpowers@superpowers-dev --scope project` | ~706 tokens/sessão + hook SessionStart |

Skills do plugin (15, namespace `superpowers:`):
- brainstorming
- diagnosing-superpowers
- dispatching-parallel-agents
- executing-plans
- finishing-a-development-branch
- receiving-code-review
- requesting-code-review
- subagent-driven-development
- systematic-debugging
- test-driven-development
- using-git-worktrees
- using-superpowers
- verification-before-completion
- writing-plans
- writing-skills

## Skills de projeto (`.claude/skills/`, 15)

| Skill | Origem e commit | Licença | Usada por |
|---|---|---|---|
| senior-product-manager | iamdemetris/lude-kit @ b41c7a6 | Apache-2.0 | verion-product |
| staff-software-architect | lude-kit @ b41c7a6 | Apache-2.0 | verion-architect |
| senior-cv-engineer | lude-kit @ b41c7a6 | Apache-2.0 | verion-media-engineer |
| senior-eval-engineer | lude-kit @ b41c7a6 | Apache-2.0 | verion-ai-engineer |
| python-expert | lude-kit @ b41c7a6 | Apache-2.0 | verion-backend |
| principal-security-engineer | lude-kit @ b41c7a6 | Apache-2.0 | verion-security |
| senior-code-reviewer | lude-kit @ b41c7a6 | Apache-2.0 | verion-code-reviewer |
| uv-package-manager | wshobson/agents @ 7e2fb55 | MIT | verion-backend |
| python-resilience | wshobson/agents @ 7e2fb55 | MIT | verion-ai-engineer |
| python-testing-patterns | wshobson/agents @ 7e2fb55 | MIT | verion-qa |
| llm-evaluation | wshobson/agents @ 7e2fb55 | MIT | verion-ai-engineer |
| gemini-api-dev | google-gemini/gemini-skills @ 39d81f4 | Apache-2.0 | verion-ai-engineer |
| gemini-omni-flash-api | google-gemini/gemini-skills @ 39d81f4 | Apache-2.0 | verion-ai-engineer |
| verion-ffmpeg-assembly | própria (receitas testadas: `scripts/test_recipes.sh`) | do projeto | verion-media-engineer |
| verion-fidelity-qc | própria | do projeto | verion-media-engineer, QC |

As cópias de terceiros não foram modificadas. As licenças estão em `docs/engineering/third-party/`.

**Skills da conta claude.ai, não duplicadas aqui:** frontend-design, ui-ux-pro-max, vercel-react-best-practices,
webapp-testing, skill-creator. Elas só existem nas sessões com a conta conectada.

## Ferramentas avaliadas e rejeitadas ou adiadas

| Ferramenta | Decisão | Motivo | Reavaliar quando |
|---|---|---|---|
| ruvnet/ruflo (Claude Flow) | Rejeitada | 239 MB, 26 dependências, servidor MCP próprio, sobreposição com subagentes nativos | Nunca, salvo necessidade de swarm multi-máquina |
| Yeachan-Heo/oh-my-claudecode | Rejeitada | Hooks em 11 eventos (PreToolUse, UserPromptSubmit…): custo e interferência em toda chamada | — |
| steveyegge/beads | Adiada | Binário `bd` + Dolt em cada sessão efêmera; sincroniza via push de `refs/dolt/data` | Mais de 30 tarefas abertas ou agentes paralelos disputando tarefas |
| Graphify-Labs/graphify | Adiada | Repositório ainda sem código | Mais de 50 arquivos de código |
| Dicklesworthstone/mcp_agent_mail | Rejeitada | Versão Python sem manutenção (substituída pela em Rust); o retorno nativo basta | Vários supervisores simultâneos |
| yusufkaraaslan/Skill_Seekers | Não executada | O proxy da sessão bloqueia ffmpeg.org; documentação da ferramenta já é bem conhecida pelo modelo | Ao precisar de skill para documentação nova e não coberta |
| wshobson/agents (plugins completos) | Rejeitados | Agentes com `model: opus` e 16+ skills por plugin | — |
| VoltAgent/awesome-claude-code-subagents | Referência | Prompts dependem de um "context-manager" inexistente | — |
| VoltAgent/awesome-agent-skills | Catálogo | De onde vieram as skills oficiais do Gemini | — |

## Como reinstalar numa máquina nova

Basta clonar o repositório:
- **Skills e agentes de projeto:** vêm junto.
- **Plugin:** declarado em `.claude/settings.json`. O Claude Code oferece instalar ao confiar no projeto. Para instalar manualmente:
  `claude plugin marketplace add obra/superpowers --scope project && claude plugin install superpowers@superpowers-dev --scope project`.
- **Verificação:**
  - `claude plugin list` mostra o superpowers ativo.
  - Numa sessão nova, `/agents` lista os 9 `verion-*` e as skills aparecem na ferramenta Skill.
  - `bash .claude/skills/verion-ffmpeg-assembly/scripts/test_recipes.sh` termina com `OK todas as receitas`.
