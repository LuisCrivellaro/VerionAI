---
name: verion-fidelity-qc
description: Use when judging whether a generated or animated VÉRION scene is faithful to the source photo, deciding approve / redo / reject, writing the per-scene fidelity report, or designing automated fidelity checks. Applies to image-to-video (Veo, Kling, Wan), 2.5D depth parallax and Ken Burns clips.
---

# VÉRION — controle de fidelidade por cena

Regra do brief, sem exceção: **nenhuma característica inventada chega ao vídeo final**.
Cena duvidosa é rejeitada, registrada e excluída. Na dúvida entre "refazer" e "rejeitar",
rejeite e use o método mais fiel (2,5D ou Ken Burns da mesma foto).

## Decisão por cena

| Veredito | Quando |
|---|---|
| Aprovada | Nenhum defeito bloqueante; no máximo 1 defeito leve, invisível em velocidade normal |
| Refazer | Defeito bloqueante e ainda há tentativas (máximo 3 por cena no MVP) |
| Rejeitada | 3 tentativas falharam, ou o defeito é inerente ao método (ex.: a câmera precisa "ver" um espaço que a foto não mostra) |

## Defeitos bloqueantes (qualquer um reprova)

1. Elemento novo: móvel, objeto, pessoa, planta, luminária ou obra que não está na foto.
2. Abertura alterada: porta, janela ou vão criado, removido, movido ou com outra proporção.
3. Vista trocada: paisagem, mar, piscina ou céu diferente do original pela janela ou varanda.
4. Geometria deformada: parede curvando, linha reta ondulando, piso "respirando", pé-direito mudando.
5. Material trocado: acabamento, cor de parede, piso ou bancada diferente do original.
6. Escala mudada: ambiente parecendo maior ou menor que na foto.
7. Texto ou marca inventada: placas, logotipos, letras ilegíveis surgindo.

## Defeitos leves (até 1 por cena)

Leve tremido em textura fina, reflexo pouco natural em vidro, borda suavizada em movimento rápido.

## Como inspecionar

1. Extraia quadros com a receita R6 de `verion-ffmpeg-assembly` (1 por segundo).
2. Compare primeiro e último quadro com a foto de origem, lado a lado.
3. Assista uma vez em velocidade normal e uma vez a 0,5x.
4. Para cada defeito: tipo (1–7), segundo do vídeo e descrição em uma linha.

Métricas automáticas (SSIM/LPIPS contra a foto, detecção de objetos novos) servem só como
**triagem** para priorizar o olhar humano. Nunca aprovam uma cena sozinhas no MVP.

## Registro obrigatório por cena

```json
{"projeto": "...", "cena": 3, "metodo": "veo-3.1-fast|kling-3.0|wan-2.2-5b|parallax-2.5d|ken-burns|video-real",
 "tentativa": 2, "veredito": "aprovada|refazer|rejeitada",
 "defeitos": [{"tipo": 2, "segundo": 3.0, "nota": "janela da sala ganhou uma folha extra"}],
 "custo_brl": 1.84, "revisor": "humano|agente"}
```

## Relatório de fidelidade (entregue ao cliente)

Por cena: método usado (real, editado, animado por IA), veredito e motivo das rejeições.
Simulações ou movimentos gerados por IA são declarados como tal. Nunca chame o resultado de "tour virtual".
