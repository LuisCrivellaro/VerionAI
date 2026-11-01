# Arquitetura de agentes do VÉRION

Atualizado: 2026-11-01. Fonte operacional: `CLAUDE.md` (o que o Claude Code carrega). Este documento explica o desenho.

## Hierarquia

```
                 Usuário (Luis)
                       │ pede, autoriza gastos e decisões de produto
                       ▼
        Supervisor / CTO = agente principal do Claude Code
     (entende, consulta memória, divide, delega, revisa, integra, reporta)
                       │ ferramenta Agent (subagentes sob demanda, contexto isolado)
   ┌──────────┬────────┼───────────┬─────────────┬──────────┐
 product   architect  media-eng  ai-eng      backend    frontend
   │          │          │          │           │      (dormente até Fase 3)
   └──── implementação ──┴──────────┴───────────┘
                       ▼
             qa  →  security (se sensível)  →  code-reviewer (sempre)
                       ▼
          Supervisor integra, verifica, atualiza memória, commit
```

Os subagentes não chamam outros subagentes. Toda coordenação passa pelo supervisor, o que evita cadeias de custo
invisíveis e mantém uma única visão do projeto.

## Papéis e responsabilidades

| Agente | Modelo | Ferramentas | Papéis do pedido | Skills pré-carregadas |
|---|---|---|---|---|
| `verion-product` | sonnet | leitura, escrita em docs, web | Product Manager; Finance & Cost Analyst | senior-product-manager |
| `verion-architect` | opus | leitura, escrita, Bash, web | Software Architect; DevOps | staff-software-architect |
| `verion-media-engineer` | sonnet | leitura, escrita, Bash | Computer Vision; Video Production | verion-ffmpeg-assembly, verion-fidelity-qc, senior-cv-engineer |
| `verion-ai-engineer` | sonnet | leitura, escrita, Bash, web | AI Engineer | gemini-api-dev, gemini-omni-flash-api, llm-evaluation, senior-eval-engineer, python-resilience |
| `verion-backend` | sonnet | leitura, escrita, Bash | Backend; Database; Automation | python-expert, uv-package-manager |
| `verion-frontend` | sonnet | leitura, escrita, Bash | Frontend; UI/UX | (skills da conta: frontend-design, ui-ux-pro-max, vercel-react-best-practices) |
| `verion-qa` | sonnet | leitura, escrita, Bash | QA | python-testing-patterns |
| `verion-security` | sonnet | **somente leitura** + Bash | Security (LGPD) | principal-security-engineer |
| `verion-code-reviewer` | sonnet | **somente leitura** + Bash | Code Reviewer | senior-code-reviewer |

Todos os agentes também têm acesso às skills de metodologia do plugin Superpowers (`superpowers:*`), carregadas sob
demanda. Os 15 papéis do pedido original são responsabilidades, não processos: 9 agentes cobrem todos (ADR-002).

## Regras de delegação

1. **Tarefa simples** (uma resposta, uma edição pequena, um comando): o supervisor faz sozinho, sem delegar.
2. **Funcionalidade nova:** product (aceite) → architect (spec com arquivos por agente) → implementadores → qa →
   security (se tocar em segredos, dados pessoais, uploads ou webhooks) → code-reviewer → supervisor.
3. **Todo pedido de delegação contém:** objetivo, contexto já decidido (para o agente não reanalisar), arquivos
   permitidos, critério de pronto e o lembrete do formato de retorno.
4. **Paralelismo:** só entre tarefas sem arquivos em comum e com dependências declaradas. Se o mesmo arquivo for
   inevitável, usar `isolation: worktree`.
5. **Revisão:** `verion-code-reviewer` é obrigatório antes de commit com código ou spec. Bloqueantes voltam ao autor,
   com no máximo 2 rodadas. Depois disso, o supervisor corrige pontualmente ou escala ao usuário.
6. **Escalar ao usuário:** só para gasto, serviço pago, ação irreversível, permissão sensível ou decisão de produto.

## Comunicação

- **Ida:** o prompt da ferramenta Agent (o subagente começa sem o histórico da conversa).
- **Volta:** o relatório final do subagente, em formato fixo: Resultado / Arquivos / Verificação / Riscos e pendências.
- **Persistente:** arquivos no repositório. Não há caixa de mensagens entre agentes. O MCP Agent Mail foi rejeitado
  (ADR-003) porque o retorno nativo basta para um supervisor único.

## Memória compartilhada

| Arquivo | Conteúdo | Quem escreve |
|---|---|---|
| `CLAUDE.md` | Regras, roteamento e fluxo | Supervisor, com decisão registrada |
| `docs/memory/STATE.md` | Fase, tarefas, bloqueios, autorizações de gasto, decisões pendentes | Supervisor, ao fim de cada tarefa |
| `docs/memory/DECISIONS.md` | ADRs curtos, o mais recente no topo | Arquiteto e supervisor |
| `docs/engineering/*.md` | Specs e arquitetura | Arquiteto |
| `docs/engineering/evidence/` | Evidências de testes e revisões | Supervisor e QA |
| `docs/product/` | Brief, plano do MVP, pedido original | Usuário e product |

Beads e Graphify foram avaliados e adiados (ADR-004), com gatilhos de reavaliação definidos.

## Controle de custos

- **Teto do produto:** R$ 500/mês. Chamadas pagas exigem autorização em `STATE.md` e, no harness, a trava
  `SpendToken` com reserva prévia (ver `FASE0_HARNESS.md`).
- **Custo de tokens dos agentes:**
  - `sonnet` por padrão; `opus` só no arquiteto.
  - Revisores são somente leitura e respondem com no máximo 12 comentários.
  - Pedidos trazem o contexto já decidido.
- **Medições reais desta configuração:**

  | Medição | Valor |
  |---|---|
  | Plugin Superpowers | ~706 tokens fixos por sessão (`claude plugin details`) |
  | Spec pelo arquiteto | ≈60 mil tokens |
  | Revisão (por rodada) | ≈45–51 mil tokens |

  Uma funcionalidade com spec, duas revisões e implementação custa na ordem de 200–300 mil tokens de subagentes.
  Por isso tarefas simples não passam pela equipe.

## Critérios de revisão (code-reviewer)

Corretude, aderência à spec, testes executados de verdade, arquivos fora do escopo, segredos, custo e bloqueio de
gasto, regras de fidelidade. Cada comentário tem severidade (bloqueante / sugestão forte / detalhe) e cita
`caminho:linha`. Veredito: APROVADO ou MUDANÇAS NECESSÁRIAS.

## Fluxo demonstrado (evidências 03–05)

1. O supervisor delegou a spec do harness ao `verion-architect` (247 linhas, 12 tarefas).
2. O `verion-code-reviewer` reprovou com 7 bloqueantes, entre eles a trava de gasto contornável e dependências ocultas. Ele verificou os arquivos por script.
3. O `verion-architect` corrigiu os 12 comentários (rodada 2).
4. O `verion-code-reviewer` confirmou 11 de 12 e achou 2 bloqueantes novos e pontuais.
5. O supervisor corrigiu esses pontos e registrou a aprovação.

## Limitação conhecida do Claude Code

Agentes de `.claude/agents/` do projeto são carregados no **início** da sessão. Numa sessão já aberta, copiar os
arquivos para `~/.claude/agents/` faz o harness anunciá-los na hora (evidência 03). Skills de projeto recarregam
sozinhas.
