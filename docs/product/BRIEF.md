# Brief do Produto — Plataforma de Produção Audiovisual com IA para Imóveis Premium

**Documento de visão e orientação inicial para desenvolvimento no Claude**  
**Status:** briefing estratégico inicial — decisões registradas até 31/10/2026  
**Mercado inicial:** Brasil  
**Orçamento operacional inicial:** até R$ 500/mês

---

## 1. Visão do produto

Criar uma plataforma SaaS/serviço que usa inteligência artificial e automações para encontrar imóveis premium anunciados no Airbnb, identificar oportunidades comerciais e oferecer produção de vídeos imobiliários de alto padrão a partir de fotos e vídeos existentes.

A plataforma deve operar como uma empresa audiovisual imobiliária altamente automatizada: identifica oportunidades, analisa imóveis, estima o potencial comercial, prepara demonstrações, prospecta clientes por canais legítimos, conduz o processo comercial, recebe pagamentos, coordena a produção, acompanha aprovações e entrega os vídeos.

O produto não deve ser apenas um editor de vídeos. Seu diferencial é combinar:

- Inteligência comercial para encontrar imóveis com potencial.
- Produção audiovisual assistida por IA, com forte preocupação com fidelidade ao imóvel real.
- Automação de prospecção, atendimento e follow-up.
- Precificação baseada em custos, complexidade e margem.
- Portal do cliente para acompanhar o projeto.
- Operação supervisionada por agentes especializados e um agente supervisor.

## 2. Princípios obrigatórios de produto e negócio

1. **Gastar o mínimo possível.** O orçamento de até R$ 500/mês é um teto inicial, não uma meta de gasto. Não gastar todo o orçamento sem necessidade.
2. **Priorizar ferramentas gratuitas, open-source e planos gratuitos**, desde que atendam aos requisitos de qualidade, segurança e confiabilidade.
3. **Qualidade audiovisual acima de quantidade.** Não produzir vídeos que inventem características do imóvel ou induzam o cliente a erro.
4. **MVP antes de plataforma completa.** Validar primeiro se é possível produzir um vídeo comercialmente convincente e vendê-lo com margem positiva.
5. **Arquitetura evolutiva.** Começar simples, modular e barato, sem bloquear expansão futura para equipes e múltiplas empresas.
6. **Automação com limites.** Agentes não podem assumir compromissos financeiros, contratar serviços, elevar orçamento ou conceder descontos fora das regras aprovadas.
7. **Transparência.** Diferenciar imagens reais, imagens editadas, reconstruções e simulações geradas por IA.
8. **Medição econômica.** Registrar custo por lead, custo por demonstração, custo por vídeo, tempo de produção, conversão, receita e margem.
9. **Não construir funcionalidades complexas antes de comprovar que elas geram valor.**
10. **Claude deve questionar decisões tecnicamente inviáveis ou economicamente ruins**, em vez de apenas concordar com o briefing.

## 3. Público, mercado e oferta

### Mercado inicial
- Brasil inteiro.
- Imóveis anunciados no Airbnb.
- Diária mínima de referência: **R$ 2.000**.
- Todos os tipos de imóveis de alto padrão, sem restringir a casas, apartamentos, mansões ou outras categorias.

### Oferta principal
Produção de vídeos imobiliários premium a partir de fotos e vídeos existentes do imóvel.

### Oferta adicional
Captação ou inclusão de imagens reais de drone quando houver material legítimo disponível e autorizado. Não apresentar imagens geradas por IA como captação real de drone.

### Formato e estilo dos vídeos
- Formato prioritário: horizontal.
- Duração variável conforme imóvel, objetivo e material disponível.
- Linguagem cinematográfica, sofisticada e adaptada à arquitetura e ao público de cada imóvel.
- Assinatura discreta da marca da plataforma e identidade visual adaptada ao imóvel.

### Método de produção
Método híbrido, escolhendo conforme o caso:
- Reconstrução espacial/3D, quando viável.
- Image-to-video.
- Edição e composição de vídeos originais.
- Combinação das técnicas acima.

Não presumir que qualquer ferramenta consegue reconstruir fielmente um imóvel a partir de poucas fotos. A solução deve testar e documentar as limitações de cada método.

## 4. Descoberta e avaliação de imóveis

A fonte inicial de descoberta será o Airbnb. A plataforma deverá analisar até **50 imóveis por dia**, dentro dos limites técnicos, legais e econômicos.

