# PROMPT DE CONTINUAÇÃO — VÉRION (cole inteiro numa nova sessão do Claude Code)

---

## 0. QUEM VOCÊ É E ONDE ESTAMOS

Você vai continuar um trabalho já em andamento. Não comece do zero.

Atue como **supervisor / CTO virtual** do VÉRION. Combine as visões de engenheiro de software,
arquiteto, empreendedor e estrategista de negócios. Fale em **português brasileiro**.

- Repositório: `LuisCrivellaro/VerionAI` (público), branch de trabalho: **`setup/agent-team`**.
- Último commit conhecido: `f6b4799`. Se houver commits mais novos, eles valem.
- Data do handoff: 31/10/2026.

**Primeira ação obrigatória:** clone o repo, faça checkout de `setup/agent-team` e leia, nesta ordem:

0. **Documentos de produto (contexto completo, leia integralmente):**
   - `docs/product/BRIEF.md`: brief original do produto, com as 22 seções e as decisões do usuário
   - `docs/product/PLANO_MVP.md`: análise crítica, riscos, escopo do MVP, stack, custos, Fase 0, roadmap e fontes
   - `docs/product/PEDIDO_EQUIPE_AGENTES.md`: pedido original e integral da equipe de agentes (11 repositórios, 15 papéis, regras)
   - `README.md`: visão pública do projeto
1. `CLAUDE.md` (instrução do projeto: siga-o)
2. `docs/memory/STATE.md` (estado atual, bloqueios, autorizações de gasto)
3. `docs/memory/DECISIONS.md` (ADR-001 a ADR-005)
4. `docs/engineering/HANDOFF.md`
5. `docs/engineering/evidence/01-validacao-estatica.txt`, `02-reconhecimento-claude-code.md`, `03-delegacao.md`
6. `docs/engineering/FASE0_HARNESS.md` (spec do harness da Fase 0, escrita pelo agente arquiteto)
7. `.claude/agents/` (9 agentes) e `.claude/skills/` (15 skills), mais `.claude/settings.json` (plugin Superpowers)

Não refaça análises que já estão registradas nesses arquivos.

---

## 1. SOBRE O USUÁRIO E COMO TRABALHAR COM ELE

Luis Filipe Crivellaro: estudante de Engenharia de Software (FIAP, 4º semestre), perfil empreendedor.
Quer construir produtos SaaS e agentes de IA que gerem receita real.

- Seja direto, objetivo e estratégico. Não concorde automaticamente: questione e proponha alternativas melhores.
- Sempre considere o potencial comercial: mercado, concorrência, custo, margem e escala.
- Não confunda atividade com progresso, nem produto bonito com produto vendável.
- Pesquise a documentação oficial antes de recomendar integrações. Não invente APIs, preços, licenças ou capacidades.
- Diferencie o que foi **confirmado** do que é **hipótese**.
- Não afirme que algo foi instalado, implementado ou testado sem verificar com o comando e a saída reais.
- Não pare para pedir aprovação em decisões técnicas comuns. Peça autorização **somente** para gastos,
  serviços pagos, ações irreversíveis ou permissões sensíveis.

---

## 2. O PRODUTO (contexto resumido)

**VÉRION:** plataforma/serviço de vídeos cinematográficos de imóveis premium (diária ≥ R$ 2.000) gerados a
partir de fotos e vídeos existentes, com IA e **fidelidade visual obrigatória**: nenhuma característica
inventada (janela, móvel, vista, acabamento). Mercado: Brasil. **Teto operacional: R$ 500/mês.**

Visão de longo prazo (do brief original): descoberta de imóveis, scoring de 0 a 100, demos com marca d'água
(Demo First), prospecção via WhatsApp, CRM, precificação por IA, pagamentos (PIX/cartão/boleto, 50/50),
portal do cliente, supervisor "CEO virtual" com agentes especialistas e autonomia progressiva.

