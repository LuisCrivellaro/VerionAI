"""Testes dos quadros de QC (T7, receita R6): 1 quadro por segundo e folha de contato."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from verion.errors import FfmpegError, InputError
from verion.local import qc_frames


def make_clip(path: Path, seconds: int) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=30:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return path


@pytest.fixture(scope="module")
def clip3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_clip(tmp_path_factory.mktemp("qc") / "c3.mp4", 3)


def make_red_then_blue_clip(path: Path) -> Path:
    """3 s vermelhos; os últimos 0,15 s ficam azuis. O ``fps=1`` (t=0,1,2) nunca vê o azul."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=30:duration=3",
            "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,2.85)'",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return path


def mean_rgb(path: Path) -> tuple[float, float, float]:
    with Image.open(path) as img:
        r, g, b = (float(v) for v in np.asarray(img.convert("RGB")).reshape(-1, 3).mean(axis=0))
    return r, g, b


def test_extract_gives_one_frame_per_second_and_the_contact_sheet(clip3: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "qc"
    frames, sheet, _last = qc_frames.extract(clip3, out_dir)

    assert [p.name for p in frames] == ["qc_001.jpg", "qc_002.jpg", "qc_003.jpg"]
    assert sheet == out_dir / "qc_contato.jpg"
    assert all(p.is_file() and p.parent == out_dir for p in frames)
    assert sheet.is_file()
    with Image.open(frames[0]) as img:
        assert img.size == (480, 270)  # scale=480:-1
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "qc_001.jpg", "qc_002.jpg", "qc_003.jpg", "qc_contato.jpg", "qc_ultimo.jpg",
    ]  # fmt: skip


def test_last_frame_is_the_real_end_of_the_clip_not_the_last_whole_second(tmp_path: Path) -> None:
    clip = make_red_then_blue_clip(tmp_path / "rb.mp4")
    result = qc_frames.extract(clip, tmp_path / "qc")
    assert result.last == tmp_path / "qc" / "qc_ultimo.jpg" and result.last.is_file()
    # os quadros a 1/s (t=0, 1, 2) são vermelhos; só o quadro final vê o azul do fim
    for frame in result.frames:
        r, _, b = mean_rgb(frame)
        assert r > 150 and b < 60
    r, _, b = mean_rgb(result.last)
    assert b > 150 and r < 60
    with Image.open(result.last) as img:
        assert img.size == (480, 270)  # mesma escala dos demais quadros


def test_contact_sheet_shows_every_frame_even_beyond_16(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "c18.mp4", 18)
    frames, sheet, _ = qc_frames.extract(clip, tmp_path / "qc")
    assert len(frames) == 18
    with Image.open(sheet) as img:
        # 4 colunas de 320 px; 18 quadros pedem 5 linhas de 180 px (a folha não corta no 16º quadro)
        assert img.size == (4 * 320, 5 * 180)


def test_extract_replaces_stale_frames_from_a_previous_run(clip3: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "qc"
    out_dir.mkdir()
    (out_dir / "qc_009.jpg").write_bytes(b"velho")
    (out_dir / "anotacao.txt").write_text("fica")
    frames, _, _ = qc_frames.extract(clip3, out_dir)
    assert len(frames) == 3
    assert not (out_dir / "qc_009.jpg").exists()
    assert (out_dir / "anotacao.txt").read_text() == "fica"


def test_missing_clip(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="não encontrado"):
        qc_frames.extract(tmp_path / "nao.mp4", tmp_path / "qc")


def test_corrupt_clip_leaves_no_files(tmp_path: Path) -> None:
    bad = tmp_path / "ruim.mp4"
    bad.write_text("isto não é vídeo")
    out_dir = tmp_path / "qc"
    with pytest.raises(FfmpegError):
        qc_frames.extract(bad, out_dir)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
