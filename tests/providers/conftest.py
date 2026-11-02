"""Fixtures da pasta providers (dono: T2). Fixtures comuns (prices, conn, authorize, fake_clock) vêm de tests/conftest.py.

Sem rede, sem chave de API: tudo aqui é local e fictício.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from verion.ledger import repo
from verion.providers.base import Scene


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """Arquivo qualquer no lugar da foto (o ``fake`` não lê a foto; o caminho só precisa existir)."""
    path = tmp_path / "fotos" / "sala.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-real-jpeg")
    return path


@pytest.fixture
def scene(photo: Path) -> Scene:
    return Scene(
        index=1,
        photo=photo,
        room="sala",
        camera="push-in",
        duration_s=5.0,
        prompt="Push-in lento e contínuo. Não adicionar objetos.",
    )


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "saida"


@pytest.fixture
def new_run(conn: sqlite3.Connection) -> Callable[..., int]:
    """Cria um run no ledger de teste e devolve o id."""

    def _new_run(method: str = "fake") -> int:
        return repo.create_run(
            conn,
            property_id="casa-1",
            method=method,
            script_path="r.toml",
            script_sha256="0" * 64,
        )

    return _new_run
