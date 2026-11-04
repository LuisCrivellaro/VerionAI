# Evidência 11: T6 (parallax 2,5D) e T7 (montagem e quadros de QC)

Data: 2026-11-02. As duas tarefas rodaram em paralelo, sem arquivos em comum.

| Etapa | Agente | Tokens | Resultado |
|---|---|---|---|
| T6 | `verion-media-engineer` | 146.396 | SSIM do 1º quadro **0,9972**; EXIF 6 e retrato testados |
| T7 | `verion-media-engineer` | 70.239 | 8+5+5 s com alvo de 5 s → 14,0 s em formato de entrega |
| Revisão | `verion-code-reviewer` | 112.333 | **MUDANÇAS NECESSÁRIAS** (1 bloqueante) |

## O que o revisor mediu em 1920x1080

- **Leitura dentro da foto.** O parallax nunca lê fora da foto. O determinante do jacobiano fica entre 0,166 e 0,94, sem dobra e sem conteúdo inventado.
- **Pior esticamento.** É de cerca de 6x numa aresta de profundidade, ao longo de ~27 px. Isso é o defeito 4 da skill de QC, que fica para o QC humano.
- **Mutações.** Três foram pegas. Uma sobreviveu: o blur com fator 0,5, que em produção gera conteúdo espelhado.

## Bloqueante e sugestões

1. **[bloqueante] `_encode` do parallax sem timeout real.** Um ffmpeg que para de ler travava o harness para sempre, e a exceção `TimeoutExpired` saía crua.
2. **[forte] Teste de não-dobra na escala errada.** Rodava em 192x108, o que escondia a mutação do blur.
3. **[forte] QC sem o último quadro.** O QC nunca extraía o último quadro, onde o deslocamento do parallax é máximo.
4. **[forte] Skill, spec e ADR desatualizados.**
5. **[forte] Tempo da suíte apertado.**

## Correções (rodada 2, autores retomados com o próprio contexto)

- **T6 / bloqueante.** Foi criada a função `ffmpeg.run_rawvideo` e o parallax passou a usá-la.
  - Ela usa `start_new_session` e um `threading.Timer` com `killpg(SIGKILL)` no prazo, e sempre levanta `FfmpegError` com o fim do stderr.
  - 8 testes usam um ffmpeg falso no PATH: sai com código ≠ 0, para de ler, nunca termina, gerador lento e gerador que levanta. Os testes conferem que não sobra processo órfão.
- **T6 / não-dobra.** O teste agora roda em 1920x1080 (xadrez de 40 px e degrau, 4 movimentos, p = 0, 0,5 e 1) e há também uma asserção analítica. A mutação do blur passou a ser pega.
- **T6 / mapa de profundidade.** Aceita os modos de Pillow `I;16*` e `I`.
- **T6 / tempo.** `test_parallax.py` caiu de cerca de 31 s para 12,9 s.
- **T7 / último quadro.** `extract` devolve `QcFrames(frames, sheet, last)`. O `qc_ultimo.jpg` é o quadro final real; o teste usa 0,15 s azuis no fim de um clipe vermelho.
- **T7 / fonte.** `demo_font` recusa fonte de fallback: uma fonte sem `Ç` ou `Ã` estragaria a marca d'água.
- **T7 / validação.** A regra "≥ 2×transição" é validada antes de qualquer codificação.
- **T7 / tempo.** Os testes de montagem caíram de 43 s para 33 s.

## Conferência do supervisor (sem nova chamada ao revisor)

- `uv run pytest -o addopts= -q` → **655 passed** (90 s). A meta da T12 é ficar abaixo de 120 s.
- `ruff` limpo.
- Os 8 testes de `run_rawvideo` passam.

**Status: T6 e T7 APROVADAS.**
