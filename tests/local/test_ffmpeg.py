"""Testes de ``verion.local.ffmpeg`` (T5): executor, probe e formato de entrega."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from verion.errors import FfmpegError
from verion.local import ffmpeg
from verion.local.ffmpeg import Probe


@pytest.fixture(scope="module")
def tiny_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Clipe de 1 s em formato de entrega, feito pelo próprio ``run`` (também testa o executor)."""
    out = tmp_path_factory.mktemp("clip") / "tiny.mp4"
    ffmpeg.run(
        [
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "1",
            "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(out),
        ]
    )  # fmt: skip
    return out


def _delivery(**overrides: Any) -> Probe:
    base: dict[str, Any] = {
        "codec": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "fps": "30/1",
        "duration_s": 5.0,
    }
    return Probe(**{**base, **overrides})


# --- run ------------------------------------------------------------------------------------------


def test_run_produces_file(tiny_clip: Path) -> None:
    assert tiny_clip.is_file() and tiny_clip.stat().st_size > 0


def test_run_failure_raises_ffmpeg_error_with_stderr(tmp_path: Path) -> None:
    with pytest.raises(FfmpegError) as info:
        ffmpeg.run(["-i", str(tmp_path / "nao-existe.jpg"), str(tmp_path / "x.mp4")])
    assert info.value.exit_code == 6
    assert "nao-existe.jpg" in info.value.stderr


def test_run_uses_argument_list_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ffmpeg.run(["-i", "a; rm -rf /", "b.mp4"])
    assert isinstance(seen["cmd"], list)
    assert seen["cmd"][0] == "ffmpeg" and seen["cmd"][-3:] == ["-i", "a; rm -rf /", "b.mp4"]
    assert not seen["kwargs"].get("shell", False)
    assert "-y" in seen["cmd"], "sobrescrever a saída é o contrato (o harness grava num caminho novo por tentativa)"
    assert seen["kwargs"]["timeout"] > 0


def test_run_missing_binary_is_ffmpeg_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(FfmpegError, match="ffmpeg"):
        ffmpeg.run(["-version"])


def test_run_timeout_is_ffmpeg_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(cmd: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(FfmpegError, match="tempo"):
        ffmpeg.run(["-version"], timeout_s=1)


# --- probe ----------------------------------------------------------------------------------------


def test_probe_reads_delivery_clip(tiny_clip: Path) -> None:
    p = ffmpeg.probe(tiny_clip)
    assert (p.codec, p.width, p.height, p.pix_fmt, p.fps) == ("h264", 1920, 1080, "yuv420p", "30/1")
    assert p.duration_s == pytest.approx(1.0, abs=0.05)
    assert ffmpeg.is_delivery_format(p)


def test_probe_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FfmpegError):
        ffmpeg.probe(tmp_path / "nao-existe.mp4")


def test_probe_not_a_media_file_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "lixo.mp4"
    bogus.write_bytes(b"VERION-FAKE-CLIP v1\n")
    with pytest.raises(FfmpegError):
        ffmpeg.probe(bogus)


def test_probe_without_video_stream_raises(tmp_path: Path) -> None:
    audio = tmp_path / "som.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=duration=1", str(audio)])
    with pytest.raises(FfmpegError, match="vídeo"):
        ffmpeg.probe(audio)


def test_probe_fps_value() -> None:
    assert _delivery().fps_value == 30.0
    assert _delivery(fps="30000/1001").fps_value == pytest.approx(29.97, abs=0.01)
    assert _delivery(fps="0/0").fps_value == 0.0


# --- is_delivery_format ---------------------------------------------------------------------------


def test_delivery_format_accepts_exact_spec() -> None:
    assert ffmpeg.is_delivery_format(_delivery())


@pytest.mark.parametrize(
    "override",
    [
        {"codec": "hevc"},
        {"codec": "mpeg4"},
        {"width": 1280, "height": 720},
        {"width": 1080, "height": 1920},
        {"pix_fmt": "yuv444p"},  # o bug que o xfade/drawtext já causou (skill)
        {"pix_fmt": "yuvj420p"},
        {"fps": "25/1"},
        {"fps": "30000/1001"},
        {"fps": "60/1"},
    ],
)
def test_delivery_format_rejects_each_deviation(override: dict[str, Any]) -> None:
    assert not ffmpeg.is_delivery_format(_delivery(**override))


def test_delivery_format_accepts_equivalent_30_fps_fraction() -> None:
    assert ffmpeg.is_delivery_format(_delivery(fps="60/2"))


