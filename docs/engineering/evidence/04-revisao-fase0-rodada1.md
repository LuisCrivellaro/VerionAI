# Evidência 04: revisão cruzada da spec da Fase 0 (rodada 1)

Data: 2026-11-01. Revisor: `verion-code-reviewer` (somente leitura, sonnet). Autor revisado: `verion-architect`.
44.982 tokens, 6 chamadas de ferramenta, 129 s.

**Veredito: MUDANÇAS NECESSÁRIAS.** Bloqueantes: comentários 1 a 7.

Verificação mecânica feita pelo revisor: um script cruzou as listas de arquivos das 12 tarefas e encontrou
**nenhum arquivo compartilhado**. A afirmação do arquiteto foi confirmada. As dependências de interface entre
tarefas, porém, não estavam declaradas.

## Comentários (referências de linha em `docs/engineering/FASE0_HARNESS.md`)

1. **[bloqueante] O bloqueio de gasto é convenção, não impossibilidade.**
   - **Problema:** `generate` é público e cria o cliente real com a chave do ambiente (`:96-101`), e `budget.check` só é chamado em `run_scenes` (`:125,130-138`). Além disso, `paid` é declarado pelo próprio adaptador, `check` não rejeita custo ≤ 0 em método pago e `budget authorize` só é restrito por texto.
   - **Correção:** derivar `paid` da `PriceTable`; exigir um `SpendToken` emitido por `budget.check`; rejeitar custo ≤ 0; confirmação de `authorize` fora do alcance de agentes.
2. **[bloqueante] Um crash entre `generate` e `record_attempt` perde custo faturado e permite furar o teto.**
   - **Problema:** sem reserva prévia, e sem transação nem exclusão entre execuções concorrentes (`:135-138,167`).
   - **Correção:** linha `pending` com reserva do custo dentro de `BEGIN IMMEDIATE` antes da chamada; tratar toda exceção mantendo a reserva.
3. **[bloqueante] T1 (`budget.check`) depende de `CostEstimate` e `SceneGenerator`, que só a T2 cria (`:125,203-204`).**
4. **[bloqueante] T6 e T7 dependem de `local/ffmpeg.py`, da T5, mas estão marcadas como paralelas (`:196,207-209`).**
5. **[bloqueante] Falta configuração da CLI (caminho de preços, diretório de dados, modo do `fake`), então a T12 não consegue rodar o próprio critério (`:183-185,214`).**
6. **[bloqueante] `AttemptRow` e as assinaturas do repositório (`create_run`, `finish_run`, `record_qc`, tipos do `report`) não estão definidos (`:171-175`).**
7. **[bloqueante] O teste de contrato da T2 exige rede e mocks de outras tarefas.**
   - **Problema:** os nomes de classe do registry não estão fixados, a injeção do `DepthEstimator` não está definida e o `conftest.py` exclusivo da T1 bloqueia fixtures comuns (`:36,99,203-204`).
8. **[sugestão forte] Uma cena pode ficar sem veredito final.**
   - **Problema:** nada transforma a 3ª tentativa reprovada em `rejeitada`, e 3 erros seguidos não recebem QC (`:138,168`).
9. **[sugestão forte] O esquema de defeitos não distingue leve de bloqueante, e um `agente` pode aprovar sozinho (`:168,173`).**
   - **Problema:** isso contradiz a skill `verion-fidelity-qc`.
10. **[sugestão forte] O export JSONL não está mapeado para o formato da skill (tentativas sem QC, métodos `fake` e Omni) (`:190`).**
11. **[sugestão forte] Os códigos de saída não cobrem `AttemptLimit`, `AwaitingQC`, `SceneClosed`, `ScriptError` e `FfmpegError` (`:192`).**
12. **[sugestão forte] `assemble` não tem duração alvo, então um clipe de 8 s numa cena de 5 s não é cortado (`:157,239`).**
    - **Detalhe:** os critérios de pesquisa (licença na T6, SDK na T8 e T9) não são testes e devem sair da coluna "Pronto quando".

## Elogios do revisor

- O relógio injetável viabiliza a medição de `wall_time_s`.
- O `fake` com erro faturado torna testável o pior caso de custo.
- A regra "`ok` sem QC bloqueia nova tentativa" evita gasto duplicado.