### Filtros iniciais
- Brasil.
- Diária anunciada a partir de R$ 2.000.
- Perfil de alto padrão.
- Fotos e informações suficientes para avaliação.
- Potencial de valorização por meio de produção audiovisual.

### Avaliação
Criar um sistema de pontuação multidimensional de 0 a 100, considerando, no mínimo:
- Padrão aparente do imóvel.
- Qualidade e quantidade das fotos e vídeos disponíveis.
- Potencial visual e arquitetônico.
- Quantidade de ambientes e variedade de cenas possíveis.
- Potencial comercial.
- Adequação ao serviço oferecido.
- Complexidade e custo provável de produção.
- Disponibilidade de contato comercial legítimo.
- Risco de baixa fidelidade ou falta de material.

Somente imóveis com nota **75 ou superior** avançam para a etapa de demonstração, salvo exceção autorizada.

O ranking deve explicar por que cada imóvel recebeu sua nota. Não inventar dados ausentes; sinalizar estimativas e incertezas.

### Limite de operação
Meta inicial de até 50 imóveis analisados por dia. O sistema deve respeitar limites de acesso, custos, termos das plataformas e disponibilidade dos dados. Não implementar scraping agressivo, login automatizado ou mecanismos para contornar proteções do Airbnb. Avaliar primeiro meios permitidos de acesso aos dados, fontes públicas e eventuais APIs/licenças autorizadas.

## 5. Demonstrações comerciais

Modelo **Demo First**: sempre que fizer sentido, a plataforma prepara uma demonstração antes de apresentar a oferta comercial.

- A IA decide o tamanho da demonstração conforme o potencial do imóvel e o custo estimado.
- A demonstração deve conter marca d'água.
- O uso de fotos e vídeos de terceiros exige autorização ou outra base jurídica adequada.
- A demonstração não deve ser publicada ou distribuída como se fosse material oficial do imóvel.
- Registrar custo da demonstração e resultado comercial.

## 6. Fidelidade visual e controle de qualidade

Este é um dos requisitos mais importantes do produto.

A IA deve:
- Analisar o material de origem.
- Identificar elementos arquitetônicos, móveis, acabamentos, proporções e disposição dos ambientes.
- Reconstruir espacialmente quando houver material suficiente e a técnica for confiável.
- Preferir movimentos de câmera conservadores quando houver risco de distorção.
- Detectar alterações visuais não sustentadas pelo material de origem.
- Validar automaticamente o vídeo e gerar relatório de qualidade.

**Regra de rejeição:** se uma cena não puder ser reproduzida com segurança e fidelidade, ela deve ser rejeitada, registrada no relatório e excluída do vídeo final. Não inventar cômodos, janelas, portas, vistas, móveis, acabamentos, dimensões ou características.

Quando o resultado for conceitual ou uma simulação, isso deve ser informado claramente. Nunca prometer um tour virtual fiel se a reconstrução não for confiável.

## 7. Prospecção e aquisição de clientes

### Estratégia
Máquina de aquisição completa, evoluindo por etapas:
- Prospecção ativa.
- Programa de indicações.
- Conteúdo para redes sociais.
- Campanhas e experimentos comerciais.
- Análise de conversão e custo de aquisição.
- Otimização das estratégias com base em resultados.

### Canal principal de contato
WhatsApp.

### Origem dos contatos
Pesquisar contatos públicos associados ao imóvel ou ao anfitrião, quando houver uma fonte legítima. Não presumir que o Airbnb fornece telefone ou autoriza contato comercial externo.

### Autonomia
A IA poderá conduzir mensagens e follow-ups automaticamente, desde que:
- O contato seja legítimo e compatível com a legislação aplicável.
- Haja base adequada para a comunicação.
- A abordagem seja individualizada e respeitosa.
- Exista mecanismo simples de descadastramento/opt-out.
- Haja limites de frequência, horário, tentativas e bloqueio de contatos que não desejem receber mensagens.
- Todas as mensagens e decisões sejam registradas.

Não realizar disparos indiscriminados, spam ou extração indevida de dados pessoais. Não usar automação não autorizada dentro do Airbnb.

### Processo comercial
Modelo híbrido inteligente:
- A IA conduz etapas padronizadas.
- A IA prepara propostas e acompanha respostas.
- O usuário é acionado para negociações especiais, exceções, dúvidas fora do escopo ou situações de risco.
- A IA não pode prometer resultados financeiros ou aumento de reservas como certeza.

