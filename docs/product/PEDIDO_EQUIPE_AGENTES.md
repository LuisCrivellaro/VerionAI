# VÉRION — EXPANSÃO DO ECOSSISTEMA DE SKILLS, SUBAGENTES E MULTI-AGENTES

## 1. OBJETIVO

Expanda o ambiente de desenvolvimento do VÉRION para utilizar uma equipe de agentes especializados, subagentes, skills e ferramentas de orquestração.

Não quero trabalhar somente com um agente principal executando todas as tarefas.

Quero que Claude funcione como um CTO que coordena uma equipe de especialistas, delega atividades, distribui tarefas, revisa resultados e integra as entregas.

A equipe deverá ser modular, escalável e economicamente eficiente.

## 2. REPOSITÓRIOS ADICIONAIS PARA INSPEÇÃO

### A. Bibliotecas de agentes e subagentes

**1. WSHOBSON — Agents**
https://github.com/wshobson/agents

Prioridade máxima.

Avalie os plugins, agentes, skills e ferramentas disponíveis.

Utilize especialistas para:
- Arquitetura.
- Backend.
- Frontend.
- React.
- TypeScript.
- Python.
- Segurança.
- Banco de dados.
- Testes.
- DevOps.
- Inteligência artificial.

**2. VoltAgent — Claude Code Subagents**
https://github.com/VoltAgent/awesome-claude-code-subagents

Avalie os especialistas disponíveis e selecione os mais adequados ao VÉRION.

**3. Lude Kit**
https://github.com/iamdemetris/lude-kit

Avalie as 70 skills e 30 subagentes disponíveis.

Priorize especialistas em:
- AI Engineering.
- Computer Vision.
- RAG.
- LLM.
- Python.
- TypeScript.
- PostgreSQL.
- Next.js.
- Segurança.
- Performance.
- Code Review.

**4. Claude Flow / Ruflo**
https://github.com/ruvnet/ruflo

Avalie como alternativa avançada para orquestração de múltiplos agentes, coordenação, memória e execução de tarefas em paralelo.

**5. Oh My ClaudeCode**
https://github.com/Yeachan-Heo/oh-my-claudecode

Avalie como alternativa de orquestração de equipes de agentes e execução paralela.

**6. Superpowers**
https://github.com/obra/superpowers

Utilize suas metodologias de desenvolvimento, planejamento, debugging e execução orientada a testes.

### B. Inteligência e memória do projeto

**7. Graphify**
https://github.com/Graphify-Labs/graphify

Avalie a criação de um grafo de conhecimento do código e da arquitetura do VÉRION.

Objetivos:
- Entender dependências entre módulos.
- Identificar relações entre arquivos.
- Facilitar navegação em projetos grandes.
- Ajudar agentes a entender o contexto.
- Evitar alterações que quebrem funcionalidades relacionadas.

**8. Beads**
https://github.com/steveyegge/beads

Avalie como sistema de memória e gerenciamento de tarefas para agentes de programação.

Objetivos:
- Manter tarefas estruturadas.
- Registrar dependências.
- Preservar contexto entre sessões.
- Evitar perda de progresso.
- Facilitar execução de tarefas complexas.

**9. MCP Agent Mail**
https://github.com/Dicklesworthstone/mcp_agent_mail

Avalie para comunicação e coordenação assíncrona entre agentes.

Não instalar se a comunicação nativa do Claude Code já resolver o problema.

### C. Descoberta de novas skills

**10. Skill Seekers**
https://github.com/Skill-Seeker/Skill_Seekers

Avalie a possibilidade de transformar documentação oficial de ferramentas utilizadas no VÉRION em skills especializadas.

Exemplos:
- Documentação do FFmpeg.
- ComfyUI.
- FastAPI.
- Supabase.
- n8n.
- Modelos de geração audiovisual.
- APIs de provedores de IA.

Não criar skills baseadas em documentação desatualizada ou não verificada.

**11. Awesome Agent Skills**
https://github.com/VoltAgent/awesome-agent-skills

Utilize como catálogo para descobrir especialistas adicionais.

Priorize skills oficiais e mantidas pelos próprios desenvolvedores das ferramentas.

Não instale centenas de skills sem necessidade.

## 3. ARQUITETURA DE MULTI-AGENTES DO VÉRION

Quero uma estrutura hierárquica com um agente supervisor e especialistas.

### Nível 1 — Supervisor / CTO

Responsável por:
- Entender o objetivo do usuário.
- Analisar o estado do projeto.
- Dividir tarefas.
- Escolher os especialistas necessários.
- Delegar atividades.
- Acompanhar execução.
- Resolver conflitos.
- Revisar entregas.
- Integrar resultados.
- Gerar relatórios.

O supervisor deve manter visão global da arquitetura e das prioridades.

### Nível 2 — Equipe de especialistas

Criar os seguintes perfis, aproveitando subagentes existentes sempre que possível.

**1. Product Manager**
- Requisitos.
- Priorização.
- Roadmap.
- Experiência do usuário.
- Regras de negócio.

**2. Software Architect**
- Arquitetura geral.
- Estrutura de pastas.
- Integrações.
- Escalabilidade.
- Decisões técnicas.

**3. UI/UX Designer**
- Identidade visual.
- Design system.
- Tipografia.
- Layout.
- Responsividade.
- Acessibilidade.

**4. Frontend Engineer**
- React.
- Next.js.
- TypeScript.
- Tailwind.
- Componentes.
- Performance.

**5. Backend Engineer**
- APIs.
- TypeScript.
- FastAPI.
- Integrações.
- Regras de negócio.

