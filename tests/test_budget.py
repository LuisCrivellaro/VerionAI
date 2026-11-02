"""Testes de budget.py: reserva, SpendToken e liquidação (spec 3.3)."""

from __future__ import annotations

import copy
import math
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from verion import budget, errors
from verion.budget import SpendToken
from verion.config import MethodPrice, PriceTable, UnitKind
from verion.ledger import repo
from verion.ledger.repo import Defect

Authorize = Callable[[float], int]

# --- helpers ----------------------------------------------------------------------------------


def make_run(conn: sqlite3.Connection, method: str = "fake") -> int:
    return repo.create_run(
        conn, property_id="casa-1", method=method, script_path="r.toml", script_sha256="0" * 64
    )


def reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    run_id: int,
    *,
    scene: int = 1,
    method: str = "fake",
    units: float = 2.0,
    unit_kind: UnitKind = "video_seconds",
    allow_paid: bool = True,
) -> SpendToken:
    return budget.reserve(
        conn,
        prices,
        run_id=run_id,
        scene_index=scene,
        target_s=5.0,
        method=method,
        provider="fake",
        model="fake-1",
        units=units,
        unit_kind=unit_kind,
        allow_paid=allow_paid,
    )


def settle_ok(
    conn: sqlite3.Connection,
    token: SpendToken,
    *,
    units: float = 2.0,
    job: str | None = None,
    consume: bool = True,
) -> repo.AttemptRow:
    """Simula o fluxo real: ``generate`` consome o token e só então o harness liquida (achado 2)."""
    if consume:
        token.consume(token.method)
    return budget.settle_ok(
        conn,
        token,
        units=units,
        cost_is_estimate=False,
        clip_path=Path("clip.mp4"),
        duration_s=5.0,
        wall_time_s=1.5,
        provider_job_id=job,
    )


def settle_error(
    conn: sqlite3.Connection, token: SpendToken, *, billed: float = 0.0, job: str | None = None
) -> repo.AttemptRow:
    return budget.settle_error(
        conn, token, billed_units=billed, error="falhou", wall_time_s=0.5, provider_job_id=job
    )


# --- (a) pago sem allow_paid ------------------------------------------------------------------


