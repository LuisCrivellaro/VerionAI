"""Fixtures da aceitação ponta a ponta da Fase 0 (dono: T12).

Tudo roda pela CLI real (``python -m verion.cli``) em subprocesso, com ``VERION_DATA_DIR`` e
``VERION_PRICES`` em ``tmp_path``. Sem rede, sem chave real, sem chamada paga: o único método "pago" é o
``fake`` (R$ 1,00 por segundo, preço de teste). As fotos e os mapas de profundidade são sintéticos.

Custo de tempo (medido em CPU de 2 núcleos): o render 1080p é o item caro. O pipeline completo (12 cenas de
``ken-burns``, 3 de ``parallax-2.5d`` e a montagem de 12 clipes) leva ~110 s sozinho; a suíte anterior à T12
já leva ~93 s, e o critério é "pytest inteiro < 120 s". Por isso há dois níveis:

- padrão: testes leves (``fake``, trava de gasto, TTY, recusas) e um fumaça com render real de 2 cenas
  ``ken-burns``, aprovação em pty, montagem com fallback, relatório e export (~10 s);
- completo (``VERION_E2E_FULL=1``, marcador ``e2e_full``): o fluxo da spec com 12 cenas, parallax e montagem
  de 12 clipes. Os renders pesados rodam UMA vez por módulo (fixtures ``module``) e os testes só leem o resultado.

As fotos têm 960x540 (16:9, sem recorte) e as cenas, 3 s (mínimo do roteiro).
"""

from __future__ import annotations

import os
import pty
import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from verion.ledger import repo

FULL_ENV_VAR = "VERION_E2E_FULL"
PHOTO_SIZE = (960, 540)
SCENE_SECONDS = 3
CAMERAS = ("push-in", "pull-out", "pan-left", "pan-right", "static")
ROOMS = ("sala", "cozinha", "suite", "banheiro", "varanda", "escritorio", "quarto", "lavabo", "piscina", "jardim", "hall", "garagem")

# Chave SINTÉTICA (formato AIza + 35 caracteres): existe no ambiente de todo subprocesso para provar que nada a vaza.
SYNTHETIC_KEY = "AIzaSyD" + "x" * 32
_TIMEOUT_S = 300


@dataclass
class CliResult:
    code: int
    out: str
    err: str


@dataclass
class Env:
    """Ambiente isolado: pasta de dados, tabela de preços, roteiro e fotos em ``root``."""

    root: Path
    data_dir: Path
    prices: Path
    script: Path
    photos: tuple[Path, ...]
    log: list[CliResult] = field(default_factory=list)
    secret: str = SYNTHETIC_KEY
    scene_seconds: int = SCENE_SECONDS

    @property
    def db_path(self) -> Path:
        return self.data_dir / "verion.db"

    def environ(self, **extra: str) -> dict[str, str]:
        full = {k: v for k, v in os.environ.items() if not k.startswith("VERION_")}
        full.update(
            VERION_DATA_DIR=str(self.data_dir),
            VERION_PRICES=str(self.prices),
            GEMINI_API_KEY=SYNTHETIC_KEY,
            PYTHONDONTWRITEBYTECODE="1",
        )
        full.update(extra)
        return full

    def cli(self, *argv: str, stdin: int | None = subprocess.DEVNULL, **extra_env: str) -> CliResult:
        """Roda ``python -m verion.cli`` SEM terminal (stdin fechado por padrão)."""
        proc = subprocess.run(
            [sys.executable, "-m", "verion.cli", *argv],
            stdin=stdin,
            capture_output=True,
            text=True,
            env=self.environ(**extra_env),
            timeout=_TIMEOUT_S,
            check=False,
        )
        return self._record(CliResult(proc.returncode, proc.stdout, proc.stderr))

    def cli_pty(self, argv: Sequence[str], typed: bytes) -> CliResult:
        """Roda a CLI com um pseudo-TTY como stdin e ``typed`` já digitado (como o usuário faria)."""
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-m", "verion.cli", *argv],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environ(),
            text=True,
        )
        os.close(slave)
        os.write(master, typed)
        try:
            out, err = proc.communicate(timeout=_TIMEOUT_S)
        finally:
            os.close(master)
        return self._record(CliResult(proc.returncode, out, err))

    def _record(self, result: CliResult) -> CliResult:
        self.log.append(result)
        return result

    def connect(self) -> sqlite3.Connection:
        return repo.connect(self.db_path)


