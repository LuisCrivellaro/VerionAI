# VÉRION — Análise crítica e plano do MVP

31/10/2026. Cópia do documento original no claude.ai: https://claude.ai/artifact/95sdC6W42pbfEdrkh5N13E
(os dois diagramas estão descritos em texto nesta versão).

## Resumo executivo

O VÉRION é viável como **serviço**, mas não como a plataforma autônoma descrita no brief. A geração de vídeo
cabe no orçamento: cerca de R$ 73 a R$ 154 por vídeo de 60 s nas APIs comerciais de faixa média, ou menos de
R$ 10 com modelo open-source em GPU alugada. O que pode quebrar o negócio é a aquisição de clientes, não a tecnologia.

Três premissas do brief esbarram em regras externas confirmadas:

- Os Termos do Airbnb proíbem coleta automatizada, então a descoberta de 50 imóveis/dia não pode ser automatizada lá.
- O WhatsApp exige opt-in antes de qualquer mensagem iniciada pela empresa, então a prospecção ativa a frio nesse canal é vetada.
- Pelas respostas da comunidade oficial do Airbnb, o anúncio aceita só fotos. O vídeo serve ao anfitrião fora do Airbnb
  (Instagram, site, reserva direta), o que enfraquece a proposta de valor.

Recomendação: rodar a Fase 0 (validação audiovisual, até R$ 350) em paralelo com 10 conversas comerciais. Vender 3 vídeos
manualmente antes de escrever qualquer linha de dashboard, CRM ou agente. Público sugerido: gestoras de temporada e
imobiliárias de alto padrão, não anfitriões individuais.

## Avaliação crítica e contradições do brief

O brief acerta no essencial: validar o motor audiovisual primeiro e tratar fidelidade como regra. Mas ele descreve uma
empresa inteira antes de existir um cliente.

| # | O brief pede | Conflita com | Proposta |
|---|---|---|---|
| 1 | Analisar até 50 imóveis/dia no Airbnb | Termos do Airbnb proíbem bots, crawlers e scrapers | Curadoria manual (10–20/dia) ou fonte de dados licenciada |
| 2 | Demo First com fotos do anúncio | O próprio brief exige autorização para usar fotos de terceiros | Pedir permissão antes da demo; a permissão vira o opt-in |
| 3 | WhatsApp como canal principal de prospecção | Política da Meta exige opt-in antes de mensagem iniciada pela empresa | Primeiro contato por canal comercial público (site, e-mail, Instagram da gestora) |
| 4 | Vídeo para valorizar anúncio do Airbnb | Galeria do anúncio aceita só fotos (comunidade Airbnb) | Vender para uso em Instagram, site e reserva direta; validar se isso vale dinheiro |
| 5 | Reconstrução 3D quando viável | Gaussian splatting precisa de muitas vistas; com poucas fotos a qualidade cai muito | 3D só quando houver vídeo de celular do imóvel |
| 6 | Validação automática de fidelidade | Image-to-video gerativo inventa geometria por natureza; não existe detecção automática confiável pronta | QC humano obrigatório no MVP; métricas automáticas só como triagem |
| 7 | CEO virtual + 8 agentes | Orçamento de R$ 500 e regra "MVP antes de plataforma" | Zero agentes de produto até haver operação repetida para automatizar |
| 8 | Retenção indefinida | Princípio de necessidade da LGPD | Prazo padrão (ex.: 12 meses) com renovação pelo cliente |
| 9 | Preço com fator "potencial comercial" | Regra do próprio brief contra preço arbitrário | Tabela por faixa de cenas/duração; IA só sugere a faixa |

**Concorrência.** Foto-para-vídeo com IA já é categoria lotada lá fora. Há serviços a partir de US$ 89 por vídeo,
SaaS dedicados como o Reel-E (plano Pro de US$ 599/mês) e ferramentas que geram vídeo colando o link do Airbnb.
O diferencial possível é garantia de fidelidade, acabamento cinematográfico com revisão humana, atendimento em
português e relacionamento com gestoras.

