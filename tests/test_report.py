"""Testes de ``verion.report`` (spec 3.9, T4). Banco semeado pela API pública (``budget`` e ``repo``).

Cenário semeado (valores calculados à mão, preços fictícios de ``tests/conftest.py``: veo = US$ 0,10/s x 5,0 = R$ 0,50/s):

ken-burns, run 1 (``casa-01``):
  cena 1: t1 ok (2 s) + aprovada
  cena 2: t1 ok (4 s) + refazer [defeito 2]; t2 ok (6 s) + aprovada
  cena 3: t1 ok (3 s) + rejeitada [defeitos 1 e 4]
  cena 4: t1 ok (5 s) sem QC (aguardando_qc)
  cenas 5..12: sem tentativa
ken-burns, run 2: 0 tentativas (ignorado)
veo-3.1-fast, run 3: cena 1 com 3 erros de 5 s (cada um R$ 2,50) -> rejeitada pelo limite
fake, run 4: ignorado
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from verion import budget, report
from verion.config import PriceTable
from verion.ledger import repo
from verion.ledger.repo import AttemptRow, Defect
from verion.report import MethodReport


def new_run(conn: sqlite3.Connection, method: str, property_id: str = "casa-01") -> int:
    return repo.create_run(conn, property_id=property_id, method=method, script_path="r.toml", script_sha256="x")


def seed(
    conn: sqlite3.Connection,
    prices: PriceTable,
    run_id: int,
    scene: int,
    *,
    method: str,
    ok: bool = True,
    wall: float = 1.0,
    units: float = 5.0,
) -> AttemptRow:
    paid = prices.is_paid(method)
    use_units = units if paid else 0.0
    token = budget.reserve(
        conn,
        prices,
        run_id=run_id,
        scene_index=scene,
        target_s=5.0,
        method=method,
        provider="teste",
        model="teste-1",
        units=use_units,
        unit_kind=prices.unit_kind_of(method),
        allow_paid=True,
    )
    if ok:
        token.consume(method)
        return budget.settle_ok(
            conn,
            token,
            units=use_units,
            cost_is_estimate=False,
            clip_path=Path("/tmp/clip.mp4"),
            duration_s=5.0,
            wall_time_s=wall,
            provider_job_id=None,
        )
    return budget.settle_error(
        conn, token, billed_units=0.0, error="falha simulada", wall_time_s=wall, provider_job_id=None
    )


def review(conn: sqlite3.Connection, attempt: AttemptRow, verdict: str, *tipos: int | str) -> None:
    defects = [Defect(tipo, 1.0, "nota") for tipo in tipos]
    repo.record_qc(conn, attempt.id, verdict=verdict, reviewer="humano", defects=defects)


@pytest.fixture
def seeded(conn: sqlite3.Connection, prices: PriceTable, authorize: Callable[[float], int]) -> sqlite3.Connection:
    authorize(1000.0)
    kb = new_run(conn, "ken-burns")
    a = seed(conn, prices, kb, 1, method="ken-burns", wall=2.0)
    review(conn, a, "aprovada")
    a = seed(conn, prices, kb, 2, method="ken-burns", wall=4.0)
    review(conn, a, "refazer", 2)
    a = seed(conn, prices, kb, 2, method="ken-burns", wall=6.0)
    review(conn, a, "aprovada")
    a = seed(conn, prices, kb, 3, method="ken-burns", wall=3.0)
    review(conn, a, "rejeitada", 1, 4)
    seed(conn, prices, kb, 4, method="ken-burns", wall=5.0)  # sem QC

    new_run(conn, "ken-burns")  # run 2: 0 tentativas

    veo = new_run(conn, "veo-3.1-fast")
    for _ in range(3):
        seed(conn, prices, veo, 1, method="veo-3.1-fast", ok=False, wall=9.0)

    fake = new_run(conn, "fake")
    a = seed(conn, prices, fake, 1, method="fake")
    review(conn, a, "aprovada")
    return conn


def by_method(rows: list[MethodReport]) -> dict[str, MethodReport]:
    return {row.method: row for row in rows}


def test_report_matches_manual_calculation(seeded: sqlite3.Connection) -> None:
    rows = report.build_report(seeded)

    assert [r.method for r in rows] == ["ken-burns", "veo-3.1-fast"]  # ordenado, sem o fake
    kb, veo = rows

    assert (kb.scenes_total, kb.approved, kb.rejected, kb.open) == (12, 2, 1, 9)
    assert kb.attempts == 5
    assert kb.attempts_per_approved == pytest.approx(2.5)
    assert kb.cost_brl == 0.0
    assert kb.cost_per_approved_brl == 0.0
    assert kb.mean_wall_time_s == pytest.approx((2 + 4 + 6 + 3 + 5) / 5)
    assert dict(kb.blocking_defects) == {1: 1, 2: 1, 3: 0, 4: 1, 5: 0, 6: 0, 7: 0}

    assert (veo.scenes_total, veo.approved, veo.rejected, veo.open) == (12, 0, 1, 11)
    assert veo.attempts == 3
    assert veo.attempts_per_approved is None
    assert veo.cost_brl == pytest.approx(7.5)
    assert veo.cost_per_approved_brl is None
    assert veo.mean_wall_time_s is None  # nenhuma tentativa ok
    assert dict(veo.blocking_defects) == dict.fromkeys(range(1, 8), 0)


def test_run_with_zero_attempts_does_not_inflate_scenes_total(
    conn: sqlite3.Connection, prices: PriceTable
) -> None:
    new_run(conn, "ken-burns")  # bloqueado na cena 1: nenhuma tentativa
    assert report.build_report(conn) == []

    run = new_run(conn, "ken-burns")
    seed(conn, prices, run, 1, method="ken-burns")
    (row,) = report.build_report(conn)
    assert row.scenes_total == 12  # 12 x 1 run com tentativa, e não 12 x 2


def test_scenes_total_is_12_per_run_with_attempts(conn: sqlite3.Connection, prices: PriceTable) -> None:
    for _ in range(2):
        run = new_run(conn, "parallax-2.5d")
        seed(conn, prices, run, 1, method="parallax-2.5d")
    (row,) = report.build_report(conn)
    assert row.scenes_total == 24
    assert row.approved + row.rejected + row.open == 24


def test_report_excludes_fake_and_handles_empty_db(conn: sqlite3.Connection, prices: PriceTable) -> None:
    assert report.build_report(conn) == []
    run = new_run(conn, "fake")
    # fake nunca aparece, mesmo com tentativas
    repo.add_authorization(conn, 100.0, "teste")
    seed(conn, prices, run, 1, method="fake")
    assert report.build_report(conn) == []


def test_pending_attempt_counts_as_attempt_and_cost(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Callable[[float], int]
) -> None:
    authorize(100.0)
    run = new_run(conn, "veo-3.1-fast")
    budget.reserve(
        conn,
        prices,
        run_id=run,
        scene_index=1,
        target_s=5.0,
        method="veo-3.1-fast",
        provider="teste",
        model="m",
        units=5.0,
        unit_kind="video_seconds",
        allow_paid=True,
    )
    (row,) = report.build_report(conn)
    assert (row.attempts, row.approved, row.open) == (1, 0, 12)
    assert row.cost_brl == pytest.approx(2.5)
    assert row.mean_wall_time_s is None


def test_render_markdown(seeded: sqlite3.Connection) -> None:
    text = report.render(report.build_report(seeded), "md")
    lines = text.strip().splitlines()
    assert lines[0].startswith("| Método |")
    assert set(lines[1].replace("|", "").replace(" ", "")) <= {"-", ":"}  # separador de tabela
    assert len(lines) == 2 + 2  # cabeçalho, separador e 2 métodos
    kb_line = next(line for line in lines if line.startswith("| ken-burns"))
    assert "2,50" in kb_line  # tentativas por aprovada, com vírgula decimal
    veo_line = next(line for line in lines if line.startswith("| veo-3.1-fast"))
    assert "7,50" in veo_line
    assert "—" in veo_line  # sem aprovadas: nada de divisão por zero


def test_render_csv_is_machine_readable(seeded: sqlite3.Connection) -> None:
    text = report.render(report.build_report(seeded), "csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["method"] for r in rows] == ["ken-burns", "veo-3.1-fast"]
    kb, veo = rows
    assert kb["scenes_total"] == "12" and kb["approved"] == "2"
    assert float(kb["attempts_per_approved"]) == pytest.approx(2.5)
    assert kb["defects_1"] == "1" and kb["defects_2"] == "1" and kb["defects_3"] == "0"
    assert veo["cost_per_approved_brl"] == "" and veo["attempts_per_approved"] == ""
    assert float(veo["cost_brl"]) == pytest.approx(7.5)


def test_render_rejects_unknown_format(seeded: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        report.render(report.build_report(seeded), "html")  # type: ignore[arg-type]


def test_render_empty(conn: sqlite3.Connection) -> None:
    assert "sem runs" in report.render([], "md").lower()
    assert report.render([], "csv").splitlines()[0].startswith("method,")