## 8. Precificação e pagamentos

### Precificação
A IA calcula um preço recomendado individualmente, considerando:
- Padrão e tamanho do imóvel.
- Quantidade de ambientes e cenas.
- Quantidade e qualidade do material recebido.
- Complexidade de reconstrução e edição.
- Duração e formato do vídeo.
- Prazo e urgência.
- Custos de processamento, ferramentas e mão de obra.
- Margem mínima de lucro configurada pelo usuário.
- Potencial comercial, sem usar esse fator para justificar preços arbitrários.

A IA deve apresentar memória de cálculo e justificativa do preço. O usuário configura margem mínima, limites de desconto e condições especiais. A IA não pode vender abaixo do piso aprovado.

### Pagamentos
Meios aceitos:
- PIX.
- Cartão.
- Boleto.

Condição padrão:
- 50% na contratação.
- 50% na entrega.

A integração de pagamento deverá usar um provedor adequado ao Brasil, escolhido após comparar tarifas, custos fixos, APIs, facilidade de integração e suporte a PIX/cartão/boleto. Não escolher um fornecedor sem análise de custo total.

A plataforma deve acompanhar pagamentos, confirmar recebimentos por integração confiável e sinalizar inadimplência. Não liberar automaticamente etapas que dependam de pagamento confirmado.

## 9. Jornada do cliente e entrega

O cliente terá uma experiência automatizada e um portal próprio.

### Portal do cliente
- Acesso por link seguro ou autenticação.
- Status do projeto.
- Informações do imóvel e do pedido.
- Pagamentos e comprovantes.
- Upload de fotos e vídeos.
- Prévia do vídeo.
- Aprovação ou solicitação de ajustes.
- Histórico de versões e mensagens.
- Entrega final e acesso aos arquivos.

### Comunicação
WhatsApp para notificações e comunicação; portal para acompanhamento e gerenciamento do projeto.

### Fluxo de produção
1. Cliente contrata.
2. Pagamento inicial de 50% é confirmado.
3. Cliente envia ou autoriza o uso dos materiais.
4. IA analisa o material e propõe o plano de produção.
5. Agentes produzem o vídeo.
6. Sistema realiza auditoria de qualidade e fidelidade.
7. O usuário faz controle de qualidade enquanto a operação estiver em fase inicial.
8. Cliente recebe prévia e aprova ou solicita ajustes.
9. Estão incluídas **duas rodadas de ajustes**.
10. Cliente paga os 50% restantes.
11. Plataforma libera a entrega final.
12. Arquivos, versões e histórico ficam disponíveis no portal.

Não confundir aprovação do cliente com validação técnica. O controle de qualidade interno continua necessário.

## 10. Autonomia e agentes de IA

### Arquitetura desejada
Arquitetura hierárquica com um agente supervisor central e agentes especialistas. A arquitetura deve ser implementada gradualmente, sem criar dezenas de agentes desnecessários no MVP.

### Supervisor — CEO virtual
Responsabilidades:
- Coordenar os agentes.
- Definir prioridades dentro das regras.
- Analisar indicadores.
- Identificar gargalos e oportunidades.
- Sugerir estratégias e experimentos.
- Acompanhar orçamento e custos.
- Acionar o usuário em exceções.
- Gerar relatório diário.

O supervisor não tem autoridade para ultrapassar orçamento, contratar serviços ou alterar regras comerciais sem autorização.

### Especialistas possíveis
Criar somente quando necessário:
1. **Agente de descoberta:** encontra imóveis e coleta informações permitidas.
2. **Agente de análise:** avalia imóvel, material e pontuação.
3. **Agente audiovisual:** prepara plano de cenas e coordena geração/edição.
4. **Agente de qualidade:** verifica fidelidade, consistência e qualidade.
5. **Agente comercial:** prepara propostas e conduz contatos autorizados.
6. **Agente de CRM:** mantém histórico, tarefas e pipeline.
7. **Agente financeiro:** acompanha custos, pagamentos e margens.
8. **Agente de atendimento:** acompanha o cliente e solicita aprovações.

Memória compartilhada deve ser estruturada em banco de dados, com logs e permissões; não depender apenas de memória textual dos modelos.

## 11. Aprovação e autonomia progressiva

