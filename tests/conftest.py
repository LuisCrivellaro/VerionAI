"""Fixtures comuns do harness da Fase 0 (dono: T1; as demais tarefas só consomem).

Todos os preços aqui são FICTÍCIOS e servem apenas aos testes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from verion.config import MethodPrice, PriceTable
from verion.ledger import repo

_FICTITIOUS = "fictício (testes)"


@pytest.fixture
def prices() -> PriceTable:
    """Tabela com os 2 métodos grátis e os 5 pagos (fake = R$ 1,00 por video_second)."""
    return PriceTable(
        fx_usd_brl=5.0,
        methods={
            "ken-burns": MethodPrice("none", 0.0, "BRL", _FICTITIOUS),
            "parallax-2.5d": MethodPrice("none", 0.0, "BRL", _FICTITIOUS),
            "veo-3.1-fast": MethodPrice("video_seconds", 0.10, "USD", _FICTITIOUS),
            "gemini-omni-1.1-flash": MethodPrice("video_seconds", 0.10, "USD", _FICTITIOUS),
            "kling-3.0": MethodPrice("video_seconds", 0.20, "USD", _FICTITIOUS),
            "wan-2.2-5b": MethodPrice("gpu_seconds", 0.0002, "USD", _FICTITIOUS),
            "fake": MethodPrice("video_seconds", 1.0, "BRL", _FICTITIOUS),
        },
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Banco SQLite novo em tmp_path, com o esquema aplicado."""
    connection = repo.connect(tmp_path / "verion.db")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def authorize(conn: sqlite3.Connection) -> Callable[[float], int]:
    """Semeia uma autorização de gasto (apenas testes podem fazer isso fora da CLI)."""

    def _authorize(brl: float, reference: str = "teste") -> int:
        return repo.add_authorization(conn, brl, reference)

    return _authorize


class FakeClock:
    """Relógio determinístico: ``clock()`` devolve o instante atual; ``advance(s)`` o move."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
