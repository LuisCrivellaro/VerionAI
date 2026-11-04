# VÉRION
### AI-Powered Real Estate Visual Experience

**Imóveis extraordinários. Experiências visuais.**

O VÉRION é uma plataforma de inteligência artificial especializada em imóveis premium, que combina prospecção inteligente, análise imobiliária e produção audiovisual cinematográfica.

Seu objetivo é transformar fotos e vídeos de imóveis em experiências visuais de alto padrão, enquanto automatiza o processo comercial, desde a identificação de oportunidades até a entrega final ao cliente.

---

## 🎯 Visão do projeto

Construir uma operação inteligente e altamente automatizada capaz de:

- Encontrar imóveis premium anunciados no Airbnb.
- Analisar características e potencial comercial dos imóveis.
- Identificar oportunidades de valorização audiovisual.
- Criar demonstrações personalizadas com inteligência artificial.
- Prospectar proprietários por canais comerciais legítimos.
- Gerenciar propostas, clientes e pagamentos.
- Produzir vídeos imobiliários realistas e cinematográficos.
- Automatizar aprovações, ajustes e entregas.

## ✨ Principais funcionalidades planejadas

| Módulo | Funcionalidade |
|---|---|
| Real Estate Intelligence | Descoberta e análise de imóveis |
| AI Scoring | Pontuação de oportunidades de 0 a 100 |
| AI Video Studio | Produção audiovisual com IA |
| AI Agents | Agentes especializados e supervisor central |
| Smart Prospecting | Prospecção e acompanhamento comercial |
| CRM | Gestão de leads e clientes |
| Smart Pricing | Precificação inteligente |
| Financial | Pagamentos, custos e rentabilidade |
| Client Portal | Acompanhamento e aprovação dos projetos |
| Analytics | Indicadores e relatórios operacionais |

## 🤖 Arquitetura de inteligência artificial

O projeto utilizará uma arquitetura hierárquica, coordenada por um agente supervisor, responsável por organizar e acompanhar os agentes especializados.

Agentes previstos:

1. Property Discovery Agent
2. Property Analysis Agent
3. Video Production Agent
4. Quality Control Agent
5. Sales Agent
6. CRM Agent
7. Financial Agent
8. Client Support Agent

A implementação será gradual, priorizando os agentes realmente necessários para o MVP.

## 🎬 Produção audiovisual

O VÉRION utilizará uma abordagem híbrida de produção:

- Image-to-video.
- Reconstrução espacial e 3D quando viável.
- Edição e composição de vídeos originais.
- Movimentos cinematográficos de câmera.
- Tratamento e aprimoramento visual.

**Fidelidade é prioridade:** cenas que não possam ser reproduzidas com segurança a partir do material original serão rejeitadas.

## 💰 Modelo de negócio

- Cobrança por vídeo produzido.
- Precificação calculada por IA.
- Pagamentos via PIX, cartão e boleto.
- 50% na contratação e 50% na entrega.
- Duas rodadas de ajustes incluídas.
- Demonstrações personalizadas com marca d'água.

## 🧠 Estratégia de desenvolvimento

O desenvolvimento seguirá uma abordagem MVP-first.

A primeira prioridade será validar a capacidade de produzir vídeos imobiliários de qualidade comercial, com fidelidade visual e custos sustentáveis.

Etapas planejadas:

1. Validação técnica e econômica da produção audiovisual.
2. Desenvolvimento do MVP audiovisual.
3. Implementação da inteligência imobiliária e CRM.
4. Integração comercial e financeira.
5. Portal do cliente.
6. Expansão dos agentes e automações.
7. Evolução para uma plataforma completa.

## 💻 Infraestrutura e tecnologia

Diretrizes técnicas:

- Preferência por ferramentas open-source.
- Arquitetura híbrida.
- Processamento audiovisual em GPU local ou cloud sob demanda.
- Dashboard web responsivo.
- Banco de dados centralizado.
- Storage especializado para arquivos de mídia.
- Integrações por APIs.
- Arquitetura preparada para expansão multiempresa.

As tecnologias definitivas serão escolhidas após análise de viabilidade, qualidade, custos e requisitos de infraestrutura.

### Harness da Fase 0: como rodar

Requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/) e ffmpeg no PATH.

```bash
uv sync --frozen --extra gemini   # o extra "gemini" é necessário para os testes dos adaptadores (Omni/Veo, todos com mock)
uv run pytest -o addopts= -q      # nível padrão: ~1124 testes, sem nenhuma chamada paga
uv run ruff check . && uv run mypy src
uv run verion --help              # CLI (roteiro em examples/roteiro.example.toml)
```

Fotos reais e o banco do harness ficam em `data/` (fora do git). Chamadas pagas são bloqueadas por padrão.

## 💸 Orçamento inicial

**Teto operacional: R$ 500/mês.**

O objetivo é gastar o mínimo possível sem comprometer a qualidade do produto.

Princípios:
- Priorizar soluções gratuitas e open-source.
- Evitar infraestrutura permanente desnecessária.
- Controlar custos por lead, demonstração e vídeo.
- Utilizar GPU sob demanda quando economicamente vantajoso.
- Exigir autorização para despesas adicionais.

## 🔐 Segurança e responsabilidade

- Respeito à LGPD.
- Uso autorizado de imagens e vídeos.
- Respeito aos termos e mecanismos de acesso das plataformas utilizadas.
- Proteção de credenciais e dados dos clientes.
- Controle de acesso e histórico de operações.
- Transparência sobre imagens geradas por IA.
- Comunicação comercial responsável.

## 📊 Objetivo operacional

| Indicador | Meta inicial |
|---|---:|
| Imóveis analisados | Até 50/dia |
| Pontuação mínima para demonstração | 75/100 |
| Orçamento mensal | Até R$ 500 |
| Rodadas de ajustes | 2 |
| Retenção de materiais | Até exclusão manual |

## 🚀 Filosofia do VÉRION

O VÉRION não pretende ser apenas mais uma ferramenta de geração de vídeos.

A proposta é construir uma operação inteligente que conecta tecnologia, inteligência artificial e mercado imobiliário premium.

**Construir com eficiência. Automatizar com inteligência. Entregar qualidade.**

---

*VÉRION — AI-Powered Real Estate Visual Experience.*