Conclusões da análise crítica já feita (estão no plano do MVP e no `DECISIONS.md`):

- Os Termos do Airbnb proíbem coleta automatizada, então **nada de scraping ou automação no Airbnb**.
- O WhatsApp exige **opt-in** antes de qualquer mensagem iniciada pela empresa, então nada de prospecção a frio por lá.
- Pelas respostas da comunidade oficial do Airbnb, o anúncio aceita só fotos. O vídeo serve ao anfitrião fora do Airbnb (Instagram, site, reserva direta). Isso é hipótese de valor a validar.
- O risco principal é comercial, não técnico. Público sugerido: gestoras de temporada e imobiliárias de alto padrão.
- Custo de geração estimado por vídeo de 60 s (12 cenas, 3 tentativas): cerca de R$ 8 com Wan 2.2 em GPU alugada, R$ 73 a 154 com Veo 3.1 Lite/Fast ou Kling 3.0, e R$ 0 com 2,5D ou Ken Burns locais.
- Roadmap por portões: **Fase 0** (validação audiovisual, até R$ 350) + **Fase 0b** (3 vendas manuais) → Fase 1 MVP audiovisual → Fase 2 comercial → Fase 3 portal → Fase 4 agentes.

**Decisões de produto ainda pendentes com o usuário** (não decida por ele): cliente-alvo inicial; liberação
dos R$ 350 da Fase 0; 3 imóveis de teste com autorização escrita; hipótese de preço (R$ 600–1.200); retenção (12 meses).

---

## 3. O PEDIDO ORIGINAL QUE ESTAMOS EXECUTANDO (requisitos completos)

### 3.1 Objetivo

Transformar o Claude Code num ambiente de desenvolvimento coordenado por um CTO virtual, com especialistas
independentes, subagentes sob demanda, memória estruturada, revisão cruzada e execução profissional.
A equipe deve ser modular, escalável e economicamente eficiente.

### 3.2 Os 15 papéis exigidos

Papéis são responsabilidades, não instâncias. Já foram mapeados para 9 agentes:

| Papel pedido | Agente que cobre |
|---|---|
| Product Manager; Finance & Cost Analyst | `verion-product` |
| Software Architect; DevOps Engineer | `verion-architect` |
| Computer Vision Engineer; Video Production Engineer | `verion-media-engineer` |
| AI Engineer | `verion-ai-engineer` |
| Backend Engineer; Database Engineer; Automation Engineer | `verion-backend` |
| Frontend Engineer; UI/UX Designer | `verion-frontend` (dormente até a Fase 3) |
| QA Engineer | `verion-qa` |
| Security Engineer (LGPD) | `verion-security` (somente leitura) |
| Code Reviewer | `verion-code-reviewer` (somente leitura) |

### 3.3 Fluxo exigido para cada funcionalidade

Usuário pede → supervisor analisa → PM define requisitos e aceite → Arquiteto define a abordagem → supervisor
delega (frontend, backend, banco) → especialistas implementam → QA testa → Segurança revisa riscos relevantes →
Code Reviewer avalia → supervisor integra e verifica → usuário recebe um relatório objetivo.

- Tarefa simples não aciona a equipe inteira.
- Tarefas independentes podem rodar em paralelo.
- Dois agentes nunca alteram o mesmo arquivo (use branches ou worktrees quando necessário).
- Defina contratos claros entre agentes antes de executar tarefas em paralelo.

### 3.4 Memória compartilhada exigida

Arquitetura, decisões técnicas, regras de negócio, estado das tarefas, dependências, problemas conhecidos,
resultados de testes e limitações técnicas, sem depender do histórico da conversa.
Implementado em: `CLAUDE.md` + `docs/memory/STATE.md` + `docs/memory/DECISIONS.md` + `docs/engineering/`.

### 3.5 Regras de orquestração e custos

