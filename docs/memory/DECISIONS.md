# Decisões (a mais recente no topo)

## 2026-11-03 · ADR-013 · Open-Higgsfield-AI (fork Autom8AI) como ferramenta principal de criação de vídeo
- **Decisão do usuário:** o fork `Autom8AI/Open-Higgsfield-AI` (commit `d32d022`, 25/10/2026) passa a ser a ferramenta principal de geração image-to-video. Substitui o Gemini Omni como caminho pago principal (revisa ADR-009 e ADR-011). Ken Burns e parallax continuam como caminhos gratuitos.
- **O que a ferramenta é (verificado lendo o código):**
  - Interface web (Vite + JS puro; há também uma versão Next.js) que **não gera vídeo localmente**. Toda geração vai para APIs pagas: **kie.ai** (`api.kie.ai/api/v1/jobs/createTask`, chave Bearer no `localStorage`) e, no backend Netlify, fal.ai.
  - Modelos de vídeo com entrada de imagem no fork: Seedance 2.0, Seedance 2.0 Fast, Seedance 1.5 Pro, Kling 3.0, HappyHorse 1.1 Reference. Wan 2.5 e Kling 3 Turbo são só texto→vídeo.
  - O fork difere do original (`Anil-matcha/Open-Higgsfield-AI`, que usa Muapi.ai). Tem um backend "content-factory" com login por PIN, Airtable, Composio e Resend, e edge functions que referenciam scripts ausentes no repositório (deploy particular de terceiro).
  - Licença: o README declara MIT, mas **o repositório não tem arquivo LICENSE**. Uso interno ok; redistribuir ou embutir no produto exige confirmar a licença.
  - `npm install --ignore-scripts` + `npx vite build` compilam sem erro (03/11). Nenhuma geração foi testada.
- **Como encaixa no VÉRION:**
  - Uso como **estúdio manual** na Fase 0: o usuário roda localmente (`npm install && npm run vite:dev`), cola a chave do kie.ai e gera cena a cena a partir das fotos.
  - O backend Netlify do fork (Airtable, Composio, Resend, login) **não será usado**.
  - As regras continuam: cada clipe passa pelo `verion-fidelity-qc`; termo das fotos obrigatório; gasto só com autorização registrada.
  - Integração automática: se o teste manual aprovar, criar um adaptador `kie` no harness (mesmo contrato de T2, com reserva e liquidação no ledger), em vez de depender da interface.
- **Custos (hipótese, fontes secundárias, conferir em kie.ai antes de gastar):** Seedance 2.0 ~US$ 0,057/s no kie.ai (bitdoze.com); Kling 3.0 ~US$ 0,075–0,15/s conforme resolução e áudio (evolink.ai). Vídeo de 60 s, 12 cenas × 5 s × 3 tentativas = 180 s gerados ≈ US$ 10–27 (R$ 52–140 a 5,10).
- **Riscos:** (1) a interface não registra custo no ledger do VÉRION, então o gasto precisa ser anotado à mão até existir o adaptador; (2) o `localStorage` guarda a chave e o histórico no navegador; (3) `api.kie.ai` está bloqueado no proxy da sessão em nuvem, então a geração só roda na máquina do usuário; (4) fidelidade não é garantida por nenhum desses modelos e continua sendo o critério de aprovação.

## 2026-11-03 · ADR-012 · Higiene do ambiente limpo; teto do settle_ok adiado para antes da 1ª chamada paga
- **Ruff:** `[tool.ruff] extend-exclude = [".claude"]`. Os scripts vendorizados nas skills (Google, Apache-2.0) não são código do projeto; `src` e `tests` já passavam.
- **Setup documentado no README:** os testes exigem `uv sync --frozen --extra gemini`.
- **Teto do `settle_ok` (follow-up do ADR-009): não implementado agora.** O custo real deve continuar sendo gravado (truncar esconderia gasto). O excesso de uma tentativa é limitado à duração máxima do modelo, e a `reserve` seguinte já vê o saldo. Com chamadas pagas bloqueadas e o preço do Omni ainda hipótese, isso entra como pré-requisito da 1ª chamada paga. Opção preferida: reservar pela duração máxima que o modelo pode devolver, para a liquidação nunca passar da reserva.