**Concorrente real no Brasil.** Um videomaker com drone em SP/RJ cobra cerca de R$ 700–1.200 por fotos + vídeo de 2 min,
e R$ 1.500–3.000 no pacote premium. O VÉRION precisa ganhar em preço, prazo e alcance remoto, porque não ganha em autenticidade.

## Principais riscos

| Risco | Status | Impacto | Mitigação |
|---|---|---|---|
| Disposição a pagar baixa, porque o vídeo não entra no anúncio do Airbnb | Hipótese | Mata o negócio | 10 conversas e 3 vendas manuais antes do MVP |
| Mercado pequeno: poucos anúncios no Brasil com diária acima de R$ 2.000 | Hipótese | Limita a escala | Amostragem manual em 5 destinos; incluir imóveis à venda |
| Distorção visual (janela, móvel ou vista inventados) | Confirmado | Risco jurídico e de reputação | Movimentos curtos, QC humano, 2,5D quando houver dúvida |
| Bloqueio do número de WhatsApp por mensagem sem opt-in | Confirmado (política da Meta) | Perde o canal principal | Opt-in antes de qualquer mensagem iniciada pela empresa |
| Uso de fotos de terceiros sem autorização | Confirmado | Direito autoral | Permissão explícita antes da demo |
| Custo de demos sem conversão (Demo First) | Hipótese | Queima o orçamento | Demo curta (10–15 s) e barata (2,5D ou open-source) |
| Preço de GPU e APIs muda sem aviso | Confirmado: RunPod subiu o Secure Cloud em até 20% em 15/10/2026 | Margem | Provedores trocáveis; registro de custo por chamada |
| Tempo humano de edição e QC maior que o custo de geração | Hipótese provável | Custo real no tempo do usuário | Medir horas por vídeo na Fase 0 |
| Câmbio: APIs cobradas em dólar | Confirmado (dólar a R$ 5,08 em 16/10/2026) | Variação de custo | Preço de venda revisado por trimestre |

Com 20–30 fotos de anúncio, não existe tour fiel. Os métodos disponíveis criam **movimento sobre cada foto**.
O produto promete "vídeo cinematográfico das suas fotos", nunca "tour virtual".

## Escopo do MVP

O MVP é um pipeline operado pelo usuário que transforma uma pasta de fotos autorizadas em um vídeo de 60 s com
relatório de qualidade. Venda, cobrança e atendimento continuam manuais até haver clientes pagando.

**Entra:**
- Ingestão de fotos e vídeos com registro da autorização de uso.
- Roteiro de cenas por um modelo de visão, revisado pelo usuário.
- Três métodos por cena: vídeo real, movimento 2,5D por profundidade ou image-to-video gerativo.
- Registro de custo de cada chamada.
- QC por cena (aprovada, refazer, rejeitada) e relatório de fidelidade.
- Montagem com trilha licenciada, marca discreta e exportação 16:9 em 1080p.
- Planilha de leads e vendas; cobrança por link de pagamento.

**Fica fora até haver 3 vendas pagas:** dashboard, portal do cliente, CRM próprio, alertas no Telegram, descoberta
automatizada, scoring automático, WhatsApp via API, agentes de venda, supervisor de negócio, precificação por IA,
multiempresa e reconstrução 3D.

## Arquitetura proposta

Monólito modular em Python, rodado por CLI, com um adaptador por provedor de vídeo.
Fluxo (diagrama no original):
1. Ingestão
2. Roteiro de cenas
3. Um método por cena (vídeo real / 2,5D / image-to-video)
4. QC humano por cena (refazer volta ao método)
5. Montagem (ffmpeg)
6. Entrega com relatório

Todas as etapas gravam em um registro de custos e decisões (SQLite).

Regra de escolha do método: o mais fiel que resolve, nesta ordem: vídeo real, 2,5D, gerativo.
Fase 2: Postgres, webhooks do Asaas, WhatsApp Cloud API. Fase 3: Next.js lendo o mesmo banco.

## Stack recomendada