def test_run_never_inherits_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg interativo consome teclas do terminal ('q' encerra a codificação): stdin sempre DEVNULL."""
    import subprocess

    from verion.local import ffmpeg as ff

    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    ff.run(["-version"])
    assert seen.get("stdin") is subprocess.DEVNULL


# --- run_rawvideo: quadros por pipe, com ffmpeg falso no PATH -------------------------------------------------

_BIG_FRAME = b"\x00" * (1 << 20)  # 1 MiB: bem acima do buffer de 64 KiB do pipe, então a escrita bloqueia se ninguém ler
_RAW_ARGS = ["-f", "rawvideo", "-i", "pipe:0", "out.mp4"]


def _fake_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """Instala um ``ffmpeg`` falso (script sh) à frente do PATH. ``body`` roda com ``$PIDFILE`` e ``$ARGSFILE``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ffmpeg"
    script.write_text(f'#!/bin/sh\nPIDFILE="{tmp_path}/pid"\nARGSFILE="{tmp_path}/args"\n{body}\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return bin_dir


def _alive(pid: int) -> bool:
    """Processo vivo (zumbi não conta: já morreu, só falta ser colhido)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def _assert_no_orphan(tmp_path: Path) -> None:
    pid = int((tmp_path / "pid").read_text())
    deadline = time.monotonic() + 3.0
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid), f"processo órfão {pid} continua vivo"


def test_run_rawvideo_feeds_frames_and_prefixes_the_standard_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ffmpeg(tmp_path, monkeypatch, 'echo "$@" > "$ARGSFILE"; cat > "$ARGSFILE.in"')
    ffmpeg.run_rawvideo([b"abc", memoryview(b"def")], _RAW_ARGS, timeout_s=10)
    assert (tmp_path / "args").read_text().split()[:5] == ["-y", "-hide_banner", "-loglevel", "error", "-f"]
    assert (tmp_path / "args.in").read_bytes() == b"abcdef"


def test_run_rawvideo_nonzero_exit_is_ffmpeg_error_with_stderr_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ffmpeg(tmp_path, monkeypatch, 'echo $$ > "$PIDFILE"; echo "codec explodiu" >&2; exit 3')
    with pytest.raises(FfmpegError, match="código 3: codec explodiu") as info:
        ffmpeg.run_rawvideo([_BIG_FRAME] * 4, _RAW_ARGS, timeout_s=10)  # ffmpeg sai antes de ler: BrokenPipe interno
    assert "codec explodiu" in info.value.stderr


def test_run_rawvideo_missing_binary_is_ffmpeg_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "vazio"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(FfmpegError, match="não encontrado"):
        ffmpeg.run_rawvideo([b"x"], _RAW_ARGS, timeout_s=10)


def test_run_rawvideo_ffmpeg_that_stops_reading_times_out_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O bug reproduzido na revisão: o ffmpeg para de ler, a escrita bloqueia e nada mais acontece."""
    _fake_ffmpeg(tmp_path, monkeypatch, 'sleep 1000 & echo $! > "$PIDFILE"; echo "travado" >&2; wait')
    start = time.monotonic()
    with pytest.raises(FfmpegError, match="tempo limite de 0.5 s: travado"):
        ffmpeg.run_rawvideo([_BIG_FRAME] * 64, _RAW_ARGS, timeout_s=0.5)
    assert time.monotonic() - start < 5.0
    _assert_no_orphan(tmp_path)


def test_run_rawvideo_ffmpeg_that_never_finishes_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lê tudo, mas não termina depois de fechar o stdin (nem o ``wait`` pode travar)."""
    _fake_ffmpeg(tmp_path, monkeypatch, 'cat > /dev/null; sleep 1000 & echo $! > "$PIDFILE"; wait')
    start = time.monotonic()
    with pytest.raises(FfmpegError, match="tempo limite"):
        ffmpeg.run_rawvideo([b"pouco"], _RAW_ARGS, timeout_s=0.5)
    assert time.monotonic() - start < 5.0
    _assert_no_orphan(tmp_path)


def test_run_rawvideo_slow_frame_source_also_counts_against_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, monkeypatch, 'sleep 1000 & echo $! > "$PIDFILE"; cat > /dev/null; wait')

    def slow() -> Any:
        yield b"a"
        time.sleep(0.9)  # o gerador demora mais que o prazo
        yield b"b"

    start = time.monotonic()
    with pytest.raises(FfmpegError, match="tempo limite"):
        ffmpeg.run_rawvideo(slow(), _RAW_ARGS, timeout_s=0.5)
    assert time.monotonic() - start < 6.0
    _assert_no_orphan(tmp_path)


def test_run_rawvideo_frame_source_error_propagates_and_kills_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, monkeypatch, 'sleep 1000 & echo $! > "$PIDFILE"; cat > /dev/null; wait')

    def broken() -> Any:
        yield b"a"
        deadline = time.monotonic() + 3.0
        while not (tmp_path / "pid").exists() and time.monotonic() < deadline:
            time.sleep(0.02)  # espera o script falso gravar o pid do filho, para checar que ele morre
        raise RuntimeError("render falhou")

    with pytest.raises(RuntimeError, match="render falhou"):
        ffmpeg.run_rawvideo(broken(), _RAW_ARGS, timeout_s=30)
    _assert_no_orphan(tmp_path)


def test_run_rawvideo_works_with_the_real_ffmpeg(tmp_path: Path) -> None:
    out = tmp_path / "raw.mp4"
    frames = [bytes([i * 40]) * (64 * 48 * 3) for i in range(5)]
    ffmpeg.run_rawvideo(
        frames,
        ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x48", "-r", "30", "-i", "pipe:0",
         "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(out)],
        timeout_s=60,
    )  # fmt: skip
    assert ffmpeg.probe(out).width == 64
