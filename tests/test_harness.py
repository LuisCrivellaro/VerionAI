"""Testes de ``verion.harness.run_scenes`` (spec 3.5, T3). Sem rede, sem chamada paga.

Usam o adaptador ``fake`` (pago fictício, R$ 1,00 por ``video_second``) e o ``fake_clock`` de
``tests/conftest.py``. Os adaptadores de teste abaixo estendem o ``FakeAdapter`` só aqui (o teste AST
da T1 varre ``src/verion/**``, não ``tests/``).
"""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import pytest

from verion import budget, harness
from verion.config import PriceTable
from verion.errors import (
    BudgetExceeded,
    EstimateInvalid,
    InputError,
    PaidCallBlocked,
    ProviderError,
)
from verion.harness import RunSummary, run_scenes
from verion.ledger import repo
from verion.providers.base import CostEstimate, Scene, SceneRequest, SceneResult
from verion.providers.fake import FakeAdapter, FakeClient
from verion.script import Script


class FakeClock(Protocol):
    """Forma do ``fake_clock`` de tests/conftest.py (T1; só consumido aqui)."""

    def __call__(self) -> float: ...

    def advance(self, seconds: float) -> None: ...


DURATIONS = (3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 3.0, 4.0, 5.0, 6.0)


# --- fixtures e dublês ----------------------------------------------------------------------


def make_script(tmp_path: Path, durations: tuple[float, ...] = (5.0,) * 12) -> Script:
    photos = tmp_path / "fotos"
    photos.mkdir(exist_ok=True)
    scenes = []
    for index, duration in enumerate(durations, start=1):
        photo = photos / f"{index:02d}.jpg"
        photo.write_bytes(b"foto-ficticia")
        scenes.append(
            Scene(
                index=index,
                photo=photo,
                room=f"comodo {index}",
                camera="push-in",
                duration_s=duration,
                prompt="Push-in lento. Não adicionar objetos.",
            )
        )
    return Script(
        property_id="casa-teste",
        path=tmp_path / "roteiro.toml",
        sha256="a" * 64,
        scenes=tuple(scenes),
    )


@pytest.fixture(autouse=True)
def _no_fake_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERION_FAKE_MODE", raising=False)


@pytest.fixture
def script(tmp_path: Path) -> Script:
    return make_script(tmp_path)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def run(
    conn: sqlite3.Connection, script: Script, prices: PriceTable, data_dir: Path, fake_clock: FakeClock
) -> Callable[..., RunSummary]:
    """``run_scenes`` com conexão, roteiro, preços, pasta e relógio de teste já ligados."""

    def _run(gen: object, **kwargs: object) -> RunSummary:
        kwargs.setdefault("allow_paid", True)
        return run_scenes(
            conn, script, gen, prices, data_dir=data_dir, clock=fake_clock, **kwargs  # type: ignore[arg-type]
        )

    return _run


def fake_adapter(prices: PriceTable, *modes: str, latency_s: float = 0.0, clock: FakeClock | None = None) -> FakeAdapter:
    sleep = clock.advance if clock is not None else None
    return FakeAdapter(prices, FakeClient(modes or ("ok",), latency_s=latency_s, sleep=sleep))


class ClockedAdapter(FakeAdapter):
    """Avança o relógio em ``index * 0.25`` s dentro de ``generate``: o tempo de cada cena é distinto."""

    def __init__(self, prices: PriceTable, clock: FakeClock) -> None:
        super().__init__(prices, FakeClient(("ok",)))
        self._clock = clock

    def generate(self, req: SceneRequest, token: budget.SpendToken) -> SceneResult:
        self._clock.advance(req.scene.index * 0.25)
        return super().generate(req, token)


class ProbeAdapter(FakeAdapter):
    """Registra o que o ledger mostra DENTRO de ``generate`` (a reserva precisa existir antes)."""

    def __init__(self, prices: PriceTable, conn: sqlite3.Connection) -> None:
        super().__init__(prices, FakeClient(("ok",)))
        self._conn = conn
        self.seen: list[tuple[int, str, float, float, int]] = []

    def generate(self, req: SceneRequest, token: budget.SpendToken) -> SceneResult:
        row = repo.get_attempt(self._conn, token.attempt_id)
        assert row is not None
        self.seen.append((req.scene.index, row.status, row.cost_brl, repo.spent_brl(self._conn), req.attempt_no))
        assert req.attempt_no == token.attempt_no
        return super().generate(req, token)