| Camada | Recomendação | Alternativa mais barata | Por quê |
|---|---|---|---|
| Image-to-video comercial | Veo 3.1 Fast e Kling 3.0 (testar os dois); Gemini Omni 1.1 Flash também é candidato | Veo 3.1 Lite (US$ 0,05/s em 720p) | Preço por segundo publicado |
| Image-to-video open-source | Wan 2.2 TI2V-5B (Apache 2.0) | Versão quantizada (GGUF) | Custo quase zero; qualidade a confirmar |
| Movimento seguro | 2,5D: mapa de profundidade + câmera curta | — | Não inventa geometria |
| GPU sob demanda | RunPod Community, RTX 4090 a US$ 0,34/h, cobrado por segundo | Vast.ai | Ligar só durante o lote |
| Montagem | ffmpeg (skill `verion-ffmpeg-assembly`) + DaVinci Resolve gratuito | — | Gratuito e reproduzível |
| Visão (roteiro) | Claude ou Gemini via API | — | Centavos por imóvel |
| Dados | SQLite no MVP; Postgres na Fase 2 | — | Custo fixo zero |
| Pagamento | Asaas: sem mensalidade; Pix ou boleto R$ 1,99; cartão à vista 2,99% + R$ 0,49 | iugu (Pix R$ 0,99, com mensalidade) | Custo fixo zero |
| WhatsApp | App Business manual | — | API só com base de opt-in; marketing custa US$ 0,0625 por mensagem no Brasil |

Cuidados de licença: o modelo de profundidade precisa permitir uso comercial, e a trilha precisa de licença comercial documentada.

## Custos

Premissas: vídeo de 60 s, 12 cenas de 5 s, 3 tentativas por cena (180 s gerados), dólar a R$ 5,10.

| Caminho | Preço unitário | Custo por vídeo |
|---|---|---|
| 2,5D local | — | ≈ R$ 0 |
| Wan 2.2 5B em RTX 4090 | US$ 0,34/h; ~6 min por clipe; 36 clipes + 1 h de setup | ≈ R$ 8 |
| Veo 3.1 Lite 1080p | US$ 0,08/s | ≈ R$ 73 |
| Veo 3.1 Fast 1080p | US$ 0,12/s | ≈ R$ 110 |
| Kling 3.0 Std / Pro | US$ 0,126 / 0,168 por s | ≈ R$ 116 / 154 |
| Veo 3.1 Standard | US$ 0,40/s | ≈ R$ 367 |

Economia de uma venda (hipótese: preço R$ 800; geração com Veo Fast; 1 venda a cada 5 demos; tempo a R$ 50/h; impostos fora):

| Item | Demo com Veo Fast | Demo em 2,5D |
|---|---|---|
| Receita | R$ 800 | R$ 800 |
| Geração do vídeo | −R$ 110 | −R$ 110 |
| 5 demos de 15 s | −R$ 138 | ≈ R$ 0 |
| Pix: 2 cobranças | −R$ 4 | −R$ 4 |
| Tempo: 4 h | −R$ 200 | −R$ 200 |
| **Margem antes de impostos** | **R$ 348 (43%)** | **R$ 486 (61%)** |

A demo precisa ser barata.

Orçamento da Fase 0 (teto de R$ 350):

| Item | Valor |
|---|---|
| Testes comerciais: 3 imóveis × 2 provedores | até R$ 300 |
| GPU para Wan 2.2 | até R$ 30 |
| API de visão | até R$ 20 |
| Ferramentas gratuitas | R$ 0 |

## Plano de validação (Fase 0)

Pergunta a responder em 2–3 semanas: existe um fluxo que entrega 60 s vendáveis, sem distorção, por até R$ 150 de
geração e até 3 h de trabalho humano?

**Material:** 3 imóveis com autorização escrita:
- um só com fotos de anúncio;
- um com fotos profissionais;
- um com fotos e vídeo de celular.

**Pipelines comparados:**
- A: 2,5D + montagem (linha de base);
- B: Veo 3.1 Fast;
- C: Kling 3.0;
- D: Wan 2.2 em GPU alugada;
- E: splatting a partir do vídeo de celular, só no imóvel 3.

