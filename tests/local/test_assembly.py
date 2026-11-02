"""Testes da montagem (T7): R2 com corte no alvo, R3 crossfade e R5 marca d'água de demonstração.

Sem rede: os clipes vêm do ``testsrc`` do lavfi, pequenos e curtos, em resoluções diferentes.
"""

from __future__ import annotations

import itertools
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from verion.errors import FfmpegError, InputError
from verion.local import assembly, ffmpeg
from verion.local.assembly import AssemblyResult, ClipSpec

TRANSITION_S = 0.5
FRAME_TOLERANCE_S = 0.1


def make_clip(path: Path, *, seconds: float, size: str = "320x180", fps: int = 30, pix_fmt: str = "yuv420p") -> Path:
    """Clipe sintético H.264 de ``seconds`` s; a cor de ``testsrc`` muda com o tempo."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix_fmt, str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return path


def frame_at(video: Path, second: float, dest: Path) -> np.ndarray:
    """Quadro do vídeo em ``second`` como matriz RGB."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(second), "-i", str(video), "-frames:v", "1", str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    with Image.open(dest) as img:
        return np.asarray(img.convert("RGB"), dtype=np.int16)


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """8 s 16:9, 5 s em 4:3 e 5 s em retrato (resoluções e proporções diferentes), mais um de 3 s."""
    d = tmp_path_factory.mktemp("clipes")
    return {
        "c8": make_clip(d / "c8.mp4", seconds=8, size="640x360"),
        "c5": make_clip(d / "c5.mp4", seconds=5, size="320x240"),
        "c5p": make_clip(d / "c5p.mp4", seconds=5, size="180x320", fps=25),
        "c3": make_clip(d / "c3.mp4", seconds=3, size="320x180"),
        "c1": make_clip(d / "c1.mp4", seconds=1.2, size="320x180"),
    }


