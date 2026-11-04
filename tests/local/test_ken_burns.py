"""Testes do ``KenBurnsAdapter`` (T5): R1 sobre foto sintética, formato de entrega e contrato."""

from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, get_args

import numpy as np
import pytest
from PIL import Image

from verion import budget
from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import FfmpegError, InputError, TokenInvalid
from verion.ledger import repo
from verion.local import ffmpeg, ken_burns
from verion.local.ken_burns import KenBurnsAdapter
from verion.providers import registry
from verion.providers.base import Camera, Scene, SceneRequest, SceneResult
from verion.providers.contract import assert_generator_contract

CAMERAS: tuple[str, ...] = get_args(Camera)
FPS = 30
ZOOM_MAX = 1.06


def _prices_stub() -> PriceTable:
    return PriceTable(fx_usd_brl=5.0, methods={})


def make_scene(photo: Path, camera: str = "push-in", duration_s: float = 5.0) -> Scene:
    return Scene(
        index=1,
        photo=photo,
        room="sala",
        camera=camera,  # type: ignore[arg-type]
        duration_s=duration_s,
        prompt="Movimento de câmera lento e contínuo. Não adicionar objetos.",
    )


def new_token(conn: sqlite3.Connection, prices: PriceTable, scene: Scene) -> tuple[int, SpendToken]:
    run_id = repo.create_run(
        conn, property_id="casa-1", method="ken-burns", script_path="r.toml", script_sha256="0" * 64
    )
    token = budget.reserve(
        conn,
        prices,
        run_id=run_id,
        scene_index=scene.index,
        target_s=scene.duration_s if scene.duration_s == scene.duration_s else 5.0,  # nan só nos testes de erro
        method="ken-burns",
        provider="local",
        model="ffmpeg-zoompan",
        units=0.0,
        unit_kind="none",
        allow_paid=False,
    )
    return run_id, token


def generate(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    out_path: Path,
    client: object | None = None,
) -> SceneResult:
    run_id, token = new_token(conn, prices, scene)
    req = SceneRequest(scene=scene, run_id=run_id, attempt_no=token.attempt_no, out_path=out_path)
    return KenBurnsAdapter(prices, client).generate(req, token)


# --- expressões do zoompan (matemática pura, sem ffmpeg) -------------------------------------------


@dataclass(frozen=True)
class Window:
    zoom: float
    x: float
    y: float
    w: float  # largura da janela de recorte, em px da imagem escalada 3840x2160
    h: float


IW, IH = 3840, 2160


def evaluate(camera: str, frames: int) -> list[Window]:
    """Avalia as expressões de ``zoom_expressions`` quadro a quadro, como o zoompan do ffmpeg faria."""
    z_expr, x_expr, y_expr = ken_burns.zoompan_expressions(camera, frames)  # type: ignore[arg-type]
    out = []
    for on in range(frames):
        env: dict[str, Any] = {"on": on, "iw": IW, "ih": IH, "min": min, "max": max}
        zoom = float(eval(z_expr, {"__builtins__": {}}, env))
        env["zoom"] = zoom
        x = float(eval(x_expr, {"__builtins__": {}}, env))
        y = float(eval(y_expr, {"__builtins__": {}}, env))
        out.append(Window(zoom, x, y, IW / zoom, IH / zoom))
    return out


@pytest.mark.parametrize("camera", CAMERAS)
def test_zoom_never_exceeds_1_06_and_window_stays_inside_image(camera: str) -> None:
    for w in evaluate(camera, 150):
        assert 1.0 <= w.zoom <= ZOOM_MAX + 1e-9
        assert w.x >= -1e-6 and w.y >= -1e-6
        assert w.x + w.w <= IW + 1e-6 and w.y + w.h <= IH + 1e-6


def test_push_in_zooms_from_1_to_1_06_centered() -> None:
    ws = evaluate("push-in", 150)
    assert ws[0].zoom == pytest.approx(1.0)
    assert ws[-1].zoom == pytest.approx(ZOOM_MAX)
    assert all(a.zoom <= b.zoom for a, b in itertools.pairwise(ws))
    for w in ws:  # centrado
        assert w.x + w.w / 2 == pytest.approx(IW / 2)
        assert w.y + w.h / 2 == pytest.approx(IH / 2)


def test_pull_out_zooms_from_1_06_to_1() -> None:
    ws = evaluate("pull-out", 150)
    assert ws[0].zoom == pytest.approx(ZOOM_MAX)
    assert ws[-1].zoom == pytest.approx(1.0)
    assert all(a.zoom >= b.zoom for a, b in itertools.pairwise(ws))


