"""Relatório por método (spec 3.9): qual método entrega cenas aprovadas com o menor custo.

Só lê o ledger pela API pública de ``verion.ledger.repo``. O método ``fake`` (pago fictício, só testes) nunca
aparece. Um run sem nenhuma tentativa (ex.: bloqueado na cena 1 pela trava de gasto) é ignorado: ele não
tentou nenhuma cena e inflaria ``scenes_total``.

Contagem de cenas, por run com tentativas e cena 1..12 (``repo.scene_status``): ``aprovada`` -> ``approved``;
``rejeitada`` -> ``rejected`` (inclui o limite de 3 tentativas); qualquer outro estado (aberta, pendente,
aguardando QC) -> ``open``. Logo ``approved + rejected + open == scenes_total``.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from verion.ledger import repo
from verion.script import SCENE_COUNT

__all__ = ["MethodReport", "build_report", "render"]

_EXCLUDED_METHODS: Final = frozenset({"fake"})
_BLOCKING_TYPES: Final = tuple(range(1, 8))  # tipos 1..7 da skill verion-fidelity-qc
_NONE: Final = "—"


@dataclass(frozen=True)
class MethodReport:
    method: str
    scenes_total: int  # SCENE_COUNT x runs do método que tentaram ao menos uma cena
    approved: int
    rejected: int
    open: int
    attempts: int  # inclui erro e pending
    attempts_per_approved: float | None  # None sem cena aprovada
    cost_brl: float  # soma de cost_brl (pending conta pela reserva)
    cost_per_approved_brl: float | None
    mean_wall_time_s: float | None  # média só das tentativas ok
    blocking_defects: Mapping[int, int]  # tipo 1..7 -> contagem em vereditos refazer/rejeitada


def build_report(conn: sqlite3.Connection) -> list[MethodReport]:
    """Uma linha por método (ordenadas por id), sem o ``fake``."""
    runs_by_method: dict[str, list[int]] = defaultdict(list)
    for run in repo.list_runs(conn):
        if run.method not in _EXCLUDED_METHODS:
            runs_by_method[run.method].append(run.id)

    rows: list[MethodReport] = []
    for method in sorted(runs_by_method):
        row = _method_row(conn, method, runs_by_method[method])
        if row is not None:
            rows.append(row)
    return rows


def _method_row(conn: sqlite3.Connection, method: str, run_ids: list[int]) -> MethodReport | None:
    approved = rejected = open_ = runs_counted = attempts = 0
    cost = 0.0
    ok_times: list[float] = []
    defects = dict.fromkeys(_BLOCKING_TYPES, 0)

    for run_id in run_ids:
        run_attempts = repo.list_attempts(conn, run_id=run_id)
        if not run_attempts:
            continue  # run que não tentou nenhuma cena
        runs_counted += 1
        attempts += len(run_attempts)
        for attempt in run_attempts:
            cost += attempt.cost_brl
            if attempt.status == "ok" and attempt.wall_time_s is not None:
                ok_times.append(attempt.wall_time_s)
            qc = repo.get_qc(conn, attempt.id)
            if qc is not None and qc.verdict in ("refazer", "rejeitada"):
                for defect in qc.defects:
                    if isinstance(defect.tipo, int) and defect.tipo in defects:
                        defects[defect.tipo] += 1
        for scene in range(1, SCENE_COUNT + 1):
            status, _ = repo.scene_status(conn, run_id, scene)
            if status == "aprovada":
                approved += 1
            elif status == "rejeitada":
                rejected += 1
            else:
                open_ += 1

    if runs_counted == 0:
        return None
    return MethodReport(
        method=method,
        scenes_total=SCENE_COUNT * runs_counted,
        approved=approved,
        rejected=rejected,
        open=open_,
        attempts=attempts,
        attempts_per_approved=attempts / approved if approved else None,
        cost_brl=cost,
        cost_per_approved_brl=cost / approved if approved else None,
        mean_wall_time_s=sum(ok_times) / len(ok_times) if ok_times else None,
        blocking_defects=MappingProxyType(defects),
    )


# --- apresentação ---------------------------------------------------------------------------


def render(rows: Sequence[MethodReport], fmt: Literal["md", "csv"]) -> str:
    """Tabela Markdown (decimais com vírgula) ou CSV (decimais com ponto, vazio = indefinido)."""
    if fmt == "md":
        return _render_md(rows)
    if fmt == "csv":
        return _render_csv(rows)
    raise ValueError(f"formato desconhecido: {fmt!r} (use 'md' ou 'csv')")


def _md_num(value: float | None, digits: int = 2) -> str:
    return _NONE if value is None else f"{value:.{digits}f}".replace(".", ",")


def _md_defects(defects: Mapping[int, int]) -> str:
    parts = [f"{tipo}:{count}" for tipo, count in sorted(defects.items()) if count]
    return " ".join(parts) if parts else _NONE


def _render_md(rows: Sequence[MethodReport]) -> str:
    header = (
        "Método",
        "Cenas",
        "Aprovadas",
        "Rejeitadas",
        "Abertas",
        "Tentativas",
        "Tent./aprovada",
        "Custo (R$)",
        "R$/aprovada",
        "Tempo médio (s)",
        "Defeitos bloqueantes",
    )
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for r in rows:
        cells = (
            r.method,
            str(r.scenes_total),
            str(r.approved),
            str(r.rejected),
            str(r.open),
            str(r.attempts),
            _md_num(r.attempts_per_approved),
            _md_num(r.cost_brl),
            _md_num(r.cost_per_approved_brl),
            _md_num(r.mean_wall_time_s, 1),
            _md_defects(r.blocking_defects),
        )
        lines.append("| " + " | ".join(cells) + " |")
    if not rows:
        lines.append("")
        lines.append("(sem runs com tentativas)")
    return "\n".join(lines) + "\n"


def _csv_num(value: float | None) -> str:
    return "" if value is None else repr(round(value, 6))


def _render_csv(rows: Sequence[MethodReport]) -> str:
    columns = [
        "method",
        "scenes_total",
        "approved",
        "rejected",
        "open",
        "attempts",
        "attempts_per_approved",
        "cost_brl",
        "cost_per_approved_brl",
        "mean_wall_time_s",
        *(f"defects_{tipo}" for tipo in _BLOCKING_TYPES),
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for r in rows:
        writer.writerow(
            [
                r.method,
                r.scenes_total,
                r.approved,
                r.rejected,
                r.open,
                r.attempts,
                _csv_num(r.attempts_per_approved),
                _csv_num(r.cost_brl),
                _csv_num(r.cost_per_approved_brl),
                _csv_num(r.mean_wall_time_s),
                *(r.blocking_defects.get(tipo, 0) for tipo in _BLOCKING_TYPES),
            ]
        )
    return buffer.getvalue()