class RaisingAdapter(FakeAdapter):
    """``generate`` levanta ``exc`` (opcionalmente sem consumir o token antes)."""

    def __init__(self, prices: PriceTable, exc: BaseException, *, consume: bool = True) -> None:
        super().__init__(prices, FakeClient(("ok",)))
        self._exc = exc
        self._consume = consume

    def generate(self, req: SceneRequest, token: budget.SpendToken) -> SceneResult:
        if self._consume:
            token.consume(self.method)
        raise self._exc


class BadResultAdapter(FakeAdapter):
    """Gera normalmente, mas devolve ``units``/``unit_kind`` alterados (``settle_ok`` deve recusar)."""

    def __init__(self, prices: PriceTable, **changes: object) -> None:
        super().__init__(prices, FakeClient(("ok",)))
        self._changes = changes

    def generate(self, req: SceneRequest, token: budget.SpendToken) -> SceneResult:
        return dataclasses.replace(super().generate(req, token), **self._changes)  # type: ignore[arg-type]


class FreeAdapter:
    """Método grátis fictício (``ken-burns``): sem custo, sem ``--allow-paid``."""

    method = "ken-burns"
    provider = "local"
    model = "stub-1"

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=0.0, unit_kind="none")

    def generate(self, req: SceneRequest, token: budget.SpendToken) -> SceneResult:
        token.consume(self.method)
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        req.out_path.write_bytes(b"clipe-local")
        return SceneResult(
            clip_path=req.out_path,
            duration_s=req.scene.duration_s,
            width=1920,
            height=1080,
            fps=30.0,
            units=0.0,
            unit_kind="none",
            cost_is_estimate=False,
        )


def only_run_id(conn: sqlite3.Connection) -> int:
    (row,) = repo.list_runs(conn)
    return row.id


def approve(conn: sqlite3.Connection, attempt_id: int) -> None:
    repo.record_qc(conn, attempt_id, verdict="aprovada", reviewer="humano", defects=[])


# --- caminho feliz --------------------------------------------------------------------------


def test_twelve_scenes_all_ok_with_exact_wall_time(
    conn: sqlite3.Connection,
    script: Script,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    data_dir: Path,
    fake_clock: FakeClock,
) -> None:
    authorize(1000.0)
    script = make_script(script.path.parent, DURATIONS)
    gen = fake_adapter(prices, "ok", latency_s=2.5, clock=fake_clock)

    summary = run_scenes(
        conn, script, gen, prices, allow_paid=True, data_dir=data_dir, clock=fake_clock
    )

    run_id = only_run_id(conn)
    attempts = repo.list_attempts(conn, run_id=run_id)
    assert len(attempts) == 12
    assert [a.scene_index for a in attempts] == list(range(1, 13))
    assert all(a.status == "ok" and a.attempt_no == 1 for a in attempts)
    assert all(a.wall_time_s == 2.5 for a in attempts)  # igualdade exata, sem tolerância
    assert [a.cost_brl for a in attempts] == list(DURATIONS)  # R$ 1,00 por segundo
    assert [a.reserved_brl for a in attempts] == list(DURATIONS)
    assert [a.target_s for a in attempts] == list(DURATIONS)
    assert all(a.provider_job_id and not a.cost_is_estimate for a in attempts)

    assert summary == RunSummary(
        run_id=run_id,
        attempted=12,
        ok=12,
        errors=0,
        cost_brl=sum(DURATIONS),
        skipped={},
        stopped_by=None,
    )
    assert repo.spent_brl(conn) == sum(DURATIONS)
    assert repo.get_run(conn, run_id).finished_at is not None


