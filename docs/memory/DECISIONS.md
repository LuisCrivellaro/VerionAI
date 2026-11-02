# Decisões (a mais recente no topo)

## 2026-11-02 · ADR-006 · Trava de gasto da T1: desvios aceitos e endurecimento
Foram aceitos estes desvios, revisados nas evidências 06 e 07:
- `_MIN_COST_BRL`: custo arredondado a 6 casas abaixo de 1e-6 gera `EstimateInvalid`.
- `_BALANCE_EPS = 1e-9` na conta do saldo.
- O token guarda `prices`.
- `settle_error` aceita token não consumido, para registrar a reserva quando o adaptador falha antes do `consume`.

Endurecimento:
- O registro `_ISSUED` (`WeakKeyDictionary`) guarda um `_Grant` imutável por token.
- `settle_*` leem preços, conexão e id só do `_Grant` e exigem o tipo exato `SpendToken`.
- O trigger `attempts_reservation_immutable` impede alterar `reserved_brl`, `method`, `run_id`, `scene_index` e `attempt_no`.
- A regra 6 do AST restringe `finish_attempt` a `budget.py` e `ledger/repo.py`.

O esquema continua na versão 1: o trigger entrou sem migração, porque ainda não existe banco real.
O limite fica declarado: reflexão e escrita direta no SQLite contornam a trava. As barreiras reais são a chave de API e a autorização.

## 2026-10-31 · ADR-005 · Harness da Fase 0: adaptador síncrono + ledger SQLite com guarda de orçamento
Status: proposta (aguarda revisão do verion-code-reviewer). Spec: `docs/engineering/FASE0_HARNESS.md`.
Todo método (Ken Burns, parallax 2,5D, Veo, Gemini Omni, Kling, Wan) implementa o Protocol `SceneGenerator`
(`estimate` + `generate` bloqueante, com polling interno). O harness roda as 12 cenas em sequência e grava cada
tentativa (custo em R$, unidades, tempo, status) e cada veredito de QC em SQLite (`runs`, `attempts`, `qc_reviews`,
`authorizations`). Toda chamada paga exige `--allow-paid` **e** saldo em `authorizations`; o limite é de 3 tentativas
por cena, e uma tentativa sem QC bloqueia a próxima. O registry carrega os adaptadores sob demanda, para que cada
um tenha seus próprios arquivos. O ledger é exportado em JSONL versionado, porque a sessão é efêmera.
Rejeitados: interface assíncrona ou fila (12 cenas sequenciais não justificam isso); Pydantic, SQLAlchemy/Alembic, Typer
e tenacity (o stdlib basta e são dependências a menos); commit do `.db` binário (o JSONL é diffável).
Reavaliar com paralelismo de cenas ou na migração para Postgres (Fase 2).

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