## 2026-11-02 · ADR-011 · Decisões do usuário: sem gasto por ora, só Gemini Omni, trava de permissões aplicada
- **Orçamento:** nenhuma chamada paga por enquanto. A Fase 0 começa só com os métodos gratuitos (Ken Burns e parallax).
- **Imóveis:** o usuário tem de 1 a 3 imóveis com termo de uso das fotos. Vai anexar as fotos e o código do termo (`photo_release_ref`).
- **Provedor pago:** só o Gemini Omni. T10 (Kling) e T11 (Wan) ficam **fora do escopo** até nova decisão.
- **Trava de permissões aplicada** em `.claude/settings.json` (`permissions.deny`):
  - comandos com `budget authorize`, `--reviewer humano` e `GEMINI_API_KEY`;
  - `printenv`;
  - edição de `data/**` e `.claude/settings*.json`;
  - leitura de `.env*`.
- **Validação da trava:** o `claude doctor` validou a sintaxe; as regras `Write(...)` foram removidas porque `Edit(...)` já cobre todas as ferramentas de edição. Na sessão em que foi criada, um teste com `echo` **não foi bloqueado**, porque as regras de projeto são lidas no início da sessão. **Pendente:** confirmar o bloqueio numa sessão nova.

## 2026-11-02 · ADR-010 · CLI: teto de autorização, export por lista de permitidos, termo das fotos, aprovação humana com TTY
- **Teto de autorização:** `budget authorize` aceita no máximo R$ 500 por autorização e R$ 500 acumulados no mês calendário (America/Sao_Paulo), o teto do brief. Subir exige decisão do usuário e mudança de código.
- **Export JSONL:**
  - Sai por **lista de permitidos**: só campos estruturados (`erro` como classe e `erro_codigo`, defeitos com tipo, segundo e gravidade, `motivo_codigo`).
  - Notas, motivos em texto, referências, caminhos e job id ficam **só no banco local**.
  - A spec 3.8 e o registro da skill `verion-fidelity-qc` valem para o banco local. O export é um subconjunto.
- **Termo das fotos:** `photo_release_ref` é obrigatório no roteiro para métodos que enviam foto a terceiros (Omni, Veo, Kling, Wan). A referência é gravada no run antes de qualquer envio. O `property_id` deve ser um código sem relação com o imóvel (`imv-001`).
- **Aprovação humana:** aprovar QC como humano exige TTY e a frase `APROVO tentativa N`. Agentes só gravam `refazer` ou `rejeitada`.
- **Banco:** só `db init`, `run` e `authorize` o criam. `--out` fica confinado a `VERION_DATA_DIR` ou `docs/engineering/evidence/`.
- **Segredos:** `verion.redact.redact_secrets` é a redação central, aplicada no harness, na CLI e no export.
- **Interrupção:** código 130 para Ctrl-C e SIGTERM, com liquidação. Código 2 para erro de ambiente ou arquivo. Código 1 só para bug, com uma linha (traceback com `--debug`, redigido).
- **Proposta NÃO aplicada (decisão do usuário):** regra `permissions.deny` no `.claude/settings.json`.
  - Bloquearia `Bash(*budget authorize*)`, `Bash(*--reviewer humano*)`, `Bash(*verion.db*)`, `Edit/Write(data/**)` e `Edit/Write(.claude/settings*.json)`.
  - É atrito, não fronteira: a barreira real continua sendo a chave fora do ambiente do agente.
  - A sintaxe precisa ser conferida em `/permissions`.

## 2026-11-02 · ADR-009 · Gemini Omni como provedor pago principal; regras de dinheiro dos adaptadores
- **Veo descontinuado:** a documentação oficial dá o `veo-3.1-fast-generate-preview` como desligado desde 22/10/2026, e o substituto indicado é o `gemini-omni-1.1-flash`.
- **Provedor principal da Fase 0:** o Omni. O adaptador do Veo fica no código, mas só é usado se um ID GA confirmado for configurado.
- **Plano do MVP:** a comparação "Veo × Kling" vira "Omni × Kling/Wan".
- **Chave:** só `GEMINI_API_KEY`, com `vertexai=False` fixo. Vertex exigiria outro client, outra chave e outro preço, e uma nova decisão.
- **Retry interno do SDK desligado** (`HttpRetryOptions(attempts=1, http_status_codes=[599])`):
  - Erros ambíguos depois do envio (408, 5xx exceto 503, timeout de leitura) são faturados com a duração pedida e **nunca reenviados**.
  - Recusas sem job (429, 503, falha de conexão antes do envio) podem ser repetidas pelo `retry_before_accept` (até 3).