def test_pan_right_moves_window_right_and_pan_left_moves_it_left() -> None:
    right, left = evaluate("pan-right", 150), evaluate("pan-left", 150)
    assert right[0].x == pytest.approx(0.0) and right[-1].x + right[-1].w == pytest.approx(IW)
    assert left[0].x + left[0].w == pytest.approx(IW) and left[-1].x == pytest.approx(0.0)
    assert all(a.x <= b.x for a, b in itertools.pairwise(right))
    assert all(a.x >= b.x for a, b in itertools.pairwise(left))
    assert all(w.zoom == pytest.approx(ZOOM_MAX) for w in right + left)  # só desloca, não muda a escala
    assert all(w.y + w.h / 2 == pytest.approx(IH / 2) for w in right + left)


def test_static_never_moves() -> None:
    ws = evaluate("static", 150)
    assert {(w.zoom, w.x, w.y) for w in ws} == {(1.0, 0.0, 0.0)}


def test_unknown_camera_is_input_error() -> None:
    with pytest.raises(InputError, match="câmera"):
        ken_burns.zoompan_expressions("dolly-zoom", 150)  # type: ignore[arg-type]


def test_filter_chain_follows_recipe_r1() -> None:
    vf = ken_burns.build_filter("push-in", 150)
    assert vf.startswith("scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,zoompan=")
    assert ":d=150:s=1920x1080:fps=30" in vf
    assert vf.endswith(",format=yuv420p")


def test_frame_count_matches_duration() -> None:
    assert ken_burns.frame_count(5.0) == 150
    assert ken_burns.frame_count(3.0) == 90
    assert ken_burns.frame_count(7.5) == 225


# --- adaptador sem ffmpeg real (clients falsos) ---------------------------------------------------


def test_construction_does_not_touch_client() -> None:
    class Hostile:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"construtor leu o atributo {name!r} do client")

        def __call__(self, args: list[str]) -> None:
            raise AssertionError("construtor chamou o client")

    KenBurnsAdapter(_prices_stub(), Hostile())


def test_identity_and_estimate(prices: PriceTable, tmp_path: Path) -> None:
    gen = KenBurnsAdapter(prices)
    assert (gen.method, gen.provider) == ("ken-burns", "local")
    est = gen.estimate(make_scene(tmp_path / "x.jpg"))
    assert (est.units, est.unit_kind) == (0.0, "none")
    assert registry.METHODS["ken-burns"] == "verion.local.ken_burns:KenBurnsAdapter"


def test_default_client_is_the_real_ffmpeg_runner(prices: PriceTable) -> None:
    assert KenBurnsAdapter(prices)._run is ffmpeg.run


def test_ffmpeg_receives_one_call_with_recipe_arguments(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"x")
    calls: list[list[str]] = []

    def fake_runner(args: list[str]) -> None:
        calls.append(list(args))
        Path(args[-1]).write_bytes(b"nao-e-mp4")

    with pytest.raises(FfmpegError):  # o ffprobe real recusa o arquivo falso
        generate(conn, prices, make_scene(photo, "pan-left", 4.0), tmp_path / "out" / "s01.mp4", fake_runner)
    assert len(calls) == 1
    args = calls[0]
    assert args[args.index("-i") + 1] == str(photo.resolve())
    assert args[args.index("-frames:v") + 1] == "120"
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-preset") + 1] == "veryfast" and args[args.index("-crf") + 1] == "20"
    assert "+faststart" in args and "-an" in args
    assert args[args.index("-vf") + 1] == ken_burns.build_filter("pan-left", 120)


def test_failed_output_leaves_no_file_at_out_path(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"x")
    out = tmp_path / "out" / "s01.mp4"

    def runner(args: list[str]) -> None:
        Path(args[-1]).write_bytes(b"lixo")

    with pytest.raises(FfmpegError):
        generate(conn, prices, make_scene(photo), out, runner)
    assert not out.exists()
    assert list(out.parent.iterdir()) == [], "arquivo parcial não pode sobrar"


def test_output_outside_delivery_format_is_rejected(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    """Um client que entrega MP4 válido, mas 1280x720, não passa: o adaptador confere com o ffprobe."""
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"x")
    out = tmp_path / "out" / "s01.mp4"

    def wrong_runner(args: list[str]) -> None:
        ffmpeg.run(
            ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "4", "-pix_fmt", "yuv420p",
             "-c:v", "libx264", "-preset", "ultrafast", args[-1]]
        )  # fmt: skip

    with pytest.raises(FfmpegError, match="formato de entrega"):
        generate(conn, prices, make_scene(photo, duration_s=4.0), out, wrong_runner)
    assert not out.exists() and list(out.parent.iterdir()) == []