Modelo de autonomia progressiva:
- No início, o usuário aprova todas as produções antes do envio ao cliente.
- O sistema registra avaliações, correções, falhas e resultados.
- Após evidências suficientes de consistência, a IA poderá receber autonomia limitada para aprovar casos de baixo risco.
- Casos fora dos critérios, baixa confiança, alterações visuais suspeitas ou problemas de qualidade continuam exigindo aprovação humana.
- Toda decisão automática deve ser auditável e reversível.

## 12. Dashboard e operação

A visão final é uma plataforma completa, mas não é necessário desenvolver tudo no MVP.

Módulos planejados:
- Dashboard executivo.
- CRM e pipeline comercial.
- Central de agentes e tarefas.
- Inteligência de imóveis.
- Estúdio audiovisual.
- Portal do cliente.
- Financeiro e pagamentos.
- Relatórios e métricas.
- Configurações de orçamento, políticas e permissões.

Alertas no próprio dashboard e pelo Telegram.

Relatório diário do supervisor deve incluir:
- Imóveis analisados.
- Imóveis aprovados/reprovados e motivos.
- Leads encontrados e contatados.
- Respostas e conversões.
- Demonstrações geradas.
- Vídeos em produção e entregues.
- Receita, custos e margem.
- Consumo do orçamento mensal.
- Problemas, riscos e decisões que exigem aprovação.

## 13. Orçamento e controle financeiro

**Teto operacional inicial: R$ 500 por mês.**

Regras:
- O orçamento é um limite máximo, não uma obrigação de gastar.
- Priorizar soluções gratuitas e open-source.
- Avaliar custos variáveis de geração de vídeo, APIs, armazenamento, mensagens e processamento.
- Registrar custo estimado e real de cada operação.
- Definir limites por lead, demonstração e produção.
- Interromper automaticamente operações que excedam os limites.
- Pedir autorização antes de contratar serviço pago, elevar plano, comprar créditos ou aumentar orçamento.
- Não manter GPU alugada continuamente se processamento sob demanda for mais econômico.
- Comparar GPU local, cloud sob demanda e serviços externos.
- Considerar que open-source não significa custo zero: GPU, energia, hospedagem, manutenção e tempo também custam.

Se a qualidade comercial necessária não couber no orçamento, apresentar alternativas e evidências. Não esconder limitações nem sacrificar a fidelidade visual para caber artificialmente no orçamento.

## 14. Tecnologia e infraestrutura

### Diretrizes já escolhidas
- Preferência por soluções open-source para geração audiovisual, quando a qualidade for adequada.
- Infraestrutura híbrida: dashboard, CRM e agentes na nuvem; processamento audiovisual em GPU local ou servidor especializado, conforme custo e necessidade.
- Experiência via navegador, com layout responsivo para celular.
- Não desenvolver aplicativo nativo próprio no início.
- Começar como ferramenta individual, mas preparar arquitetura para equipes e múltiplas empresas no futuro.

### Diretrizes técnicas para Claude
Antes de escolher stack, comparar alternativas por:
- Custo total mensal.
- Custo variável por vídeo.
- Qualidade real de saída.
- Facilidade de integração.
- Licença comercial.
- Requisitos de GPU e memória.
- Segurança e manutenção.
- Possibilidade de migração.

Não assumir antecipadamente uma stack específica. Apresentar recomendação enxuta e justificativa. Evitar microserviços e complexidade prematura. Preferir monólito modular no MVP, com interfaces claras para serviços externos.

## 15. Armazenamento, retenção e privacidade

Modelo de armazenamento híbrido:
- Banco de dados central para usuários, imóveis, leads, projetos, pagamentos, tarefas, logs e metadados.
- Storage especializado para fotos, vídeos, prévias e entregas.

Retenção: **indefinidamente, até exclusão manual**.

Requisitos:
- Exclusão manual pelo usuário.
- Controle de acesso e permissões.
- Links temporários para arquivos privados.
- Backup e recuperação.
- Registro de operações.
- Política para exclusão de dados pessoais e solicitações de titulares.
- Separação lógica de dados entre empresas quando houver suporte multiempresa.
- Armazenar somente o necessário.
- Definir política para materiais de clientes, demonstrações e arquivos de terceiros.

Retenção indefinida não significa ausência de controles legais: oferecer exclusão, respeitar solicitações e definir tratamento de dados conforme legislação aplicável.

## 16. Marca e identidade visual

