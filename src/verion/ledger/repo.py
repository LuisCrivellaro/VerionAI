"""Repositório SQLite do ledger (spec 3.7). Sem ORM; transações explícitas.

Regras que moram aqui: estado da cena (``scene_status``), limite de 3 tentativas, validação de
veredito de QC e imutabilidade do gasto. ``insert_pending`` só é chamada por ``budget.reserve`` e
``add_authorization`` só pela CLI (ambas protegidas pelo teste AST ``tests/test_architecture.py``).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from verion.errors import (
    AwaitingQC,
    InputError,
    InvalidVerdict,
    PendingAttempt,
    SceneClosed,
    SchemaMismatch,
)

SCHEMA_VERSION: Final = 1
MAX_ATTEMPTS: Final = 3
_SCHEMA_PATH: Final = Path(__file__).with_name("schema.sql")

AttemptStatus = Literal["pending", "ok", "error"]
SceneStatus = Literal["aberta", "pendente", "aguardando_qc", "aprovada", "rejeitada"]

_VERDICTS: Final = ("aprovada", "refazer", "rejeitada")
_REVIEWERS: Final = ("humano", "agente")


# --- tipos de linha -------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRow:
    id: int
    property_id: str
    method: str
    script_path: str
    script_sha256: str
    started_at: str
    finished_at: str | None
    notes: str


@dataclass(frozen=True)
class AttemptRow:
    id: int
    run_id: int
    scene_index: int
    attempt_no: int
    method: str
    provider: str
    model: str
    status: AttemptStatus
    target_s: float
    reserved_brl: float
    cost_brl: float
    cost_is_estimate: bool
    units: float
    unit_kind: str
    clip_path: str | None
    duration_s: float | None
    wall_time_s: float | None
    provider_job_id: str | None
    error: str | None
    created_at: str
    finished_at: str | None


@dataclass(frozen=True)
class Defect:
    tipo: int | Literal["leve"]  # 1..7 = bloqueante (skill verion-fidelity-qc)
    segundo: float
    nota: str


@dataclass(frozen=True)
class QcRow:
    id: int
    attempt_id: int
    verdict: str
    reviewer: str
    defects: tuple[Defect, ...]
    reviewed_at: str


_RUN_COLS: Final = "id, property_id, method, script_path, script_sha256, started_at, finished_at, notes"
_ATTEMPT_COLS: Final = (
    "id, run_id, scene_index, attempt_no, method, provider, model, status, target_s, reserved_brl,"
    " cost_brl, cost_is_estimate, units, unit_kind, clip_path, duration_s, wall_time_s,"
    " provider_job_id, error, created_at, finished_at"
)


def _attempt(row: tuple[object, ...]) -> AttemptRow:
    values = list(row)
    values[11] = bool(values[11])  # cost_is_estimate
    return AttemptRow(*values)  # type: ignore[arg-type]  # ordem fixada por _ATTEMPT_COLS


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- conexão e transações -------------------------------------------------------------------


def connect(path: Path | str, *, create: bool = True) -> sqlite3.Connection:
    """Abre o banco. ``create=True`` (padrão) cria pasta (0700) e arquivo (0600) e aplica o esquema se preciso.

    Com ``create=False`` o banco precisa existir (``mode=rw``): ausente → ``InputError``, sem criar nada.
    ``SchemaMismatch`` se ``user_version`` não for 0 nem 1. As permissões restritas valem também para os
    arquivos ``-wal``/``-shm``, que o SQLite cria com as permissões do banco.
    """
    db_path = Path(path)
    if create:
        db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not db_path.exists():
            os.close(os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600))  # permissão fixada antes do 1º uso
        conn = sqlite3.connect(db_path, isolation_level=None)  # transações explícitas
    else:
        uri = f"{db_path.resolve().as_uri()}?mode=rw"
        try:
            conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        except sqlite3.OperationalError as exc:
            raise InputError(
                f"banco não encontrado em {db_path} ({exc}); rode `verion db init`"
            ) from exc
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise SchemaMismatch(
                f"{db_path}: user_version = {version}, esperado {SCHEMA_VERSION}"
            )
        conn.execute("PRAGMA journal_mode = WAL")
        if version == 0:
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except BaseException:
        conn.close()
        raise
    return conn


def drop_all(conn: sqlite3.Connection) -> None:
    """Reverte o esquema (volta ``user_version`` a 0). Só para testes."""
    for table in ("qc_reviews", "attempts", "authorizations", "runs"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute("PRAGMA user_version = 0")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """``BEGIN IMMEDIATE`` (serializa escritores, inclusive de outros processos); rollback em exceção.

    O ``COMMIT`` fica dentro do ``try``: ele pode falhar (ex.: FK adiado) e, nesse caso, a transação
    continua aberta no SQLite, então também precisa do ``ROLLBACK``.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


