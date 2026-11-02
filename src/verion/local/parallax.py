"""Método ``parallax-2.5d`` (spec 3.6): movimento de câmera sobre UMA foto com deslocamento por profundidade.

Fidelidade primeiro (skill ``verion-fidelity-qc``): cada pixel de cada quadro é lido da foto original, só
em outra posição. Nada é sintetizado, nem por IA nem por preenchimento.

Método (``backward warp`` contínuo)
-----------------------------------
1. A foto é orientada pelo EXIF, escalada para cobrir 1920x1080 e recortada ao centro em 16:9 (o mesmo
   enquadramento do ``ken-burns``). O mapa de profundidade (``DepthEstimator``, PNG 16 bits, branco = perto)
   recebe o mesmo enquadramento, é normalizado (percentis 2 e 98) e suavizado.
2. Em cada quadro, o pixel de saída ``(x, y)`` lê a foto na posição ``(xs, ys)``; ``progress`` vai de 0 a 1
   com aceleração e frenagem suaves. ``d`` é a profundidade normalizada (0 = longe, 1 = perto):

   - ``pan-right`` / ``pan-left``: ``xs = x_zoom ± A·s·d``. As camadas próximas deslizam mais que as distantes
     (parallax de translação); a camada ``d = 0`` não se move.
   - ``push-in``: ``(xs, ys) = c + (p - c) / (1 + K·s·d)``. Camadas próximas se aproximam do centro mais depressa
     que as distantes (dolly). ``pull-out`` é o mesmo movimento no sentido inverso do tempo.
   - ``static``: sem movimento (a mesma cadeia, para o resultado sair no mesmo formato).

3. Amostragem bilinear da foto (inteira, 8 bits por canal) e codificação H.264 pela receita da skill
   (``-crf 20``, ``format=yuv420p``, ``+faststart``).

Como as bordas reveladas ficam fiéis (sem inventar conteúdo)
-----------------------------------------------------------
Não há camadas recortadas, logo não há buracos para tapar nem fantasmas. O mapa de deslocamento é contínuo, e as
regiões que o movimento "revela" são pixels vizinhos da PRÓPRIA foto esticados (interpolação). Três travas, todas
testadas:

- **Amplitude**: o deslocamento relativo entre a camada mais próxima e a mais distante é ``SHIFT_FRAC`` (1,5%) da
  largura, abaixo do teto de 2% da spec (``MAX_SHIFT_FRAC``).
- **Suavização da profundidade**: três passadas de filtro de média, de largura ``w``, limitam a inclinação do mapa
  a ``3/(4w)`` por pixel, o que garante amplitude x inclinação < 1. Assim o mapeamento nunca dobra: nada de
  conteúdo espelhado ou duplicado. O custo é que arestas de profundidade viram transições de ~``w`` px (a aresta de
  um móvel "estica" até ``w`` px; é o defeito 4 da skill de QC, e por isso o QC humano é obrigatório).
- **Zoom de segurança** (só nos panoramas): o recorte é ampliado ao redor do centro por ``1/(1 - A·s/cx)`` (no
  máximo ~1,03), exatamente o necessário para que o deslocamento nunca leia fora da foto. Não há borda esticada
  nem preta. No 1º quadro ``s = 0``: zoom 1,00 e quadro idêntico à foto escalada. No ``push-in`` não precisa:
  ler em direção ao centro nunca sai da foto.

Custo R$ 0 (``unit_kind`` "none"), roda em CPU: cerca de 0,1 s por quadro em 1080p medido neste ambiente.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final, cast

import numpy as np
from PIL import Image, ImageOps

from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import FfmpegError, InputError
from verion.local import ffmpeg
from verion.local.depth import DepthEstimator, DepthFromFile
from verion.providers.base import (
    CAMERAS,
    Camera,
    CostEstimate,
    Scene,
    SceneRequest,
    SceneResult,
)

__all__ = [
    "MAX_SHIFT_FRAC",
    "SHIFT_FRAC",
    "ParallaxAdapter",
    "ease",
    "frame_count",
    "prepare_depth",
    "render_frame",
    "source_coords",
]

SHIFT_FRAC: Final = 0.015  # deslocamento relativo máximo entre camadas, em fração da largura
MAX_SHIFT_FRAC: Final = 0.02  # teto da spec (3.6)
_FPS: Final = ffmpeg.DELIVERY_FPS
_OUT_W, _OUT_H = ffmpeg.DELIVERY_WIDTH, ffmpeg.DELIVERY_HEIGHT
_MAX_DURATION_S: Final = 60.0
_DURATION_TOLERANCE_S: Final = 0.05
_BAND_ROWS: Final = 60  # faixas de linhas: os temporários cabem no cache e a amostragem fica ~30% mais rápida
_LOW_PERCENTILE, _HIGH_PERCENTILE = 2.0, 98.0
_MIN_RELIEF: Final = 0.02  # percentil 98 - percentil 2 abaixo de 2% da faixa de 16 bits = mapa plano
_BLUR_PASSES: Final = 3
_ENCODE_TIMEOUT_S: Final = 1800.0

if sys.byteorder != "little":  # pragma: no cover
    raise ImportError("o amostrador do parallax empacota RGBA em uint32 little-endian")


def frame_count(duration_s: float) -> int:
    """Quadros da cena: ``round(duração * 30)``. ``InputError`` se a duração não gerar ao menos 2 quadros."""
    if (
        not isinstance(duration_s, int | float)
        or not math.isfinite(duration_s)
        or duration_s <= 0
        or duration_s > _MAX_DURATION_S
    ):
        raise InputError(f"duração inválida para o parallax: {duration_s!r} (esperado 0 < s <= {_MAX_DURATION_S:g})")
    frames = round(duration_s * _FPS)
    if frames < 2:
        raise InputError(f"duração curta demais para o parallax: {duration_s!r} s ({frames} quadro)")
    return frames


def ease(progress: float) -> float:
    """Aceleração e frenagem suaves (cosseno): 0 → 1 com velocidade nula nas pontas."""
    return 0.5 - 0.5 * math.cos(math.pi * min(max(progress, 0.0), 1.0))


# --- profundidade -----------------------------------------------------------------------------------


def _box_blur(a: np.ndarray, width: int, axis: int) -> np.ndarray:
    """Média móvel de largura ímpar ``width`` ao longo de ``axis`` (bordas replicadas), em O(n) com soma acumulada."""
    moved = np.moveaxis(a, axis, -1)
    radius = width // 2
    padded = np.pad(moved, [(0, 0)] * (moved.ndim - 1) + [(radius, radius)], mode="edge")
    cumulative = np.concatenate(
        [np.zeros(padded.shape[:-1] + (1,), np.float64), np.cumsum(padded, axis=-1, dtype=np.float64)], axis=-1
    )
    n = moved.shape[-1]
    smoothed = (cumulative[..., width : width + n] - cumulative[..., :n]) / width
    return np.moveaxis(smoothed, -1, axis)


def blur_width(amplitude_px: float) -> int:
    """Largura (ímpar) do filtro de média que garante amplitude x inclinação < 0,8: ``w >= 0,9375 * amplitude``."""
    width = math.ceil(0.9375 * amplitude_px)
    return max(1, width + (width + 1) % 2)


def prepare_depth(raw: np.ndarray) -> np.ndarray:
    """Mapa na resolução de saída → ``float32`` em [0, 1] (0 = longe, 1 = perto), suavizado.

    Normaliza pelos percentis 2 e 98 (um pixel quente não achata o resto do mapa). Mapa quase plano levanta
    ``InputError``: viraria um vídeo parado com cara de parallax e contaminaria a comparação de métodos.
    """
    values = np.asarray(raw, dtype=np.float64)
    if values.ndim != 2:
        raise InputError(f"mapa de profundidade deve ser 2D, veio {values.shape}")
    low, high = np.percentile(values, [_LOW_PERCENTILE, _HIGH_PERCENTILE])
    if high - low < _MIN_RELIEF * 65535.0:
        raise InputError(
            "mapa de profundidade sem relevo (quase plano): o parallax não teria o que deslocar; "
            "gere o mapa de novo ou use o ken-burns"
        )
    norm = np.clip((values - low) / (high - low), 0.0, 1.0)
    width = blur_width(SHIFT_FRAC * values.shape[1])
    for axis in (1, 0):
        for _ in range(_BLUR_PASSES):
            norm = _box_blur(norm, width, axis)
    return np.clip(norm, 0.0, 1.0).astype(np.float32)


# --- geometria do deslocamento ----------------------------------------------------------------------


def source_coords(
    camera: Camera, depth: np.ndarray, progress: float, rows: slice = slice(None)
) -> tuple[np.ndarray, np.ndarray]:
    """Posição ``(xs, ys)`` na foto lida por cada pixel de saída (``float32``), nas linhas ``rows``.

    ``depth``: mapa preparado (``prepare_depth``), 0 = longe, 1 = perto. ``progress`` em [0, 1] ao longo da cena.
    Sem recorte: os testes verificam que o resultado já fica dentro de ``[0, largura-1] x [0, altura-1]``.
    """
    if camera not in CAMERAS:
        raise InputError(f"câmera inválida para o parallax: {camera!r}; válidas: {', '.join(sorted(CAMERAS))}")
    height, width = depth.shape
    cx, cy = (width - 1) / 2, (height - 1) / 2
    row_ids = np.arange(height, dtype=np.float32)[rows][:, None]
    col_ids = np.arange(width, dtype=np.float32)[None, :]
    d = depth[rows]
    amplitude = SHIFT_FRAC * width

    if camera == "static":
        s = 0.0
    elif camera == "pull-out":
        s = ease(1.0 - progress)  # push-in no sentido inverso do tempo
    else:
        s = ease(progress)

    if camera in ("pan-left", "pan-right"):
        zoom = 1.0 / (1.0 - amplitude * s / cx)  # o mínimo para o deslocamento nunca sair da foto
        sign = 1.0 if camera == "pan-right" else -1.0  # a câmera vai à direita → o conteúdo desliza à esquerda
        xs = cx + (col_ids - cx) / zoom + sign * amplitude * s * d
        ys = np.broadcast_to(cy + (row_ids - cy) / zoom, xs.shape)
    else:  # push-in, pull-out, static
        k = amplitude / math.hypot(cx, cy)  # no canto, a camada mais próxima desloca no máximo ``amplitude``
        inv = 1.0 / (1.0 + k * s * d)
        xs = cx + (col_ids - cx) * inv
        ys = cy + (row_ids - cy) * inv
    return xs.astype(np.float32, copy=False), np.asarray(ys, dtype=np.float32)


_LANES = np.uint32(0x00FF00FF)
_HALF = np.uint32(0x00800080)  # +128 em cada faixa de 16 bits antes do ``>> 8``: arredonda em vez de truncar


def _lerp(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> np.ndarray:
    """``a + (b - a) * w/256`` nos 4 canais de pixels RGBA empacotados em uint32 (2 canais por passada, 16 bits cada)."""
    inv = np.uint32(256) - w
    rb = (((a & _LANES) * inv + (b & _LANES) * w + _HALF) >> 8) & _LANES
    ga = ((((a >> 8) & _LANES) * inv + ((b >> 8) & _LANES) * w + _HALF) >> 8) & _LANES
    return rb | (ga << 8)


def _bilinear(canvas: np.ndarray, width: int, height: int, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Amostra ``canvas`` (RGBA em uint32, ``height*width``) em ``(xs, ys)`` com interpolação bilinear."""
    xs = np.clip(xs, 0.0, width - 1)  # só contra erro de arredondamento: ``source_coords`` já fica dentro
    ys = np.clip(ys, 0.0, height - 1)
    x_floor, y_floor = np.floor(xs), np.floor(ys)
    wx = ((xs - x_floor) * 256 + 0.5).astype(np.uint32)
    wy = ((ys - y_floor) * 256 + 0.5).astype(np.uint32)
    x0, y0 = x_floor.astype(np.int32), y_floor.astype(np.int32)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    r0, r1 = y0 * width, y1 * width
    top = _lerp(canvas[r0 + x0], canvas[r0 + x1], wx)
    bottom = _lerp(canvas[r1 + x0], canvas[r1 + x1], wx)
    return _lerp(top, bottom, wy)