def test_paid_without_allow_paid_is_blocked_and_writes_nothing(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(1000.0)  # saldo não muda nada: falta o --allow-paid
    run_id = make_run(conn)
    with pytest.raises(errors.PaidCallBlocked):
        reserve(conn, prices, run_id, allow_paid=False)
    assert repo.list_attempts(conn) == []
    assert repo.spent_brl(conn) == 0.0


def test_free_method_needs_neither_allow_paid_nor_balance(conn: sqlite3.Connection, prices: PriceTable) -> None:
    run_id = make_run(conn, "ken-burns")
    token = reserve(conn, prices, run_id, method="ken-burns", units=0.0, unit_kind="none", allow_paid=False)
    assert token.reserved_brl == 0.0
    row = repo.get_attempt(conn, token.attempt_id)
    assert row is not None and row.status == "pending" and row.cost_brl == 0.0
    assert settle_ok(conn, token, units=0.0).cost_brl == 0.0


# --- (b) saldo -------------------------------------------------------------------------------


def test_budget_exceeded_when_spent_349_plus_estimate_2_of_350(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(350.0)
    run_id = make_run(conn)
    big = reserve(conn, prices, run_id, scene=1, units=349.0)
    settle_ok(conn, big, units=349.0)
    assert repo.spent_brl(conn) == pytest.approx(349.0)
    with pytest.raises(errors.BudgetExceeded):
        reserve(conn, prices, run_id, scene=2, units=2.0)
    assert len(repo.list_attempts(conn)) == 1  # nenhuma linha nova
    assert not conn.in_transaction  # rollback feito
    # o que cabe ainda passa: saldo exatamente igual ao custo
    reserve(conn, prices, run_id, scene=2, units=1.0)
    assert repo.spent_brl(conn) == pytest.approx(350.0)


def test_no_authorization_means_no_paid_spend(conn: sqlite3.Connection, prices: PriceTable) -> None:
    run_id = make_run(conn)
    with pytest.raises(errors.BudgetExceeded):
        reserve(conn, prices, run_id)
    assert repo.list_attempts(conn) == []


def test_float_accumulation_does_not_reject_exact_balance(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(0.3)
    run_id = make_run(conn)
    for scene in (1, 2, 3):  # 0.1 + 0.1 + 0.1 cabe em 0.3
        reserve(conn, prices, run_id, scene=scene, units=0.1)
    with pytest.raises(errors.BudgetExceeded):
        reserve(conn, prices, run_id, scene=4, units=0.1)


# --- (c) estimativa e preço inválidos ---------------------------------------------------------


@pytest.mark.parametrize("units", [0.0, -1.0, math.nan, math.inf])
def test_invalid_units_raise_estimate_invalid(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, units: float
) -> None:
    authorize(1000.0)
    with pytest.raises(errors.EstimateInvalid):
        reserve(conn, prices, make_run(conn), units=units)
    assert repo.list_attempts(conn) == []


def test_unit_kind_must_match_price_table(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(1000.0)
    with pytest.raises(errors.EstimateInvalid):
        reserve(conn, prices, make_run(conn), unit_kind="gpu_seconds")  # a tabela diz video_seconds
    assert repo.list_attempts(conn) == []


def test_missing_price_raises_price_missing(conn: sqlite3.Connection, authorize: Authorize) -> None:
    authorize(1000.0)
    empty = PriceTable(fx_usd_brl=5.0, methods={})
    with pytest.raises(errors.PriceMissing):
        reserve(conn, empty, make_run(conn))
    assert repo.list_attempts(conn) == []


def test_reserve_validates_run_and_scene(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(1000.0)
    run_id = make_run(conn)
    with pytest.raises(errors.InputError):
        reserve(conn, prices, 999)
    for scene in (0, 13):
        with pytest.raises(errors.InputError):
            reserve(conn, prices, run_id, scene=scene)
    assert repo.list_attempts(conn) == []


def test_zero_cost_for_paid_method_is_invalid(conn: sqlite3.Connection, authorize: Authorize) -> None:
    """Preço positivo cujo custo arredonda a 0 não pode virar reserva grátis."""
    authorize(1000.0)
    table = PriceTable(5.0, {"fake": MethodPrice("video_seconds", 1e-200, "BRL", "teste")})
    with pytest.raises(errors.EstimateInvalid):
        reserve(conn, table, make_run(conn), units=1e-200)


# --- (d) concorrência -------------------------------------------------------------------------


def _race(db_path: Path, prices: PriceTable, run_id: int, scenes: list[int], units: float) -> list[str]:
    barrier = threading.Barrier(len(scenes))

    def worker(scene: int) -> str:
        own = repo.connect(db_path)  # uma conexão por thread
        try:
            barrier.wait(timeout=10)
            try:
                reserve(own, prices, run_id, scene=scene, units=units)
            except errors.BudgetExceeded:
                return "bloqueada"
            return "reservada"
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=len(scenes)) as pool:
        return list(pool.map(worker, scenes))


def test_two_connections_with_balance_for_one_make_exactly_one_pending(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, tmp_path: Path
) -> None:
    authorize(10.0)
    run_id = make_run(conn)
    outcomes = _race(tmp_path / "verion.db", prices, run_id, [1, 2], units=8.0)  # R$ 8 cada, saldo R$ 10
    assert sorted(outcomes) == ["bloqueada", "reservada"]
    pending = [a for a in repo.list_attempts(conn) if a.status == "pending"]
    assert len(pending) == 1
    assert repo.spent_brl(conn) == pytest.approx(8.0)


def test_many_connections_never_overspend(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, tmp_path: Path
) -> None:
    authorize(10.0)
    run_id = make_run(conn)
    outcomes = _race(tmp_path / "verion.db", prices, run_id, [1, 2, 3, 4, 5, 6], units=4.0)  # cabem 2 de R$ 4
    assert outcomes.count("reservada") == 2
    assert len(repo.list_attempts(conn)) == 2
    assert repo.spent_brl(conn) <= repo.authorized_brl(conn)


# --- (e) reserva não liquidada ----------------------------------------------------------------


def test_unsettled_reservation_counts_as_spent_and_blocks_scene(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    run_id = make_run(conn)
    token = reserve(conn, prices, run_id, units=3.0)  # processo "morre" aqui: nunca liquida
    assert token.reserved_brl == pytest.approx(3.0)
    assert repo.spent_brl(conn) == pytest.approx(3.0)
    assert repo.scene_status(conn, run_id, 1)[0] == "pendente"
    with pytest.raises(errors.PendingAttempt):
        reserve(conn, prices, run_id, scene=1, units=3.0)
    assert len(repo.list_attempts(conn)) == 1
    # outra cena segue livre
    reserve(conn, prices, run_id, scene=2, units=1.0)


def test_reservation_reduces_available_balance(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(10.0)
    run_id = make_run(conn)
    reserve(conn, prices, run_id, scene=1, units=8.0)
    with pytest.raises(errors.BudgetExceeded):
        reserve(conn, prices, run_id, scene=2, units=3.0)


# --- (f) liquidação ---------------------------------------------------------------------------


def test_settle_error_below_reserve_records_the_reserve(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)  # reserva R$ 5
    row = settle_error(conn, token, billed=1.0)  # faturado R$ 1 < reserva
    assert row.status == "error"
    assert row.cost_brl == pytest.approx(5.0)
    assert row.reserved_brl == pytest.approx(5.0)
    assert row.units == pytest.approx(5.0)  # consistente com o custo
    assert row.error == "falhou" and row.wall_time_s == 0.5 and row.finished_at is not None
    assert repo.spent_brl(conn) == pytest.approx(5.0)


def test_settle_error_with_no_billing_still_records_the_reserve(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    assert settle_error(conn, token, billed=0.0).cost_brl == pytest.approx(5.0)


@pytest.mark.parametrize("billed", [-3.0, math.nan])
def test_settle_error_ignores_garbage_billed_units(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, billed: float
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    assert settle_error(conn, token, billed=billed).cost_brl == pytest.approx(5.0)


def test_settle_error_above_reserve_records_billed_cost(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    row = settle_error(conn, token, billed=8.0)
    assert row.cost_brl == pytest.approx(8.0)
    assert row.units == pytest.approx(8.0)
    assert repo.spent_brl(conn) == pytest.approx(8.0)


def test_settle_error_works_on_a_token_that_was_never_consumed(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    """Se o adaptador falhar antes de consume(), o harness ainda precisa liquidar com a reserva."""
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    assert settle_error(conn, token).cost_brl == pytest.approx(5.0)


def test_settle_error_keeps_job_id_from_token_when_not_given(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    token.consume("fake")
    token.job_accepted("job-77")
    assert settle_error(conn, token, billed=2.0).provider_job_id == "job-77"


def test_settle_pending_cannot_reduce_cost_after_a_crash(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    with pytest.raises(errors.InputError):
        repo.settle_pending(conn, token.attempt_id, cost_brl=0.0, note="tentando zerar")
    assert repo.spent_brl(conn) == pytest.approx(5.0)
    assert repo.settle_pending(conn, token.attempt_id, cost_brl=6.0, note="fatura").cost_brl == 6.0
    assert repo.spent_brl(conn) == pytest.approx(6.0)


def test_settle_ok_records_real_cost_from_units(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    token.consume("fake")
    row = budget.settle_ok(
        conn,
        token,
        units=3.0,
        cost_is_estimate=True,
        clip_path=Path("out/s01_t1.mp4"),
        duration_s=3.0,
        wall_time_s=12.5,
        provider_job_id="job-1",
    )
    assert row.status == "ok"
    assert row.cost_brl == pytest.approx(3.0)  # R$ 1,00 por segundo no fake
    assert row.units == 3.0 and row.reserved_brl == pytest.approx(5.0)
    assert row.cost_is_estimate is True
    assert (row.clip_path, row.duration_s, row.wall_time_s, row.provider_job_id) == (
        str(Path("out/s01_t1.mp4")),
        3.0,
        12.5,
        "job-1",
    )
    assert repo.spent_brl(conn) == pytest.approx(3.0)


def test_settle_ok_converts_usd_with_fx(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn, "veo-3.1-fast"), method="veo-3.1-fast", units=8.0)
    assert token.reserved_brl == pytest.approx(8 * 0.10 * 5.0)
    assert settle_ok(conn, token, units=8.0).cost_brl == pytest.approx(4.0)


def test_settle_ok_with_invalid_units_leaves_row_pending(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=5.0)
    token.consume("fake")
    for units in (0.0, -1.0, math.nan):
        with pytest.raises(errors.EstimateInvalid):
            settle_ok(conn, token, units=units, consume=False)
    assert repo.get_attempt(conn, token.attempt_id).status == "pending"  # type: ignore[union-attr]
    assert repo.spent_brl(conn) == pytest.approx(5.0)  # a reserva continua valendo
    assert settle_ok(conn, token, units=5.0, consume=False).status == "ok"  # e ainda dá para liquidar


def test_cannot_settle_twice(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    run_id = make_run(conn)
    ok_token = reserve(conn, prices, run_id, scene=1, units=2.0)
    settle_ok(conn, ok_token)
    err_token = reserve(conn, prices, run_id, scene=2, units=2.0)
    err_token.consume("fake")
    settle_error(conn, err_token)
    for token in (ok_token, err_token):  # ambos já consumidos: a recusa vem da linha não-pending
        with pytest.raises(errors.TokenInvalid, match="pending"):
            settle_ok(conn, token, consume=False)
        with pytest.raises(errors.TokenInvalid):
            settle_error(conn, token)
    assert repo.spent_brl(conn) == pytest.approx(4.0)


# --- (g) SpendToken forjado, reusado ou de outro método ---------------------------------------


def test_token_cannot_be_built_without_the_seal(conn: sqlite3.Connection) -> None:
    with pytest.raises(errors.TokenInvalid):
        SpendToken(object(), conn=conn, attempt_id=1, attempt_no=1, method="fake", reserved_brl=1.0, prices=None)  # type: ignore[arg-type]


def test_forged_token_via_new_is_refused(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    real = reserve(conn, prices, make_run(conn), units=2.0)
    forged = SpendToken.__new__(SpendToken)
    forged.__dict__.update(real.__dict__)  # copia estado, mas não passa pelo selo
    with pytest.raises(errors.TokenInvalid):
        forged.consume("fake")
    with pytest.raises(errors.TokenInvalid):
        copy.copy(real).consume("fake")
    with pytest.raises(errors.TokenInvalid):
        SpendToken.__new__(SpendToken).consume("fake")
    real.consume("fake")  # o original continua válido


def test_token_is_single_use(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    token.consume("fake")
    with pytest.raises(errors.TokenInvalid):
        token.consume("fake")


def test_token_of_another_method_is_refused_without_burning_it(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)  # método fake
    with pytest.raises(errors.TokenInvalid):
        token.consume("veo-3.1-fast")
    token.consume("fake")  # o adaptador certo ainda consegue


def test_consume_refuses_a_row_that_is_no_longer_pending(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    settle_error(conn, token)
    with pytest.raises(errors.TokenInvalid):
        token.consume("fake")


def test_job_accepted_requires_consume_and_persists_job_id(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    with pytest.raises(errors.TokenInvalid):
        token.job_accepted("job-1")  # antes do consume
    token.consume("fake")
    with pytest.raises(errors.InputError):
        token.job_accepted("  ")
    token.job_accepted("job-1")
    row = repo.get_attempt(conn, token.attempt_id)
    assert row is not None and row.provider_job_id == "job-1" and row.status == "pending"
    assert settle_ok(conn, token, consume=False).provider_job_id == "job-1"


def test_token_exposes_reservation_details(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=4.0)
    assert (token.attempt_no, token.method) == (1, "fake")
    assert token.reserved_brl == pytest.approx(4.0)
    assert token.attempt_id == repo.list_attempts(conn)[0].id


# --- (h) regras de tentativa via reserve ------------------------------------------------------


def test_three_errors_then_fourth_reserve_is_scene_closed(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    run_id = make_run(conn)
    for expected_no in (1, 2, 3):
        token = reserve(conn, prices, run_id, units=1.0)
        assert token.attempt_no == expected_no
        settle_error(conn, token, billed=1.0)
    assert repo.scene_status(conn, run_id, 1)[0] == "rejeitada"
    with pytest.raises(errors.SceneClosed):
        reserve(conn, prices, run_id, units=1.0)
    assert len(repo.list_attempts(conn)) == 3
    assert repo.spent_brl(conn) == pytest.approx(3.0)


def test_ok_without_qc_blocks_next_reserve_until_verdict(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    run_id = make_run(conn)
    token = reserve(conn, prices, run_id, units=2.0)
    settle_ok(conn, token)
    with pytest.raises(errors.AwaitingQC):
        reserve(conn, prices, run_id, units=2.0)
    assert len(repo.list_attempts(conn)) == 1
    block = Defect(tipo=2, segundo=1.0, nota="x")
    repo.record_qc(conn, token.attempt_id, verdict="refazer", reviewer="humano", defects=[block])
    assert reserve(conn, prices, run_id, units=2.0).attempt_no == 2


def test_attempt_rule_errors_are_not_spend_blocked(conn: sqlite3.Connection, prices: PriceTable) -> None:
    """Free method: AttemptRuleError sobe sem criar linha (o harness põe a cena em `skipped`)."""
    run_id = make_run(conn, "ken-burns")
    kwargs = dict(method="ken-burns", units=0.0, unit_kind="none", allow_paid=False)
    token = reserve(conn, prices, run_id, **kwargs)  # type: ignore[arg-type]
    settle_ok(conn, token, units=0.0)
    with pytest.raises(errors.AwaitingQC):
        reserve(conn, prices, run_id, **kwargs)  # type: ignore[arg-type]
    assert len(repo.list_attempts(conn)) == 1


# --- revisão T1 rodada 1 ----------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

import numpy as np  # noqa: E402


@pytest.mark.parametrize(
    ("field", "value"),
    [("method", "ken-burns"), ("attempt_id", 999), ("attempt_no", 3), ("reserved_brl", 0.0)],
)
def test_token_public_fields_are_read_only(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, field: str, value: object
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn, "veo-3.1-fast"), method="veo-3.1-fast", units=32.0)
    assert token.reserved_brl == pytest.approx(16.0)
    with pytest.raises(AttributeError):
        setattr(token, field, value)
    assert getattr(token, field) != value
    assert repo.spent_brl(conn) == pytest.approx(16.0)


def test_token_tampered_via_dict_is_refused_and_cost_never_drops(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    """Reprodução da revisão: reserva veo de R$ 16, troca o método para grátis e liquida com 0."""
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn, "veo-3.1-fast"), method="veo-3.1-fast", units=32.0)
    private = {k: v for k, v in vars(token).items() if v == "veo-3.1-fast"}
    assert private, "o token deve guardar o método em algum atributo"
    for key in private:
        vars(token)[key] = "ken-burns"
    with pytest.raises(errors.TokenInvalid, match="adulterado"):
        token.consume("ken-burns")
    with pytest.raises(errors.TokenInvalid, match="adulterado"):
        settle_ok(conn, token, units=0.0, consume=False)
    with pytest.raises(errors.TokenInvalid, match="adulterado"):
        settle_error(conn, token)
    assert repo.spent_brl(conn) == pytest.approx(16.0)


@pytest.mark.parametrize("field_value", [("attempt_no", 2), ("reserved_brl", 0.5), ("attempt_id", 2)])
def test_token_tampered_numeric_fields_are_refused(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, field_value: tuple[str, object]
) -> None:
    authorize(100.0)
    run_id = make_run(conn)
    token = reserve(conn, prices, run_id, scene=1, units=2.0)
    reserve(conn, prices, run_id, scene=2, units=2.0)  # attempt_id 2 existe, pending, outra cena
    field, value = field_value
    keys = [k for k, v in vars(token).items() if k.endswith(field) and k != field]
    assert keys
    for key in keys:
        vars(token)[key] = value
    with pytest.raises(errors.TokenInvalid, match="adulterado"):
        token.consume("fake")
    assert repo.spent_brl(conn) == pytest.approx(4.0)


def test_settle_ok_requires_consumed_token(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    with pytest.raises(errors.TokenInvalid, match="consumido"):
        settle_ok(conn, token, consume=False)
    assert repo.get_attempt(conn, token.attempt_id).status == "pending"  # type: ignore[union-attr]
    token.consume("fake")
    assert settle_ok(conn, token, consume=False).status == "ok"


@pytest.mark.parametrize("billed", [Decimal(5), np.int64(5), np.float32(5.0), 5])
def test_settle_error_accepts_numeric_types(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, billed: object
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=1.0)  # reserva R$ 1
    row = settle_error(conn, token, billed=billed)  # type: ignore[arg-type]
    assert row.cost_brl == pytest.approx(5.0)
    assert row.units == pytest.approx(5.0)


@pytest.mark.parametrize("billed", [math.inf, -1.0, math.nan, "abc", None, Decimal("NaN")])
def test_settle_error_invalid_billed_falls_back_to_reserve_and_is_logged(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, billed: object
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=1.0)
    row = settle_error(conn, token, billed=billed)  # type: ignore[arg-type]
    assert row.cost_brl == pytest.approx(1.0)
    assert row.error is not None and "falhou" in row.error and repr(billed) in row.error


def test_reserved_brl_cannot_be_updated_in_sql(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE attempts SET reserved_brl = 0, cost_brl = 0 WHERE id = ?", (token.attempt_id,))
    row = repo.get_attempt(conn, token.attempt_id)
    assert row is not None and row.reserved_brl == 2.0 and row.cost_brl == 2.0


@pytest.mark.parametrize("scene", [True, 1.0, "1", None])
def test_reserve_requires_int_scene_index(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, scene: object
) -> None:
    authorize(100.0)
    with pytest.raises(errors.InputError):
        reserve(conn, prices, make_run(conn), scene=scene)  # type: ignore[arg-type]
    assert repo.list_attempts(conn) == []


def test_reserve_non_finite_cost_is_estimate_invalid(conn: sqlite3.Connection, authorize: Authorize) -> None:
    authorize(1000.0)
    table = PriceTable(5.0, {"fake": MethodPrice("video_seconds", 10.0, "BRL", "teste")})
    with pytest.raises(errors.EstimateInvalid):
        reserve(conn, table, make_run(conn), units=1e308)  # 1e308 * 10 = inf
    assert repo.list_attempts(conn) == []


def test_settle_with_another_connection_is_refused(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, tmp_path: Path
) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    token.consume("fake")
    other = repo.connect(tmp_path / "verion.db")
    try:
        with pytest.raises(errors.TokenInvalid):
            settle_ok(other, token)
        with pytest.raises(errors.TokenInvalid):
            settle_error(other, token)
    finally:
        other.close()
    assert repo.get_attempt(conn, token.attempt_id).status == "pending"  # type: ignore[union-attr]


@pytest.mark.parametrize("field", ["_prices", "_conn"])
def test_token_tampered_prices_or_connection_are_refused(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize, tmp_path: Path, field: str
) -> None:
    """Trocar a tabela de preços (ou a conexão) do token não pode baratear nem desviar a liquidação."""
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn, "veo-3.1-fast"), method="veo-3.1-fast", units=32.0)
    cheap = PriceTable(5.0, {"veo-3.1-fast": MethodPrice("video_seconds", 1e-6, "USD", "adulterada")})
    other = repo.connect(tmp_path / "outro.db")
    try:
        vars(token)[field] = cheap if field == "_prices" else other
        for call in (
            lambda: token.consume("veo-3.1-fast"),
            lambda: settle_ok(conn, token, units=1.0, consume=False),
            lambda: settle_error(conn, token),
        ):
            with pytest.raises(errors.TokenInvalid, match="adulterado"):
                call()
    finally:
        other.close()
    assert repo.get_attempt(conn, token.attempt_id).status == "pending"  # type: ignore[union-attr]
    assert repo.spent_brl(conn) == pytest.approx(16.0)


def test_token_cannot_be_deleted_attribute(conn: sqlite3.Connection, prices: PriceTable, authorize: Authorize) -> None:
    authorize(100.0)
    token = reserve(conn, prices, make_run(conn), units=2.0)
    with pytest.raises(AttributeError):
        del token._method  # type: ignore[attr-defined]  # noqa: SLF001
    with pytest.raises(AttributeError):
        token.brand_new_attribute = 1  # type: ignore[attr-defined]
