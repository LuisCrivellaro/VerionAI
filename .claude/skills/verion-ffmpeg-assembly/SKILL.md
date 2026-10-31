---
name: verion-ffmpeg-assembly
description: Use when assembling, normalizing, transitioning, watermarking, adding music to, or extracting QC frames from VÉRION property videos with ffmpeg. Covers Ken Burns motion from still photos, normalizing provider clips to 1920x1080/30fps, xfade crossfades, discreet brand mark, DEMO watermark, music fade-out, and contact sheets for QC.
---

# VÉRION — montagem de vídeo com ffmpeg

Receitas testadas em ffmpeg 6.1.1 com mídia sintética (`scripts/test_recipes.sh`, ~40 s em CPU).
Antes de mudar uma receita, rode o script: ele precisa terminar com `OK todas as receitas`
e o `ffprobe` final precisa mostrar `h264,1920,1080,yuv420p,30/1`.

## Padrão de saída (não negociável no MVP)

- 16:9, 1920x1080, 30 fps, H.264, `yuv420p`, `-movflags +faststart`.
- `-preset veryfast -crf 20` para entrega; `-crf 23` para demos e prévias.
- Sempre termine cadeias de filtro com `format=yuv420p`. `xfade` e `drawtext` podem
  devolver `yuv444p`, que vários players e o WhatsApp não reproduzem (bug pego no teste).

## Receitas

**R1. Movimento conservador sobre foto (5 s, zoom 1,00 → 1,06).** Escalar para 2x a saída
antes do `zoompan` evita tremido. Zoom acima de ~1,10 amplia defeitos da foto.

```bash
ffmpeg -y -loop 1 -i foto.jpg -t 5 -vf "scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,zoompan=z='min(1+0.06*on/150,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=150:s=1920x1080:fps=30,format=yuv420p" -c:v libx264 -preset veryfast -crf 20 cena.mp4
```

`d` = duração × fps (5 s × 30 = 150). Para pan lateral, varie `x` com `on` em vez de `zoom`.

**R2. Normalizar clipe de qualquer origem** (API de vídeo, celular): letterbox sem distorcer.

```bash
ffmpeg -y -i entrada.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1" -an -c:v libx264 -preset veryfast -crf 20 saida.mp4
```

Todos os clipes precisam passar por R2 antes de R3, senão o `xfade` falha por resolução ou fps diferentes.

**R3. Crossfade entre cenas.** `offset` da transição k = soma das durações dos clipes
anteriores − k × duração da transição. Para 3 clipes de 5 s e fade de 0,5 s: 4,5 e 9.

```bash
ffmpeg -y -i c1.mp4 -i c2.mp4 -i c3.mp4 -filter_complex "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.5[v01];[v01][2:v]xfade=transition=fade:duration=0.5:offset=9,format=yuv420p[v]" -map "[v]" -c:v libx264 -preset veryfast -crf 20 montagem.mp4
```

Duração final = soma − (n−1) × 0,5. Gere o `filter_complex` em Python a partir da lista de cenas; não escreva à mão para 12 cenas.

**R4. Marca discreta + trilha com fade-out.**

```bash
ffmpeg -y -i montagem.mp4 -i trilha.m4a -filter_complex "[0:v]drawtext=fontfile='$FONT':text='VÉRION':fontsize=28:fontcolor=white@0.55:x=w-tw-48:y=h-th-40,format=yuv420p[v];[1:a]afade=t=out:st=$FADE_START:d=1.5[a]" -map "[v]" -map "[a]" -shortest -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart final.mp4
```

`FADE_START` = duração do vídeo − 1,5 (via `ffprobe`). Só use trilha com licença comercial registrada.

**R5. Marca d'água de DEMONSTRAÇÃO** (obrigatória em toda demo, conforme o brief):

```bash
ffmpeg -y -i cena.mp4 -vf "drawtext=fontfile='$FONT':text='DEMONSTRAÇÃO':fontsize=110:fontcolor=white@0.35:x=(w-tw)/2:y=(h-th)/2,format=yuv420p" -c:v libx264 -preset veryfast -crf 23 demo.mp4
```

**R6. Quadros para QC** (alimenta a skill `verion-fidelity-qc`):

```bash
ffmpeg -y -i final.mp4 -vf "fps=1,scale=480:-1" qc_%03d.jpg
ffmpeg -y -i final.mp4 -vf "fps=1,scale=320:-1,tile=4x4" -frames:v 1 qc_contato.jpg
```

## Fonte

`FONT=$(fc-match -f '%{file}' 'Inter:style=Regular')`. Se a fonte não existir na máquina,
`fc-match` devolve outra: confira o caminho antes de renderizar a entrega final.

## O que esta skill não cobre

Geração de movimento por IA (Veo, Kling, Wan) e parallax 2,5D por profundidade. Essas etapas
produzem clipes que entram por R2. O registro de custo de cada chamada é obrigatório (`CLAUDE.md`).