- **Cobrança por clipe:** `units = max(medido, pedido)`, uma escolha conservadora (pode superestimar).
- **Ressalvas da reserva:** o excesso por tentativa é limitado ao máximo do modelo (8 a 10 s). O `settle_ok` ainda não tem teto contra a reserva (follow-up no harness).
- **Pré-requisitos da 1ª chamada paga:**
  - preço oficial do Omni em 1080p (o `prices.toml` hoje traz uma hipótese de 720p);
  - confirmar que o upload da Files API é gratuito;
  - liberar `generativelanguage.googleapis.com` no proxy;
  - autorização do usuário.

## 2026-11-02 · ADR-008 · Parallax por warp contínuo, encoder com prazo e QC com último quadro
- **Parallax 2,5D:**
  - É um backward warp contínuo por pixel, não "por camada" como dizia a spec. Assim não há buracos nem fantasmas.
  - Amplitude de 1,5% da largura (o teto é 2%), zoom de segurança de até ~1,03 e profundidade suavizada (3 passadas, ~27 px).
  - A não-dobra é garantida por teste em 1920x1080 e por uma asserção analítica.
- **Mapa de profundidade:**
  - Convenção de nome `<foto.jpg>.depth.png`: PNG 16 bits, branco = perto, na orientação já corrigida pelo EXIF.
  - Mapa plano gera `InputError`.
- **Encoder de quadros:** `ffmpeg.run_rawvideo` centraliza a execução com prazo real (POSIX: `killpg`). Nenhum subprocesso do ffmpeg roda fora de `local/ffmpeg.py`, exceto `fc-match` na escolha de fonte.
- **QC:** `extract` devolve `QcFrames(frames, sheet, last)`. O último quadro é obrigatório, porque é onde o parallax desloca mais. A folha de contato usa 4 colunas e linhas variáveis; a skill foi atualizada.
- **Montagem:**
  - Cada clipe precisa de pelo menos 2×`transition_s` (regra conservadora).
  - `short_clips` usa índice base 0 da lista recebida, não número de cena.
  - A marca DEMONSTRAÇÃO exige a fonte Inter.
- **Pendente:** verificar a licença comercial do modelo de profundidade real (o proxy bloqueia o Hugging Face). Até lá, só `DepthFromFile`.

## 2026-11-02 · ADR-007 · Harness relança erros; run travado ao roteiro; enquadramento 16:9
- **Erros relançados:** `run_scenes` relança toda exceção depois de liquidar com a reserva (spec 3.5).
  - A exceção leva `verion_run_id` e a nota `run_id=N`.
  - `RunSummary.errors` e `stopped_by` ficam reservados, sempre 0 e `None` num retorno normal.
- **Run travado ao roteiro:** continuar um run (`--run`) exige o mesmo método, `property_id` e `sha256` do roteiro.
  - Mudar o prompt para "refazer" exige um run novo, e isso zera o limite de 3 tentativas por cena.
  - Aceito porque cada tentativa continua passando pela reserva e pelo teto autorizado: o dinheiro segue protegido.
  - A rastreabilidade (vereditos de QC ligados ao roteiro exato) vale mais que o contador.
- **Enquadramento:** o Ken Burns recorta ao centro em 16:9.
  - Uma foto 4:3 perde 25% da altura, e uma foto em retrato mostra só cerca de 42%.
  - Não é defeito de fidelidade (nada é inventado), mas é **risco de produto**: o QC humano compara o recorte com a foto inteira, e o relatório ao cliente declara o enquadramento.
- **EXIF:** o ffmpeg 6.1.1 aplica a orientação EXIF (medido pelo revisor). Falta um teste que fixe esse comportamento, previsto para a T6 ou T7.

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
