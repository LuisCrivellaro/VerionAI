# Evidência 13: T4 (CLI `verion`, relatório e export), com revisão de código e de segurança

Data: 2026-11-02.

| Etapa | Agente | Tokens | Resultado |
|---|---|---|---|
| T4 | `verion-backend` | 177.074 | 10 comandos, códigos de saída 0 e 2 a 6, 952 testes |
| Revisão de código | `verion-code-reviewer` | 113.454 | **MUDANÇAS NECESSÁRIAS** (1 bloqueante). Relatório conferido contra conta manual; ensaio ponta a ponta com ffmpeg real ok |
| Revisão de segurança | `verion-security` | 115.685 | **MUDANÇAS NECESSÁRIAS** (2 bloqueantes, 4 importantes) |

## Achados principais

1. **[código, bloqueante]** Valores absurdos em `--brl` e IDs acima de 2^63 geravam traceback cru, até no comando de autorização.
2. **[segurança, bloqueante]** Uma chave de API dentro de uma exceção ia crua para o stderr, o SQLite, o JSONL exportado e o traceback do `--debug`. O teste usou chave sintética.
3. **[segurança, bloqueante]** O export vazava dados pessoais: a redação de caminhos parava no espaço (`Rua das Flores 123`), e os campos de nota, motivo e referência não passavam por redação.
4. **[importante] Banco criado em qualquer comando:** um `ledger export` com `VERION_DATA_DIR` errado zerava a evidência commitada.
5. **[importante] `--out` sem limite:** sobrescrevia qualquer arquivo, inclusive `verion.db` e `.git/config`.
6. **[importante] `authorize` sem teto:** aceitava até R$ 1 bilhão.
7. **[importante] Aprovação "humano" autodeclarada:** `qc record ... aprovada --reviewer humano` gravava sem TTY.
8. **[importante] Fotos sem termo:** nenhum registro de autorização das fotos antes de enviá-las a terceiros (LGPD, art. 33).

## Correções (rodada final; o autor foi retomado com o próprio contexto)

1. **Números:**
   - Regex ASCII em `--brl` e `--cost-brl`, mais `ArithmeticError` capturado.
   - Tipo `_id_arg` (1 a 2^63−1).
   - O `parse_args` também é protegido.
   - Resultado: código 2.
2. **Segredos:** novo `verion.redact.redact_secrets`, que cobre padrões de chave e os valores literais de `GEMINI_API_KEY` e `GOOGLE_API_KEY`.
   - Aplicado no harness, no `_report_failure`, no `--debug` e no export.
   - O teste confere que a chave sintética não aparece no stdout, no stderr, nos bytes do `verion.db`, `-wal` e `-shm`, nem nos JSONL.
3. **Export por lista de permitidos:**
   - `erro` sai só como classe da exceção e `erro_codigo`.
   - Defeitos saem só com tipo, segundo e gravidade.
   - Cena sai com `motivo_codigo`.
   - Não saem: notas, referência, caminhos e job id.
4. **Banco:** só `db init`, `run` e `budget authorize` criam o banco.
   - Os demais comandos dão código 2 com "rode `verion db init`".
   - O caminho do banco é impresso.
   - O export com banco vazio não sobrescreve saída existente sem `--force`.
5. **`--out`:** fica confinado a `VERION_DATA_DIR` ou `docs/engineering/evidence/`.
   - São recusados `verion.db*`, `.git`, `.claude`, `..` e symlinks que escapam.
6. **Teto de autorização:** R$ 500 por autorização e R$ 500 acumulados no mês calendário, em horário de São Paulo, que é o teto do brief.
   - `--ref` passa por sanitização.
7. **Aprovação humana:** `aprovada` por humano exige TTY e a frase `APROVO tentativa N`, testado com pty real.
8. **Termo das fotos:** campo `photo_release_ref` obrigatório no roteiro para métodos que enviam foto a terceiros (Omni, Veo, Kling, Wan).
   - A referência fica registrada no run antes de qualquer envio.
9. **Erros de ambiente:** `OSError` e `sqlite3.Error` saem com código 2, não mais como "bug".
10. **SIGTERM:** vira `KeyboardInterrupt`, então a reserva é liquidada.
    - A mensagem "reservas liquidadas" só aparece quando o estado é confirmado.
11. **Detalhes:**
    - Export de cenas pula runs vazios.
    - O `assemble` cita "cena N" nas mensagens.
    - `qc frames` mostra a foto de origem.
    - `.gitignore` ignora `*.db*` e arquivos parciais.
    - Permissões 0700 na pasta e 0600 no banco.
    - `ruff` e `mypy` entram no grupo `dev`.

O autor fez 11 mutações nos comportamentos novos, e todas derrubaram algum teste.

## Conferência do supervisor

- `uv run pytest -o addopts= -q` → **1111 passed** (92,8 s; meta < 120 s).
- `ruff` limpo.
- `mypy src` → **Success: no issues found in 26 source files**.

**Status: T4 APROVADA.**

## Pendente: decisão do usuário

O revisor de segurança sugeriu uma regra `deny` de permissões no `.claude/settings.json`. Ela impediria agentes de rodar `verion budget authorize`, de aprovar QC como humano e de editar `data/**` ou `.claude/settings*.json`. Como muda a configuração de permissões do ambiente do usuário, o supervisor **não aplicou**. A proposta está registrada no ADR-010.
