"""Fixtures da pasta local (dono: T5). Fixtures comuns (prices, conn, authorize, fake_clock) vêm de tests/conftest.py.

Sem rede e sem chave de API: toda mídia é sintética, gerada pelo ffmpeg local (lavfi).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PHOTO_WIDTH, PHOTO_HEIGHT = 4000, 3000


@pytest.fixture(scope="session")
def synthetic_photo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """JPEG 4000x3000 (4:3, como uma foto de celular) gerado pelo lavfi ``testsrc2``; uma vez por sessão."""
    if shutil.which("ffmpeg") is None:
        pytest.fail("ffmpeg não encontrado no PATH: os testes de mídia local precisam dele (ver skill verion-ffmpeg-assembly)")
    path = tmp_path_factory.mktemp("fotos") / "sala.jpg"
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={PHOTO_WIDTH}x{PHOTO_HEIGHT}:rate=1",
            "-frames:v", "1", "-q:v", "2", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    if proc.returncode != 0 or not path.is_file():
        pytest.fail(f"não foi possível gerar a foto sintética: {proc.stderr}")
    return path