def _make_photo(path: Path, seed: int) -> None:
    """Foto sintética 960x540: gradiente colorido com blocos (nenhum imóvel, nenhuma pessoa)."""
    width, height = PHOTO_SIZE
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    rgb = np.stack(
        [
            (60 + 150 * x + 10 * seed) % 256 + 0 * y,
            (40 + 170 * y + 7 * seed) % 256 + 0 * x,
            (200 - 90 * x * y + 5 * seed) % 256,
        ],
        axis=-1,
    )
    rgb = np.broadcast_to(rgb, (height, width, 3)).copy()
    rgb[height // 3 : 2 * height // 3, width // 4 : width // 2] = (235, 235, 225)  # "janela"
    rgb[2 * height // 3 :, width // 2 : 3 * width // 4] = (90, 60, 40)  # "móvel"
    Image.fromarray(rgb.clip(0, 255).astype(np.uint8), "RGB").save(path, "JPEG", quality=90)


def _make_depth(photo: Path) -> None:
    """Mapa de profundidade sintético, PNG 16 bits cinza (ADR-008): branco = perto, ``<foto>.depth.png``."""
    width, height = PHOTO_SIZE
    x = np.linspace(0.0, 1.0, width, dtype=np.float64)[None, :]
    y = np.linspace(0.0, 1.0, height, dtype=np.float64)[:, None]
    depth = (0.3 * x + 0.7 * y) * 65535.0  # rampa: o fundo (topo) é longe, o chão (base) é perto
    array = np.broadcast_to(depth, (height, width)).astype(np.uint16)
    Image.fromarray(array).save(photo.with_name(photo.name + ".depth.png"))


def _script_text(photos: Sequence[Path], *, with_release: bool) -> str:
    lines = ['property_id = "imv-e2e"']
    if with_release:
        lines.append('photo_release_ref = "termo-e2e"')
    for i, photo in enumerate(photos, start=1):
        lines += [
            "",
            "[[scenes]]",
            f"index = {i}",
            f'photo = "{photo.as_posix()}"',
            f'room = "{ROOMS[i - 1]}"',
            f'camera = "{CAMERAS[(i - 1) % len(CAMERAS)]}"',
            f"duration_s = {SCENE_SECONDS}.0",
            'prompt = "Movimento de camera suave. Nao adicionar objetos, pessoas ou mudancas de luz."',
        ]
    return "\n".join(lines) + "\n"


PRICES_TEXT = """\
# Preços de TESTE (fictícios): o `fake` não está em config/prices.toml.
fx_usd_brl = 5.0

[methods."ken-burns"]
unit_kind = "none"
price_per_unit = 0.0
currency = "BRL"
source = "teste"

[methods."parallax-2.5d"]
unit_kind = "none"
price_per_unit = 0.0
currency = "BRL"
source = "teste"

[methods."fake"]
unit_kind = "video_seconds"
price_per_unit = 1.0
currency = "BRL"
source = "teste (fictício)"
"""


def build_env(root: Path, *, depth_for: Sequence[int] = ()) -> Env:
    """Cria fotos, roteiro de 12 cenas de 3 s, preços e pasta de dados em ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    photos_dir = root / "fotos"
    photos_dir.mkdir()
    photos = tuple(photos_dir / f"foto-{i:02d}.jpg" for i in range(1, 13))
    for i, photo in enumerate(photos, start=1):
        _make_photo(photo, i)
        if i in depth_for:
            _make_depth(photo)
    script = root / "roteiro.toml"
    script.write_text(_script_text(photos, with_release=False), encoding="utf-8")
    prices = root / "prices.toml"
    prices.write_text(PRICES_TEXT, encoding="utf-8")
    data_dir = root / "data"
    data_dir.mkdir()
    return Env(root=root, data_dir=data_dir, prices=prices, script=script, photos=photos)


@pytest.fixture
def light_env(tmp_path: Path) -> Env:
    """Ambiente leve (fotos sem profundidade, sem render) para os testes do ``fake`` e da CLI; um por teste."""
    return build_env(tmp_path / "light_env")


@pytest.fixture(scope="module")
def e2e_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    """Ambiente do fluxo completo com mídia real: mapas de profundidade nas cenas 1 a 3."""
    yield build_env(tmp_path_factory.mktemp("e2e"), depth_for=(1, 2, 3))


# --- marcador e opt-in do pipeline completo ----------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    # registrado aqui porque só o supervisor edita o pyproject.toml; --strict-markers exige o registro
    config.addinivalue_line(
        "markers", f"e2e_full: pipeline completo com render real (~110 s); só roda com {FULL_ENV_VAR}=1"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(FULL_ENV_VAR) == "1":
        return
    skip = pytest.mark.skip(reason=f"pipeline completo (~110 s de render): rode com {FULL_ENV_VAR}=1")
    for item in items:
        if "e2e_full" in item.keywords:
            item.add_marker(skip)