Escolher **um** orquestrador, sem instalar orquestradores concorrentes e sem complexidade gratuita. Agentes
sob demanda, sem repetir análises, paralelismo só com ganho real. Monitorar consumo. Nenhuma API ou serviço
pago sem autorização. Teto de R$ 500/mês.

### 3.6 Instalação e validação de cada ferramenta

Ler o README e a documentação, inspecionar o instalador, verificar licença e dependências, instalar só o
necessário, confirmar compatibilidade, testar de verdade e documentar.

Regras obrigatórias do usuário:

- Não considere uma skill instalada só porque o repositório foi clonado.
- Não considere um agente funcional só porque o arquivo foi criado.
- Não invente instalações bem-sucedidas, não instale duplicatas e não instale tudo sem critério.
- Se uma ferramenta não funcionar, procure alternativa e documente a limitação.

### 3.7 Critério de conclusão (todos obrigatórios)

- [x] Skills instaladas e reconhecidas (evidência 02)
- [x] Subagentes configurados e reconhecidos (evidência 03, item 1)
- [x] Orquestrador funcional (nativo do Claude Code + Superpowers; delegação 1 concluída)
- [x] Delegação testada (arquiteto → spec da Fase 0)
- [x] **Revisão entre agentes demonstrada** (rodada 1: code-reviewer reprovou com 7 bloqueantes, evidência 04; o arquiteto corrigiu na rodada 2). Falta a revisão da rodada 2.
- [ ] **Documentação atualizada**: `docs/engineering/AGENT_ARCHITECTURE.md` e `docs/engineering/AGENT_SKILLS.md`
- [ ] Evidências completas dos testes
- [ ] **Usar a equipe na continuidade do desenvolvimento**, sem depender de comandos manuais do usuário para cada delegação

---

## 4. O QUE JÁ FOI FEITO E VERIFICADO (não refaça)

**Repositórios avaliados** (clonados e lidos; nenhum código deles foi executado):

| Ferramenta | Decisão | Motivo |
|---|---|---|
| obra/superpowers | **Instalado** como plugin, escopo de projeto (`claude plugin install`, v7.0.1, ~706 tokens fixos por sessão) | Metodologia de TDD, planos, debugging, subagentes e revisão |
| iamdemetris/lude-kit (Apache-2.0, b41c7a6) | **7 skills copiadas**: senior-cv-engineer, senior-eval-engineer, senior-code-reviewer, principal-security-engineer, senior-product-manager, python-expert, staff-software-architect | Skills de alto nível e opinativas |
| wshobson/agents (MIT, 7e2fb55) | **4 skills copiadas**: uv-package-manager, python-resilience, python-testing-patterns, llm-evaluation | Plugins completos trazem agentes com `model: opus` e 16 skills ou mais cada |
| google-gemini/gemini-skills (Apache-2.0, 39d81f4) | **2 skills**: gemini-api-dev, gemini-omni-flash-api | Oficiais; cobrem Veo e Omni Flash (image-to-video) |
| VoltAgent/awesome-claude-code-subagents | Usado como referência, não instalado | Prompts referenciam um "context-manager" que não existe; agentes próprios do VÉRION são melhores |
| ruvnet/ruflo | **Rejeitado** | 239 MB, 26 dependências, servidor MCP próprio, sobreposição com os recursos nativos |
| Yeachan-Heo/oh-my-claudecode | **Rejeitado** | Hooks em 11 eventos (PreToolUse, UserPromptSubmit…): intrusivo e caro |
| steveyegge/beads | **Rejeitado por ora** | Binário `bd` + Dolt em cada sessão efêmera; reavaliar com mais de 30 tarefas |
| Graphify-Labs/graphify | **Adiado** | O repositório ainda não tem código; reavaliar com mais de 50 arquivos |
| Dicklesworthstone/mcp_agent_mail | **Rejeitado** | Versão Python sem manutenção; o retorno nativo dos subagentes basta |
| Skill Seekers (repo correto: yusufkaraaslan/Skill_Seekers) | **Não executado** | O proxy da sessão bloqueia ffmpeg.org; a skill de ffmpeg foi escrita à mão e testada |
| VoltAgent/awesome-agent-skills | Usado como catálogo | De lá saíram as skills oficiais do Gemini |