Posicionamento:
- Híbrido: luxo imobiliário + tecnologia + produção cinematográfica.
- Sofisticado, moderno, confiável e internacionalizável.
- Nome inventado, curto, simples e com potencial de marca internacional.
- Ainda não há nome definitivo; não inventar disponibilidade de domínio ou registro de marca.

A identidade visual deverá funcionar em:
- Dashboard.
- Portal do cliente.
- Propostas comerciais.
- Marca d'água das demonstrações.
- Vídeos finais.
- Redes sociais e materiais comerciais.

## 17. Métricas essenciais

No MVP, acompanhar no mínimo:
- Imóveis pesquisados por dia.
- Percentual aprovado na triagem.
- Leads com contato legítimo disponível.
- Taxa de resposta.
- Taxa de demonstração para contratação.
- Custo por lead.
- Custo por demonstração.
- Custo por vídeo finalizado.
- Tempo médio de produção.
- Taxa de aprovação na primeira versão.
- Número médio de rodadas de ajuste.
- Receita por vídeo.
- Margem bruta por vídeo.
- Receita mensal.
- Consumo do teto mensal de R$ 500.

## 18. Roadmap recomendado

### Fase 0 — Viabilidade técnica e econômica
Antes de desenvolver o produto inteiro:
- Testar 2–3 fluxos de produção com materiais de imóveis reais autorizados.
- Comparar ferramentas open-source e serviços comerciais.
- Medir fidelidade visual, qualidade, tempo e custo.
- Identificar o menor conjunto de ferramentas que entrega resultado vendável.
- Definir critérios objetivos de aprovação e rejeição.

**Critério de saída:** demonstrar que existe um fluxo reproduzível para criar vídeos de qualidade comercial, com custos conhecidos e sem distorções relevantes.

### Fase 1 — MVP audiovisual
- Upload de materiais.
- Análise básica do imóvel.
- Planejamento de cenas.
- Produção e edição assistidas por IA.
- Auditoria de qualidade.
- Revisão humana.
- Exportação do vídeo e relatório.

### Fase 2 — MVP comercial
- Cadastro de imóveis e leads.
- Pontuação e priorização.
- CRM básico.
- Propostas e precificação assistida.
- WhatsApp com abordagem controlada e autorizada.
- Registro de follow-ups.
- Métricas comerciais.

### Fase 3 — Pagamento e portal
- Integração com provedor de pagamento.
- Portal do cliente.
- Upload e aprovação.
- Solicitações de ajustes.
- Entrega final.
- Histórico e retenção.

### Fase 4 — Agentes e autonomia
- Supervisor.
- Especialistas necessários.
- Relatório diário.
- Otimização de campanhas.
- Autonomia progressiva baseada em evidências.
- Expansão para equipes e múltiplas empresas.

**Importante:** as fases podem ser ajustadas se os testes mostrarem que outro caminho é mais barato ou reduz risco. Não desenvolver todas as fases antes de validar a anterior.

## 19. Requisitos de segurança, conformidade e confiança

- Respeitar termos de uso e mecanismos de acesso do Airbnb.
- Não contornar autenticação, CAPTCHA, bloqueios ou proteções.
- Não coletar ou usar dados pessoais sem base adequada.
- Não enviar spam ou mensagens em massa não autorizadas.
- Respeitar opt-out e limites de comunicação.
- Obter autorização para usar fotos e vídeos em demonstrações e produções comerciais.
- Não representar simulações como imagens reais.
- Não prometer aumento de reservas, diária ou receita como resultado garantido.
- Proteger credenciais e tokens em gerenciador de segredos; nunca expor em frontend ou repositório.
- Registrar ações importantes dos agentes.
- Exigir aprovação humana para gastos, compromissos e exceções não autorizadas.
- Aplicar princípios da LGPD e revisar obrigações legais aplicáveis.

## 20. Instruções específicas para Claude

Atue como arquiteto de software, engenheiro de IA, especialista em automação e consultor de produto. Transforme este briefing em um plano de implementação realista.

### Não faça
- Não comece construindo a plataforma inteira.
- Não escolha tecnologias caras por conveniência.
- Não presuma que ferramentas open-source geram vídeos premium sem custo de GPU.
- Não prometa reconstrução 3D fiel a partir de qualquer conjunto de fotos.
- Não implemente scraping ou automação que viole termos de serviços.
- Não crie agentes só para aumentar a quantidade de agentes.
- Não adicione funcionalidades que não contribuam para validar o MVP.
- Não ultrapasse o teto mensal sem autorização.
- Não invente preços, APIs, disponibilidade, licenças ou capacidades técnicas.