@pytest.fixture(scope="module")
def three(clips: dict[str, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[AssemblyResult, Path]:
    out = tmp_path_factory.mktemp("saida3") / "montagem.mp4"
    specs = [ClipSpec(clips[k], 5.0) for k in ("c8", "c5", "c5p")]
    return assembly.assemble(specs, out), out


class TestCriteriaOfDone:
    def test_8_5_5_with_different_resolutions_give_14_seconds_in_delivery_format(
        self, three: tuple[AssemblyResult, Path]
    ) -> None:
        result, out = three
        info = ffmpeg.probe(out)
        assert ffmpeg.is_delivery_format(info), info
        assert info.duration_s == pytest.approx(14.0, abs=FRAME_TOLERANCE_S)
        assert result.duration_s == pytest.approx(info.duration_s, abs=0.02)
        assert result.out == out
        assert result.short_clips == ()

    def test_3s_clip_with_5s_target_is_listed_and_not_stretched(self, clips: dict[str, Path], tmp_path: Path) -> None:
        out = tmp_path / "curto.mp4"
        result = assembly.assemble([ClipSpec(clips["c3"], 5.0)], out)
        assert result.short_clips == (0,)
        info = ffmpeg.probe(out)
        assert ffmpeg.is_delivery_format(info)
        assert info.duration_s == pytest.approx(3.0, abs=FRAME_TOLERANCE_S)  # 3 s, não 5 s

    def test_short_clip_in_the_middle_of_a_montage_uses_its_index(self, clips: dict[str, Path], tmp_path: Path) -> None:
        out = tmp_path / "meio.mp4"
        specs = [ClipSpec(clips["c5"], 1.0), ClipSpec(clips["c1"], 3.0), ClipSpec(clips["c8"], 1.0)]
        result = assembly.assemble(specs, out, transition_s=0.25)
        assert result.short_clips == (1,)  # índice 0-based na lista recebida
        # 1,0 + 1,2 (inteiro, não os 3 do alvo) + 1,0 - 2 * 0,25
        assert result.duration_s == pytest.approx(2.7, abs=FRAME_TOLERANCE_S)

    def test_single_long_clip_is_cut_at_target(self, clips: dict[str, Path], tmp_path: Path) -> None:
        out = tmp_path / "um.mp4"
        result = assembly.assemble([ClipSpec(clips["c8"], 2.0)], out)  # 8 s de origem
        assert result.short_clips == ()
        info = ffmpeg.probe(out)
        assert ffmpeg.is_delivery_format(info)
        assert info.duration_s == pytest.approx(2.0, abs=FRAME_TOLERANCE_S)

    def test_demo_changes_the_frame_but_not_the_timing(self, clips: dict[str, Path], tmp_path: Path) -> None:
        specs = [ClipSpec(clips["c5"], 1.0), ClipSpec(clips["c3"], 1.0)]
        plain = tmp_path / "sem.mp4"
        demo = tmp_path / "demo.mp4"
        r_plain = assembly.assemble(specs, plain, transition_s=0.25)
        r_demo = assembly.assemble(specs, demo, transition_s=0.25, demo=True)

        assert ffmpeg.is_delivery_format(ffmpeg.probe(demo))  # drawtext não pode deixar yuv444p
        assert r_demo.duration_s == pytest.approx(r_plain.duration_s, abs=0.05)

        a = frame_at(plain, 0.4, tmp_path / "a.png")
        b = frame_at(demo, 0.4, tmp_path / "b.png")
        assert a.shape == b.shape == (1080, 1920, 3)
        h, w = a.shape[:2]
        center = (slice(h // 2 - 60, h // 2 + 60), slice(w // 2 - 450, w // 2 + 450))
        top = (slice(0, 120), slice(0, 900))
        diff_center = float(np.abs(a[center] - b[center]).mean())
        diff_top = float(np.abs(a[top] - b[top]).mean())
        assert diff_center > 3.0  # o texto aparece no centro
        assert diff_center > 3 * diff_top  # e só lá (o resto muda apenas pelo crf 20 -> 23)


class TestNormalization:
    def test_portrait_clip_is_letterboxed_not_distorted(self, clips: dict[str, Path], tmp_path: Path) -> None:
        out = tmp_path / "retrato.mp4"
        assembly.assemble([ClipSpec(clips["c5p"], 1.0)], out)
        frame = frame_at(out, 0.5, tmp_path / "f.png")
        w = frame.shape[1]
        # 180x320 em 1920x1080 ocupa 607 px de largura no centro; as laterais são barras pretas.
        assert frame[:, :400].max() <= 20
        assert frame[:, w - 400 :].max() <= 20
        assert frame[:, w // 2 - 250 : w // 2 + 250].max() > 100

    def test_output_has_no_leftovers(self, three: tuple[AssemblyResult, Path]) -> None:
        _, out = three
        assert sorted(p.name for p in out.parent.iterdir()) == ["montagem.mp4"]

    def test_overwrites_existing_output_and_creates_missing_directory(self, clips: dict[str, Path], tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "x.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"lixo")
        assembly.assemble([ClipSpec(clips["c3"], 1.0)], out)
        assert ffmpeg.is_delivery_format(ffmpeg.probe(out))
        assert [p.name for p in out.parent.iterdir()] == ["x.mp4"]


class TestFilterGraph:
    def test_offsets_for_three_clips_of_5s_are_4_5_and_9(self) -> None:
        assert assembly.crossfade_offsets([5.0, 5.0, 5.0], 0.5) == pytest.approx([4.5, 9.0])

    def test_offsets_use_real_durations_and_grow_for_twelve_scenes(self) -> None:
        durs = [5.0, 3.2, 4.0, 5.0, 5.0, 4.8, 5.0, 3.0, 5.0, 5.0, 5.0, 5.0]
        offsets = assembly.crossfade_offsets(durs, 0.5)
        assert len(offsets) == 11
        assert offsets[0] == pytest.approx(4.5)
        assert offsets[1] == pytest.approx(5.0 + 3.2 - 1.0)
        assert all(b > a for a, b in itertools.pairwise(offsets))
        # a última transição começa antes do fim: duração total = soma - (n-1)*t
        assert offsets[-1] + durs[-1] == pytest.approx(sum(durs) - 11 * 0.5)

    def test_graph_chains_xfade_and_ends_with_yuv420p(self) -> None:
        graph = assembly.build_filter_complex([5.0, 5.0, 5.0], 0.5, fontfile=None)
        assert "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.5[v01]" in graph
        assert "[v01][2:v]xfade=transition=fade:duration=0.5:offset=9[v]" not in graph  # o fim leva format
        assert "[v01][2:v]xfade=transition=fade:duration=0.5:offset=9,format=yuv420p[v]" in graph
        assert "drawtext" not in graph

    def test_single_clip_graph_has_no_xfade(self) -> None:
        graph = assembly.build_filter_complex([5.0], 0.5, fontfile=None)
        assert "xfade" not in graph
        assert graph.endswith("format=yuv420p[v]")

    def test_demo_graph_puts_format_after_drawtext(self) -> None:
        graph = assembly.build_filter_complex([5.0, 5.0], 0.5, fontfile="/x/Inter.otf")
        assert "drawtext=fontfile='/x/Inter.otf':text='DEMONSTRAÇÃO'" in graph
        assert graph.index("drawtext") < graph.rindex("format=yuv420p")
        assert graph.endswith("format=yuv420p[v]")

    def test_font_path_with_unsafe_characters_is_refused(self) -> None:
        with pytest.raises(FfmpegError):
            assembly.build_filter_complex([5.0], 0.5, fontfile="/x/it's.otf")

    def test_demo_font_comes_from_fc_match_and_must_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        font = assembly.demo_font()
        assert Path(font).is_file()
        monkeypatch.setattr(assembly, "_fc_match", lambda: "/nao/existe/Inter.otf")
        with pytest.raises(FfmpegError, match="fonte"):
            assembly.demo_font()


class TestInputValidation:
    def test_empty_list(self, tmp_path: Path) -> None:
        with pytest.raises(InputError):
            assembly.assemble([], tmp_path / "x.mp4")

    def test_missing_clip(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="não encontrado"):
            assembly.assemble([ClipSpec(tmp_path / "nao.mp4", 5.0)], tmp_path / "x.mp4")

    @pytest.mark.parametrize("target", [0.0, -1.0, math.nan, math.inf])
    def test_invalid_target(self, clips: dict[str, Path], tmp_path: Path, target: float) -> None:
        with pytest.raises(InputError, match="alvo"):
            assembly.assemble([ClipSpec(clips["c3"], target)], tmp_path / "x.mp4")

    @pytest.mark.parametrize("transition", [0.0, -0.5, math.nan, math.inf])
    def test_invalid_transition(self, clips: dict[str, Path], tmp_path: Path, transition: float) -> None:
        specs = [ClipSpec(clips["c3"], 3.0), ClipSpec(clips["c3"], 3.0)]
        with pytest.raises(InputError, match="transição"):
            assembly.assemble(specs, tmp_path / "x.mp4", transition_s=transition)

    def test_clip_too_short_for_the_transition(self, clips: dict[str, Path], tmp_path: Path) -> None:
        specs = [ClipSpec(clips["c3"], 0.8), ClipSpec(clips["c3"], 3.0)]
        with pytest.raises(InputError, match="transição"):
            assembly.assemble(specs, tmp_path / "x.mp4")

    def test_output_cannot_be_an_input(self, clips: dict[str, Path], tmp_path: Path) -> None:
        with pytest.raises(InputError, match="entrada"):
            assembly.assemble([ClipSpec(clips["c3"], 2.0)], clips["c3"])

    def test_corrupt_clip_fails_without_leaving_output(self, tmp_path: Path) -> None:
        bad = tmp_path / "ruim.mp4"
        bad.write_text("isto não é vídeo")
        out = tmp_path / "x.mp4"
        with pytest.raises(FfmpegError):
            assembly.assemble([ClipSpec(bad, 5.0)], out)
        assert list(tmp_path.glob("x*")) == [] and not list(tmp_path.glob(".*"))