def test_ffmpeg_failure_propagates_and_leaves_no_file(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"x")
    out = tmp_path / "out" / "s01.mp4"

    def runner(args: list[str]) -> None:
        Path(args[-1]).write_bytes(b"parcial")
        raise FfmpegError("ffmpeg falhou", stderr="boom")

    with pytest.raises(FfmpegError, match="falhou"):
        generate(conn, prices, make_scene(photo), out, runner)
    assert not out.exists() and list(out.parent.iterdir()) == []


def test_missing_photo_is_input_error_after_consume(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path
) -> None:
    def never(args: list[str]) -> None:
        raise AssertionError("ffmpeg não deve rodar sem foto")

    scene = make_scene(tmp_path / "nao-existe.jpg")
    run_id, token = new_token(conn, prices, scene)
    req = SceneRequest(scene=scene, run_id=run_id, attempt_no=token.attempt_no, out_path=tmp_path / "o.mp4")
    with pytest.raises(InputError, match="foto"):
        KenBurnsAdapter(prices, never).generate(req, token)
    with pytest.raises(TokenInvalid):  # o token foi consumido antes de qualquer validação
        token.consume("ken-burns")


def test_invalid_camera_is_input_error(conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path) -> None:
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"x")

    def never(args: list[str]) -> None:
        raise AssertionError("ffmpeg não deve rodar com câmera inválida")

    with pytest.raises(InputError, match="câmera"):
        generate(conn, prices, make_scene(photo, "dolly-zoom"), tmp_path / "o.mp4", never)


@pytest.mark.parametrize("duration_s", [0.0, 0.01, -1.0, float("nan"), float("inf")])
def test_absurd_duration_is_input_error(
    conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path, duration_s: float
) -> None:
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"x")

    def never(args: list[str]) -> None:
        raise AssertionError("ffmpeg não deve rodar com duração inválida")

    with pytest.raises(InputError, match="duração"):
        generate(conn, prices, make_scene(photo, duration_s=duration_s), tmp_path / "o.mp4", never)


# --- ffmpeg real: formato de entrega e fidelidade -------------------------------------------------


# (zoom, posição horizontal da janela: 0 = borda esquerda, 0,5 = centro, 1 = borda direita) no 1º e no último quadro
EXPECTED_VIEWS: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "push-in": ((1.0, 0.5), (ZOOM_MAX, 0.5)),
    "pull-out": ((ZOOM_MAX, 0.5), (1.0, 0.5)),
    "pan-right": ((ZOOM_MAX, 0.0), (ZOOM_MAX, 1.0)),
    "pan-left": ((ZOOM_MAX, 1.0), (ZOOM_MAX, 0.0)),
    "static": ((1.0, 0.5), (1.0, 0.5)),
}


@pytest.fixture(scope="module")
def render(
    synthetic_photo: Path, tmp_path_factory: pytest.TempPathFactory
) -> Callable[[str], SceneResult]:
    """Renderiza 5 s por câmera com o ffmpeg real, uma vez por câmera para o módulo inteiro.

    Fixture de módulo não pode usar ``conn``/``prices`` (escopo de função): cria banco e tabela próprios.
    """
    cache: dict[str, SceneResult] = {}

    def _render(camera: str) -> SceneResult:
        if camera not in cache:
            work = tmp_path_factory.mktemp(f"kb-{camera}")
            connection = repo.connect(work / "verion.db")
            try:
                scene = make_scene(synthetic_photo, camera)
                cache[camera] = generate(connection, _prices_stub(), scene, work / "s01.mp4")
            finally:
                connection.close()
        return cache[camera]

    return _render


@pytest.mark.parametrize("camera", CAMERAS)
def test_delivery_format_and_duration_for_each_camera(render: Callable[[str], SceneResult], camera: str) -> None:
    """Critério de pronto: h264, 1920x1080, yuv420p, 30 fps (ffprobe) e 5,0 +- 0,05 s."""
    p = ffmpeg.probe(render(camera).clip_path)
    assert (p.codec, p.width, p.height, p.pix_fmt, p.fps) == ("h264", 1920, 1080, "yuv420p", "30/1")
    assert ffmpeg.is_delivery_format(p)
    assert p.duration_s == pytest.approx(5.0, abs=0.05)


@pytest.mark.parametrize("camera", CAMERAS)
def test_result_describes_the_real_file(render: Callable[[str], SceneResult], camera: str) -> None:
    r = render(camera)
    p = ffmpeg.probe(r.clip_path)
    assert (r.width, r.height) == (1920, 1080)
    assert r.fps == pytest.approx(30.0)
    assert r.duration_s == pytest.approx(p.duration_s)
    assert (r.units, r.unit_kind, r.cost_is_estimate) == (0.0, "none", False)
    assert r.provider_job_id is None
    assert r.meta["camera"] == camera


