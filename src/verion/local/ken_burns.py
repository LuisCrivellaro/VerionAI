"""Método ``ken-burns`` (spec 3.6): movimento de câmera conservador sobre UMA foto, receita R1.

A foto é escalada para 3840x2160 (2x a saída, evita tremido), recortada ao centro em 16:9 e passa pelo
``zoompan`` até 1920x1080 a 30 fps. Nada é sintetizado: cada quadro é um recorte da foto, no máximo com
zoom 1,06 (acima de ~1,10 a receita amplia defeitos da foto). É o método mais fiel e o fallback do
roteiro; custa R$ 0 (``unit_kind`` "none") e roda localmente.

Câmera → expressões do ``zoompan`` (``on`` = índice do quadro de saída, ``N`` = quadros, ``p = on/(N-1)``):

- ``push-in``:   zoom 1,00 → 1,06, centrado;
- ``pull-out``:  zoom 1,06 → 1,00, centrado;
- ``pan-right``: zoom fixo 1,06, a janela vai da borda esquerda à direita da foto;
- ``pan-left``:  zoom fixo 1,06, a janela vai da borda direita à esquerda;
- ``static``:    zoom 1,00, sem movimento (mesma cadeia, para o resultado sair no mesmo formato).
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import FfmpegError, InputError
from verion.local import ffmpeg
from verion.providers.base import (
    CAMERAS,
    Camera,
    CostEstimate,
    Scene,
    SceneRequest,
    SceneResult,
)

__all__ = ["KenBurnsAdapter", "build_filter", "frame_count", "zoompan_expressions"]

ZOOM_MAX: Final = 1.06
_ZOOM_SPAN: Final = ZOOM_MAX - 1.0
_FPS: Final = ffmpeg.DELIVERY_FPS
_UPSCALE_W, _UPSCALE_H = 3840, 2160  # 2x a saída, antes do zoompan
_MAX_DURATION_S: Final = 60.0  # muito acima dos 3 a 10 s do roteiro; só barra valor absurdo
_DURATION_TOLERANCE_S: Final = 0.05

_CENTER_X: Final = "iw/2-(iw/zoom/2)"
_CENTER_Y: Final = "ih/2-(ih/zoom/2)"

_Runner = Callable[[list[str]], None]


def frame_count(duration_s: float) -> int:
    """Quadros da cena: ``round(duração * 30)``. ``InputError`` se a duração não gerar ao menos 2 quadros."""
    if (
        not isinstance(duration_s, int | float)
        or not math.isfinite(duration_s)
        or duration_s <= 0
        or duration_s > _MAX_DURATION_S
    ):
        raise InputError(f"duração inválida para o ken-burns: {duration_s!r} (esperado 0 < s <= {_MAX_DURATION_S:g})")
    frames = round(duration_s * _FPS)
    if frames < 2:
        raise InputError(f"duração curta demais para o ken-burns: {duration_s!r} s ({frames} quadro)")
    return frames


def zoompan_expressions(camera: Camera, frames: int) -> tuple[str, str, str]:
    """Expressões ``(z, x, y)`` do ``zoompan`` para a câmera. ``InputError`` se a câmera não existir."""
    if camera not in CAMERAS:
        raise InputError(f"câmera inválida para o ken-burns: {camera!r}; válidas: {', '.join(sorted(CAMERAS))}")
    last = max(frames - 1, 1)
    progress = f"on/{last}"
    span = f"{_ZOOM_SPAN:.2f}"
    match camera:
        case "push-in":
            return f"min(1+{span}*{progress},{ZOOM_MAX})", _CENTER_X, _CENTER_Y
        case "pull-out":
            return f"min({ZOOM_MAX},max(1,{ZOOM_MAX}-{span}*{progress}))", _CENTER_X, _CENTER_Y
        case "pan-right":
            return f"{ZOOM_MAX}", f"(iw-iw/zoom)*{progress}", _CENTER_Y
        case "pan-left":
            return f"{ZOOM_MAX}", f"(iw-iw/zoom)*(1-{progress})", _CENTER_Y
        case _:  # static
            return "1", "0", "0"


def build_filter(camera: Camera, frames: int) -> str:
    """Cadeia R1 completa, sempre terminada em ``format=yuv420p``."""
    z, x, y = zoompan_expressions(camera, frames)
    return (
        f"scale={_UPSCALE_W}:{_UPSCALE_H}:force_original_aspect_ratio=increase,"
        f"crop={_UPSCALE_W}:{_UPSCALE_H},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}"
        f":s={ffmpeg.DELIVERY_WIDTH}x{ffmpeg.DELIVERY_HEIGHT}:fps={_FPS},"
        "format=yuv420p"
    )


class KenBurnsAdapter:
    """Gera a cena com a receita R1 do ffmpeg. ``client`` é o executor (padrão ``ffmpeg.run``)."""

    method = "ken-burns"
    provider = "local"
    model = "ffmpeg-zoompan"

    def __init__(self, prices: PriceTable, client: object | None = None) -> None:
        self._prices = prices
        # Só guarda a referência: o contrato proíbe ler atributos do client no construtor.
        self._run: _Runner = cast("_Runner", client if client is not None else ffmpeg.run)

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=0.0, unit_kind=self._prices.unit_kind_of(self.method))

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        """Renderiza ``req.out_path`` (H.264 1920x1080 yuv420p 30 fps). O arquivo só aparece ao final."""
        token.consume(self.method)
        scene = req.scene
        frames = frame_count(scene.duration_s)
        vf = build_filter(scene.camera, frames)
        photo = Path(scene.photo)
        if not photo.is_file():
            raise InputError(f"foto da cena {scene.index} não encontrada: {photo}")

        out = Path(req.out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_name(f".{out.stem}.part.mp4")  # ``.mp4`` fixo: escolhe o muxer
        try:
            self._run(
                [
                    "-i", str(photo.resolve()),
                    "-vf", vf,
                    "-frames:v", str(frames),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-movflags", "+faststart",
                    "-an", str(part),
                ]
            )  # fmt: skip
            info = ffmpeg.probe(part)
            expected_s = frames / _FPS
            if not ffmpeg.is_delivery_format(info) or abs(info.duration_s - expected_s) > _DURATION_TOLERANCE_S:
                raise FfmpegError(
                    f"saída fora do formato de entrega: {info} (esperado h264 1920x1080 yuv420p 30/1, {expected_s:.2f} s)"
                )
            os.replace(part, out)
        finally:
            part.unlink(missing_ok=True)
        return SceneResult(
            clip_path=out,
            duration_s=info.duration_s,
            width=info.width,
            height=info.height,
            fps=info.fps_value,
            units=0.0,
            unit_kind=self._prices.unit_kind_of(self.method),
            cost_is_estimate=False,
            provider_job_id=None,
            meta={"recipe": "R1", "camera": scene.camera, "frames": str(frames), "zoom_max": str(ZOOM_MAX)},
        )