**Medir por cena:** defeitos de fidelidade, tentativas, custo por segundo aprovado, tempo de máquina e humano,
e nota cega de 1 a 5 dada por 5–10 corretores ou gestores.

| Critério de saída | Meta |
|---|---|
| Características inventadas no vídeo final | zero |
| Cenas aprovadas em até 3 tentativas | ≥ 70% |
| Custo de geração por vídeo de 60 s | ≤ R$ 150 |
| Tempo humano por vídeo | ≤ 3 h |
| Nota média cega | ≥ 4 de 5 |
| Avaliadores que pagariam R$ 600 ou mais | ≥ 3 |

**Validação comercial em paralelo:** 10 gestoras ou corretores, abordados por canal comercial público, com
pré-venda e sinal de 50%. Três pagamentos valem mais que qualquer métrica técnica.

## Roadmap por portões

| Fase | Conteúdo | Portão para avançar |
|---|---|---|
| 0 | Validação audiovisual (2–3 semanas, até R$ 350) | Primeiro vídeo-piloto aprovado no QC |
| 0b | Venda manual (4 semanas, sobrepõe o fim da Fase 0) | 3 vendas pagas com sinal de 50% |
| 1 | MVP audiovisual (4–6 semanas): pipeline CLI, adaptadores, QC, relatório, custos | 10 vídeos entregues com margem positiva |
| 2 | MVP comercial: Postgres, Asaas, WhatsApp com opt-in | Custo de aquisição e conversão medidos |
| 3 | Portal do cliente (Next.js) | Etapa manual repetida toda semana |
| 4 | Agentes de produto | — |

Se um portão falhar, a fase seguinte não começa: ajuste oferta, público ou método e repita.

## Decisões pendentes do usuário

| # | Decisão | Recomendação |
|---|---|---|
| 1 | Cliente-alvo inicial | Gestoras de temporada e imobiliárias de alto padrão |
| 2 | Descoberta no Airbnb | Curadoria manual de 10–20 imóveis/dia |
| 3 | Demo First | Só com permissão explícita; demo de 10–15 s em 2,5D |
| 4 | WhatsApp | App Business manual, só com opt-in; API na Fase 2 |
| 5 | Orçamento da Fase 0 | Autorizar até R$ 350 |
| 6 | Imóveis de teste | 3 com autorização escrita |
| 7 | Hipótese de preço | R$ 600–1.200 por vídeo de 60 s |
| 8 | Retenção | 12 meses com renovação |

## Fontes

- Kling, preços da API: https://costbench.com/software/ai-media-apis/kling-api/
- Veo 3.1, preços na Gemini API: https://benchlm.ai/media-pricing/veo
- Wan 2.2, licença e GPU: https://www.hivenet.com/post/wan-2-2-cloud-gpu-comfyui
- Wan 2.2 5B, tempo em RTX 4090: https://stable-diffusion-art.com/wan-2-2-image-to-video/
- RunPod, preços: https://www.hackceleration.com/labs/runpod-pricing
- RunPod, aumento em 15/10/2026: https://www.usagepricing.com/blueprint/activity/runpod-2026-10-15-secure-cloud-second-hike
- WhatsApp, preços (Meta): https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- WhatsApp, opt-in (Meta): https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in.md
- Airbnb, termos contra coleta automatizada: https://www.octoparse.com/tutorial/how-to-extract-data-from-airbnb
- Airbnb, anúncio só com fotos: https://community.withairbnb.com/t5/Ask-about-your-listing/Videos/m-p/2205961/highlight/true
- Asaas, tarifas: https://quasa.io/pt/media/asaas-ou-iugu-tarifa-publicada-vence-ate-o-volume-pedir-proposta
- Drone imobiliário no Brasil: https://pickdrones.com/pt/drone-fotografia-imobiliaria/
- Reel-E: https://www.capterra.com/p/10037342/Reel-E/
- Splatting com poucas vistas: https://arxiv.org/html/2503.12535v1
- Dólar PTAX 16/10/2026: https://www.simianx.ai/pt/stories/us-dollar-exchange-rate-by-year-every-year-1971-2026-table