### Faça primeiro
1. Leia e critique o briefing.
2. Identifique contradições, riscos, dependências e pontos ainda não comprovados.
3. Faça uma análise de viabilidade técnica e econômica considerando o teto de R$ 500/mês.
4. Pesquise/compare ferramentas e modelos atuais quando necessário, verificando licenças comerciais, preços e requisitos de hardware.
5. Defina o MVP mínimo, com escopo explícito do que entra e do que fica fora.
6. Proponha arquitetura simples, modular e evolutiva.
7. Apresente stack recomendada e alternativas mais baratas.
8. Estime custos fixos e variáveis, incluindo custo estimado por vídeo em cenários diferentes.
9. Defina critérios de qualidade visual e testes de validação.
10. Crie um roadmap de implementação dividido em entregas pequenas e verificáveis.

### Formato da primeira resposta esperada do Claude
- Resumo executivo.
- Avaliação crítica da ideia.
- Principais riscos e limitações.
- Escopo do MVP.
- Arquitetura proposta.
- Stack e ferramentas recomendadas, com justificativas.
- Estimativa de custos mensais e por vídeo.
- Plano de validação audiovisual.
- Roadmap por etapas.
- Decisões que precisam de confirmação do usuário.

**Não comece a programar ainda.** Primeiro apresente a análise e o plano. Depois, aguarde aprovação para iniciar a implementação.

---

## 21. Decisões consolidadas — resumo rápido

| Tema | Decisão |
|---|---|
| Mercado | Brasil inteiro |
| Fonte inicial de imóveis | Airbnb, respeitando termos e acesso permitido |
| Perfil | Imóveis premium, diária mínima de R$ 2.000 |
| Volume de análise | Até 50 imóveis/dia |
| Nota mínima para demonstração | 75/100 |
| Oferta principal | Produção de vídeos |
| Oferta adicional | Drone, quando houver captação real autorizada |
| Método audiovisual | Híbrido: 3D, image-to-video e vídeos originais |
| Formato | Horizontal, duração variável |
| Demonstração | IA define tamanho; com marca d'água |
| Estratégia comercial | Demo First |
| Canal de contato | WhatsApp |
| Aquisição | Prospecção, indicações e marketing |
| Processo comercial | IA conduz; humano em exceções |
| Receita | Pagamento por vídeo |
| Precificação | IA calcula preço com piso de margem |
| Pagamento | PIX, cartão e boleto |
| Condição | 50% contratação / 50% entrega |
| Experiência do cliente | Portal automatizado + WhatsApp |
| Ajustes incluídos | Duas rodadas |
| Aprovação | Autonomia progressiva; revisão humana no início |
| Dashboard final | Executivo, CRM, agentes, estúdio e financeiro |
| Alertas | Dashboard + Telegram |
| Supervisor | CEO virtual, relatório diário |
| Arquitetura de agentes | Hierárquica, com especialistas necessários |
| Orçamento | Até R$ 500/mês; gastar menos sempre que possível |
| Tecnologia audiovisual | Preferência open-source, priorizando qualidade |
| Infraestrutura | Híbrida, com GPU sob demanda quando vantajoso |
| Usuários | Começa individual; preparado para equipes |
| Acesso | Navegador e mobile responsivo |
| Armazenamento | Híbrido |
| Retenção | Indefinida até exclusão manual |
| Marca | Luxo + tecnologia + cinema |
| Lançamento | MVP enxuto e evolutivo |
| Primeira prioridade | Validar motor audiovisual |

## 22. Recomendação final

A maior incerteza do negócio não é o dashboard, o CRM ou a quantidade de agentes: é conseguir transformar fotos e vídeos de imóveis em um resultado audiovisual realmente convincente, fiel e economicamente viável.

Portanto, a primeira entrega deve ser um **teste de viabilidade audiovisual**, e não uma plataforma completa. O sucesso desse teste deve determinar quais tecnologias, custos e automações fazem sentido.

A regra de decisão para todo o projeto é:

**Não construir porque é interessante. Construir porque melhora a qualidade, reduz custos, aumenta conversão ou viabiliza uma operação que possa gerar lucro.**
