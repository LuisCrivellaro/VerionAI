# Decisões (a mais recente no topo)

## 2026-10-31 · ADR-004 · Memória compartilhada em arquivos versionados
Memória em `CLAUDE.md` + `docs/memory/STATE.md` + `docs/memory/DECISIONS.md`.
Beads foi avaliado e rejeitado por enquanto: exige o binário `bd` e Dolt embutido em cada sessão
efêmera, e a sincronização depende de push para `refs/dolt/data`. Reavaliar com mais de 30 tarefas abertas
ou quando houver agentes paralelos disputando tarefas.

## 2026-10-31 · ADR-003 · Orquestração nativa do Claude Code
Supervisor = agente principal; especialistas = subagentes em `.claude/agents/`; metodologia = Superpowers.
Ruflo foi rejeitado (239 MB, 26 dependências, servidor MCP próprio, sobreposição com os recursos nativos).
Oh My ClaudeCode foi rejeitado (hooks em 11 eventos, incluindo PreToolUse e UserPromptSubmit, o que aumenta
o custo e a interferência em toda chamada). MCP Agent Mail foi rejeitado (versão Python sem manutenção;
o retorno nativo dos subagentes basta).

## 2026-10-31 · ADR-002 · 9 agentes para 15 papéis
Os papéis são responsabilidades, não instâncias. Agentes enxutos e específicos do VÉRION, com skills
pré-carregadas. Modelo `sonnet` por padrão; `opus` só no arquiteto, onde o erro é mais caro.
Revisores (código e segurança) são somente leitura.

## 2026-10-31 · ADR-001 · Skills curadas e copiadas, sem plugins completos
Foram copiadas 13 skills de terceiros com commit fixado e licença (ver `docs/engineering/AGENT_SKILLS.md`)
e criadas 2 skills próprias. Os plugins completos do wshobson foram descartados: trazem agentes com
`model: opus` e 16 skills ou mais cada. Superpowers é a exceção, instalado como plugin porque quase
todas as suas skills são usadas e elas referenciam umas às outras pelo namespace `superpowers:`.

## 2026-10-31 · Produto · Plano do MVP
Ver o documento "VÉRION — Análise crítica e plano do MVP". Fase 0 antes de qualquer plataforma.
Decisões de produto pendentes estão em `STATE.md`.
