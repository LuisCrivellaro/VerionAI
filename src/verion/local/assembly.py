"""Montagem das cenas em um vídeo só (spec 3.6): R2 com corte no alvo, R3 crossfade e R5 se for demo.

Fluxo de ``assemble``:

1. cada clipe passa pela R2 (letterbox sem distorcer, 1920x1080, 30 fps, ``yuv420p``, sem áudio) com ``-t``
   no ``target_s`` da cena. O ``-t`` só corta: um clipe mais curto que o alvo entra inteiro (nunca é esticado)
   e seu índice vai para ``AssemblyResult.short_clips``;
2. as durações reais dos clipes normalizados (``ffprobe``) alimentam os ``offset`` do ``xfade`` da R3;
3. numa única codificação final: cadeia de ``xfade`` + (R5 ``drawtext`` DEMONSTRAÇÃO se ``demo``) +
   ``format=yuv420p``. ``xfade`` e ``drawtext`` podem devolver ``yuv444p``, por isso a cadeia sempre termina
   em ``format=yuv420p``.

Duração esperada: ``Σ min(dur_i, alvo_i) − (n−1)·transição``. O arquivo só aparece em ``out`` depois de
conferido por ``ffprobe`` (formato de entrega e duração).
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from verion.errors import FfmpegError, InputError
from verion.local import ffmpeg

__all__ = [
    "AssemblyResult",
    "ClipSpec",
    "assemble",
    "build_filter_complex",
    "crossfade_offsets",
    "demo_font",
]

_W, _H, _FPS = ffmpeg.DELIVERY_WIDTH, ffmpeg.DELIVERY_HEIGHT, ffmpeg.DELIVERY_FPS
_FRAME_S: Final = 1.0 / _FPS
_DURATION_TOLERANCE_S: Final = 0.1  # ± 3 quadros sobre a duração esperada
_CRF_DELIVERY: Final = "20"
_CRF_DEMO: Final = "23"  # R5: demos e prévias
_DEMO_TEXT: Final = "DEMONSTRAÇÃO"
_FONT_PATTERN: Final = "Inter:style=Regular"

# R2: letterbox sem distorcer; ``setsar=1`` e ``fps`` iguais em todos os clipes são pré-requisito do xfade.
_R2_FILTER: Final = (
    f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
    f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2,fps={_FPS},format=yuv420p,setsar=1"
)


@dataclass(frozen=True)
class ClipSpec:
    """Um clipe da montagem e a duração máxima (alvo) que a cena ocupa no vídeo final."""

    path: Path
    target_s: float


@dataclass(frozen=True)
class AssemblyResult:
    out: Path
    duration_s: float
    # Índices BASE 0 na lista ``clips`` recebida (NÃO o número da cena, que começa em 1): quem chama mapeia para a
    # cena. São os clipes mais curtos que o alvo, usados inteiros e sem esticar.
    short_clips: tuple[int, ...]


def assemble(
    clips: Sequence[ClipSpec],
    out: Path,
    *,
    transition_s: float = 0.5,
    demo: bool = False,
) -> AssemblyResult:
    """Monta ``clips`` em ``out`` (H.264 1920x1080 yuv420p 30 fps). Ver o docstring do módulo.

    ``AssemblyResult.short_clips`` traz índices base 0 da lista ``clips`` (não números de cena).

    Regra intencional e conservadora: com 2+ clipes, cada um, já cortado no alvo, precisa durar ao menos
    ``2 * transition_s`` (uma transição na entrada e outra na saída sem se sobreporem). Nas pontas bastaria
    ``transition_s``, mas cenas reais têm 3 a 10 s e o limite simples evita offsets inválidos. A checagem roda
    com as durações de origem, antes de codificar qualquer coisa; erro: ``InputError``.
    """
    specs = list(clips)
    _validate(specs, out, transition_s)
    source_durations = [ffmpeg.probe(Path(c.path)).duration_s for c in specs]
    _check_transition_fits([min(d, c.target_s) for c, d in zip(specs, source_durations, strict=True)], transition_s)
    short = tuple(i for i, (c, d) in enumerate(zip(specs, source_durations, strict=True)) if d < c.target_s - _FRAME_S)
    font = demo_font() if demo else None

    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{out.stem}.work-", dir=out.parent))
    part = out.with_name(f".{out.stem}.part.mp4")  # ``.mp4`` fixo: escolhe o muxer
    try:
        normalized: list[Path] = []
        durations: list[float] = []
        for i, spec in enumerate(specs):
            norm = work / f"n{i:02d}.mp4"
            _normalize(Path(spec.path), spec.target_s, norm)
            info = ffmpeg.probe(norm)
            if not ffmpeg.is_delivery_format(info):
                raise FfmpegError(f"clipe {i} fora do formato após a R2: {info}")
            normalized.append(norm)
            durations.append(info.duration_s)
        _check_transition_fits(durations, transition_s)

        graph = build_filter_complex(durations, transition_s, fontfile=font)
        inputs: list[str] = []
        for norm in normalized:
            inputs += ["-i", str(norm)]
        ffmpeg.run(
            [
                *inputs,
                "-filter_complex", graph,
                "-map", "[v]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", _CRF_DEMO if demo else _CRF_DELIVERY,
                "-movflags", "+faststart",
                "-an", str(part),
            ]
        )  # fmt: skip
        info = ffmpeg.probe(part)
        expected_s = sum(durations) - (len(durations) - 1) * transition_s
        if not ffmpeg.is_delivery_format(info) or abs(info.duration_s - expected_s) > _DURATION_TOLERANCE_S:
            raise FfmpegError(
                f"montagem fora do formato de entrega: {info} (esperado h264 1920x1080 yuv420p 30/1, {expected_s:.2f} s)"
            )
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    return AssemblyResult(out=out, duration_s=info.duration_s, short_clips=short)


def crossfade_offsets(durations: Sequence[float], transition_s: float) -> list[float]:
    """``offset`` de cada ``xfade`` (R3): soma das durações anteriores − k·transição, k = 1..n−1."""
    offsets: list[float] = []
    elapsed = 0.0
    for k, duration in enumerate(durations[:-1], start=1):
        elapsed += duration
        offsets.append(elapsed - k * transition_s)
    return offsets


def build_filter_complex(durations: Sequence[float], transition_s: float, *, fontfile: str | None) -> str:
    """``filter_complex`` com a cadeia de ``xfade`` (R3), o ``drawtext`` da R5 se ``fontfile`` e o rótulo ``[v]``.

    Sempre termina em ``format=yuv420p``. ``fontfile=None`` não desenha a marca.
    """
    tail = ""
    if fontfile is not None:
        if "'" in fontfile or "\\" in fontfile:
            raise FfmpegError(f"caminho da fonte com aspas ou barra invertida não é suportado: {fontfile!r}")
        tail += (
            f"drawtext=fontfile='{fontfile}':text='{_DEMO_TEXT}':fontsize=110:fontcolor=white@0.35"
            ":x=(w-tw)/2:y=(h-th)/2,"
        )
    tail += "format=yuv420p"

    n = len(durations)
    if n == 1:
        return f"[0:v]{tail}[v]"
    fade = _num(transition_s)
    parts: list[str] = []
    left = "0:v"
    for k, offset in enumerate(crossfade_offsets(durations, transition_s), start=1):
        xfade = f"[{left}][{k}:v]xfade=transition=fade:duration={fade}:offset={_num(offset)}"
        if k == n - 1:
            parts.append(f"{xfade},{tail}[v]")
        else:
            left = f"v{k:02d}"
            parts.append(f"{xfade}[{left}]")
    return ";".join(parts)


def demo_font() -> str:
    """Caminho da fonte Inter da R5 (``fc-match``). ``FfmpegError`` se não existir ou se NÃO for a Inter.

    O ``fc-match`` devolve outra fonte em silêncio quando a pedida falta. Recusamos o fallback (em vez de aceitar
    e registrar) porque a marca é parte da identidade do vídeo e uma fonte qualquer pode não ter ``Ç``/``Ã``:
    melhor falhar com a instrução de instalar a Inter do que entregar uma demo com glifos errados.
    """
    path = _fc_match()
    if not path or not Path(path).is_file():
        raise FfmpegError(f"fonte da marca d'água não encontrada ({_FONT_PATTERN} -> {path!r}); instale a fonte Inter")
    if "inter" not in Path(path).name.lower():
        raise FfmpegError(f"o fc-match devolveu uma fonte de fallback ({path}) em vez da Inter; instale a fonte Inter")
    return path


def _fc_match() -> str:
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{file}", _FONT_PATTERN],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise FfmpegError(f"fc-match indisponível; não foi possível localizar a fonte da marca d'água ({exc})") from exc
    return proc.stdout.strip()


def _normalize(src: Path, target_s: float, dst: Path) -> None:
    """R2 com corte: ``-t target_s`` na saída corta clipe longo e deixa o curto inteiro."""
    ffmpeg.run(
        [
            "-i", str(src.resolve()),
            "-t", _num(target_s),
            "-vf", _R2_FILTER,
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", _CRF_DELIVERY,
            str(dst),
        ]
    )  # fmt: skip


def _num(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _validate(specs: list[ClipSpec], out: Path, transition_s: float) -> None:
    if not specs:
        raise InputError("montagem sem clipes")
    if not _positive(transition_s):
        raise InputError(f"duração da transição inválida: {transition_s!r} (esperado número finito > 0)")
    resolved = set()
    for i, spec in enumerate(specs):
        if not _positive(spec.target_s):
            raise InputError(f"alvo inválido no clipe {i}: {spec.target_s!r} (esperado número finito > 0)")
        path = Path(spec.path)
        if not path.is_file():
            raise InputError(f"clipe {i} não encontrado: {path}")
        resolved.add(path.resolve())
    if Path(out).resolve() in resolved:
        raise InputError(f"a saída não pode ser um dos clipes de entrada: {out}")


def _check_transition_fits(durations: Sequence[float], transition_s: float) -> None:
    """Com 2+ clipes, cada um precisa de ao menos 2 transições (entrada e saída) para os offsets fecharem."""
    if len(durations) < 2:
        return
    for i, duration in enumerate(durations):
        if duration < 2 * transition_s:
            raise InputError(
                f"clipe {i} tem {duration:.2f} s, curto demais para a transição de {transition_s:g} s (mínimo {2 * transition_s:g} s)"
            )


def _positive(value: object) -> bool:
    return isinstance(value, int | float) and math.isfinite(value) and value > 0