**6. Database Engineer**
- PostgreSQL.
- Supabase.
- Modelagem.
- RLS.
- Segurança.
- Performance.

**7. AI Engineer**
- Modelos de IA.
- LLMs.
- Agentes.
- RAG.
- Avaliações.
- Pipelines.

**8. Computer Vision Engineer**
- Análise de imagens.
- Reconstrução espacial.
- Visão computacional.
- Image-to-video.
- Fidelidade visual.

**9. Video Production Engineer**
- FFmpeg.
- Edição.
- Composição.
- Renderização.
- Processamento audiovisual.

**10. Automation Engineer**
- n8n.
- Workflows.
- Webhooks.
- APIs.
- Integrações.
- Tratamento de erros.

**11. QA Engineer**
- Testes funcionais.
- Testes de integração.
- Playwright.
- Regressão.
- Validação de entregas.

**12. Security Engineer**
- Autenticação.
- Permissões.
- Proteção de dados.
- Credenciais.
- Vulnerabilidades.
- LGPD.

**13. DevOps Engineer**
- Docker.
- Deploy.
- CI/CD.
- Infraestrutura.
- Logs.
- Monitoramento.

**14. Finance & Cost Analyst**
- Custo por operação.
- Custo por vídeo.
- Consumo de APIs.
- Margem.
- Orçamento mensal.

**15. Code Reviewer**
- Revisão independente.
- Qualidade.
- Manutenibilidade.
- Padrões.
- Detecção de erros.

Não crie 15 processos independentes se os subagentes existentes conseguirem cumprir essas funções. São papéis de responsabilidade, não uma exigência de 15 instâncias permanentes.

## 4. COMO OS AGENTES DEVEM TRABALHAR

Exemplo de desenvolvimento de uma nova funcionalidade:

1. O usuário solicita uma funcionalidade.
2. O supervisor analisa o contexto.
3. O Product Manager define requisitos e critérios de aceitação.
4. O Software Architect define a abordagem técnica.
5. O supervisor delega frontend, backend e banco de dados quando necessário.
6. Os especialistas implementam suas partes.
7. O QA executa testes.
8. O Security Engineer revisa riscos relevantes.
9. O Code Reviewer avalia a implementação.
10. O supervisor integra e verifica o resultado.
11. O usuário recebe um relatório objetivo.

Para tarefas simples, não acione a equipe inteira.

Para tarefas complexas, delegue atividades paralelas quando forem independentes.

Evite que dois agentes alterem simultaneamente os mesmos arquivos.

Utilize branches ou Git worktrees quando necessário.

Defina contratos claros entre agentes antes de executar tarefas paralelas.

## 5. MEMÓRIA COMPARTILHADA

Os agentes devem compartilhar informações relevantes por meio de mecanismos estruturados.

Manter:
- Arquitetura.
- Decisões técnicas.
- Regras de negócio.
- Estado das tarefas.
- Dependências.
- Problemas conhecidos.
- Resultados de testes.
- Limitações técnicas.

Avalie Graphify e Beads para esse propósito.

Não depender exclusivamente do histórico de conversas.

## 6. REGRAS DE ORQUESTRAÇÃO

Antes de escolher Ruflo, Oh My ClaudeCode ou outra ferramenta:

1. Compare os recursos.
2. Verifique compatibilidade com o ambiente.
3. Verifique licenças.
4. Verifique requisitos de instalação.
5. Verifique dependências.
6. Verifique custos e consumo adicional.
7. Verifique se existe sobreposição com subagentes nativos do Claude Code.

Escolha uma solução principal.

Não instale vários orquestradores concorrentes.

Não introduza complexidade só para ter muitos agentes.

## 7. CONTROLE DE CUSTOS

A utilização de vários agentes pode aumentar o consumo de tokens e recursos.

Portanto:
- Utilize agentes sob demanda.
- Evite agentes permanentemente ativos.
- Não repita análises sem necessidade.
- Compartilhe contexto relevante.
- Prefira delegação de tarefas bem delimitadas.
- Utilize paralelismo somente quando gerar ganho real.
- Monitore consumo.
- Não contratar APIs ou serviços adicionais sem autorização.

O teto operacional do VÉRION permanece em R$ 500/mês.

## 8. INSTALAÇÃO E VALIDAÇÃO

Para cada ferramenta selecionada:

1. Leia o README.
2. Leia a documentação oficial.
3. Inspecione o código de instalação.
4. Verifique a licença.
5. Identifique dependências.
6. Instale somente o necessário.
7. Confirme a compatibilidade.
8. Faça um teste real.
9. Documente a configuração.

Atualize:

`docs/engineering/AGENT_SKILLS.md`

Crie também:

`docs/engineering/AGENT_ARCHITECTURE.md`

Esse documento deve explicar:
- Hierarquia de agentes.
- Responsabilidades.
- Skills utilizadas por cada papel.
- Regras de delegação.
- Comunicação.
- Memória.
- Controle de custos.
- Critérios de revisão.
- Fluxos de execução.

## 9. RESULTADO ESPERADO

Ao concluir, apresente:

- Skills instaladas.
- Subagentes disponíveis.
- Orquestrador escolhido.
- Ferramentas rejeitadas e justificativas.
- Arquitetura da equipe.
- Fluxo de delegação.
- Configuração da memória.
- Custos e dependências.
- Teste de funcionamento.

Depois, retome o desenvolvimento do VÉRION utilizando a equipe de especialistas.

**OBJETIVO: transformar Claude em um ambiente de desenvolvimento coordenado por um CTO virtual, com especialistas independentes, subagentes sob demanda, memória estruturada, revisão cruzada e execução profissional.**