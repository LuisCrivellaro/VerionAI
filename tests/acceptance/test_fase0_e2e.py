"""T12: aceitação ponta a ponta do harness da Fase 0, pela CLI real ``verion`` em subprocesso.

Critérios (spec ``FASE0_HARNESS.md`` linha T12, ajustados pelas ADR-006 a ADR-010):

1. ``ken-burns`` completo (12 cenas ok, em formato de entrega);
2. ``fake`` sem ``--allow-paid`` -> código 3 e 0 tentativas;
3. ``fake`` com autorização semeada via ``repo`` e ``VERION_FAKE_MODE=billed_error`` -> código 4, com o gasto
   (reserva liquidada como erro) em ``budget show``;
4. ``budget authorize`` sem TTY -> código 2 e 0 linhas;
5. ``qc record`` humano em 3 cenas ``parallax-2.5d``, com pty e a frase ``APROVO tentativa N`` (ADR-010);
6. ``assemble`` com fallback ``ken-burns`` -> h264 1920x1080 yuv420p 30/1;
7. ``report`` com os 2 métodos;
8. ``ledger export`` sem caminho de foto/clipe nem segredo.

Dois níveis (ver o conftest): os testes sem marcador rodam sempre, e incluem um fumaça com render real; os
marcados com ``e2e_full`` (pipeline de 12 cenas, ~110 s) só rodam com ``VERION_E2E_FULL=1``.

Escolhas (documentadas): a aprovação humana das cenas 1 a 3 do ``parallax-2.5d`` usa pty de verdade; as das
cenas 4 a 12 do ``ken-burns`` (necessárias só para o fallback da montagem) também, porque cada uma custa
menos de um segundo. A autorização de gasto do ``fake`` é semeada via ``repo`` (como o AST da T1 permite).
O critério "pytest inteiro < 120 s" é medido pelo supervisor rodando a suíte completa; não é um teste.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from verion.ledger import repo

full = pytest.mark.e2e_full  # pipeline completo com render real (opt-in: VERION_E2E_FULL=1, ver conftest)

if TYPE_CHECKING:  # o pytest (importlib) não permite importar o conftest em tempo de execução
    from conftest import CliResult, Env

# --- utilidades -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    codec: str
    width: int
    height: int
    pix_fmt: str
    fps: str
    duration_s: float


def ffprobe(path: Path) -> Probe:
    """ffprobe direto (independente de ``verion.local.ffmpeg``): o teste não confia no código que valida."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,pix_fmt,avg_frame_rate:format=duration",
            "-of", "json", "--", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )  # fmt: skip
    data = json.loads(proc.stdout)
    s = data["streams"][0]
    return Probe(s["codec_name"], int(s["width"]), int(s["height"]), s["pix_fmt"], s["avg_frame_rate"], float(data["format"]["duration"]))


def assert_delivery_format(probe: Probe) -> None:
    assert (probe.codec, probe.width, probe.height, probe.pix_fmt, probe.fps) == ("h264", 1920, 1080, "yuv420p", "30/1")


def run_id_of(env: Env, method: str) -> int:
    with env.connect() as conn:
        ids = [r.id for r in repo.list_runs(conn) if r.method == method]
    assert len(ids) == 1, f"esperado 1 run de {method}, achei {ids}"
    return ids[0]