def render_frame(canvas: np.ndarray, depth: np.ndarray, camera: Camera, progress: float) -> np.ndarray:
    """Um quadro RGBA (``uint32``, ``altura x largura``) do ``canvas`` (foto RGBA achatada) no instante ``progress``."""
    height, width = depth.shape
    if camera == "static":
        return canvas.reshape(height, width)
    out = np.empty((height, width), dtype=np.uint32)
    for top in range(0, height, _BAND_ROWS):
        rows = slice(top, min(top + _BAND_ROWS, height))
        xs, ys = source_coords(camera, depth, progress, rows)
        out[rows] = _bilinear(canvas, width, height, xs, ys)
    return out


# --- entrada e saída --------------------------------------------------------------------------------


def _open_photo(photo: Path) -> Image.Image:
    """Foto com a orientação EXIF aplicada, em RGB. ``InputError`` se o arquivo não for uma imagem legível."""
    try:
        with Image.open(photo) as img:
            oriented = ImageOps.exif_transpose(img)
            return oriented.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InputError(f"foto ilegível: {photo} ({exc})") from exc


def _canvas(photo: Image.Image) -> np.ndarray:
    """Foto cobrindo 1920x1080 com recorte central 16:9, como RGBA empacotado em uint32."""
    fitted = ImageOps.fit(photo, (_OUT_W, _OUT_H), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    rgba = np.ascontiguousarray(np.asarray(fitted.convert("RGBA"), dtype=np.uint8))
    return rgba.view(np.uint32).reshape(-1)


def _read_depth(path: Path, expected_size: tuple[int, int]) -> np.ndarray:
    """Mapa ``float64`` na resolução de saída, com o mesmo enquadramento da foto. ``InputError`` se violar o contrato."""
    try:
        with Image.open(path) as img:
            img.load()
            mode, size = img.mode, img.size
            if mode != "I;16":
                raise InputError(
                    f"mapa de profundidade deve ser PNG de 16 bits em cinza (modo I;16), veio {mode!r}: {path}"
                )
            if size != expected_size:
                raise InputError(
                    f"mapa de profundidade {size[0]}x{size[1]} difere da foto orientada "
                    f"{expected_size[0]}x{expected_size[1]} (a orientação EXIF já aplicada): {path}"
                )
            raw = np.asarray(img, dtype=np.float32)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InputError(f"mapa de profundidade ilegível: {path} ({exc})") from exc
    fitted = ImageOps.fit(Image.fromarray(raw), (_OUT_W, _OUT_H), Image.Resampling.BICUBIC, centering=(0.5, 0.5))
    return np.asarray(fitted, dtype=np.float64)


def _encode(frames: Iterable[np.ndarray], count: int, part: Path, *, timeout_s: float = _ENCODE_TIMEOUT_S) -> None:
    """Envia ``count`` quadros RGBA (``uint32``) ao ffmpeg por pipe e grava ``part`` (receita da skill, ``-crf 20``)."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{_OUT_W}x{_OUT_H}", "-r", str(_FPS), "-i", "pipe:0",
        "-vf", "format=yuv420p",
        "-frames:v", str(count),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart",
        "-an", str(part),
    ]  # fmt: skip
    deadline = time.monotonic() + timeout_s
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        except FileNotFoundError as exc:
            raise FfmpegError("ffmpeg não encontrado no PATH; instale o ffmpeg", stderr=str(exc)) from exc
        assert proc.stdin is not None
        try:
            try:
                for frame in frames:
                    if time.monotonic() > deadline:
                        raise FfmpegError(f"parallax excedeu o tempo limite de {timeout_s:g} s")
                    proc.stdin.write(memoryview(np.ascontiguousarray(frame)).cast("B"))
            except BrokenPipeError:
                pass  # o ffmpeg encerrou antes: o stderr abaixo explica
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            code = proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    if code != 0:
        raise FfmpegError(f"ffmpeg terminou com código {code}: {stderr[-2000:].strip()}", stderr=stderr)


class ParallaxAdapter:
    """Gera a cena com parallax 2,5D por profundidade. ``client`` é o ``DepthEstimator`` (padrão ``DepthFromFile``)."""

    method = "parallax-2.5d"
    provider = "local"
    model = "depth-displacement"

    def __init__(self, prices: PriceTable, client: object | None = None) -> None:
        self._prices = prices
        # Só guarda a referência: o contrato proíbe ler atributos do client no construtor.
        self._depth: DepthEstimator = cast("DepthEstimator", client if client is not None else DepthFromFile())

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=0.0, unit_kind=self._prices.unit_kind_of(self.method))

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        """Renderiza ``req.out_path`` (H.264 1920x1080 yuv420p 30 fps). O arquivo só aparece ao final."""
        token.consume(self.method)
        scene = req.scene
        frames = frame_count(scene.duration_s)
        if scene.camera not in CAMERAS:
            raise InputError(f"câmera inválida para o parallax: {scene.camera!r}; válidas: {', '.join(sorted(CAMERAS))}")
        photo = Path(scene.photo)
        if not photo.is_file():
            raise InputError(f"foto da cena {scene.index} não encontrada: {photo}")

        image = _open_photo(photo)
        depth_path = Path(self._depth.depth(photo))  # DepthMissing sobe como está
        depth = prepare_depth(_read_depth(depth_path, image.size))
        canvas = _canvas(image)

        out = Path(req.out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_name(f".{out.stem}.part.mp4")  # ``.mp4`` fixo: escolhe o muxer
        try:
            _encode(self._frames(canvas, depth, scene.camera, frames), frames, part)
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
            meta={
                "recipe": "depth-displacement",
                "camera": scene.camera,
                "frames": str(frames),
                "shift_px": f"{SHIFT_FRAC * _OUT_W:.1f}",
                "depth_file": depth_path.name,
            },
        )

    @staticmethod
    def _frames(canvas: np.ndarray, depth: np.ndarray, camera: Camera, count: int) -> Iterator[np.ndarray]:
        for n in range(count):
            yield render_frame(canvas, depth, camera, n / (count - 1))
