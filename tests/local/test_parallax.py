"""Testes do ``ParallaxAdapter`` e do ``DepthFromFile`` (T6): 2,5D por profundidade, fidelidade e contrato.

Sem rede e sem chave: fotos e mapas de profundidade são sintéticos (ffmpeg/lavfi, Pillow e numpy).
A matemática do deslocamento é testada em numpy puro (rápida); poucas cenas passam pelo ffmpeg real.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import numpy as np
import pytest
from PIL import Image, ImageOps

from verion import budget
from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import DepthMissing, FfmpegError, InputError, TokenInvalid
from verion.ledger import repo
from verion.local import ffmpeg, parallax
from verion.local.depth import DepthEstimator, DepthFromFile, depth_path_for
from verion.local.parallax import ParallaxAdapter
from verion.providers import registry
from verion.providers.base import Camera, Scene, SceneRequest, SceneResult
from verion.providers.contract import assert_generator_contract

CAMERAS: tuple[str, ...] = get_args(Camera)
METHOD = "parallax-2.5d"
W, H = 1920, 1080


# --- helpers ---------------------------------------------------------------------------------------


def make_scene(photo: Path, camera: str = "pan-right", duration_s: float = 3.0) -> Scene:
    return Scene(
        index=1,
        photo=photo,
        room="sala",
        camera=camera,  # type: ignore[arg-type]
        duration_s=duration_s,
        prompt="Parallax suave por profundidade. Não adicionar objetos.",
    )


def new_token(conn: sqlite3.Connection, prices: PriceTable, scene: Scene) -> tuple[int, SpendToken]:
    run_id = repo.create_run(conn, property_id="casa-1", method=METHOD, script_path="r.toml", script_sha256="0" * 64)
    token = budget.reserve(
        conn,
        prices,
        run_id=run_id,
        scene_index=scene.index,
        target_s=5.0,
        method=METHOD,
        provider="local",
        model=ParallaxAdapter.model,
        units=0.0,
        unit_kind="none",
        allow_paid=False,
    )
    return run_id, token


def generate(
    conn: sqlite3.Connection, prices: PriceTable, scene: Scene, out_path: Path, client: object | None = None
) -> SceneResult:
    run_id, token = new_token(conn, prices, scene)
    req = SceneRequest(scene=scene, run_id=run_id, attempt_no=token.attempt_no, out_path=out_path)
    return ParallaxAdapter(prices, client).generate(req, token)


def oriented_size(photo: Path) -> tuple[int, int]:
    return ImageOps.exif_transpose(Image.open(photo)).size  # type: ignore[union-attr]


def write_depth(photo: Path, size: tuple[int, int] | None = None, kind: str = "gradient") -> Path:
    """Escreve ``<foto>.depth.png`` (16 bits cinza). Padrão: gradiente vertical (perto embaixo, como um piso)."""
    width, height = size or oriented_size(photo)
    if kind == "gradient":
        arr = np.repeat(np.linspace(0, 65535, height)[:, None], width, axis=1)
    elif kind == "flat":
        arr = np.full((height, width), 30000.0)
    else:  # pragma: no cover
        raise ValueError(kind)
    path = depth_path_for(photo)
    Image.fromarray(arr.astype(np.uint16)).save(path)
    return path


def make_halves_jpeg(path: Path, stored: tuple[int, int], split: str, exif_orientation: int | None = None) -> Path:
    """JPEG com duas metades: vermelha/azul. ``split``: "lr" (esquerda/direita) ou "tb" (cima/baixo)."""
    w, h = stored
    img = Image.new("RGB", stored, (255, 0, 0))
    box = (w // 2, 0, w, h) if split == "lr" else (0, h // 2, w, h)
    img.paste((0, 0, 255), box)
    kwargs: dict[str, Any] = {"quality": 95}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    img.save(path, **kwargs)
    return path


def frame_at(clip: Path, which: str, out: Path) -> np.ndarray:
    """Primeiro ('first') ou último ('last') quadro do clipe, como RGB float32."""
    if which == "first":
        ffmpeg.run(["-i", str(clip), "-frames:v", "1", str(out)])
    else:
        ffmpeg.run(["-sseof", "-0.1", "-i", str(clip), "-update", "1", str(out)])
    return np.asarray(Image.open(out).convert("RGB"), dtype=np.float32)


def ssim_first_frame(clip: Path, photo: Path, work: Path) -> float:
    """SSIM (filtro ``ssim`` do ffmpeg) do 1º quadro do clipe contra a foto escalada para cobrir 1920x1080 e
    recortada ao centro. A referência é montada pelo próprio ffmpeg, que também aplica a orientação EXIF."""
    stats = work / "ssim.log"
    graph = (
        "[0:v]format=yuv420p[a];"
        "[1:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p[b];"
        f"[a][b]ssim=stats_file={stats}"
    )
    ffmpeg.run(["-i", str(clip), "-i", str(photo), "-filter_complex", graph, "-frames:v", "1", "-f", "null", "-"])
    found = re.search(r"All:([0-9.]+)", stats.read_text())
    assert found, stats.read_text()
    return float(found.group(1))


@pytest.fixture(scope="module")
def prices_mod() -> PriceTable:
    return PriceTable(fx_usd_brl=5.0, methods={})


# --- DepthFromFile ----------------------------------------------------------------------------------


def test_depth_path_is_photo_name_plus_suffix(tmp_path: Path) -> None:
    photo = tmp_path / "sala.jpg"
    assert depth_path_for(photo) == tmp_path / "sala.jpg.depth.png"


def test_depth_from_file_returns_the_neighbor_png(tmp_path: Path) -> None:
    photo = tmp_path / "sala.jpg"
    photo.write_bytes(b"x")
    expected = write_depth_stub(photo)
    assert DepthFromFile().depth(photo) == expected


def write_depth_stub(photo: Path) -> Path:
    path = depth_path_for(photo)
    path.write_bytes(b"png")
    return path


def test_depth_from_file_missing_is_depth_missing(tmp_path: Path) -> None:
    photo = tmp_path / "sala.jpg"
    photo.write_bytes(b"x")
    with pytest.raises(DepthMissing, match=r"sala\.jpg\.depth\.png"):
        DepthFromFile().depth(photo)
    assert issubclass(DepthMissing, InputError)


def test_depth_from_file_satisfies_the_protocol() -> None:
    assert isinstance(DepthFromFile(), DepthEstimator)


# --- matemática do deslocamento (numpy puro) --------------------------------------------------------

SMALL_W, SMALL_H = 192, 108
PROGRESS = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)


def hard_depth_maps() -> dict[str, np.ndarray]:
    """Mapas de profundidade adversariais, já no formato que o parallax usa (valores em [0,1])."""
    rng = np.random.default_rng(7)
    checker = (np.indices((SMALL_H, SMALL_W)).sum(axis=0) // 6 % 2).astype(np.float32)
    left_near = np.zeros((SMALL_H, SMALL_W), np.float32)
    left_near[:, : SMALL_W // 2] = 1.0
    top_near = np.zeros((SMALL_H, SMALL_W), np.float32)
    top_near[: SMALL_H // 2] = 1.0
    return {
        "noise": rng.random((SMALL_H, SMALL_W)).astype(np.float32),
        "checker": checker,
        "step-x": left_near,
        "step-y": top_near,
        "gradient": np.repeat(np.linspace(0, 1, SMALL_H, dtype=np.float32)[:, None], SMALL_W, axis=1),
    }


@pytest.fixture(scope="module")
def prepared_maps() -> dict[str, np.ndarray]:
    """Os mapas adversariais depois de ``prepare_depth`` (normalização + suavização)."""
    return {name: parallax.prepare_depth(raw * 65535.0) for name, raw in hard_depth_maps().items()}


def test_prepare_depth_is_normalized_float32(prepared_maps: dict[str, np.ndarray]) -> None:
    for name, d in prepared_maps.items():
        assert d.dtype == np.float32 and d.shape == (SMALL_H, SMALL_W), name
        assert 0.0 <= float(d.min()) and float(d.max()) <= 1.0, name


def test_prepare_depth_rejects_flat_map() -> None:
    with pytest.raises(InputError, match="relevo"):
        parallax.prepare_depth(np.full((SMALL_H, SMALL_W), 30000.0))


def test_prepare_depth_slope_is_bounded_so_the_warp_cannot_fold(prepared_maps: dict[str, np.ndarray]) -> None:
    """A suavização limita a inclinação da profundidade: amplitude * inclinação < 1 em qualquer mapa."""
    amplitude = parallax.SHIFT_FRAC * SMALL_W
    for name, d in prepared_maps.items():
        slope = max(float(np.abs(np.diff(d, axis=0)).max()), float(np.abs(np.diff(d, axis=1)).max()))
        assert slope * amplitude < 0.8, (name, slope * amplitude)


def coords(camera: str, depth: np.ndarray | float, progress: float) -> tuple[np.ndarray, np.ndarray]:
    d = depth if isinstance(depth, np.ndarray) else np.full((SMALL_H, SMALL_W), depth, np.float32)
    return parallax.source_coords(camera, d, progress)  # type: ignore[arg-type]


def grid() -> tuple[np.ndarray, np.ndarray]:
    return np.meshgrid(np.arange(SMALL_W, dtype=np.float32), np.arange(SMALL_H, dtype=np.float32))


@pytest.mark.parametrize("camera", CAMERAS)
def test_rest_frame_is_the_photo_itself(camera: str, prepared_maps: dict[str, np.ndarray]) -> None:
    """1º quadro (último no pull-out) lê cada pixel da própria posição: o quadro de repouso é a foto."""
    gx, gy = grid()
    progress = 1.0 if camera == "pull-out" else 0.0
    for name, d in prepared_maps.items():
        xs, ys = coords(camera, d, progress)
        assert np.allclose(xs, gx, atol=1e-4) and np.allclose(ys, gy, atol=1e-4), (camera, name)


@pytest.mark.parametrize("camera", CAMERAS)
def test_every_output_pixel_reads_inside_the_photo(camera: str, prepared_maps: dict[str, np.ndarray]) -> None:
    """Fidelidade: nunca se lê fora da foto (sem borda esticada ou preta) em nenhum quadro, com mapas adversariais."""
    for name, d in prepared_maps.items():
        for p in PROGRESS:
            xs, ys = coords(camera, d, p)
            assert float(xs.min()) >= -1e-3 and float(xs.max()) <= SMALL_W - 1 + 1e-3, (camera, name, p)
            assert float(ys.min()) >= -1e-3 and float(ys.max()) <= SMALL_H - 1 + 1e-3, (camera, name, p)


@pytest.mark.parametrize("camera", CAMERAS)
def test_warp_never_folds(camera: str, prepared_maps: dict[str, np.ndarray]) -> None:
    """Sem dobra: a posição de leitura cresce com a de saída (nada de conteúdo espelhado ou duplicado)."""
    for name, d in prepared_maps.items():
        for p in PROGRESS:
            xs, ys = coords(camera, d, p)
            assert float(np.diff(xs, axis=1).min()) > 0.0, (camera, name, p, "x")
            assert float(np.diff(ys, axis=0).min()) > 0.0, (camera, name, p, "y")


def full_res_hard_maps() -> dict[str, np.ndarray]:
    """Xadrez de 40 px e degrau, em 1920x1080 (a resolução real), já preparados por ``prepare_depth``."""
    checker = (np.indices((H, W)).sum(axis=0) // 40 % 2).astype(np.float64)
    step = np.zeros((H, W))
    step[:, W // 2 :] = 1.0
    return {"checker-40px": parallax.prepare_depth(checker * 65535.0), "step": parallax.prepare_depth(step * 65535.0)}


@pytest.fixture(scope="module")
def full_res_maps() -> dict[str, np.ndarray]:
    return full_res_hard_maps()


def test_blur_width_bound_holds_analytically_at_1920() -> None:
    """Inclinação máxima da profundidade suavizada (3/(4w) por pixel) x amplitude < 0,8, em 1920 de largura.

    Em 192x108 o arredondamento de ``blur_width`` esconde um filtro pela metade; em 1920 a conta é justa.
    """
    amplitude = parallax.SHIFT_FRAC * W
    assert amplitude * 3 / (4 * parallax.blur_width(amplitude)) < 0.8


@pytest.mark.parametrize("camera", ["pan-right", "pan-left", "push-in", "pull-out"])
def test_warp_never_folds_at_full_resolution(camera: str, full_res_maps: dict[str, np.ndarray]) -> None:
    """Em 1920x1080, com xadrez de 40 px e degrau: dxs/dx > 0, dys/dy > 0 e det(J) > 0 (nada espelhado)."""
    for name, d in full_res_maps.items():
        for p in (0.0, 0.5, 1.0):
            xs, ys = parallax.source_coords(camera, d, p)  # type: ignore[arg-type]
            xs_x, xs_y = np.diff(xs, axis=1)[:-1], np.diff(xs, axis=0)[:, :-1]
            ys_x, ys_y = np.diff(ys, axis=1)[:-1], np.diff(ys, axis=0)[:, :-1]
            assert float(xs_x.min()) > 0.0, (camera, name, p, "dxs/dx")
            assert float(ys_y.min()) > 0.0, (camera, name, p, "dys/dy")
            det = xs_x * ys_y - xs_y * ys_x
            assert float(det.min()) > 0.0, (camera, name, p, "det(J)", float(det.min()))


@pytest.mark.parametrize("camera", CAMERAS)
def test_shift_between_nearest_and_farthest_layer_is_at_most_2_percent_of_width(camera: str) -> None:
    """Amplitude: deslocamento relativo entre a camada mais próxima e a mais distante <= 2% da largura."""
    limit = parallax.MAX_SHIFT_FRAC * SMALL_W
    assert parallax.SHIFT_FRAC <= parallax.MAX_SHIFT_FRAC == 0.02
    peak = 0.0
    for p in np.linspace(0, 1, 21):
        near_x, near_y = coords(camera, 1.0, float(p))
        far_x, far_y = coords(camera, 0.0, float(p))
        peak = max(peak, float(np.abs(near_x - far_x).max()), float(np.abs(near_y - far_y).max()))
    assert peak <= limit + 1e-3
    if camera != "static":
        assert peak > 0.5 * limit, "o efeito precisa ser visível, não só respeitar o teto"


def test_static_camera_never_moves() -> None:
    gx, gy = grid()
    for p in PROGRESS:
        xs, ys = coords("static", 0.7, p)
        assert np.array_equal(xs, gx) and np.array_equal(ys, gy)


def test_pan_moves_near_layers_more_and_in_opposite_directions() -> None:
    gx, _ = grid()
    right_near, _ = coords("pan-right", 1.0, 1.0)
    right_far, _ = coords("pan-right", 0.0, 1.0)
    left_near, _ = coords("pan-left", 1.0, 1.0)
    # pan-right: a câmera vai para a direita, o conteúdo próximo desliza para a esquerda (lê mais à direita)
    assert float((right_near - right_far).mean()) > 0
    assert float((left_near - coords("pan-left", 0.0, 1.0)[0]).mean()) < 0
    # a camada distante quase não se move em relação ao zoom de segurança comum às duas
    assert float(np.abs(right_far - gx).max()) < float(np.abs(right_near - gx).max())


def test_push_in_pulls_near_layers_toward_the_center_more() -> None:
    gx, gy = grid()
    cx, cy = (SMALL_W - 1) / 2, (SMALL_H - 1) / 2
    near_x, near_y = coords("push-in", 1.0, 1.0)
    far_x, _ = coords("push-in", 0.0, 1.0)
    radius_out = np.hypot(gx - cx, gy - cy)
    radius_near = np.hypot(near_x - cx, near_y - cy)
    assert float((radius_near / np.maximum(radius_out, 1e-6))[radius_out > 10].max()) < 1.0
    assert np.allclose(far_x, gx), "a camada mais distante não se move no push-in"


def test_pull_out_is_the_time_reverse_of_push_in() -> None:
    d = hard_depth_maps()["noise"]
    for p in (0.0, 0.3, 0.8, 1.0):
        a = coords("pull-out", d, p)
        b = coords("push-in", d, 1.0 - p)
        assert np.allclose(a[0], b[0]) and np.allclose(a[1], b[1])


def test_motion_is_eased_at_both_ends() -> None:
    """Movimento conservador: velocidade nula no início e no fim (sem arrancada)."""
    s = [parallax.ease(p) for p in (0.0, 0.01, 0.5, 0.99, 1.0)]
    assert s[0] == 0.0 and s[-1] == pytest.approx(1.0) and s[2] == pytest.approx(0.5)
    assert s[1] < 0.001 and s[3] > 0.999


def test_render_frame_matches_a_reference_bilinear_sampler() -> None:
    """A amostragem inteira (SWAR) concorda com bilinear em ponto flutuante em +-3 níveis (peso em 1/256 e arredondamento em 3 passos)."""
    rng = np.random.default_rng(3)
    rgb = rng.integers(0, 256, (SMALL_H, SMALL_W, 3), dtype=np.uint8)
    canvas = np.dstack([rgb, np.full((SMALL_H, SMALL_W), 255, np.uint8)])
    canvas32 = np.ascontiguousarray(canvas).view(np.uint32).reshape(-1)
    d = hard_depth_maps()["gradient"]
    d = parallax.prepare_depth(d * 65535.0)
    out = parallax.render_frame(canvas32, d, "pan-right", 0.7).view(np.uint8).reshape(SMALL_H, SMALL_W, 4)
    xs, ys = parallax.source_coords("pan-right", d, 0.7)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    x1, y1 = np.minimum(x0 + 1, SMALL_W - 1), np.minimum(y0 + 1, SMALL_H - 1)
    fx, fy = (xs - x0)[..., None], (ys - y0)[..., None]
    src = rgb.astype(np.float32)
    ref = (
        src[y0, x0] * (1 - fx) * (1 - fy) + src[y0, x1] * fx * (1 - fy) + src[y1, x0] * (1 - fx) * fy + src[y1, x1] * fx * fy
    )
    err = np.abs(out[..., :3].astype(np.float32) - ref)
    assert float(err.max()) <= 3.0 and float(err.mean()) < 0.6
    assert (out[..., 3] == 255).all()


# --- adaptador sem ffmpeg real ----------------------------------------------------------------------


def test_construction_does_not_touch_client() -> None:
    class Hostile:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"construtor leu o atributo {name!r} do client")

    ParallaxAdapter(PriceTable(fx_usd_brl=5.0, methods={}), Hostile())


def test_identity_estimate_and_registry(prices: PriceTable, tmp_path: Path) -> None:
    gen = ParallaxAdapter(prices)
    assert (gen.method, gen.provider) == (METHOD, "local")
    est = gen.estimate(make_scene(tmp_path / "x.jpg"))
    assert (est.units, est.unit_kind) == (0.0, "none")
    assert registry.METHODS[METHOD] == "verion.local.parallax:ParallaxAdapter"


def test_default_client_is_depth_from_file(prices: PriceTable) -> None:
    assert isinstance(ParallaxAdapter(prices)._depth, DepthFromFile)


def tiny_photo(tmp_path: Path, name: str = "foto.jpg", size: tuple[int, int] = (64, 48)) -> Path:
    path = tmp_path / name
    Image.new("RGB", size, (90, 120, 150)).save(path)
    return path


class Recorder:
    """Estimador falso: registra as chamadas e devolve (ou levanta) o que for configurado."""

    def __init__(self, result: Path | Exception) -> None:
        self.result = result
        self.calls: list[Path] = []

    def depth(self, photo: Path) -> Path:
        self.calls.append(photo)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_estimator_is_called_once_with_the_scene_photo_and_missing_depth_leaves_nothing(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tiny_photo(tmp_path)
    out = tmp_path / "out" / "s01.mp4"
    estimator = Recorder(DepthMissing("sem mapa"))
    with pytest.raises(DepthMissing):
        generate(conn, prices, make_scene(photo), out, estimator)
    assert estimator.calls == [photo]
    assert not out.exists() and not list(out.parent.glob("*"))


def test_without_depth_png_default_estimator_raises_depth_missing(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tiny_photo(tmp_path)
    with pytest.raises(DepthMissing, match="foto.jpg.depth.png"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4")
    assert not (tmp_path / "o.mp4").exists()


def test_missing_photo_is_input_error_before_asking_for_depth(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    estimator = Recorder(DepthMissing("não deveria ser chamado"))
    scene = make_scene(tmp_path / "nao-existe.jpg")
    run_id, token = new_token(conn, prices, scene)
    req = SceneRequest(scene=scene, run_id=run_id, attempt_no=token.attempt_no, out_path=tmp_path / "o.mp4")
    with pytest.raises(InputError, match="foto"):
        ParallaxAdapter(prices, estimator).generate(req, token)
    assert estimator.calls == []
    with pytest.raises(TokenInvalid):  # o token foi consumido antes de qualquer validação
        token.consume(METHOD)


def test_unreadable_photo_is_input_error(conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path) -> None:
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"isto nao e uma imagem")
    with pytest.raises(InputError, match="ilegível"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4", Recorder(DepthMissing("x")))


def test_invalid_camera_is_input_error(conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path) -> None:
    with pytest.raises(InputError, match="câmera"):
        generate(conn, prices, make_scene(tiny_photo(tmp_path), "dolly-zoom"), tmp_path / "o.mp4")


@pytest.mark.parametrize("duration_s", [0.0, 0.01, -1.0, float("nan"), float("inf"), 61.0])
def test_absurd_duration_is_input_error(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path, duration_s: float
) -> None:
    with pytest.raises(InputError, match="duração"):
        generate(conn, prices, make_scene(tiny_photo(tmp_path), duration_s=duration_s), tmp_path / "o.mp4")


def test_depth_must_be_16_bit_gray_png(conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path) -> None:
    photo = tiny_photo(tmp_path)
    Image.fromarray(np.zeros((48, 64), np.uint8)).save(depth_path_for(photo))  # 8 bits
    with pytest.raises(InputError, match="16 bits"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4")
    depth_path_for(photo).write_bytes(b"nao e png")
    with pytest.raises(InputError, match="ilegível"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4")


def test_depth_values_accept_16_bit_modes_and_mode_i_in_range() -> None:
    """Pillow recente abre o PNG de 16 bits como ``I;16``; versões antigas, como ``I``. Os dois valem."""
    ramp = np.linspace(0, 65535, 12).reshape(3, 4)
    as_16 = Image.fromarray(ramp.astype(np.uint16))
    as_i = Image.fromarray(ramp.astype(np.int32))
    assert as_16.mode == "I;16" and as_i.mode == "I"
    assert np.array_equal(parallax._depth_values(as_16, "x"), parallax._depth_values(as_i, "x"))
    assert float(parallax._depth_values(as_i, "x").max()) == 65535.0


def test_depth_values_reject_mode_i_out_of_16_bit_range_and_other_modes() -> None:
    too_big = Image.fromarray(np.full((3, 4), 70000, np.int32))
    negative = Image.fromarray(np.full((3, 4), -5, np.int32))
    for img in (too_big, negative):
        with pytest.raises(InputError, match="0..65535"):
            parallax._depth_values(img, "x")
    for img in (Image.new("L", (4, 3)), Image.new("RGB", (4, 3)), Image.new("F", (4, 3))):
        with pytest.raises(InputError, match="16 bits"):
            parallax._depth_values(img, "x")


def test_depth_with_other_resolution_is_input_error(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tiny_photo(tmp_path)  # 64x48
    write_depth(photo, size=(48, 64))  # girado: não bate
    with pytest.raises(InputError, match="64x48"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4")
    assert not (tmp_path / "o.mp4").exists()


def test_flat_depth_is_input_error_not_a_silent_still(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tiny_photo(tmp_path)
    write_depth(photo, kind="flat")
    with pytest.raises(InputError, match="relevo"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4")


def test_output_outside_delivery_format_is_rejected_and_leaves_nothing(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tiny_photo(tmp_path)
    write_depth(photo)
    out = tmp_path / "out" / "s01.mp4"

    def wrong_encoder(frames: Any, count: int, part: Path, **_: Any) -> None:
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "1", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", str(part)])  # fmt: skip

    monkeypatch.setattr(parallax, "_encode", wrong_encoder)
    with pytest.raises(FfmpegError, match="formato de entrega"):
        generate(conn, prices, make_scene(photo, duration_s=1.0), out)
    assert not out.exists() and list(out.parent.iterdir()) == []


def test_encoder_failure_propagates_and_leaves_no_partial_file(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tiny_photo(tmp_path)
    write_depth(photo)
    out = tmp_path / "out" / "s01.mp4"

    def failing_encoder(frames: Any, count: int, part: Path, **_: Any) -> None:
        part.write_bytes(b"parcial")
        raise FfmpegError("ffmpeg falhou", stderr="boom")

    monkeypatch.setattr(parallax, "_encode", failing_encoder)
    with pytest.raises(FfmpegError, match="falhou"):
        generate(conn, prices, make_scene(photo, duration_s=1.0), out)
    assert not out.exists() and list(out.parent.iterdir()) == []


def test_ffmpeg_exit_error_through_the_real_encoder_path_leaves_nothing(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Um ``ffmpeg`` falso que sai com erro vira FfmpegError (código 6) e não deixa arquivo parcial."""
    photo = tiny_photo(tmp_path)
    write_depth(photo)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ffmpeg"
    fake.write_text('#!/bin/sh\necho "encoder quebrou" >&2\nexit 7\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    out = tmp_path / "out" / "s01.mp4"
    with pytest.raises(FfmpegError, match="código 7: encoder quebrou") as info:
        generate(conn, prices, make_scene(photo, duration_s=0.2), out)
    assert info.value.exit_code == 6
    assert not out.exists() and list(out.parent.iterdir()) == []


def test_encoder_receives_one_frame_per_output_frame(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tiny_photo(tmp_path)
    write_depth(photo)
    seen: dict[str, Any] = {}

    def spy_encoder(frames: Any, count: int, part: Path, **_: Any) -> None:
        batch = list(frames)
        seen.update(count=count, n=len(batch), shape=batch[0].shape, dtype=batch[0].dtype)
        raise FfmpegError("parado de propósito")

    monkeypatch.setattr(parallax, "_encode", spy_encoder)
    with pytest.raises(FfmpegError):
        generate(conn, prices, make_scene(photo, duration_s=0.2), tmp_path / "o.mp4")
    assert seen == {"count": 6, "n": 6, "shape": (H, W), "dtype": np.dtype(np.uint32)}


# --- ffmpeg real: formato de entrega, fidelidade, orientação e contrato ----------------------------


@dataclass(frozen=True)
class Rendered:
    photo: Path
    result: SceneResult
    work: Path


@pytest.fixture(scope="module")
def rendered(synthetic_photo: Path, tmp_path_factory: pytest.TempPathFactory, prices_mod: PriceTable) -> Rendered:
    """Uma renderização de 1 s (30 quadros; mantém a suíte rápida) com profundidade em gradiente, via DepthFromFile."""
    work = tmp_path_factory.mktemp("parallax")
    photo = work / "sala.jpg"
    shutil.copy(synthetic_photo, photo)  # o mapa fica ao lado da cópia, sem sujar a foto compartilhada
    write_depth(photo)
    connection = repo.connect(work / "verion.db")
    try:
        result = generate(connection, prices_mod, make_scene(photo, "pan-right", 1.0), work / "s01.mp4", DepthFromFile())
    finally:
        connection.close()
    return Rendered(photo, result, work)


def test_delivery_format_and_duration(rendered: Rendered) -> None:
    """Critério de pronto: h264, 1920x1080, yuv420p, 30/1 (ffprobe) e 1,0 +- 0,05 s."""
    p = ffmpeg.probe(rendered.result.clip_path)
    assert (p.codec, p.width, p.height, p.pix_fmt, p.fps) == ("h264", 1920, 1080, "yuv420p", "30/1")
    assert ffmpeg.is_delivery_format(p)
    assert p.duration_s == pytest.approx(1.0, abs=0.05)


def test_result_describes_the_real_file(rendered: Rendered) -> None:
    r = rendered.result
    p = ffmpeg.probe(r.clip_path)
    assert (r.width, r.height) == (1920, 1080) and r.fps == pytest.approx(30.0)
    assert r.duration_s == pytest.approx(p.duration_s)
    assert (r.units, r.unit_kind, r.cost_is_estimate, r.provider_job_id) == (0.0, "none", False, None)
    assert r.meta["camera"] == "pan-right" and r.meta["frames"] == "30"
    assert float(r.meta["shift_px"]) <= 0.02 * 1920
    assert list(r.clip_path.parent.glob(".*part*")) == [], "arquivo parcial não pode sobrar"


def test_first_frame_ssim_against_scaled_photo(rendered: Rendered) -> None:
    """Critério de pronto: SSIM do 1º quadro contra a foto escalada (filtro ssim) >= 0,95."""
    score = ssim_first_frame(rendered.result.clip_path, rendered.photo, rendered.work)
    assert score >= 0.95, f"SSIM do 1º quadro = {score}"


def test_camera_actually_moves_but_stays_a_view_of_the_photo(rendered: Rendered) -> None:
    first = frame_at(rendered.result.clip_path, "first", rendered.work / "first.png")
    last = frame_at(rendered.result.clip_path, "last", rendered.work / "last.png")
    diff = float(np.abs(first - last).mean())
    assert diff > 1.0, "o parallax não mexeu nada"
    # Fidelidade: o último quadro só reposiciona pixels da foto, então a faixa de cores é a mesma
    assert float(last.min()) >= float(first.min()) - 8 and float(last.max()) <= float(first.max()) + 8


@pytest.mark.parametrize(
    ("label", "stored", "split", "orientation"),
    [
        ("retrato", (900, 1600), "tb", None),
        ("exif6", (1600, 900), "lr", 6),
    ],
)
def test_portrait_and_exif_orientation_are_respected(
    conn: sqlite3.Connection,
    prices: PriceTable,
    tmp_path: Path,
    label: str,
    stored: tuple[int, int],
    split: str,
    orientation: int | None,
) -> None:
    """Retrato e JPEG com EXIF orientação 6 (gerado com Pillow) saem na mesma posição que o espectador vê.

    Nos dois casos a imagem exibida é 900x1600 com a metade de cima vermelha e a de baixo azul. No EXIF 6 a
    foto está gravada deitada (1600x900, esquerda vermelha), e a rotação exibida põe a esquerda no topo.
    O mapa de profundidade acompanha a orientação exibida. A saída é 16:9 com recorte central.
    """
    photo = make_halves_jpeg(tmp_path / f"{label}.jpg", stored, split, orientation)
    assert oriented_size(photo) == (900, 1600)
    write_depth(photo)
    result = generate(conn, prices, make_scene(photo, "push-in", 0.2), tmp_path / f"{label}.mp4")
    assert ffmpeg.is_delivery_format(ffmpeg.probe(result.clip_path))
    assert ssim_first_frame(result.clip_path, photo, tmp_path) >= 0.95
    for which in ("first", "last"):
        frame = frame_at(result.clip_path, which, tmp_path / f"{label}-{which}.png")
        top, bottom = frame[40:240].mean(axis=(0, 1)), frame[840:1040].mean(axis=(0, 1))
        assert top[0] > 200 and top[2] < 60, f"{label}/{which}: topo deveria ser vermelho, veio {top}"
        assert bottom[2] > 200 and bottom[0] < 60, f"{label}/{which}: base deveria ser azul, veio {bottom}"


def test_exif_photo_with_depth_in_stored_orientation_is_refused(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = make_halves_jpeg(tmp_path / "exif6.jpg", (1600, 900), "lr", 6)
    write_depth(photo, size=(1600, 900))  # mapa na orientação gravada, não na exibida
    with pytest.raises(InputError, match="900x1600"):
        generate(conn, prices, make_scene(photo), tmp_path / "o.mp4")


def test_adapter_contract_with_depth_from_file(conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path) -> None:
    """Contrato da spec 3.4 com o estimador real (DepthFromFile) e profundidade sintética; cena curta (0,2 s, 6 quadros)."""
    photo = tiny_photo(tmp_path, "contrato.jpg", (1200, 900))  # foto menor: carregar 12 MP não acrescenta garantia
    write_depth(photo)
    factory: Callable[[object | None], ParallaxAdapter] = lambda client: ParallaxAdapter(prices, client)
    assert_generator_contract(
        factory,
        conn=conn,
        prices=prices,
        scene=make_scene(photo, "push-in", 0.2),
        good_client=DepthFromFile(),
        out_dir=tmp_path,
    )