# --- runs -----------------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    *,
    property_id: str,
    method: str,
    script_path: str,
    script_sha256: str,
    notes: str = "",
) -> int:
    cur = conn.execute(
        "INSERT INTO runs (property_id, method, script_path, script_sha256, started_at, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (property_id, method, script_path, script_sha256, _now(), notes),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def add_note(conn: sqlite3.Connection, run_id: int, note: str) -> None:
    """Acrescenta uma linha a ``runs.notes`` (sem mudar o esquema). ``InputError`` se o run não existir."""
    if not note or not note.strip():
        raise InputError("nota vazia")
    cur = conn.execute(
        "UPDATE runs SET notes = CASE WHEN notes = '' THEN ?1 ELSE notes || char(10) || ?1 END WHERE id = ?2",
        (note.strip(), run_id),
    )
    if cur.rowcount != 1:
        raise InputError(f"run inexistente: {run_id}")


def finish_run(conn: sqlite3.Connection, run_id: int) -> None:
    """Marca o fim do run. Idempotente: não altera um ``finished_at`` já gravado."""
    conn.execute("UPDATE runs SET finished_at = ? WHERE id = ? AND finished_at IS NULL", (_now(), run_id))


def get_run(conn: sqlite3.Connection, run_id: int) -> RunRow:
    row = conn.execute(f"SELECT {_RUN_COLS} FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise InputError(f"run inexistente: {run_id}")
    return RunRow(*row)


def list_runs(conn: sqlite3.Connection) -> list[RunRow]:
    return [RunRow(*r) for r in conn.execute(f"SELECT {_RUN_COLS} FROM runs ORDER BY id")]


# --- tentativas -----------------------------------------------------------------------------


def insert_pending(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    scene_index: int,
    attempt_no: int,
    target_s: float,
    method: str,
    provider: str,
    model: str,
    units: float,
    unit_kind: str,
    reserved_brl: float,
) -> int:
    """Grava a reserva. Só ``budget.reserve`` chama (protegido pelo teste AST)."""
    cur = conn.execute(
        "INSERT INTO attempts (run_id, scene_index, attempt_no, method, provider, model, status,"
        " target_s, reserved_brl, cost_brl, cost_is_estimate, units, unit_kind, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, 1, ?, ?, ?)",
        (
            run_id,
            scene_index,
            attempt_no,
            method,
            provider,
            model,
            target_s,
            reserved_brl,
            reserved_brl,
            units,
            unit_kind,
            _now(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def set_job_id(conn: sqlite3.Connection, attempt_id: int, job_id: str) -> None:
    cur = conn.execute(
        "UPDATE attempts SET provider_job_id = ? WHERE id = ? AND status = 'pending'",
        (job_id, attempt_id),
    )
    if cur.rowcount != 1:
        raise InputError(f"tentativa {attempt_id} não está pending")


def _require_finite_nonneg(value: float, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise InputError(f"{what} inválido: {value!r}")


def finish_attempt(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    status: Literal["ok", "error"],
    cost_brl: float,
    cost_is_estimate: bool,
    units: float,
    clip_path: str | None,
    duration_s: float | None,
    wall_time_s: float,
    provider_job_id: str | None,
    error: str | None,
) -> AttemptRow:
    """Liquida uma tentativa ``pending``. ``error`` nunca pode custar menos que a reserva."""
    if status not in ("ok", "error"):
        raise InputError(f"status inválido: {status!r}")
    _require_finite_nonneg(cost_brl, "cost_brl")
    _require_finite_nonneg(units, "units")
    current = get_attempt(conn, attempt_id)
    if current is None or current.status != "pending":
        raise InputError(f"tentativa {attempt_id} inexistente ou não está pending")
    if status == "error":
        if not error or not error.strip():
            raise InputError("tentativa com erro exige mensagem")
        if cost_brl < current.reserved_brl:
            raise InputError(
                f"custo R$ {cost_brl:.2f} menor que a reserva R$ {current.reserved_brl:.2f}"
            )
    cur = conn.execute(
        "UPDATE attempts SET status = ?, cost_brl = ?, cost_is_estimate = ?, units = ?, clip_path = ?,"
        " duration_s = ?, wall_time_s = ?, provider_job_id = COALESCE(?, provider_job_id), error = ?,"
        " finished_at = ? WHERE id = ? AND status = 'pending'",
        (
            status,
            cost_brl,
            int(cost_is_estimate),
            units,
            clip_path,
            duration_s,
            wall_time_s,
            provider_job_id,
            error,
            _now(),
            attempt_id,
        ),
    )
    if cur.rowcount != 1:  # liquidada por outro processo entre a leitura e o UPDATE
        raise InputError(f"tentativa {attempt_id} não está mais pending")
    row = get_attempt(conn, attempt_id)
    assert row is not None
    return row


def settle_pending(conn: sqlite3.Connection, attempt_id: int, *, cost_brl: float, note: str) -> AttemptRow:
    """Liquida à mão uma ``pending`` órfã: vira ``error`` com custo >= reserva (nunca reduz o gasto)."""
    if not note or not note.strip():
        raise InputError("a liquidação manual exige uma nota")
    _require_finite_nonneg(cost_brl, "cost_brl")
    current = get_attempt(conn, attempt_id)
    if current is None or current.status != "pending":
        raise InputError(f"tentativa {attempt_id} inexistente ou não está pending")
    if cost_brl < current.reserved_brl:
        raise InputError(
            f"custo R$ {cost_brl:.2f} menor que a reserva R$ {current.reserved_brl:.2f}: "
            "a liquidação nunca reduz o gasto registrado"
        )
    cur = conn.execute(
        "UPDATE attempts SET status = 'error', cost_brl = ?, cost_is_estimate = 1, error = ?,"
        " finished_at = ? WHERE id = ? AND status = 'pending'",
        (cost_brl, f"liquidada manualmente: {note.strip()}", _now(), attempt_id),
    )
    if cur.rowcount != 1:
        raise InputError(f"tentativa {attempt_id} não está mais pending")
    row = get_attempt(conn, attempt_id)
    assert row is not None
    return row


def get_attempt(conn: sqlite3.Connection, attempt_id: int) -> AttemptRow | None:
    row = conn.execute(f"SELECT {_ATTEMPT_COLS} FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    return None if row is None else _attempt(row)


def list_attempts(
    conn: sqlite3.Connection, *, run_id: int | None = None, method: str | None = None
) -> list[AttemptRow]:
    clauses: list[str] = []
    params: list[int | str] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if method is not None:
        clauses.append("method = ?")
        params.append(method)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cur = conn.execute(f"SELECT {_ATTEMPT_COLS} FROM attempts{where} ORDER BY id", params)
    return [_attempt(r) for r in cur]


# --- estado da cena -------------------------------------------------------------------------


def _defects_text(defects_json: str) -> str:
    parts = [
        f"{d['tipo']}@{d['segundo']:g}s {d['nota']}".strip() for d in json.loads(defects_json)
    ]
    return "defeitos: " + "; ".join(parts) if parts else "rejeitada sem defeitos registrados"


def scene_status(conn: sqlite3.Connection, run_id: int, scene_index: int) -> tuple[SceneStatus, str | None]:
    """Estado da cena; vale a primeira regra que casar (ver spec 3.7)."""
    rows = conn.execute(
        "SELECT a.attempt_no, a.status, q.verdict, q.defects_json FROM attempts a"
        " LEFT JOIN qc_reviews q ON q.attempt_id = a.id"
        " WHERE a.run_id = ? AND a.scene_index = ? ORDER BY a.attempt_no",
        (run_id, scene_index),
    ).fetchall()
    if any(r[2] == "aprovada" for r in rows):
        return "aprovada", None
    for _, _, verdict, defects_json in rows:
        if verdict == "rejeitada":
            return "rejeitada", _defects_text(defects_json)
    for attempt_no, status, _, _ in rows:
        if status == "pending":
            return "pendente", f"tentativa {attempt_no} pendente (ver `verion ledger settle`)"
    if rows and rows[-1][1] == "ok" and rows[-1][2] is None:
        return "aguardando_qc", f"tentativa {rows[-1][0]} sem veredito de QC"
    if len(rows) >= MAX_ATTEMPTS:
        return "rejeitada", f"limite de {MAX_ATTEMPTS} tentativas"
    return "aberta", None


def next_attempt_no(conn: sqlite3.Connection, run_id: int, scene_index: int) -> int:
    """Número da próxima tentativa, ou ``SceneClosed``/``PendingAttempt``/``AwaitingQC``."""
    status, reason = scene_status(conn, run_id, scene_index)
    where = f"cena {scene_index}"
    if status == "aberta":
        (last,) = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) FROM attempts WHERE run_id = ? AND scene_index = ?",
            (run_id, scene_index),
        ).fetchone()
        return int(last) + 1
    if status == "pendente":
        raise PendingAttempt(f"{where}: {reason}")
    if status == "aguardando_qc":
        raise AwaitingQC(f"{where}: {reason}")
    raise SceneClosed(f"{where} {status}" + (f": {reason}" if reason else ""))


# --- QC -------------------------------------------------------------------------------------


def _is_blocking(defect: Defect) -> bool:
    return not (isinstance(defect.tipo, str))


def _validate_defect(defect: Defect) -> None:
    tipo = defect.tipo
    is_block = isinstance(tipo, int) and not isinstance(tipo, bool) and 1 <= tipo <= 7
    if not (is_block or (isinstance(tipo, str) and tipo == "leve")):
        raise InvalidVerdict(f"tipo de defeito inválido: {tipo!r} (use 1..7 ou 'leve')")
    segundo = defect.segundo
    if isinstance(segundo, bool) or not isinstance(segundo, int | float) or not math.isfinite(segundo) or segundo < 0:
        raise InvalidVerdict(f"segundo inválido no defeito: {segundo!r}")
    if not isinstance(defect.nota, str):
        raise InvalidVerdict("nota do defeito deve ser texto")


def record_qc(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    verdict: str,
    reviewer: str,
    defects: Sequence[Defect],
) -> int:
    """Grava o veredito de uma tentativa ``ok``. Regras da skill ``verion-fidelity-qc`` (spec 3.7)."""
    if verdict not in _VERDICTS:
        raise InvalidVerdict(f"veredito inválido: {verdict!r}")
    if reviewer not in _REVIEWERS:
        raise InvalidVerdict(f"revisor inválido: {reviewer!r}")
    for defect in defects:
        _validate_defect(defect)
    blocking = [d for d in defects if _is_blocking(d)]
    leves = [d for d in defects if not _is_blocking(d)]

    if verdict == "aprovada":
        if blocking:
            raise InvalidVerdict("aprovada não admite defeito bloqueante (1..7)")
        if len(leves) > 1:
            raise InvalidVerdict("aprovada admite no máximo 1 defeito leve")
        if reviewer != "humano":
            raise InvalidVerdict("só revisor humano pode aprovar")
    elif not blocking:
        raise InvalidVerdict(f"{verdict} exige ao menos um defeito bloqueante (1..7)")

    payload = json.dumps(
        [{"tipo": d.tipo, "segundo": d.segundo, "nota": d.nota} for d in defects], ensure_ascii=False
    )
    with transaction(conn):
        attempt = get_attempt(conn, attempt_id)
        if attempt is None:
            raise InvalidVerdict(f"tentativa inexistente: {attempt_id}")
        if attempt.status != "ok":
            raise InvalidVerdict(f"só tentativa ok recebe QC (tentativa {attempt_id} está {attempt.status})")
        if get_qc(conn, attempt_id) is not None:
            raise InvalidVerdict(f"tentativa {attempt_id} já tem veredito")
        if verdict == "refazer" and attempt.attempt_no >= MAX_ATTEMPTS:
            raise InvalidVerdict(f"refazer é recusado na {MAX_ATTEMPTS}ª tentativa: use rejeitada")
        cur = conn.execute(
            "INSERT INTO qc_reviews (attempt_id, verdict, reviewer, defects_json, reviewed_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (attempt_id, verdict, reviewer, payload, _now()),
        )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_qc(conn: sqlite3.Connection, attempt_id: int) -> QcRow | None:
    row = conn.execute(
        "SELECT id, attempt_id, verdict, reviewer, defects_json, reviewed_at FROM qc_reviews"
        " WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    if row is None:
        return None
    defects = tuple(Defect(d["tipo"], d["segundo"], d["nota"]) for d in json.loads(row[4]))
    return QcRow(row[0], row[1], row[2], row[3], defects, row[5])


# --- gasto e autorizações -------------------------------------------------------------------


def spent_brl(conn: sqlite3.Connection) -> float:
    """Soma de ``cost_brl`` de todas as tentativas; ``pending`` conta pela reserva."""
    return float(conn.execute("SELECT COALESCE(SUM(cost_brl), 0.0) FROM attempts").fetchone()[0])


def authorized_brl(conn: sqlite3.Connection) -> float:
    return float(conn.execute("SELECT COALESCE(SUM(amount_brl), 0.0) FROM authorizations").fetchone()[0])


def add_authorization(conn: sqlite3.Connection, amount_brl: float, reference: str) -> int:
    """Registra autorização de gasto. Só ``cli.py`` chama (protegido pelo teste AST)."""
    if (
        isinstance(amount_brl, bool)
        or not isinstance(amount_brl, int | float)
        or not math.isfinite(amount_brl)
        or amount_brl <= 0
    ):
        raise InputError(f"valor de autorização inválido: {amount_brl!r}")
    if not reference or not reference.strip():
        raise InputError("a autorização exige uma referência (ex.: 'STATE.md AAAA-MM-DD')")
    cur = conn.execute(
        "INSERT INTO authorizations (amount_brl, reference, created_at) VALUES (?, ?, ?)",
        (float(amount_brl), reference.strip(), _now()),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_authorizations(conn: sqlite3.Connection) -> list[tuple[int, float, str, str]]:
    """``(id, amount_brl, reference, created_at)`` por ``id``."""
    return [
        (int(i), float(a), str(r), str(c))
        for i, a, r, c in conn.execute(
            "SELECT id, amount_brl, reference, created_at FROM authorizations ORDER BY id"
        )
    ]