def count(env: Env, table: str) -> int:
    conn = sqlite3.connect(env.db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # tabela fixa do teste
    finally:
        conn.close()


def budget(env: Env) -> dict[str, str]:
    """Campos de ``verion budget show``: autorizado, gasto, saldo (texto) e pending (R$)."""
    result = env.cli("budget", "show")
    assert result.code == 0, result.err
    fields = {m.group(1): m.group(2) for m in re.finditer(r"^(autorizado|gasto|saldo): (R\$ [\d,]+)", result.out, re.MULTILINE)}
    pending = re.search(r"inclui reservas pending: (R\$ [\d,]+)", result.out)
    assert len(fields) == 3 and pending, result.out
    fields["pending"] = pending.group(1)
    return fields


def approve_in_pty(env: Env, attempt_id: int) -> CliResult:
    return env.cli_pty(
        ("qc", "record", "--attempt", str(attempt_id), "--verdict", "aprovada", "--reviewer", "humano"),
        f"APROVO tentativa {attempt_id}\n".encode(),
    )


# --- fluxo completo com mídia real (render roda UMA vez por módulo) ---------------------------


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    result: CliResult
    elapsed_s: float


@pytest.fixture(scope="module")
def kb(e2e_env: Env) -> RunOutcome:
    """``ken-burns`` nas 12 cenas (3 s, 1080p)."""
    start = time.monotonic()
    result = e2e_env.cli("run", "--script", str(e2e_env.script), "--method", "ken-burns")
    elapsed = time.monotonic() - start
    assert result.code == 0, result.err
    return RunOutcome(run_id_of(e2e_env, "ken-burns"), result, elapsed)


@pytest.fixture(scope="module")
def px(e2e_env: Env, kb: RunOutcome) -> RunOutcome:
    """``parallax-2.5d`` nas cenas 1 a 3 (têm ``.depth.png``)."""
    start = time.monotonic()
    result = e2e_env.cli("run", "--script", str(e2e_env.script), "--method", "parallax-2.5d", "--scenes", "1,2,3")
    elapsed = time.monotonic() - start
    assert result.code == 0, result.err
    return RunOutcome(run_id_of(e2e_env, "parallax-2.5d"), result, elapsed)


def attempts_by_scene(env: Env, run_id: int) -> dict[int, repo.AttemptRow]:
    with env.connect() as conn:
        rows = repo.list_attempts(conn, run_id=run_id)
    assert len({r.scene_index for r in rows}) == len(rows), "uma tentativa por cena neste fluxo"
    return {r.scene_index: r for r in rows}


@full
def test_ken_burns_runs_all_12_scenes_ok(e2e_env: Env, kb: RunOutcome) -> None:
    assert re.search(r"run \d+: 12 tentada\(s\), 12 ok, 0 erro\(s\), custo R\$ 0,00", kb.result.out), kb.result.out
    rows = attempts_by_scene(e2e_env, kb.run_id)
    assert sorted(rows) == list(range(1, 13))
    for scene, row in rows.items():
        assert row.status == "ok" and row.cost_brl == 0.0 and row.method == "ken-burns", scene
        assert row.units == 0 and row.unit_kind == "none"
        assert row.clip_path is not None and Path(row.clip_path).is_file()
        assert row.wall_time_s is not None and row.wall_time_s > 0
    print(f"\n[T12] ken-burns 12 cenas: {kb.elapsed_s:.1f} s")


@full
def test_ken_burns_clips_are_in_delivery_format_with_the_target_duration(e2e_env: Env, kb: RunOutcome) -> None:
    for scene, row in attempts_by_scene(e2e_env, kb.run_id).items():
        probe = ffprobe(Path(row.clip_path or ""))
        assert_delivery_format(probe)
        assert probe.duration_s == pytest.approx(e2e_env.scene_seconds, abs=0.1), f"cena {scene}: {probe.duration_s}"


@full
def test_ken_burns_run_spends_nothing(e2e_env: Env, kb: RunOutcome) -> None:
    assert budget(e2e_env)["gasto"] == "R$ 0,00"


@full
def test_parallax_runs_the_3_scenes_with_depth_maps(e2e_env: Env, px: RunOutcome) -> None:
    assert re.search(r"run \d+: 3 tentada\(s\), 3 ok, 0 erro\(s\), custo R\$ 0,00", px.result.out), px.result.out
    rows = attempts_by_scene(e2e_env, px.run_id)
    assert sorted(rows) == [1, 2, 3]
    for row in rows.values():
        assert row.status == "ok" and row.method == "parallax-2.5d"
        assert_delivery_format(ffprobe(Path(row.clip_path or "")))
    print(f"\n[T12] parallax 3 cenas: {px.elapsed_s:.1f} s")


# --- QC humano com pty -----------------------------------------------------------------------


@dataclass(frozen=True)
class Approvals:
    results: dict[int, CliResult]  # cena -> resultado do `qc record`
    refazer: CliResult  # veredito do agente na cena 1 do ken-burns


@pytest.fixture(scope="module")
def approvals(e2e_env: Env, kb: RunOutcome, px: RunOutcome) -> Approvals:
    """Aprova em pty as cenas 1 a 3 do parallax e as cenas 4 a 12 do ken-burns (fallback da montagem).

    A cena 1 do ken-burns recebe `refazer` de agente com um defeito cuja nota traz um caminho de foto e a
    chave sintética: a CLI grava a nota redigida e o export nunca a leva.
    """
    results: dict[int, CliResult] = {}
    for scene, row in attempts_by_scene(e2e_env, px.run_id).items():
        results[scene] = approve_in_pty(e2e_env, row.id)
    kb_rows = attempts_by_scene(e2e_env, kb.run_id)
    for scene in range(4, 13):
        results[100 + scene] = approve_in_pty(e2e_env, kb_rows[scene].id)
    note = f"quina deformada em {e2e_env.photos[0]} chave {e2e_env.secret}"
    refazer = e2e_env.cli(
        "qc", "record", "--attempt", str(kb_rows[1].id), "--verdict", "refazer", "--reviewer", "agente",
        "--defect", f"4:1.5:{note}",
    )  # fmt: skip
    return Approvals(results, refazer)


@full
def test_human_approval_in_pty_of_3_parallax_scenes(e2e_env: Env, px: RunOutcome, approvals: Approvals) -> None:
    rows = attempts_by_scene(e2e_env, px.run_id)
    with e2e_env.connect() as conn:
        for scene in (1, 2, 3):
            result = approvals.results[scene]
            assert result.code == 0, result.err
            assert "veredito aprovada (humano)" in result.out and f"cena {scene} do run {px.run_id}: aprovada" in result.out
            qc = repo.get_qc(conn, rows[scene].id)
            assert qc is not None and (qc.verdict, qc.reviewer, qc.defects) == ("aprovada", "humano", ())
            assert repo.scene_status(conn, px.run_id, scene)[0] == "aprovada"
        assert conn.execute("SELECT COUNT(*) FROM qc_reviews WHERE attempt_id IN (?,?,?)", [r.id for r in rows.values()]).fetchone()[0] == 3


@full
def test_agent_refazer_with_defect_is_recorded_and_the_scene_reopens(e2e_env: Env, kb: RunOutcome, approvals: Approvals) -> None:
    assert approvals.refazer.code == 0, approvals.refazer.err
    row = attempts_by_scene(e2e_env, kb.run_id)[1]
    with e2e_env.connect() as conn:
        qc = repo.get_qc(conn, row.id)
        assert qc is not None and (qc.verdict, qc.reviewer) == ("refazer", "agente")
        assert [d.tipo for d in qc.defects] == [4]
        assert e2e_env.secret not in qc.defects[0].nota  # a CLI redige o segredo antes de gravar
        assert repo.scene_status(conn, kb.run_id, 1)[0] == "aberta"


@full
def test_qc_frames_extracts_frames_sheet_and_last_frame(e2e_env: Env, px: RunOutcome, approvals: Approvals) -> None:
    attempt = attempts_by_scene(e2e_env, px.run_id)[1]
    result = e2e_env.cli("qc", "frames", "--attempt", str(attempt.id))
    assert result.code == 0, result.err
    assert f"foto de origem da cena 1: {e2e_env.photos[0]}" in result.out
    qc_dir = e2e_env.data_dir / "qc" / str(attempt.id)
    assert qc_dir.is_dir() and any(qc_dir.iterdir())
    assert len(list(qc_dir.glob("*.jpg")) + list(qc_dir.glob("*.png"))) >= e2e_env.scene_seconds


# --- montagem --------------------------------------------------------------------------------


@full
def test_assemble_with_ken_burns_fallback_is_in_delivery_format(e2e_env: Env, kb: RunOutcome, px: RunOutcome, approvals: Approvals) -> None:
    out = e2e_env.data_dir / "final.mp4"
    start = time.monotonic()
    result = e2e_env.cli("assemble", "--run", str(px.run_id), "--fallback-run", str(kb.run_id), "--out", str(out))
    elapsed = time.monotonic() - start
    assert result.code == 0, result.err
    assert out.is_file()
    probe = ffprobe(out)
    assert_delivery_format(probe)
    expected = 12 * e2e_env.scene_seconds - 11 * 0.5  # Σ min(dur, alvo) - (n-1) x transição
    assert probe.duration_s == pytest.approx(expected, abs=0.3)
    # as cenas 1 a 3 vêm do parallax; as 4 a 12, do ken-burns (fallback); sem lacunas
    for scene in (1, 2, 3):
        assert re.search(rf"cena {scene:02d}: parallax-2\.5d \(run {px.run_id},", result.out), result.out
    for scene in range(4, 13):
        assert re.search(rf"cena {scene:02d}: ken-burns \(run {kb.run_id},", result.out), result.out
    assert "lacunas" not in result.out
    print(f"\n[T12] assemble 12 cenas: {elapsed:.1f} s, {probe.duration_s:.2f} s de vídeo")


@full
def test_assemble_without_fallback_lists_the_gaps(e2e_env: Env, px: RunOutcome, approvals: Approvals) -> None:
    out = e2e_env.data_dir / "so-parallax.mp4"
    result = e2e_env.cli("assemble", "--run", str(px.run_id), "--out", str(out))
    assert result.code == 0, result.err
    assert "lacunas (sem cena aprovada, omitidas): 4, 5, 6, 7, 8, 9, 10, 11, 12" in result.out
    probe = ffprobe(out)
    assert_delivery_format(probe)
    assert probe.duration_s == pytest.approx(3 * e2e_env.scene_seconds - 2 * 0.5, abs=0.3)


# --- relatório -------------------------------------------------------------------------------


@full
def test_report_has_both_methods_with_the_expected_numbers(e2e_env: Env, kb: RunOutcome, px: RunOutcome, approvals: Approvals) -> None:
    result = e2e_env.cli("report", "--format", "csv")
    assert result.code == 0, result.err
    rows = {r["method"]: r for r in csv.DictReader(io.StringIO(result.out))}
    assert sorted(rows) == ["ken-burns", "parallax-2.5d"]
    k, p = rows["ken-burns"], rows["parallax-2.5d"]
    # ken-burns: 12 cenas, 9 aprovadas (4 a 12), cena 1 refazer (aberta), cenas 2 e 3 aguardando QC (abertas)
    assert (k["scenes_total"], k["approved"], k["rejected"], k["open"], k["attempts"]) == ("12", "9", "0", "3", "12")
    assert float(k["cost_brl"]) == 0.0 and k["defects_4"] == "1" and float(k["attempts_per_approved"]) == pytest.approx(12 / 9)
    # parallax: 3 tentadas e 3 aprovadas; as outras 9 cenas do run seguem abertas
    assert (p["scenes_total"], p["approved"], p["rejected"], p["open"], p["attempts"]) == ("12", "3", "0", "9", "3")
    assert float(p["cost_brl"]) == 0.0 and float(p["attempts_per_approved"]) == 1.0
    assert float(p["mean_wall_time_s"]) > 0 and float(k["mean_wall_time_s"]) > 0


@full
def test_report_markdown_lists_both_methods(e2e_env: Env, kb: RunOutcome, px: RunOutcome, approvals: Approvals) -> None:
    result = e2e_env.cli("report")
    assert result.code == 0, result.err
    assert "| ken-burns |" in result.out and "| parallax-2.5d |" in result.out and "fake" not in result.out


# --- export ----------------------------------------------------------------------------------


@full
def test_export_has_no_photo_or_clip_path_and_no_secret(e2e_env: Env, kb: RunOutcome, px: RunOutcome, approvals: Approvals) -> None:
    # textos livres que NÃO podem sair: referência da autorização e nota do run, ambas com a chave e um caminho
    with e2e_env.connect() as conn:
        repo.add_authorization(conn, 10.0, f"STATE.md {e2e_env.secret} {e2e_env.photos[0]}")
        repo.add_note(conn, kb.run_id, f"termo em {e2e_env.photos[1]} chave {e2e_env.secret}")
    out_dir = e2e_env.data_dir / "export"
    result = e2e_env.cli("ledger", "export", "--out", str(out_dir))
    assert result.code == 0, result.err
    lines = assert_export_is_clean(e2e_env, out_dir, [str(e2e_env.photos[0]), str(e2e_env.photos[1])])
    assert len(lines["runs.jsonl"]) == 2 and len(lines["attempts.jsonl"]) == 15 and len(lines["scenes.jsonl"]) == 24
    assert len(lines["authorizations.jsonl"]) == 1
    # defeito exportado só com tipo, segundo e gravidade; o refazer do agente aparece estruturado
    refazer = [a for a in lines["attempts.jsonl"] if a["veredito"] == "refazer"]
    assert len(refazer) == 1 and refazer[0]["revisor"] == "agente"
    assert refazer[0]["defeitos"] == [{"tipo": 4, "segundo": 1.5, "gravidade": "bloqueante"}]
    approved = [a for a in lines["attempts.jsonl"] if a["veredito"] == "aprovada"]
    assert len(approved) == 12 and {a["revisor"] for a in approved} == {"humano"}
    assert {a["metodo"] for a in lines["attempts.jsonl"]} == {"ken-burns", "parallax-2.5d"}


@full
def test_no_cli_output_in_the_whole_flow_contains_the_secret(e2e_env: Env, kb: RunOutcome, px: RunOutcome, approvals: Approvals) -> None:
    assert len(e2e_env.log) >= 20
    for result in e2e_env.log:
        assert e2e_env.secret not in result.out and e2e_env.secret not in result.err
    assert all("Traceback" not in r.err for r in e2e_env.log)


# --- método pago fictício (`fake`) e trava de gasto -------------------------------------------


def seed_authorization(env: Env, brl: float = 350.0) -> None:
    with env.connect() as conn:  # connect() cria o banco; testes podem semear (authorize exige TTY)
        repo.add_authorization(conn, brl, "teste T12")


def test_fake_without_allow_paid_exits_3_and_writes_no_attempt(light_env: Env) -> None:
    seed_authorization(light_env)  # há saldo: quem bloqueia é a falta de --allow-paid
    result = light_env.cli("run", "--script", str(light_env.script), "--method", "fake", VERION_FAKE_MODE="ok")
    assert result.code == 3, result.err
    assert "--allow-paid" in result.err
    assert count(light_env, "attempts") == 0 and count(light_env, "runs") == 0
    assert budget(light_env)["gasto"] == "R$ 0,00"


def test_fake_with_allow_paid_but_no_authorization_exits_3_and_writes_no_attempt(light_env: Env) -> None:
    assert light_env.cli("db", "init").code == 0
    result = light_env.cli("run", "--script", str(light_env.script), "--method", "fake", "--allow-paid")
    assert result.code == 3, result.err
    assert count(light_env, "attempts") == 0
    assert budget(light_env)["autorizado"] == "R$ 0,00"


def test_fake_billed_error_exits_4_and_the_reservation_shows_in_budget(light_env: Env) -> None:
    seed_authorization(light_env, 350.0)
    result = light_env.cli(
        "run", "--script", str(light_env.script), "--method", "fake", "--allow-paid", VERION_FAKE_MODE="billed_error"
    )  # fmt: skip
    assert result.code == 4, result.err
    assert "ProviderError" in result.err and "run_id=" in result.err
    with light_env.connect() as conn:
        rows = repo.list_attempts(conn)
    # o primeiro erro encerra o run: 1 tentativa (cena 1, 3 s a R$ 1,00), liquidada como erro com a duração faturada
    assert [(r.scene_index, r.status, r.reserved_brl, r.cost_brl) for r in rows] == [(1, "error", 3.0, 3.0)]
    shown = budget(light_env)
    assert (shown["autorizado"], shown["gasto"], shown["saldo"], shown["pending"]) == ("R$ 350,00", "R$ 3,00", "R$ 347,00", "R$ 0,00")
    assert "(inclui reservas pending: R$ 0,00)" in light_env.cli("budget", "show").out


def test_fake_ok_spends_per_second_and_is_omitted_from_export_and_report(light_env: Env) -> None:
    seed_authorization(light_env)
    result = light_env.cli(
        "run", "--script", str(light_env.script), "--method", "fake", "--allow-paid", "--scenes", "1,2", VERION_FAKE_MODE="ok"
    )  # fmt: skip
    assert result.code == 0, result.err
    assert "2 tentada(s), 2 ok, 0 erro(s), custo R$ 6,00" in result.out
    assert budget(light_env)["saldo"] == "R$ 344,00"
    export = light_env.cli("ledger", "export", "--out", str(light_env.data_dir / "export"))
    assert export.code == 0 and "1 run fake omitido(s)" in export.out
    assert (light_env.data_dir / "export" / "attempts.jsonl").read_text() == ""
    assert "fake" not in light_env.cli("report").out


def test_fake_dry_run_reserves_nothing(light_env: Env) -> None:
    result = light_env.cli("run", "--script", str(light_env.script), "--method", "fake", "--dry-run")
    assert result.code == 0, result.err
    assert "total: R$ 36,00 em 12 cena(s)" in result.out
    assert not light_env.db_path.exists()  # --dry-run não cria o banco


def test_fake_does_not_need_photo_release_but_third_party_methods_do(light_env: Env) -> None:
    # o `fake` não envia foto a ninguém: roda sem photo_release_ref (ver teste de billed_error);
    # um método de terceiros sem a referência é recusado ANTES de qualquer reserva ou chamada
    result = light_env.cli("run", "--script", str(light_env.script), "--method", "gemini-omni-1.1-flash", "--allow-paid")
    assert result.code == 2, result.err
    assert "photo_release_ref" in result.err
    assert not light_env.db_path.exists()


# --- autorização -----------------------------------------------------------------------------


def test_authorize_without_tty_exits_2_and_writes_zero_rows(light_env: Env) -> None:
    assert light_env.cli("db", "init").code == 0
    for stdin_text in ("AUTORIZO R$ 350,00\n", ""):
        proc = subprocess.run(  # stdin é um pipe, não um terminal: mesmo com a frase certa, nada é gravado
            [sys.executable, "-m", "verion.cli", "budget", "authorize", "--brl", "350", "--ref", "STATE.md teste"],
            input=stdin_text, capture_output=True, text=True, env=light_env.environ(), timeout=60, check=False,
        )  # fmt: skip
        assert proc.returncode == 2, proc.stderr
        assert "TTY" in proc.stderr
    devnull = light_env.cli("budget", "authorize", "--brl", "350", "--ref", "STATE.md teste")
    assert devnull.code == 2 and "TTY" in devnull.err
    assert count(light_env, "authorizations") == 0
    assert budget(light_env)["autorizado"] == "R$ 0,00"


def test_authorize_above_the_monthly_cap_is_refused_even_before_the_tty_check(light_env: Env) -> None:
    result = light_env.cli("budget", "authorize", "--brl", "500.01", "--ref", "x")
    assert result.code == 2 and "teto" in result.err


# --- aprovação humana: recusas ----------------------------------------------------------------


@pytest.fixture
def fake_ok_attempt(light_env: Env) -> int:
    """Uma tentativa `ok` do `fake` (cena 1), para testar as recusas de aprovação sem renderizar nada."""
    seed_authorization(light_env)
    result = light_env.cli(
        "run", "--script", str(light_env.script), "--method", "fake", "--allow-paid", "--scenes", "1", VERION_FAKE_MODE="ok"
    )  # fmt: skip
    assert result.code == 0, result.err
    with light_env.connect() as conn:
        return repo.list_attempts(conn)[0].id


def test_human_approval_is_refused_without_tty(light_env: Env, fake_ok_attempt: int) -> None:
    result = light_env.cli("qc", "record", "--attempt", str(fake_ok_attempt), "--verdict", "aprovada", "--reviewer", "humano")
    assert result.code == 2 and "TTY" in result.err
    assert count(light_env, "qc_reviews") == 0


def test_human_approval_with_wrong_phrase_in_pty_is_refused(light_env: Env, fake_ok_attempt: int) -> None:
    argv = ("qc", "record", "--attempt", str(fake_ok_attempt), "--verdict", "aprovada", "--reviewer", "humano")
    for typed in (b"APROVO tentativa 999\n", b"aprovo tentativa %d\n" % fake_ok_attempt, b"\n"):
        result = light_env.cli_pty(argv, typed)
        assert result.code == 2, result.err
    assert count(light_env, "qc_reviews") == 0


def test_agent_can_never_record_an_approval(light_env: Env, fake_ok_attempt: int) -> None:
    result = light_env.cli("qc", "record", "--attempt", str(fake_ok_attempt), "--verdict", "aprovada", "--reviewer", "agente")
    assert result.code == 2, result.err
    assert count(light_env, "qc_reviews") == 0


# --- fumaça com render real (roda sempre; o pipeline completo é opt-in) --------------------------


def assert_export_is_clean(env: Env, out_dir: Path, secrets_and_paths: list[str]) -> dict[str, list[dict[str, object]]]:
    names = ["runs.jsonl", "attempts.jsonl", "scenes.jsonl", "authorizations.jsonl"]
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(names)
    texts = {n: (out_dir / n).read_text(encoding="utf-8") for n in names}
    forbidden = [
        *secrets_and_paths, env.secret, "AIza", str(env.root), str(env.data_dir), str(env.script),
        "foto-", ".jpg", ".mp4", ".depth", "clip", "photo", "termo", "STATE.md", "quina",
    ]  # fmt: skip
    for name, text in texts.items():
        for needle in forbidden:
            assert needle not in text, f"{name} contém {needle!r}"
    return {n: [json.loads(line) for line in t.splitlines()] for n, t in texts.items()}


def test_smoke_real_render_approve_assemble_with_fallback_report_export(light_env: Env) -> None:
    """Fluxo real em miniatura: 2 runs de ken-burns (1 cena cada), aprovação em pty, montagem com fallback."""
    env = light_env
    start = time.monotonic()
    first = env.cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "1")
    second = env.cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "2")
    assert (first.code, second.code) == (0, 0), (first.err, second.err)
    with env.connect() as conn:
        runs = repo.list_runs(conn)
        rows = [repo.list_attempts(conn, run_id=r.id)[0] for r in runs]
    assert len(runs) == 2 and [(r.scene_index, r.status) for r in rows] == [(1, "ok"), (2, "ok")]
    for row in rows:
        probe = ffprobe(Path(row.clip_path or ""))
        assert_delivery_format(probe)
        assert probe.duration_s == pytest.approx(env.scene_seconds, abs=0.1)

    for row in rows:  # aprovação humana: só com TTY e a frase exata
        result = approve_in_pty(env, row.id)
        assert result.code == 0, result.err

    out = env.data_dir / "final.mp4"
    result = env.cli("assemble", "--run", str(runs[0].id), "--fallback-run", str(runs[1].id), "--out", str(out))
    assert result.code == 0, result.err
    assert re.search(rf"cena 01: ken-burns \(run {runs[0].id},", result.out)
    assert re.search(rf"cena 02: ken-burns \(run {runs[1].id},", result.out)
    assert "lacunas (sem cena aprovada, omitidas): 3, 4, 5, 6, 7, 8, 9, 10, 11, 12" in result.out
    probe = ffprobe(out)
    assert_delivery_format(probe)
    assert probe.duration_s == pytest.approx(2 * env.scene_seconds - 0.5, abs=0.3)

    report = {r["method"]: r for r in csv.DictReader(io.StringIO(env.cli("report", "--format", "csv").out))}
    assert list(report) == ["ken-burns"]
    assert (report["ken-burns"]["scenes_total"], report["ken-burns"]["approved"], report["ken-burns"]["attempts"]) == ("24", "2", "2")

    with env.connect() as conn:
        repo.add_authorization(conn, 10.0, f"STATE.md {env.secret} {env.photos[0]}")
        repo.add_note(conn, runs[0].id, f"termo em {env.photos[1]} chave {env.secret}")
    export = env.cli("ledger", "export", "--out", str(env.data_dir / "export"))
    assert export.code == 0, export.err
    lines = assert_export_is_clean(env, env.data_dir / "export", [str(env.photos[0]), str(env.photos[1])])
    assert len(lines["attempts.jsonl"]) == 2 and {a["veredito"] for a in lines["attempts.jsonl"]} == {"aprovada"}
    assert all(env.secret not in r.out and env.secret not in r.err and "Traceback" not in r.err for r in env.log)
    print(f"\n[T12] fumaça com render real: {time.monotonic() - start:.1f} s")


# --- falhas de entrada na mídia real ---------------------------------------------------------


def test_parallax_without_depth_map_exits_2_and_does_not_invent_depth(light_env: Env) -> None:
    env = light_env  # fotos sem .depth.png
    result = env.cli("run", "--script", str(env.script), "--method", "parallax-2.5d", "--scenes", "1")
    assert result.code == 2, result.err
    assert "profundidade" in result.err.lower() or "depth" in result.err.lower()
    with env.connect() as conn:
        rows = repo.list_attempts(conn)
    assert all(r.status == "error" and r.clip_path is None for r in rows)
