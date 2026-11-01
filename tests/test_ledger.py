"""Testes do ledger SQLite: esquema, repositório, regras de cena e de QC (spec 3.7)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from verion import errors
from verion.ledger import repo
from verion.ledger.repo import Defect

# --- helpers ----------------------------------------------------------------------------------


def make_run(conn: sqlite3.Connection, method: str = "fake") -> int:
    return repo.create_run(
        conn, property_id="casa-1", method=method, script_path="roteiro.toml", script_sha256="0" * 64
    )


def add_pending(
    conn: sqlite3.Connection, run_id: int, scene: int = 1, reserved: float = 1.0, method: str = "fake"
) -> int:
    return repo.insert_pending(
        conn,
        run_id=run_id,
        scene_index=scene,
        attempt_no=repo.next_attempt_no(conn, run_id, scene),
        target_s=5.0,
        method=method,
        provider="fake",
        model="fake-1",
        units=1.0,
        unit_kind="video_seconds",
        reserved_brl=reserved,
    )


def finish_ok(conn: sqlite3.Connection, attempt_id: int, cost: float = 1.0) -> repo.AttemptRow:
    return repo.finish_attempt(
        conn,
        attempt_id,
        status="ok",
        cost_brl=cost,
        cost_is_estimate=False,
        units=1.0,
        clip_path="clip.mp4",
        duration_s=5.0,
        wall_time_s=1.5,
        provider_job_id="job-1",
        error=None,
    )


def finish_error(conn: sqlite3.Connection, attempt_id: int, cost: float = 1.0) -> repo.AttemptRow:
    return repo.finish_attempt(
        conn,
        attempt_id,
        status="error",
        cost_brl=cost,
        cost_is_estimate=True,
        units=1.0,
        clip_path=None,
        duration_s=None,
        wall_time_s=0.5,
        provider_job_id=None,
        error="falhou",
    )


def ok_attempt(conn: sqlite3.Connection, run_id: int, scene: int = 1) -> int:
    attempt_id = add_pending(conn, run_id, scene)
    finish_ok(conn, attempt_id)
    return attempt_id


BLOCK = Defect(tipo=2, segundo=3.0, nota="objeto inventado")
LEVE = Defect(tipo="leve", segundo=1.0, nota="brilho")


# --- connect / esquema ------------------------------------------------------------------------


def test_connect_applies_schema_and_pragmas(tmp_path: Path) -> None:
    conn = repo.connect(tmp_path / "sub" / "verion.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.isolation_level is None
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"runs", "attempts", "qc_reviews", "authorizations"} <= tables
    finally:
        conn.close()


def test_connect_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "verion.db"
    first = repo.connect(path)
    run_id = make_run(first)
    first.close()
    second = repo.connect(path)
    try:
        assert repo.get_run(second, run_id).property_id == "casa-1"
    finally:
        second.close()


def test_connect_schema_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "verion.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 7")
    raw.close()
    with pytest.raises(errors.SchemaMismatch):
        repo.connect(path)


def test_drop_all_then_reconnect_recreates_schema(tmp_path: Path) -> None:
    path = tmp_path / "verion.db"
    conn = repo.connect(path)
    make_run(conn)
    repo.drop_all(conn)
    conn.close()
    conn = repo.connect(path)
    try:
        assert repo.list_runs(conn) == []
    finally:
        conn.close()


# --- runs -------------------------------------------------------------------------------------


def test_run_lifecycle(conn: sqlite3.Connection) -> None:
    run_id = repo.create_run(
        conn, property_id="casa-1", method="ken-burns", script_path="r.toml", script_sha256="ab", notes="n"
    )
    row = repo.get_run(conn, run_id)
    assert (row.property_id, row.method, row.script_path, row.script_sha256, row.notes) == (
        "casa-1",
        "ken-burns",
        "r.toml",
        "ab",
        "n",
    )
    assert row.finished_at is None
    assert row.started_at.endswith("Z")
    repo.finish_run(conn, run_id)
    finished = repo.get_run(conn, run_id).finished_at
    assert finished is not None
    repo.finish_run(conn, run_id)  # idempotente: não altera o primeiro instante
    assert repo.get_run(conn, run_id).finished_at == finished


def test_get_run_missing(conn: sqlite3.Connection) -> None:
    with pytest.raises(errors.InputError):
        repo.get_run(conn, 999)


def test_list_runs_ordered_by_id(conn: sqlite3.Connection) -> None:
    ids = [make_run(conn), make_run(conn, "ken-burns"), make_run(conn)]
    assert [r.id for r in repo.list_runs(conn)] == ids


# --- tentativas -------------------------------------------------------------------------------


def test_attempt_lifecycle_ok(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    attempt_id = add_pending(conn, run_id, scene=3, reserved=2.5)
    pending = repo.get_attempt(conn, attempt_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.cost_brl == pending.reserved_brl == 2.5
    assert (pending.scene_index, pending.attempt_no, pending.target_s) == (3, 1, 5.0)
    assert pending.finished_at is None and pending.clip_path is None

    repo.set_job_id(conn, attempt_id, "job-9")
    row = finish_ok(conn, attempt_id, cost=2.0)
    assert row.status == "ok"
    assert row.cost_brl == 2.0 and row.reserved_brl == 2.5
    assert row.cost_is_estimate is False
    assert (row.clip_path, row.duration_s, row.wall_time_s) == ("clip.mp4", 5.0, 1.5)
    assert row.provider_job_id == "job-1"
    assert row.finished_at is not None


def test_finish_attempt_keeps_job_id_when_none_given(conn: sqlite3.Connection) -> None:
    attempt_id = add_pending(conn, make_run(conn))
    repo.set_job_id(conn, attempt_id, "job-9")
    row = repo.finish_attempt(
        conn,
        attempt_id,
        status="ok",
        cost_brl=1.0,
        cost_is_estimate=False,
        units=1.0,
        clip_path="c.mp4",
        duration_s=5.0,
        wall_time_s=1.0,
        provider_job_id=None,
        error=None,
    )
    assert row.provider_job_id == "job-9"


def test_finish_attempt_only_from_pending(conn: sqlite3.Connection) -> None:
    attempt_id = add_pending(conn, make_run(conn))
    finish_ok(conn, attempt_id)
    with pytest.raises(errors.InputError):
        finish_ok(conn, attempt_id)
    with pytest.raises(errors.InputError):
        finish_error(conn, 999)


def test_finish_attempt_error_needs_message(conn: sqlite3.Connection) -> None:
    attempt_id = add_pending(conn, make_run(conn))
    with pytest.raises(errors.InputError):
        repo.finish_attempt(
            conn,
            attempt_id,
            status="error",
            cost_brl=1.0,
            cost_is_estimate=True,
            units=1.0,
            clip_path=None,
            duration_s=None,
            wall_time_s=0.1,
            provider_job_id=None,
            error=None,
        )


def test_finish_attempt_error_cannot_cost_less_than_reserve(conn: sqlite3.Connection) -> None:
    attempt_id = add_pending(conn, make_run(conn), reserved=5.0)
    with pytest.raises(errors.InputError):
        finish_error(conn, attempt_id, cost=1.0)
    assert repo.get_attempt(conn, attempt_id).status == "pending"  # type: ignore[union-attr]


def test_get_attempt_missing(conn: sqlite3.Connection) -> None:
    assert repo.get_attempt(conn, 42) is None


def test_list_attempts_filters(conn: sqlite3.Connection) -> None:
    run_a, run_b = make_run(conn, "fake"), make_run(conn, "ken-burns")
    a1 = ok_attempt(conn, run_a, 1)
    a2 = ok_attempt(conn, run_a, 2)
    b1 = add_pending(conn, run_b, 1, reserved=0.0, method="ken-burns")
    assert [a.id for a in repo.list_attempts(conn)] == [a1, a2, b1]
    assert [a.id for a in repo.list_attempts(conn, run_id=run_a)] == [a1, a2]
    assert [a.id for a in repo.list_attempts(conn, method="ken-burns")] == [b1]
    assert repo.list_attempts(conn, run_id=run_b, method="fake") == []


def test_schema_checks_reject_bad_rows(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    base = dict(
        run_id=run_id,
        scene_index=1,
        attempt_no=1,
        target_s=5.0,
        method="fake",
        provider="fake",
        model="m",
        units=1.0,
        unit_kind="video_seconds",
        reserved_brl=1.0,
    )
    for override in ({"scene_index": 13}, {"scene_index": 0}, {"attempt_no": 4}, {"reserved_brl": -1.0}):
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_pending(conn, **{**base, **override})  # type: ignore[arg-type]
    repo.insert_pending(conn, **base)  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(run, cena, tentativa)
        repo.insert_pending(conn, **base)  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):  # FK
        repo.insert_pending(conn, **{**base, "run_id": 999})  # type: ignore[arg-type]


def test_db_refuses_error_below_reserve_and_editing_settled_rows(conn: sqlite3.Connection) -> None:
    attempt_id = add_pending(conn, make_run(conn), reserved=5.0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE attempts SET status='error', cost_brl=1.0, error='x' WHERE id=?", (attempt_id,))
    finish_error(conn, attempt_id, cost=5.0)
    with pytest.raises(sqlite3.IntegrityError):  # gasto de tentativa liquidada é imutável
        conn.execute("UPDATE attempts SET cost_brl=0.0 WHERE id=?", (attempt_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM attempts WHERE id=?", (attempt_id,))


# --- autorizações e gasto ---------------------------------------------------------------------


def test_authorizations_and_totals(conn: sqlite3.Connection) -> None:
    assert repo.authorized_brl(conn) == 0.0
    assert repo.spent_brl(conn) == 0.0
    first = repo.add_authorization(conn, 100.0, "STATE.md 2026-11-01")
    second = repo.add_authorization(conn, 250.5, "segunda")
    assert repo.authorized_brl(conn) == pytest.approx(350.5)
    listed = repo.list_authorizations(conn)
    assert [(i, a, r) for i, a, r, _ in listed] == [(first, 100.0, "STATE.md 2026-11-01"), (second, 250.5, "segunda")]
    assert all(created.endswith("Z") for *_, created in listed)


@pytest.mark.parametrize("amount", [0.0, -5.0, float("nan"), float("inf")])
def test_add_authorization_rejects_invalid_amount(conn: sqlite3.Connection, amount: float) -> None:
    with pytest.raises(errors.InputError):
        repo.add_authorization(conn, amount, "ref")
    assert repo.list_authorizations(conn) == []


def test_add_authorization_needs_reference(conn: sqlite3.Connection) -> None:
    with pytest.raises(errors.InputError):
        repo.add_authorization(conn, 10.0, "  ")


def test_spent_counts_pending_by_reserve(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    add_pending(conn, run_id, 1, reserved=4.0)  # pending
    ok = add_pending(conn, run_id, 2, reserved=3.0)
    finish_ok(conn, ok, cost=2.0)  # liquidada a custo real
    assert repo.spent_brl(conn) == pytest.approx(6.0)


# --- settle_pending ---------------------------------------------------------------------------


def test_settle_pending_becomes_error_and_never_reduces_cost(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    attempt_id = add_pending(conn, run_id, reserved=5.0)
    with pytest.raises(errors.InputError):
        repo.settle_pending(conn, attempt_id, cost_brl=1.0, note="menor que a reserva")
    assert repo.spent_brl(conn) == pytest.approx(5.0)  # nada mudou
    assert repo.get_attempt(conn, attempt_id).status == "pending"  # type: ignore[union-attr]

    row = repo.settle_pending(conn, attempt_id, cost_brl=7.0, note="fatura mostrou 7")
    assert row.status == "error"
    assert row.cost_brl == 7.0 and row.reserved_brl == 5.0
    assert row.error is not None and "fatura mostrou 7" in row.error
    assert row.finished_at is not None
    assert repo.spent_brl(conn) == pytest.approx(7.0)

    with pytest.raises(errors.InputError):  # só a partir de pending
        repo.settle_pending(conn, attempt_id, cost_brl=9.0, note="de novo")
    with pytest.raises(errors.InputError):
        repo.settle_pending(conn, 999, cost_brl=9.0, note="inexistente")


def test_settle_pending_accepts_exact_reserve(conn: sqlite3.Connection) -> None:
    attempt_id = add_pending(conn, make_run(conn), reserved=5.0)
    assert repo.settle_pending(conn, attempt_id, cost_brl=5.0, note="n").cost_brl == 5.0


# --- scene_status / next_attempt_no -----------------------------------------------------------


def test_new_scene_is_open(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    assert repo.scene_status(conn, run_id, 1) == ("aberta", None)
    assert repo.next_attempt_no(conn, run_id, 1) == 1


def test_pending_attempt_blocks_next(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    add_pending(conn, run_id)
    assert repo.scene_status(conn, run_id, 1)[0] == "pendente"
    with pytest.raises(errors.PendingAttempt):
        repo.next_attempt_no(conn, run_id, 1)


def test_ok_without_qc_awaits_qc(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    ok_attempt(conn, run_id)
    assert repo.scene_status(conn, run_id, 1)[0] == "aguardando_qc"
    with pytest.raises(errors.AwaitingQC):
        repo.next_attempt_no(conn, run_id, 1)


def test_three_errors_reject_scene_and_fourth_is_closed(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    for expected_no in (1, 2, 3):
        assert repo.scene_status(conn, run_id, 1)[0] == "aberta"
        assert repo.next_attempt_no(conn, run_id, 1) == expected_no
        finish_error(conn, add_pending(conn, run_id))
    status, reason = repo.scene_status(conn, run_id, 1)
    assert status == "rejeitada"
    assert reason is not None and "limite de 3 tentativas" in reason
    with pytest.raises(errors.SceneClosed):
        repo.next_attempt_no(conn, run_id, 1)


def test_errors_plus_refazer_reach_limit(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    first = ok_attempt(conn, run_id)
    repo.record_qc(conn, first, verdict="refazer", reviewer="humano", defects=[BLOCK])
    assert repo.scene_status(conn, run_id, 1)[0] == "aberta"
    finish_error(conn, add_pending(conn, run_id))
    finish_error(conn, add_pending(conn, run_id))
    assert repo.scene_status(conn, run_id, 1)[0] == "rejeitada"
    with pytest.raises(errors.SceneClosed):
        repo.next_attempt_no(conn, run_id, 1)


def test_third_attempt_ok_awaits_qc_before_limit(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    finish_error(conn, add_pending(conn, run_id))
    finish_error(conn, add_pending(conn, run_id))
    ok_attempt(conn, run_id)
    assert repo.scene_status(conn, run_id, 1)[0] == "aguardando_qc"


def test_refazer_reopens_scene(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    repo.record_qc(conn, ok_attempt(conn, run_id), verdict="refazer", reviewer="agente", defects=[BLOCK])
    assert repo.scene_status(conn, run_id, 1) == ("aberta", None)
    assert repo.next_attempt_no(conn, run_id, 1) == 2


def test_approved_scene_is_closed(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    repo.record_qc(conn, ok_attempt(conn, run_id), verdict="aprovada", reviewer="humano", defects=[LEVE])
    assert repo.scene_status(conn, run_id, 1) == ("aprovada", None)
    with pytest.raises(errors.SceneClosed):
        repo.next_attempt_no(conn, run_id, 1)


def test_rejected_scene_reports_defects_and_is_closed(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    repo.record_qc(conn, ok_attempt(conn, run_id), verdict="rejeitada", reviewer="humano", defects=[BLOCK])
    status, reason = repo.scene_status(conn, run_id, 1)
    assert status == "rejeitada"
    assert reason is not None and "objeto inventado" in reason
    with pytest.raises(errors.SceneClosed):
        repo.next_attempt_no(conn, run_id, 1)


def test_scene_status_is_per_run_and_scene(conn: sqlite3.Connection) -> None:
    run_a, run_b = make_run(conn), make_run(conn)
    repo.record_qc(conn, ok_attempt(conn, run_a, 1), verdict="aprovada", reviewer="humano", defects=[])
    assert repo.scene_status(conn, run_a, 2)[0] == "aberta"
    assert repo.scene_status(conn, run_b, 1)[0] == "aberta"


# --- record_qc (regras de veredito) -----------------------------------------------------------


def test_record_qc_roundtrip(conn: sqlite3.Connection) -> None:
    attempt_id = ok_attempt(conn, make_run(conn))
    assert repo.get_qc(conn, attempt_id) is None
    qc_id = repo.record_qc(conn, attempt_id, verdict="aprovada", reviewer="humano", defects=[LEVE])
    qc = repo.get_qc(conn, attempt_id)
    assert qc is not None
    assert (qc.id, qc.attempt_id, qc.verdict, qc.reviewer) == (qc_id, attempt_id, "aprovada", "humano")
    assert qc.defects == (LEVE,)
    assert qc.reviewed_at.endswith("Z")


def test_record_qc_requires_ok_attempt(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    pending = add_pending(conn, run_id, 1)
    errored = add_pending(conn, run_id, 2)
    finish_error(conn, errored)
    for attempt_id in (pending, errored, 999):
        with pytest.raises(errors.InvalidVerdict):
            repo.record_qc(conn, attempt_id, verdict="rejeitada", reviewer="humano", defects=[BLOCK])


@pytest.mark.parametrize("tipo", [0, 8, -1, "grave", True, 2.5, None])
def test_record_qc_rejects_invalid_defect_type(conn: sqlite3.Connection, tipo: object) -> None:
    attempt_id = ok_attempt(conn, make_run(conn))
    with pytest.raises(errors.InvalidVerdict):
        repo.record_qc(
            conn, attempt_id, verdict="rejeitada", reviewer="humano", defects=[Defect(tipo, 1.0, "x")]  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("tipo", range(1, 8))
def test_record_qc_blocking_types_1_to_7(conn: sqlite3.Connection, tipo: int) -> None:
    attempt_id = ok_attempt(conn, make_run(conn))
    repo.record_qc(conn, attempt_id, verdict="rejeitada", reviewer="humano", defects=[Defect(tipo, 1.0, "n")])
    qc = repo.get_qc(conn, attempt_id)
    assert qc is not None and qc.defects[0].tipo == tipo


def test_record_qc_aprovada_rules(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    with pytest.raises(errors.InvalidVerdict):  # defeito bloqueante
        repo.record_qc(conn, ok_attempt(conn, run_id, 1), verdict="aprovada", reviewer="humano", defects=[BLOCK])
    with pytest.raises(errors.InvalidVerdict):  # mais de 1 leve
        repo.record_qc(
            conn, ok_attempt(conn, run_id, 2), verdict="aprovada", reviewer="humano", defects=[LEVE, LEVE]
        )
    with pytest.raises(errors.InvalidVerdict):  # revisor agente nunca aprova
        repo.record_qc(conn, ok_attempt(conn, run_id, 3), verdict="aprovada", reviewer="agente", defects=[])
    # nada foi gravado pelas recusas
    assert all(repo.get_qc(conn, a.id) is None for a in repo.list_attempts(conn))
    repo.record_qc(conn, ok_attempt(conn, run_id, 4), verdict="aprovada", reviewer="humano", defects=[])
    repo.record_qc(conn, ok_attempt(conn, run_id, 5), verdict="aprovada", reviewer="humano", defects=[LEVE])


def test_record_qc_refazer_rules(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    with pytest.raises(errors.InvalidVerdict):  # sem bloqueante
        repo.record_qc(conn, ok_attempt(conn, run_id, 1), verdict="refazer", reviewer="humano", defects=[LEVE])
    with pytest.raises(errors.InvalidVerdict):
        repo.record_qc(conn, ok_attempt(conn, run_id, 2), verdict="refazer", reviewer="humano", defects=[])
    repo.record_qc(conn, ok_attempt(conn, run_id, 3), verdict="refazer", reviewer="humano", defects=[BLOCK])


def test_record_qc_refazer_refused_on_third_attempt(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    finish_error(conn, add_pending(conn, run_id))
    finish_error(conn, add_pending(conn, run_id))
    third = ok_attempt(conn, run_id)
    assert repo.get_attempt(conn, third).attempt_no == 3  # type: ignore[union-attr]
    with pytest.raises(errors.InvalidVerdict):
        repo.record_qc(conn, third, verdict="refazer", reviewer="humano", defects=[BLOCK])
    repo.record_qc(conn, third, verdict="rejeitada", reviewer="humano", defects=[BLOCK])


def test_record_qc_rejeitada_needs_blocking(conn: sqlite3.Connection) -> None:
    run_id = make_run(conn)
    for scene, defects in ((1, []), (2, [LEVE])):
        with pytest.raises(errors.InvalidVerdict):
            repo.record_qc(conn, ok_attempt(conn, run_id, scene), verdict="rejeitada", reviewer="humano", defects=defects)
    repo.record_qc(conn, ok_attempt(conn, run_id, 3), verdict="rejeitada", reviewer="agente", defects=[BLOCK])


@pytest.mark.parametrize(("verdict", "reviewer"), [("talvez", "humano"), ("rejeitada", "robo")])
def test_record_qc_rejects_unknown_values(conn: sqlite3.Connection, verdict: str, reviewer: str) -> None:
    attempt_id = ok_attempt(conn, make_run(conn))
    with pytest.raises(errors.InvalidVerdict):
        repo.record_qc(conn, attempt_id, verdict=verdict, reviewer=reviewer, defects=[BLOCK])


def test_record_qc_only_once_per_attempt(conn: sqlite3.Connection) -> None:
    attempt_id = ok_attempt(conn, make_run(conn))
    repo.record_qc(conn, attempt_id, verdict="refazer", reviewer="humano", defects=[BLOCK])
    with pytest.raises(errors.InvalidVerdict):
        repo.record_qc(conn, attempt_id, verdict="aprovada", reviewer="humano", defects=[])


def test_db_refuses_agent_approval_even_with_raw_sql(conn: sqlite3.Connection) -> None:
    attempt_id = ok_attempt(conn, make_run(conn))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO qc_reviews(attempt_id, verdict, reviewer, defects_json, reviewed_at)"
            " VALUES (?, 'aprovada', 'agente', '[]', '2026-11-01T00:00:00Z')",
            (attempt_id,),
        )


def test_seed_helper_signature_is_stable(authorize: Callable[[float], int]) -> None:
    """A fixture ``authorize`` (conftest) semeia pelo repo; conferida aqui por ser contrato das demais tarefas."""
    assert authorize(10.0) > 0