def _frame(clip: Path, which: str, out: Path) -> np.ndarray:
    """Primeiro ('first') ou último ('last') quadro do clipe, como array RGB."""
    if which == "first":
        args = ["-i", str(clip), "-frames:v", "1", str(out)]
    else:
        args = ["-sseof", "-0.1", "-i", str(clip), "-update", "1", str(out)]
    ffmpeg.run(args)
    return np.asarray(Image.open(out).convert("RGB"), dtype=np.float32)


def expected_view(photo_path: Path, zoom: float, x_pos: float) -> np.ndarray:
    """O que a câmera deve mostrar: recorte central 16:9 da foto, janela 1/zoom na posição horizontal x_pos."""
    photo = Image.open(photo_path).convert("RGB")
    w, h = photo.size
    base_h = w * 9 / 16
    top = (h - base_h) / 2
    cw, ch = w / zoom, base_h / zoom
    left = (w - cw) * x_pos
    box = (left, top + (base_h - ch) / 2, left + cw, top + (base_h - ch) / 2 + ch)
    return np.asarray(photo.resize((1920, 1080), Image.Resampling.LANCZOS, box=box), dtype=np.float32)


@pytest.mark.parametrize("which", ["first", "last"])
@pytest.mark.parametrize("camera", CAMERAS)
def test_frames_are_exact_crops_of_the_photo(
    render: Callable[[str], SceneResult], synthetic_photo: Path, tmp_path: Path, camera: str, which: str
) -> None:
    """Fidelidade: primeiro e último quadro são só recortes da foto (zoom e posição da câmera), sem conteúdo novo.

    A foto sintética é 4:3, então o recorte 16:9 central e o zoom de 1,06 decidem exatamente o que aparece.
    O limite (4,0; o medido fica perto de 2,0) fica bem abaixo da diferença que o quadro errado produziria (ver test_wrong_view_*).
    """
    zoom, x_pos = EXPECTED_VIEWS[camera][0 if which == "first" else 1]
    frame = _frame(render(camera).clip_path, which, tmp_path / f"{which}.png")
    diff = float(np.abs(frame - expected_view(synthetic_photo, zoom, x_pos)).mean())
    assert diff < 4.0, f"{camera}/{which}: quadro difere do recorte esperado (diferença média {diff:.2f})"


def test_wrong_view_would_be_detected(
    render: Callable[[str], SceneResult], synthetic_photo: Path, tmp_path: Path
) -> None:
    """Sanidade do teste anterior: o recorte errado dá diferença muito maior que o limite de 4,0."""
    frame = _frame(render("push-in").clip_path, "last", tmp_path / "last.png")
    wrong = float(np.abs(frame - expected_view(synthetic_photo, 1.0, 0.5)).mean())
    right = float(np.abs(frame - expected_view(synthetic_photo, ZOOM_MAX, 0.5)).mean())
    assert wrong > 3 * right and wrong > 8.0, (wrong, right)


def test_other_duration_changes_frame_count(
    conn: sqlite3.Connection, prices: PriceTable, synthetic_photo: Path, tmp_path: Path
) -> None:
    result = generate(conn, prices, make_scene(synthetic_photo, "push-in", 3.0), tmp_path / "s.mp4")
    p = ffmpeg.probe(result.clip_path)
    assert ffmpeg.is_delivery_format(p)
    assert p.duration_s == pytest.approx(3.0, abs=0.05)


def test_creates_missing_output_directory(
    conn: sqlite3.Connection, prices: PriceTable, synthetic_photo: Path, tmp_path: Path
) -> None:
    out = tmp_path / "runs" / "1" / "ken-burns" / "s01_t1.mp4"
    result = generate(conn, prices, make_scene(synthetic_photo, "static", 3.0), out)
    assert result.clip_path == out and out.is_file()


# --- contrato do adaptador (spec 3.4) com o executor real do ffmpeg -------------------------------


def test_adapter_contract_with_real_ffmpeg_runner(
    conn: sqlite3.Connection, prices: PriceTable, synthetic_photo: Path, tmp_path: Path
) -> None:
    """Cena de 3 s (mínimo do roteiro) para manter o teste rápido; client = executor real."""
    factory: Callable[[object | None], KenBurnsAdapter] = lambda client: KenBurnsAdapter(prices, client)
    assert_generator_contract(
        factory,
        conn=conn,
        prices=prices,
        scene=replace(make_scene(synthetic_photo, "pan-right", 3.0)),
        good_client=ffmpeg.run,
        out_dir=tmp_path,
    )
