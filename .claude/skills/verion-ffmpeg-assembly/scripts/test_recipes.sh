#!/usr/bin/env bash
# Testa as receitas da skill verion-ffmpeg-assembly com mídia sintética.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf out && mkdir -p out
FONT="${FONT:-$(fc-match -f '%{file}' 'Inter:style=Regular')}"

# Insumos sintéticos: duas "fotos" 4000x3000 e uma trilha de 20 s
ffmpeg -v error -y -f lavfi -i testsrc2=size=4000x3000 -frames:v 1 out/foto1.jpg
ffmpeg -v error -y -f lavfi -i mandelbrot=size=4000x3000 -frames:v 1 out/foto2.jpg
ffmpeg -v error -y -f lavfi -i "sine=frequency=220:duration=20" -c:a aac out/trilha.m4a

# R1. Movimento conservador (Ken Burns) a partir de foto: 5 s, 1080p, 30 fps, zoom 1.00 -> 1.06
# Escala para 2x a saída antes do zoompan para evitar tremido (jitter).
for i in 1 2; do
  ffmpeg -v error -y -loop 1 -i out/foto$i.jpg -t 5 -vf "scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,zoompan=z='min(1+0.06*on/150,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=150:s=1920x1080:fps=30,format=yuv420p" -c:v libx264 -preset veryfast -crf 20 out/cena$i.mp4
done

# R2. Normalizar clipe de qualquer origem (API, celular) para 1920x1080, 30 fps, yuv420p, sem áudio
ffmpeg -v error -y -f lavfi -i testsrc=size=1280x720:rate=24 -t 5 out/clipe_api.mp4
ffmpeg -v error -y -i out/clipe_api.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1" -an -c:v libx264 -preset veryfast -crf 20 out/clipe_norm.mp4

# R3. Encadear 3 cenas com crossfade de 0,5 s (offset = soma das durações anteriores - n*0,5)
ffmpeg -v error -y -i out/cena1.mp4 -i out/clipe_norm.mp4 -i out/cena2.mp4 -filter_complex \
 "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.5[v01];[v01][2:v]xfade=transition=fade:duration=0.5:offset=9,format=yuv420p[v]" \
 -map "[v]" -c:v libx264 -preset veryfast -crf 20 out/montagem.mp4

# R4. Marca discreta + trilha com fade-out, cortada no tamanho do vídeo
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 out/montagem.mp4)
FADE_START=$(python3 -c "print(max(0, $DUR - 1.5))")
ffmpeg -v error -y -i out/montagem.mp4 -i out/trilha.m4a -filter_complex \
 "[0:v]drawtext=fontfile='$FONT':text='VÉRION':fontsize=28:fontcolor=white@0.55:x=w-tw-48:y=h-th-40,format=yuv420p[v];[1:a]afade=t=out:st=$FADE_START:d=1.5[a]" \
 -map "[v]" -map "[a]" -shortest -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart out/final.mp4

# R5. Marca d'água de DEMO (diagonal, visível) para demonstrações
ffmpeg -v error -y -i out/cena1.mp4 -vf "drawtext=fontfile='$FONT':text='DEMONSTRAÇÃO':fontsize=110:fontcolor=white@0.35:x=(w-tw)/2:y=(h-th)/2,format=yuv420p" -c:v libx264 -preset veryfast -crf 23 out/demo.mp4

# R6. Quadros para QC: 1 por segundo + folha de contato
ffmpeg -v error -y -i out/final.mp4 -vf "fps=1,scale=480:-1" out/qc_%03d.jpg
ffmpeg -v error -y -i out/final.mp4 -vf "fps=1,scale=320:-1,tile=4x4" -frames:v 1 out/qc_contato.jpg

# Verificações
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,pix_fmt,codec_name -of csv=p=0 out/final.mp4
ffprobe -v error -show_entries format=duration -of csv=p=0 out/final.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 out/final.mp4
ls out/qc_*.jpg | wc -l
echo "OK todas as receitas"
