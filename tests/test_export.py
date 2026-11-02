"""Testes de ``verion.ledger.export`` (spec 3.8, T4). Banco semeado pela API pública (``budget`` e ``repo``)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from verion import budget
from verion.config import PriceTable
from verion.ledger import repo
from verion.ledger.export import ExportResult, export_jsonl
from verion.ledger.repo import AttemptRow, Defect

FILES = ("runs.jsonl", "attempts.jsonl", "scenes.jsonl", "authorizations.jsonl")
SKILL_KEYS = {"projeto", "cena", "metodo", "tentativa", "veredito", "defeitos", "custo_brl", "revisor"}
EXTENSION_KEYS = {
    "run_id",
    "attempt_id",
    "status",
    "custo_estimado",
    "reservado_brl",
    "unidades",
    "tipo_unidade",
    "duracao_s",
    "tempo_s",
    "erro",
}


def new_run(conn: sqlite3.Connection, method: str, tmp_path: Path, property_id: str = "casa-01") -> int:
    return repo.create_run(
        conn,
        property_id=property_id,
        method=method,
        script_path=str(tmp_path / "pessoal" / "roteiro.toml"),
        script_sha256="abc123",
    )


def seed(
    conn: sqlite3.Connection,
    prices: PriceTable,
    tmp_path: Path,
    run_id: int,
    scene: int,
    *,
    method: str,
    ok: bool = True,
    error: str = "falha simulada",
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
            cost_is_estimate=True,
            clip_path=tmp_path / "data" / "runs" / "1" / f"s{scene:02d}.mp4",
            duration_s=5.0,
            wall_time_s=1.5,
            provider_job_id="job-secreto-123",
        )
    return budget.settle_error(
        conn, token, billed_units=0.0, error=error, wall_time_s=0.5, provider_job_id=None
    )


def read(directory: Path, name: str) -> list[dict[str, Any]]:
    text = (directory / name).read_text(encoding="utf-8")
    assert text == "" or text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


@pytest.fixture
def seeded(
    conn: sqlite3.Connection, prices: PriceTable, authorize: Callable[[float], int], tmp_path: Path
) -> sqlite3.Connection:
    authorize(500.0)
    kb = new_run(conn, "ken-burns", tmp_path)
    a = seed(conn, prices, tmp_path, kb, 1, method="ken-burns")
    repo.record_qc(conn, a.id, verdict="aprovada", reviewer="humano", defects=[Defect("leve", 2.0, "brilho")])
    seed(conn, prices, tmp_path, kb, 2, method="ken-burns")  # sem QC

    veo = new_run(conn, "veo-3.1-fast", tmp_path)
    for _ in range(3):
        seed(
            conn,
            prices,
            tmp_path,
            veo,
            4,
            method="veo-3.1-fast",
            ok=False,
            error=f"FileNotFoundError: {tmp_path}/fotos/04-sala.jpg ausente",
        )

    fake = new_run(conn, "fake", tmp_path, property_id="casa-fake")
    seed(conn, prices, tmp_path, fake, 1, method="fake")
    return conn


def test_export_writes_four_files_sorted_by_id(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    result = export_jsonl(seeded, out)

    assert isinstance(result, ExportResult)
    assert [p.name for p in result.paths] == list(FILES)
    assert all(p.parent == out and p.is_file() for p in result.paths)
    assert result.skipped_fake_runs == 1

    runs = read(out, "runs.jsonl")
    assert [r["run_id"] for r in runs] == [1, 2]
    assert [r["metodo"] for r in runs] == ["ken-burns", "veo-3.1-fast"]
    attempts = read(out, "attempts.jsonl")
    ids = [a["attempt_id"] for a in attempts]
    assert ids == sorted(ids) and len(ids) == 5  # 2 + 3, sem o fake


def test_export_omits_fake(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    export_jsonl(seeded, out)
    for name in FILES:
        text = (out / name).read_text(encoding="utf-8")
        assert "fake" not in text, name
        assert "casa-fake" not in text, name
    assert {s["run_id"] for s in read(out, "scenes.jsonl")} == {1, 2}


def test_attempt_lines_have_skill_keys_and_nulls_without_qc(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    export_jsonl(seeded, out)
    attempts = read(out, "attempts.jsonl")

    assert all(set(a) == SKILL_KEYS | EXTENSION_KEYS for a in attempts)
    approved, no_qc = attempts[0], attempts[1]

    assert (approved["projeto"], approved["cena"], approved["metodo"], approved["tentativa"]) == (
        "casa-01",
        1,
        "ken-burns",
        1,
    )
    assert approved["veredito"] == "aprovada" and approved["revisor"] == "humano"
    assert approved["defeitos"] == [{"tipo": "leve", "segundo": 2.0, "nota": "brilho"}]
    assert approved["custo_brl"] == 0.0 and approved["status"] == "ok"
    assert approved["custo_estimado"] is True and approved["unidades"] == 0.0
    assert approved["tipo_unidade"] == "none" and approved["duracao_s"] == 5.0 and approved["tempo_s"] == 1.5
    assert approved["erro"] is None

    assert no_qc["veredito"] is None and no_qc["revisor"] is None
    assert no_qc["defeitos"] == []  # sem QC: lista vazia, e não null


def test_three_errors_mark_scene_rejected_with_reason(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    export_jsonl(seeded, out)
    scenes = {(s["run_id"], s["cena"]): s for s in read(out, "scenes.jsonl")}

    veo_scene = scenes[(2, 4)]
    assert veo_scene["status"] == "rejeitada"
    assert "3 tentativas" in veo_scene["motivo"]
    assert veo_scene["tentativas"] == 3
    assert veo_scene["custo_brl"] == pytest.approx(7.5)  # 3 x (5 s x R$ 0,50)
    assert (veo_scene["projeto"], veo_scene["metodo"]) == ("casa-01", "veo-3.1-fast")

    assert scenes[(1, 1)]["status"] == "aprovada" and scenes[(1, 1)]["motivo"] is None
    assert scenes[(1, 2)]["status"] == "aguardando_qc"
    assert scenes[(1, 3)]["status"] == "aberta" and scenes[(1, 3)]["tentativas"] == 0
    assert len([s for s in scenes if s[0] == 1]) == 12  # uma linha por run x cena


def test_export_never_writes_photo_or_clip_paths(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    export_jsonl(seeded, out)
    blob = "".join((out / name).read_text(encoding="utf-8") for name in FILES)

    assert str(tmp_path) not in blob  # nem clipe, nem foto, nem o caminho do roteiro
    assert ".mp4" not in blob and "clip_path" not in blob and "script_path" not in blob
    assert "<caminho>" in blob  # o caminho dentro da mensagem de erro foi ocultado
    assert "job-secreto-123" not in blob  # ids do provedor ficam fora do export
    for attempt in read(out, "attempts.jsonl"):
        assert not {"clip_path", "photo", "foto", "caminho"} & set(attempt)


def test_export_authorizations(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    export_jsonl(seeded, out)
    (auth,) = read(out, "authorizations.jsonl")
    assert auth["valor_brl"] == 500.0 and auth["referencia"] == "teste" and "criado_em" in auth


def test_export_is_idempotent_and_overwrites(seeded: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    export_jsonl(seeded, out)
    first = {name: (out / name).read_bytes() for name in FILES}
    (out / "attempts.jsonl").write_text("lixo antigo\n", encoding="utf-8")
    export_jsonl(seeded, out)
    assert {name: (out / name).read_bytes() for name in FILES} == first


def test_export_empty_database_writes_empty_files(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "nested" / "evidence"
    result = export_jsonl(conn, out)
    assert [p.name for p in result.paths] == list(FILES)
    assert result.skipped_fake_runs == 0
    assert all(p.read_text(encoding="utf-8") == "" for p in result.paths)


def test_export_keeps_utf8(conn: sqlite3.Connection, prices: PriceTable, tmp_path: Path) -> None:
    repo.add_authorization(conn, 10.0, "STATE.md 2026-11-02 — autorização")
    export_jsonl(conn, tmp_path / "e")
    text = (tmp_path / "e" / "authorizations.jsonl").read_text(encoding="utf-8")
    assert "autorização" in text  # ensure_ascii=False