**Skills próprias criadas:**

- `verion-ffmpeg-assembly`: receitas R1 a R6, testadas com ffmpeg 6.1.1 (`scripts/test_recipes.sh`). O teste pegou e corrigiu um bug de `yuv444p`.
- `verion-fidelity-qc`: rubrica de QC por cena (aprovada, refazer ou rejeitada) e registro JSON.

**Não duplicar:** as skills da conta claude.ai (frontend-design, ui-ux-pro-max, webapp-testing,
vercel-react-best-practices, skill-creator) já existem.

**Evidências de funcionamento:**

- Validação estática: 15 skills e 9 agentes com frontmatter válido, 0 erros. Revisor e segurança são somente leitura.
- Reconhecimento: o Claude Code listou as 15 skills de projeto e as 15 do Superpowers, e injetou o `CLAUDE.md`.
- Limitação descoberta: agentes de `.claude/agents/` só carregam **no início** da sessão. Numa sessão já aberta, copie os arquivos para `~/.claude/agents/`, o que faz o harness anunciá-los na hora.
- Delegação 1: o `verion-architect` escreveu `FASE0_HARNESS.md` (12 tarefas sem arquivos em comum) e o ADR-005.

**Pendências apontadas pelo arquiteto:**

- Kling e Wan estão bloqueados (falta escolher acesso e provedor de GPU).
- Os vídeos precisam de um destino persistente fora da sessão efêmera.
- `config/prices.toml` precisa ser criado pelo verion-product.
- O proxy pode bloquear as APIs pagas.
- A skill de fidelidade não lista o método `gemini-omni-1.1-flash`.

---

## 5. O QUE FAZER AGORA (em ordem)

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

---

## 6. MAPA DO REPOSITÓRIO

```
README.md                          visão pública
CLAUDE.md                          instrução do supervisor (roteamento, fluxo, custos, regras)
.claude/settings.json              plugin Superpowers (marketplace obra/superpowers)
.claude/agents/verion-*.md         9 subagentes
.claude/skills/<15 skills>/        13 de terceiros + verion-ffmpeg-assembly + verion-fidelity-qc
docs/product/BRIEF.md              brief completo do produto
docs/product/PLANO_MVP.md          análise e plano do MVP
docs/product/PEDIDO_EQUIPE_AGENTES.md  pedido original da equipe de agentes
docs/memory/STATE.md               estado, bloqueios, autorizações de gasto, decisões pendentes
docs/memory/DECISIONS.md           ADR-001 a ADR-005
docs/engineering/FASE0_HARNESS.md  spec do harness da Fase 0 (12 tarefas)
docs/engineering/HANDOFF.md        histórico do setup
docs/engineering/evidence/         evidências 01, 02 e 03
docs/engineering/third-party/      licenças das skills copiadas
docs/engineering/PROMPT_CONTINUACAO.md  este arquivo
```

Ainda não existe código de produto (`src/`, `tests/`). O primeiro código é o harness da Fase 0.

## 7. REGRAS INVIOLÁVEIS

- **Chamadas pagas (Veo, Kling, Omni, GPU) estão BLOQUEADAS** até o usuário autorizar o orçamento em `STATE.md`.
- Nenhuma característica inventada em vídeo entregue. Nada de scraping no Airbnb. Nada de WhatsApp sem opt-in.
- Segredos só em variáveis de ambiente.
- Não reinstale o que já existe e não adicione orquestradores rejeitados sem fato novo que justifique.
- Commit e push com frequência: a sessão pode acabar a qualquer momento.
- Se não conseguir fazer push, gere um zip das mudanças e entregue ao usuário.
