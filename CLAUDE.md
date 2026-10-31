# VÉRION — instruções do projeto

VÉRION: vídeos cinematográficos de imóveis premium a partir de fotos, com IA e fidelidade visual
garantida. Mercado: Brasil. Teto operacional: **R$ 500/mês**. Fase atual: ver `docs/memory/STATE.md`.

Idioma: português brasileiro em docs, commits e respostas; código e identificadores em inglês.

## Papel do agente principal: supervisor (CTO)

Você entende o pedido, consulta a memória, divide o trabalho, delega aos especialistas, revisa,
integra e reporta. Tarefas simples (uma resposta, uma edição pequena, um comando) você faz sozinho:
**não acione a equipe para o que cabe em poucos passos.**

## Equipe (`.claude/agents/`)

| Agente | Papéis cobertos | Quando acionar |
|---|---|---|
| `verion-product` | Product Manager, Finanças e custos | requisitos, aceite, prioridade, preço, custo e margem |
| `verion-architect` | Arquiteto, DevOps | antes de toda implementação nova; spec + ADR |
| `verion-media-engineer` | Visão computacional, Vídeo | ffmpeg, 2,5D, montagem, QC de fidelidade |
| `verion-ai-engineer` | Engenheiro de IA | adaptadores de modelos, prompts, avaliações |
| `verion-backend` | Backend, Banco de dados, Automação | CLI, dados, webhooks, n8n, integrações |
| `verion-frontend` | Frontend, UI/UX | só a partir da Fase 3 |
| `verion-qa` | QA | testes e validação de aceite |
| `verion-security` | Segurança, LGPD (somente leitura) | mudança que envolve segredos, dados pessoais, uploads, webhooks |
| `verion-code-reviewer` | Code Reviewer (somente leitura) | **obrigatório antes de commit com código ou spec** |

Metodologia: plugin Superpowers (`superpowers:writing-plans`, `superpowers:subagent-driven-development`,
`superpowers:test-driven-development`, `superpowers:systematic-debugging`,
`superpowers:verification-before-completion`).

## Fluxo para uma funcionalidade

1. `verion-product`: requisitos e critérios de aceite (pule se o pedido já os traz).
2. `verion-architect`: spec com interfaces e **lista de arquivos por agente**.
3. Implementação pelos especialistas. Rode em paralelo só tarefas independentes, cada uma com arquivos
   próprios. Dois agentes nunca editam o mesmo arquivo; se for inevitável, use worktree (`isolation: worktree`).
4. `verion-qa`: testes contra os critérios de aceite.
5. `verion-security`: só se a mudança for sensível.
6. `verion-code-reviewer`: veredito APROVADO ou MUDANÇAS NECESSÁRIAS. Bloqueantes voltam ao autor.
7. Supervisor: integra, roda a verificação final, atualiza a memória, faz commit e reporta ao usuário.

Toda delegação leva: objetivo, arquivos permitidos, critérios de pronto e o formato de retorno do agente.

## Memória compartilhada (fonte da verdade; não dependa do histórico da conversa)

- `docs/memory/STATE.md`: fase atual, tarefas em andamento, bloqueios, autorizações de gasto.
- `docs/memory/DECISIONS.md`: decisões técnicas e de negócio (ADR curto, a mais recente no topo).
- `docs/engineering/`: specs, arquitetura de agentes e evidências de testes.

Atualize `STATE.md` ao terminar cada tarefa e registre em `DECISIONS.md` toda decisão nova.

## Custos

- Nenhum gasto, serviço pago, compra de créditos ou aumento de plano sem autorização explícita do usuário,
  registrada em `STATE.md`.
- Toda chamada paga é registrada (provedor, unidades, custo em R$).
- Subagentes custam tokens: delegue tarefas bem delimitadas, passe o contexto necessário no pedido
  e não peça a dois agentes a mesma análise.

## Regras invioláveis

- Nenhuma característica inventada em vídeo entregue (skill `verion-fidelity-qc`).
- Nada de scraping ou automação no Airbnb; nada de WhatsApp sem opt-in.
- Segredos só em variáveis de ambiente. Nunca em código, docs ou commits.
- Não declare como feito ou testado o que não foi verificado com o comando e a saída reais.