def test_each_scene_is_timed_separately_by_the_injected_clock(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    fake_clock: FakeClock,
) -> None:
    authorize(1000.0)
    fake_clock.advance(100.0)  # o relógio não precisa começar em zero

    run(ClockedAdapter(prices, fake_clock))

    attempts = repo.list_attempts(conn)
    assert [a.wall_time_s for a in attempts] == [i * 0.25 for i in range(1, 13)]


def test_order_is_reserve_then_generate_then_settle(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    gen = ProbeAdapter(prices, conn)

    run(gen)

    # Dentro de generate a linha já está pending, com a reserva somada ao gasto.
    assert [s[:3] for s in gen.seen] == [(i, "pending", 5.0) for i in range(1, 13)]
    assert [s[3] for s in gen.seen] == [5.0 * i for i in range(1, 13)]
    assert [s[4] for s in gen.seen] == [1] * 12
    assert {a.status for a in repo.list_attempts(conn)} == {"ok"}


def test_clips_go_to_the_run_method_folder(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    data_dir: Path,
) -> None:
    authorize(1000.0)

    run(fake_adapter(prices))

    run_id = only_run_id(conn)
    first, *_ = repo.list_attempts(conn)
    expected = data_dir / "runs" / str(run_id) / "fake" / "s01_t1.mp4"
    assert first.clip_path == str(expected)
    assert expected.is_file()
    assert (data_dir / "runs" / str(run_id) / "fake" / "s12_t1.mp4").is_file()


def test_run_row_records_script_and_method(
    conn: sqlite3.Connection,
    script: Script,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    run(fake_adapter(prices))

    row = repo.get_run(conn, only_run_id(conn))
    assert (row.property_id, row.method) == ("casa-teste", "fake")
    assert (row.script_path, row.script_sha256) == (str(script.path), script.sha256)


def test_default_clock_is_time_monotonic() -> None:
    assert inspect.signature(run_scenes).parameters["clock"].default is time.monotonic


def test_free_method_runs_without_allow_paid_or_authorization(
    conn: sqlite3.Connection, run: Callable[..., RunSummary]
) -> None:
    summary = run(FreeAdapter(), allow_paid=False)

    assert (summary.attempted, summary.ok, summary.cost_brl) == (12, 12, 0.0)
    assert repo.get_run(conn, summary.run_id).method == "ken-burns"
    assert {a.cost_brl for a in repo.list_attempts(conn)} == {0.0}
    assert repo.spent_brl(conn) == 0.0


# --- seleção de cenas -----------------------------------------------------------------------


def test_scenes_subset_runs_in_script_order(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    summary = run(fake_adapter(prices), scenes=[7, 3])

    assert [a.scene_index for a in repo.list_attempts(conn)] == [3, 7]
    assert (summary.attempted, summary.ok) == (2, 2)


@pytest.mark.parametrize("scenes", [[], [13], [0], [1, 1], [1, 99]])
def test_invalid_scene_selection_is_input_error_and_creates_no_run(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    scenes: list[int],
) -> None:
    authorize(1000.0)

    with pytest.raises(InputError):
        run(fake_adapter(prices), scenes=scenes)

    assert repo.list_runs(conn) == []
    assert repo.list_attempts(conn) == []


# --- bloqueio de gasto ----------------------------------------------------------------------


def test_budget_exceeded_on_first_scene_creates_zero_attempts(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(4.99)  # a cena 1 custa R$ 5,00

    with pytest.raises(BudgetExceeded):
        run(fake_adapter(prices))

    assert repo.list_attempts(conn) == []
    assert repo.spent_brl(conn) == 0.0
    assert repo.get_run(conn, only_run_id(conn)).finished_at is not None


def test_budget_exceeded_mid_run_keeps_earlier_scenes_and_adds_no_row(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(8.0)  # só a cena 1 cabe

    with pytest.raises(BudgetExceeded):
        run(fake_adapter(prices))

    attempts = repo.list_attempts(conn)
    assert [(a.scene_index, a.status) for a in attempts] == [(1, "ok")]
    assert repo.spent_brl(conn) == 5.0


def test_paid_method_without_allow_paid_is_blocked_with_zero_attempts(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(PaidCallBlocked):
        run(fake_adapter(prices), allow_paid=False)

    assert repo.list_attempts(conn) == []
    assert repo.get_run(conn, only_run_id(conn)).finished_at is not None


# --- erro faturado e custo mínimo = reserva -------------------------------------------------


def test_billed_error_settles_with_max_of_billed_and_reserve_and_ends_the_run(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    gen = fake_adapter(prices, "ok", "billed_error")  # cena 1 ok, cena 2 falha faturada

    with pytest.raises(ProviderError) as info:
        run(gen)

    assert info.value.billed_units == 5.0 and not info.value.transient
    attempts = repo.list_attempts(conn)
    assert [(a.scene_index, a.status) for a in attempts] == [(1, "ok"), (2, "error")]  # run encerrado na cena 2
    failed = attempts[1]
    assert failed.reserved_brl == 5.0
    assert failed.cost_brl == max(5.0, failed.reserved_brl) == 5.0
    assert failed.provider_job_id == "fake-job-0002"
    assert failed.error is not None and "ProviderError" in failed.error and "faturada" in failed.error
    assert failed.wall_time_s == 0.0
    assert repo.spent_brl(conn) == 10.0
    assert repo.get_run(conn, only_run_id(conn)).finished_at is not None


@pytest.mark.parametrize(
    ("billed", "expected_cost"),
    [(0.0, 5.0), (1.0, 5.0), (5.0, 5.0), (8.0, 8.0)],
)
def test_error_cost_is_the_larger_of_billed_and_reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    billed: float,
    expected_cost: float,
) -> None:
    authorize(1000.0)
    exc = ProviderError("falha do provedor", transient=False, billed_units=billed, provider_job_id="job-x")

    with pytest.raises(ProviderError):
        run(RaisingAdapter(prices, exc))

    (attempt,) = repo.list_attempts(conn)
    assert attempt.status == "error"
    assert attempt.cost_brl == expected_cost
    assert attempt.reserved_brl == 5.0
    assert attempt.provider_job_id == "job-x"
    assert repo.spent_brl(conn) == expected_cost


def test_transient_failure_still_keeps_the_reserve_and_ends_the_run(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(ProviderError) as info:
        run(fake_adapter(prices, "transient"))

    assert info.value.transient
    (attempt,) = repo.list_attempts(conn)  # a cena 2 nem foi tentada
    assert (attempt.status, attempt.cost_brl) == ("error", 5.0)


def test_one_adapter_per_run_keeps_the_fake_mode_sequence(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sequência de VERION_FAKE_MODE vale por instância: o harness não recria o adaptador por cena."""
    authorize(1000.0)
    monkeypatch.setenv("VERION_FAKE_MODE", "ok,ok,billed_error")

    with pytest.raises(ProviderError):
        run(FakeAdapter(prices))

    assert [(a.scene_index, a.status) for a in repo.list_attempts(conn)] == [
        (1, "ok"),
        (2, "ok"),
        (3, "error"),
    ]


# --- KeyboardInterrupt e qualquer outra exceção ---------------------------------------------


def test_keyboard_interrupt_in_generate_settles_error_with_reserve_and_reraises(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    interrupt = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt) as info:
        run(RaisingAdapter(prices, interrupt))

    assert info.value is interrupt
    (attempt,) = repo.list_attempts(conn)
    assert attempt.status == "error"
    assert attempt.cost_brl == attempt.reserved_brl == 5.0
    assert attempt.error is not None and "KeyboardInterrupt" in attempt.error
    assert repo.spent_brl(conn) == 5.0
    assert repo.get_run(conn, only_run_id(conn)).finished_at is not None


@pytest.mark.parametrize(
    "exc",
    [SystemExit(3), RuntimeError("bug no adaptador"), OSError("disco cheio"), ValueError("x")],
    ids=["SystemExit", "RuntimeError", "OSError", "ValueError"],
)
def test_any_exception_in_generate_settles_error_with_reserve_and_reraises(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    exc: BaseException,
) -> None:
    authorize(1000.0)

    with pytest.raises(type(exc)) as info:
        run(RaisingAdapter(prices, exc))

    assert info.value is exc
    (attempt,) = repo.list_attempts(conn)
    assert (attempt.status, attempt.cost_brl) == ("error", 5.0)
    assert attempt.error is not None and type(exc).__name__ in attempt.error
    assert repo.get_run(conn, only_run_id(conn)).finished_at is not None


def test_exception_before_token_consume_still_settles_the_reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(RuntimeError):
        run(RaisingAdapter(prices, RuntimeError("falhou antes do consume"), consume=False))

    (attempt,) = repo.list_attempts(conn)
    assert (attempt.status, attempt.cost_brl) == ("error", 5.0)


def test_error_message_is_capped(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(RuntimeError):
        run(RaisingAdapter(prices, RuntimeError("x" * 10_000)))

    (attempt,) = repo.list_attempts(conn)
    assert attempt.error is not None and len(attempt.error) <= 600


def test_failed_settle_error_keeps_original_exception_and_the_pending_reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize(1000.0)

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(budget, "settle_error", broken)
    original = ProviderError("falha do provedor", transient=False)

    with pytest.raises(ProviderError) as info:
        run(RaisingAdapter(prices, original))

    assert info.value is original  # a falha de liquidação não mascara a causa
    assert any("pending" in note for note in info.value.__notes__)
    (attempt,) = repo.list_attempts(conn)
    assert attempt.status == "pending"  # a reserva continua somada ao gasto
    assert repo.spent_brl(conn) == 5.0
    assert repo.get_run(conn, only_run_id(conn)).finished_at is not None


# --- exceção em settle_ok -------------------------------------------------------------------


def test_settle_ok_failure_falls_back_to_settle_error_with_reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(EstimateInvalid):  # units=0 num método pago: settle_ok recusa
        run(BadResultAdapter(prices, units=0.0))

    (attempt,) = repo.list_attempts(conn)
    assert attempt.status == "error"
    assert attempt.cost_brl == attempt.reserved_brl == 5.0
    assert attempt.clip_path is None
    assert attempt.error is not None and "EstimateInvalid" in attempt.error
    assert repo.spent_brl(conn) == 5.0


def test_unit_kind_mismatch_in_result_is_an_error_with_reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(EstimateInvalid, match="unit_kind"):
        run(BadResultAdapter(prices, unit_kind="gpu_seconds"))

    (attempt,) = repo.list_attempts(conn)
    assert (attempt.status, attempt.cost_brl) == ("error", 5.0)


# --- regras de cena: skipped ----------------------------------------------------------------


def test_scene_awaiting_qc_is_skipped_and_the_run_continues(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    data_dir: Path,
) -> None:
    authorize(1000.0)
    first = run(fake_adapter(prices), scenes=[1, 2])
    assert (first.attempted, first.skipped) == (2, {})

    second = run(fake_adapter(prices), run_id=first.run_id)  # 1 e 2 aguardam QC

    assert set(second.skipped) == {1, 2}
    assert all("QC" in reason for reason in second.skipped.values())
    assert (second.attempted, second.ok, second.errors) == (10, 10, 0)
    assert second.run_id == first.run_id
    assert [a.scene_index for a in repo.list_attempts(conn)] == list(range(1, 13))
    assert len(repo.list_runs(conn)) == 1  # reaproveitou o run


def test_all_scenes_skipped_attempts_nothing_and_does_not_raise(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    first = run(fake_adapter(prices), scenes=[1, 2])

    again = run(fake_adapter(prices), run_id=first.run_id, scenes=[1, 2])

    assert (again.attempted, again.ok, again.errors, again.cost_brl) == (0, 0, 0, 0.0)
    assert set(again.skipped) == {1, 2}
    assert len(repo.list_attempts(conn)) == 2


def test_approved_and_pending_scenes_are_skipped_too(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    first = run(fake_adapter(prices), scenes=[1])
    approve(conn, repo.list_attempts(conn)[0].id)
    orphan = budget.reserve(  # reserva órfã na cena 2 (processo que morreu antes de liquidar)
        conn,
        prices,
        run_id=first.run_id,
        scene_index=2,
        target_s=5.0,
        method="fake",
        provider="fake",
        model="fake-1",
        units=5.0,
        unit_kind="video_seconds",
        allow_paid=True,
    )

    summary = run(fake_adapter(prices), run_id=first.run_id, scenes=[1, 2, 3])

    assert set(summary.skipped) == {1, 2}
    assert "aprovada" in summary.skipped[1]
    assert "pendente" in summary.skipped[2] or "pending" in summary.skipped[2]
    assert (summary.attempted, summary.ok) == (1, 1)
    assert repo.get_attempt(conn, orphan.attempt_id).status == "pending"  # type: ignore[union-attr]


def test_redo_verdict_reopens_the_scene_with_next_attempt_number(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
    data_dir: Path,
) -> None:
    authorize(1000.0)
    first = run(fake_adapter(prices), scenes=[1])
    attempt = repo.list_attempts(conn)[0]
    repo.record_qc(
        conn, attempt.id, verdict="refazer", reviewer="humano", defects=[repo.Defect(2, 1.5, "borda deformada")]
    )

    second = run(fake_adapter(prices), run_id=first.run_id, scenes=[1])

    assert (second.attempted, second.ok, second.skipped) == (1, 1, {})
    redo = repo.list_attempts(conn)[1]
    assert redo.attempt_no == 2
    assert redo.clip_path == str(data_dir / "runs" / str(first.run_id) / "fake" / "s01_t2.mp4")


# --- método e roteiro do run são respeitados ------------------------------------------------


def test_run_id_with_another_method_is_input_error_and_touches_nothing(
    conn: sqlite3.Connection,
    script: Script,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    other = repo.create_run(
        conn, property_id=script.property_id, method="ken-burns", script_path=str(script.path), script_sha256=script.sha256
    )

    with pytest.raises(InputError, match="ken-burns"):
        run(fake_adapter(prices), run_id=other)

    assert repo.list_attempts(conn) == []
    assert repo.spent_brl(conn) == 0.0
    assert repo.get_run(conn, other).finished_at is None  # entrada inválida não encerra o run alheio


def test_run_id_of_a_run_over_a_different_script_is_input_error(
    conn: sqlite3.Connection,
    script: Script,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    other = repo.create_run(
        conn, property_id=script.property_id, method="fake", script_path=str(script.path), script_sha256="b" * 64
    )

    with pytest.raises(InputError, match="roteiro"):
        run(fake_adapter(prices), run_id=other)

    assert repo.list_attempts(conn) == []

    other_property = repo.create_run(
        conn, property_id="outra-casa", method="fake", script_path=str(script.path), script_sha256=script.sha256
    )
    with pytest.raises(InputError, match="property_id"):
        run(fake_adapter(prices), run_id=other_property)


def test_unknown_run_id_is_input_error(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)

    with pytest.raises(InputError, match="run"):
        run(fake_adapter(prices), run_id=999)

    assert repo.list_attempts(conn) == []


def test_run_method_comes_from_the_adapter(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    """Um run de ``fake`` não aceita ser continuado por outro método, nem o contrário."""
    authorize(1000.0)
    first = run(fake_adapter(prices), scenes=[1])

    with pytest.raises(InputError, match="fake"):
        run(FreeAdapter(), run_id=first.run_id, allow_paid=False)

    assert len(repo.list_attempts(conn)) == 1


def test_run_summary_is_immutable_and_skipped_is_read_only(
    conn: sqlite3.Connection,
    prices: PriceTable,
    authorize: Callable[[float], int],
    run: Callable[..., RunSummary],
) -> None:
    authorize(1000.0)
    first = run(fake_adapter(prices), scenes=[1])
    again = run(fake_adapter(prices), run_id=first.run_id, scenes=[1])

    with pytest.raises(dataclasses.FrozenInstanceError):
        again.ok = 5  # type: ignore[misc]
    with pytest.raises(TypeError):
        again.skipped[9] = "x"  # type: ignore[index]


def test_harness_module_has_no_forbidden_spend_entry_points() -> None:
    """O harness só usa a API pública do orçamento (reserve/settle_*), nunca o repo para gastar."""
    source = Path(harness.__file__).read_text(encoding="utf-8")
    for forbidden in ("insert_pending", "finish_attempt", "add_authorization", "_SEAL", "_ISSUED"):
        assert forbidden not in source
