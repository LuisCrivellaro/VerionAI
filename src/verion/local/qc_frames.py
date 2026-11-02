"""Quadros para o QC de fidelidade (spec 3.6, receita R6): 1 quadro por segundo e uma folha de contato.

Alimenta a skill ``verion-fidelity-qc``: quem revisa compara primeiro e último quadro com a foto de origem.
A folha de contato tem 4 colunas e linhas suficientes para TODOS os quadros (a receita original, ``tile=4x4``,
cortaria o vídeo no 16º segundo).
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Final

from verion.errors import FfmpegError, InputError
from verion.local import ffmpeg

__all__ = ["SHEET_NAME", "extract"]

SHEET_NAME: Final = "qc_contato.jpg"
_FRAME_PATTERN: Final = "qc_%03d.jpg"
_FRAME_NAME: Final = re.compile(r"qc_(\d{3,})\.jpg")
_FRAME_WIDTH: Final = 480
_SHEET_THUMB_WIDTH: Final = 320
_SHEET_COLUMNS: Final = 4
_JPEG_QUALITY: Final = "2"  # 2 = alta qualidade: o revisor procura defeitos finos


def extract(clip: Path, out_dir: Path) -> tuple[list[Path], Path]:
    """Extrai 1 quadro/s de ``clip`` (``qc_001.jpg``...) e a folha de contato (``qc_contato.jpg``) em ``out_dir``.

    Quadros e folha de uma execução anterior em ``out_dir`` são substituídos; outros arquivos ficam. Nada aparece
    em ``out_dir`` se o ffmpeg falhar. Devolve ``(quadros em ordem, folha)``.
    """
    clip = Path(clip)
    if not clip.is_file():
        raise InputError(f"clipe não encontrado: {clip}")
    ffmpeg.probe(clip)  # FfmpegError antes de criar qualquer coisa se não houver vídeo legível

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".qc-", dir=out_dir))
    try:
        ffmpeg.run(
            [
                "-i", str(clip.resolve()),
                "-vf", f"fps=1,scale={_FRAME_WIDTH}:-1",
                "-q:v", _JPEG_QUALITY,
                str(work / _FRAME_PATTERN),
            ]
        )  # fmt: skip
        names = sorted((p.name for p in work.iterdir() if _FRAME_NAME.fullmatch(p.name)), key=_frame_number)
        if not names:
            raise FfmpegError(f"{clip}: nenhum quadro extraído")
        rows = math.ceil(len(names) / _SHEET_COLUMNS)
        ffmpeg.run(
            [
                "-i", str(clip.resolve()),
                "-vf", f"fps=1,scale={_SHEET_THUMB_WIDTH}:-1,tile={_SHEET_COLUMNS}x{rows}",
                "-frames:v", "1", "-update", "1",
                "-q:v", _JPEG_QUALITY,
                str(work / SHEET_NAME),
            ]
        )  # fmt: skip
        if not (work / SHEET_NAME).is_file():
            raise FfmpegError(f"{clip}: folha de contato não foi gerada")

        for stale in out_dir.iterdir():
            if stale.is_file() and (_FRAME_NAME.fullmatch(stale.name) or stale.name == SHEET_NAME):
                stale.unlink()
        for name in (*names, SHEET_NAME):
            os.replace(work / name, out_dir / name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return [out_dir / n for n in names], out_dir / SHEET_NAME


def _frame_number(name: str) -> int:
    match = _FRAME_NAME.fullmatch(name)
    assert match is not None
    return int(match.group(1))
