"""Executor de ``ffmpeg``/``ffprobe`` e verificação do formato de entrega (spec 3.6).

Sem shell: os argumentos vão como lista para ``subprocess.run``, então nomes de arquivo com espaços,
``;`` ou ``$`` não são interpretados. Toda falha (binário ausente, código de saída ≠ 0, tempo esgotado,
saída ilegível) vira ``FfmpegError`` (código de saída 6) com o ``stderr`` anexado.

Formato de entrega do MVP (skill ``verion-ffmpeg-assembly``): H.264, 1920x1080, ``yuv420p``, 30 fps.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Buffer, Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final

from verion.errors import FfmpegError

__all__ = [
    "DELIVERY_FPS",
    "DELIVERY_HEIGHT",
    "DELIVERY_WIDTH",
    "Probe",
    "is_delivery_format",
    "probe",
    "run",
    "run_rawvideo",
]

DELIVERY_CODEC: Final = "h264"
DELIVERY_WIDTH: Final = 1920
DELIVERY_HEIGHT: Final = 1080
DELIVERY_PIX_FMT: Final = "yuv420p"
DELIVERY_FPS: Final = 30

_DEFAULT_TIMEOUT_S: Final = 600.0
_PROBE_TIMEOUT_S: Final = 60.0
_STDERR_TAIL: Final = 2000  # a mensagem da exceção leva só o fim do stderr; ``exc.stderr`` guarda tudo


@dataclass(frozen=True)
class Probe:
    """Primeiro fluxo de vídeo do arquivo, como o ``ffprobe`` o descreve."""

    codec: str
    width: int
    height: int
    pix_fmt: str
    fps: str  # fração do ffprobe, ex.: "30/1"
    duration_s: float

    @property
    def fps_value(self) -> float:
        """``fps`` como número; fração inválida ou ``0/0`` dá 0,0."""
        try:
            return float(Fraction(self.fps))
        except (ValueError, ZeroDivisionError):
            return 0.0


def run(args: list[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
    """Roda ``ffmpeg -y -hide_banner -loglevel error <args>``. ``args`` NÃO inclui o binário.

    ``-y`` sobrescreve a saída: quem chama escolhe um caminho novo por tentativa. Levanta
    ``FfmpegError`` com o ``stderr`` completo se o ffmpeg falhar ou estourar ``timeout_s``.
    """
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    _execute(cmd, timeout_s)


def run_rawvideo(frames: Iterable[Buffer], args: list[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
    """Roda ``ffmpeg -y -hide_banner -loglevel error <args>`` alimentando o stdin com ``frames`` (bytes brutos).

    ``args`` NÃO inclui o binário e precisa ler de ``pipe:0``. Cada quadro é um objeto com buffer contíguo
    (``bytes``, ``memoryview``, ``numpy.ndarray`` C-contíguo). As garantias são as de ``run``: sem shell, o stdin
    do processo é o pipe de quadros (nunca o terminal), e toda falha vira ``FfmpegError`` com o fim do ``stderr``
    (``exc.stderr`` guarda tudo): binário ausente, código de saída ≠ 0, ffmpeg que encerra sem ler os quadros e
    tempo esgotado.

    O prazo ``timeout_s`` vale para a chamada inteira, inclusive o tempo que ``frames`` leva para produzir os
    quadros. Um vigia (``threading.Timer``) mata o grupo de processos do ffmpeg quando o prazo acaba: isso destrava
    uma escrita bloqueada (ffmpeg que parou de ler) e não deixa processo órfão. Se ``frames`` levantar, o ffmpeg
    é morto e a exceção sobe sem alteração.
    """
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=err,
                start_new_session=True,  # grupo próprio: o vigia mata o ffmpeg e qualquer filho
            )
        except FileNotFoundError as exc:
            raise FfmpegError(f"{cmd[0]} não encontrado no PATH; instale o ffmpeg", stderr=str(exc)) from exc
        expired = threading.Event()

        def _expire() -> None:
            expired.set()
            _kill_group(proc)

        watchdog = threading.Timer(timeout_s, _expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            stdin = proc.stdin
            assert stdin is not None
            try:
                for frame in frames:
                    stdin.write(memoryview(frame).cast("B"))
            except BrokenPipeError:
                pass  # o ffmpeg encerrou (ou foi morto) antes do fim: o código de saída e o stderr explicam
            finally:
                try:
                    stdin.close()
                except BrokenPipeError:
                    pass
            code = proc.wait()
        except BaseException:
            _kill_group(proc)
            proc.wait()
            raise
        finally:
            watchdog.cancel()
        if code != 0:
            _kill_group(proc)  # filhos que sobraram
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    tail = stderr[-_STDERR_TAIL:].strip()
    if code != 0 and expired.is_set():
        raise FfmpegError(f"{cmd[0]} excedeu o tempo limite de {timeout_s:g} s: {tail}", stderr=stderr)
    if code != 0:
        raise FfmpegError(f"{cmd[0]} terminou com código {code}: {tail}", stderr=stderr)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL no grupo de processos do ffmpeg (``start_new_session``: pgid = pid). Silencioso se já acabou."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _execute(cmd: list[str], timeout_s: float) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,  # ffmpeg interativo leria teclas do terminal ('q' encerra)
        )
    except FileNotFoundError as exc:
        raise FfmpegError(
            f"{cmd[0]} não encontrado no PATH; instale o ffmpeg", stderr=str(exc)
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(
            f"{cmd[0]} excedeu o tempo limite de {timeout_s:g} s", stderr=_text(exc.stderr)
        ) from exc
    if proc.returncode != 0:
        raise FfmpegError(
            f"{cmd[0]} terminou com código {proc.returncode}: {proc.stderr[-_STDERR_TAIL:].strip()}",
            stderr=proc.stderr,
        )
    return proc.stdout


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def probe(path: Path) -> Probe:
    """Descreve o primeiro fluxo de vídeo de ``path`` (``ffprobe``). ``FfmpegError`` se não houver vídeo."""
    out = _execute(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate:format=duration",
            "-of", "json", "--", str(path),
        ],
        _PROBE_TIMEOUT_S,
    )  # fmt: skip
    try:
        data = json.loads(out)
        streams = data.get("streams") or []
        if not streams:
            raise FfmpegError(f"{path}: sem fluxo de vídeo")
        s = streams[0]
        return Probe(
            codec=str(s["codec_name"]),
            width=int(s["width"]),
            height=int(s["height"]),
            pix_fmt=str(s["pix_fmt"]),
            fps=str(s["avg_frame_rate"]),
            duration_s=float(data["format"]["duration"]),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FfmpegError(f"{path}: saída do ffprobe ilegível ({exc!r})", stderr=out) from exc


def is_delivery_format(p: Probe) -> bool:
    """H.264, 1920x1080, ``yuv420p`` e 30 fps exatos (30/1 ou fração equivalente, como 60/2)."""
    return (
        p.codec == DELIVERY_CODEC
        and (p.width, p.height) == (DELIVERY_WIDTH, DELIVERY_HEIGHT)
        and p.pix_fmt == DELIVERY_PIX_FMT
        and _fps_is(p.fps, DELIVERY_FPS)
    )


def _fps_is(fps: str, expected: int) -> bool:
    try:
        return Fraction(fps) == expected
    except (ValueError, ZeroDivisionError):
        return False
