"""Testes da CLI ``verion`` (spec seção 4, T4). Sem rede, sem chamada paga, sem ffmpeg.

Os testes chamam ``cli.main(argv)`` em processo e leem stdout/stderr com ``capsys``. A mídia é mockada
(``qc_frames.extract`` e ``assembly.assemble`` trocados por dublês). Preços e chaves são FICTÍCIOS. Só os testes
de autorização com pseudo-TTY rodam em subprocesso, porque a prova exige um TTY de verdade.
"""

from __future__ import annotations

import io
import os
import pty
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from verion import budget, cli
from verion.config import PriceTable
from verion.errors import FfmpegError, ProviderError
from verion.ledger import repo
from verion.local import assembly, qc_frames
from verion.providers import registry
from verion.providers.base import (
    CostEstimate,
    SceneGenerator,
    SceneRequest,
    SceneResult,
)
from verion.providers.fake import FakeAdapter

PRICES_TOML = """\
fx_usd_brl = 5.0

[methods."ken-burns"]
unit_kind = "none"
price_per_unit = 0.0
currency = "BRL"
source = "fictício (testes)"

[methods."parallax-2.5d"]
unit_kind = "none"
price_per_unit = 0.0
currency = "BRL"
source = "fictício (testes)"

[methods."veo-3.1-fast"]
unit_kind = "video_seconds"
price_per_unit = 0.10
currency = "USD"
source = "fictício (testes)"

[methods."gemini-omni-1.1-flash"]
unit_kind = "video_seconds"
price_per_unit = 0.10
currency = "USD"
source = "fictício (testes)"

[methods.fake]
unit_kind = "video_seconds"
price_per_unit = 1.0
currency = "BRL"
source = "fictício (testes)"
"""


# --- ambiente e dublês ----------------------------------------------------------------------


@dataclass(frozen=True)
class Env:
    root: Path
    data_dir: Path
    prices: Path
    script: Path

    @property
    def db(self) -> Path:
        return self.data_dir / "verion.db"


def write_script(
    root: Path,
    name: str = "roteiro.toml",
    property_id: str = "casa-01",
    release_ref: str | None = "termo-001",
) -> Path:
    photos = root / f"fotos-{name}"
    photos.mkdir(exist_ok=True)
    blocks = [f'property_id = "{property_id}"\n']
    if release_ref is not None:
        blocks.append(f'photo_release_ref = "{release_ref}"\n')
    for i in range(1, 13):
        (photos / f"{i:02d}.jpg").write_bytes(b"foto-ficticia")
        blocks.append(
            f'[[scenes]]\nindex = {i}\nphoto = "{photos.name}/{i:02d}.jpg"\nroom = "sala"\n'
            f'camera = "push-in"\nduration_s = 5.0\nprompt = "Push-in lento. Não adicionar objetos."\n'
        )
    path = root / name
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    prices = tmp_path / "prices.toml"
    prices.write_text(PRICES_TOML, encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("VERION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VERION_PRICES", str(prices))
    monkeypatch.delenv("VERION_FAKE_MODE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return Env(tmp_path, data_dir, prices, write_script(tmp_path))


@pytest.fixture
def run_cli(capsys: pytest.CaptureFixture[str]) -> Callable[..., tuple[int, str, str]]:
    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def db(env: Env) -> Iterator[sqlite3.Connection]:
    conn = repo.connect(env.db)
    try:
        yield conn
    finally:
        conn.close()


def authorize(db: sqlite3.Connection, brl: float) -> None:
    repo.add_authorization(db, brl, "teste")


class StubFree:
    """Método grátis sem mídia: grava bytes. Substitui o ``ken-burns`` real (que chamaria o ffmpeg)."""

    method = "ken-burns"
    provider = "local"
    model = "stub"

    def __init__(self, prices: Any, client: object | None = None) -> None:
        self.prices = prices

    def estimate(self, scene: Any) -> CostEstimate:
        return CostEstimate(units=0.0, unit_kind="none")

    def generate(self, req: SceneRequest, token: Any) -> SceneResult:
        token.consume(self.method)
        req.out_path.write_bytes(b"clipe-ficticio")
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


class InterruptingFake(FakeAdapter):
    def generate(self, req: SceneRequest, token: Any) -> SceneResult:
        token.consume(self.method)
        raise KeyboardInterrupt


@pytest.fixture
def stub_free(monkeypatch: pytest.MonkeyPatch) -> None:
    original = registry.create

    def create(method: str, prices: Any, client: object | None = None) -> SceneGenerator:
        if method == "ken-burns":
            return StubFree(prices)  # type: ignore[return-value]  # dublê estrutural
        return original(method, prices, client)

    monkeypatch.setattr(registry, "create", create)


def attempts(db: sqlite3.Connection) -> list[repo.AttemptRow]:
    return repo.list_attempts(db)


def auth_rows(data_dir: Path) -> int:
    path = data_dir / "verion.db"
    if not path.exists():
        return 0
    raw = sqlite3.connect(path)
    try:
        return int(raw.execute("SELECT COUNT(*) FROM authorizations").fetchone()[0])
    finally:
        raw.close()


def approve(db: sqlite3.Connection, attempt_id: int) -> None:
    repo.record_qc(db, attempt_id, verdict="aprovada", reviewer="humano", defects=[])


# --- db init e erros de banco ---------------------------------------------------------------


def test_db_init_creates_database_and_is_idempotent(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    assert not env.db.exists()
    code, out, _ = run_cli("db", "init")
    assert code == 0 and env.db.is_file()
    assert str(env.db) in out
    raw = sqlite3.connect(env.db)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        raw.close()
    assert run_cli("db", "init")[0] == 0


def test_schema_mismatch_is_exit_2(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    env.data_dir.mkdir()
    raw = sqlite3.connect(env.db)
    raw.execute("PRAGMA user_version = 7")
    raw.close()
    code, _, err = run_cli("budget", "show")
    assert code == 2 and "user_version" in err


def test_data_dir_defaults_to_data_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.delenv("VERION_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert run_cli("db", "init")[0] == 0
    assert (tmp_path / "data" / "verion.db").is_file()


# --- códigos de saída: um teste por código --------------------------------------------------


def test_exit_0_success(env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    authorize(db, 350.0)
    code, out, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1")
    assert (code, err) == (0, "")
    assert "1 ok" in out


def test_exit_2_input_errors(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    assert run_cli("run", "--script", str(env.script), "--method", "inexistente")[0] == 2
    assert run_cli("run", "--script", str(env.root / "nao-existe.toml"), "--method", "fake")[0] == 2
    assert run_cli("run", "--method", "fake")[0] == 2  # argparse: --script ausente
    assert run_cli("nao-existe")[0] == 2  # argparse: comando desconhecido
    assert run_cli()[0] == 2  # sem comando


def test_exit_2_missing_prices_file(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setenv("VERION_PRICES", str(env.root / "sem-precos.toml"))
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run")
    assert code == 2 and "preços" in err


def test_exit_3_paid_method_without_allow_paid(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake")
    assert code == 3 and "--allow-paid" in err
    assert repo.list_runs(db) == [] and attempts(db) == []  # nada gravado: nem run vazio


def test_exit_3_budget_exceeded_reports_run_id(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 3.0)  # a reserva da cena 1 é R$ 5,00
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 3
    assert "run_id=1" in err and "BudgetExceeded" in err
    assert attempts(db) == []


def test_exit_4_provider_error_reports_run_and_keeps_reservation(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    authorize(db, 350.0)
    monkeypatch.setenv("VERION_FAKE_MODE", "ok,billed_error")
    code, out, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 4
    assert "run_id=1" in err and "ProviderError" in err
    assert "1 ok" in out and "1 erro" in out  # estado do run no ledger, mesmo com a falha
    rows = attempts(db)
    assert [(r.scene_index, r.status, r.cost_brl) for r in rows] == [(1, "ok", 5.0), (2, "error", 5.0)]
    code, out, _ = run_cli("budget", "show")
    assert code == 0 and "gasto: R$ 10,00" in out


def test_exit_4_transient_error_still_reserves(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    authorize(db, 350.0)
    monkeypatch.setenv("VERION_FAKE_MODE", "transient")
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 4 and "run_id=1" in err
    assert [(r.status, r.cost_brl) for r in attempts(db)] == [("error", 5.0)]


def test_exit_5_when_every_requested_scene_is_skipped(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    args = ("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert run_cli(*args, "--scenes", "1")[0] == 0  # cena 1 fica aguardando QC
    code, out, err = run_cli(*args, "--run", "1", "--scenes", "1")
    assert code == 5
    assert "cena 1" in err and "QC" in err
    assert "0 tentada" in out


def test_exit_0_when_some_scene_is_skipped_but_one_was_attempted(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    args = ("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert run_cli(*args, "--scenes", "1")[0] == 0
    code, out, _ = run_cli(*args, "--run", "1", "--scenes", "1,2")
    assert code == 0
    assert "cena 1 pulada" in out and "1 tentada" in out


def test_exit_6_ffmpeg_error(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    authorize(db, 350.0)
    run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1")

    def boom(clip: Path, out_dir: Path) -> qc_frames.QcFrames:
        raise FfmpegError("ffmpeg quebrou", stderr="detalhe")

    monkeypatch.setattr(qc_frames, "extract", boom)
    code, _, err = run_cli("qc", "frames", "--attempt", "1")
    assert code == 6 and "ffmpeg quebrou" in err


def test_exit_1_unexpected_error_has_code_and_no_raw_traceback(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    def boom(method: str, prices: Any, client: object | None = None) -> SceneGenerator:
        raise RuntimeError("bug inesperado")

    monkeypatch.setattr(registry, "create", boom)
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run")
    assert code == 1
    assert "erro interno" in err and "RuntimeError" in err and "bug inesperado" in err
    assert "Traceback" not in err

    code, _, err = run_cli("--debug", "run", "--script", str(env.script), "--method", "fake", "--dry-run")
    assert code == 1 and "Traceback" in err


def test_unexpected_error_after_reserve_still_reports_run_id(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    authorize(db, 350.0)

    class Broken(FakeAdapter):
        def generate(self, req: SceneRequest, token: Any) -> SceneResult:
            token.consume(self.method)
            raise ZeroDivisionError("bug no adaptador")

    monkeypatch.setattr(registry, "create", lambda method, prices, client=None: Broken(prices))
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 1 and "run_id=1" in err and "erro interno" in err
    assert [(r.status, r.cost_brl) for r in attempts(db)] == [("error", 5.0)]  # a reserva foi liquidada


def test_keyboard_interrupt_settles_reservation_and_exits_130(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    authorize(db, 350.0)
    monkeypatch.setattr(registry, "create", lambda method, prices, client=None: InterruptingFake(prices))
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 130 and "run_id=1" in err
    assert "reservas liquidadas" in err and "KeyboardInterrupt:" not in err
    assert [(r.status, r.cost_brl) for r in attempts(db)] == [("error", 5.0)]


# --- budget authorize ------------------------------------------------------------------------


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def authorize_args(brl: str = "350") -> tuple[str, ...]:
    return ("budget", "authorize", "--brl", brl, "--ref", "STATE.md 2026-11-02")


def test_authorize_without_tty_is_refused_and_writes_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("AUTORIZO R$ 350,00\n"))  # frase certa, mas sem TTY
    code, _, err = run_cli(*authorize_args())
    assert code == 2 and "TTY" in err
    assert auth_rows(env.data_dir) == 0


def test_authorize_with_wrong_phrase_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    for wrong in ("sim\n", "AUTORIZO R$ 350\n", "autorizo r$ 350,00\n", "AUTORIZO R$ 351,00\n", "\n", ""):
        monkeypatch.setattr(sys, "stdin", FakeTTY(wrong))
        code, _, err = run_cli(*authorize_args())
        assert code == 2, wrong
        assert "AUTORIZO R$ 350,00" in err or "frase" in err
    assert auth_rows(env.data_dir) == 0


def test_authorize_with_tty_and_exact_phrase_writes_one_row(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY("AUTORIZO R$ 350,00\n"))
    code, out, _ = run_cli(*authorize_args())
    assert code == 0 and "AUTORIZO R$ 350,00" in out  # o prompt mostra a frase esperada
    assert auth_rows(env.data_dir) == 1
    code, out, _ = run_cli("budget", "show")
    assert "autorizado: R$ 350,00" in out and "saldo: R$ 350,00" in out and "STATE.md 2026-11-02" in out


def test_authorize_phrase_uses_two_decimals_and_comma(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY("AUTORIZO R$ 12,50\n"))
    assert run_cli(*authorize_args("12.5"))[0] == 0
    monkeypatch.setattr(sys, "stdin", FakeTTY("AUTORIZO R$ 7,00\n"))
    assert run_cli(*authorize_args("7,00"))[0] == 0  # vírgula decimal aceita
    assert auth_rows(env.data_dir) == 2


@pytest.mark.parametrize(
    "brl",
    ["0", "-5", "abc", "350.123", "nan", "inf", "", "1e30", "1E+400", "１０", "1e3", "0x10", "501", "10 0", "999999999999"],
)
def test_authorize_rejects_invalid_amounts(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]], brl: str
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY("AUTORIZO R$ 350,00\n"))
    code, _, err = run_cli(*authorize_args(brl))
    assert code == 2
    assert "Traceback" not in err and "erro interno" not in err  # nunca "bug"
    assert auth_rows(env.data_dir) == 0


def test_authorize_requires_nonblank_reference(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY("AUTORIZO R$ 350,00\n"))
    assert run_cli("budget", "authorize", "--brl", "350", "--ref", "   ")[0] == 2
    assert auth_rows(env.data_dir) == 0


def _subprocess_env(env: Env) -> dict[str, str]:
    full = os.environ.copy()
    full["VERION_DATA_DIR"] = str(env.data_dir)
    full["VERION_PRICES"] = str(env.prices)
    return full


def test_authorize_in_real_pty_with_exact_phrase_writes_one_row(env: Env) -> None:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "verion.cli", *authorize_args()],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env(env),
        text=True,
    )
    os.close(slave)
    os.write(master, b"AUTORIZO R$ 350,00\n")
    try:
        _, err = proc.communicate(timeout=60)
    finally:
        os.close(master)
    assert proc.returncode == 0, err
    assert auth_rows(env.data_dir) == 1


def test_authorize_in_real_pty_with_wrong_phrase_writes_nothing(env: Env) -> None:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "verion.cli", *authorize_args()],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env(env),
        text=True,
    )
    os.close(slave)
    os.write(master, b"AUTORIZO R$ 349,00\n")
    try:
        _, err = proc.communicate(timeout=60)
    finally:
        os.close(master)
    assert proc.returncode == 2, err
    assert auth_rows(env.data_dir) == 0


def test_authorize_with_stdin_closed_in_real_process_is_refused(env: Env) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "verion.cli", *authorize_args()],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_subprocess_env(env),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2 and "TTY" in proc.stderr
    assert auth_rows(env.data_dir) == 0


# --- budget show -----------------------------------------------------------------------------


def test_budget_show_includes_pending_reservations(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    from verion.config import load_prices

    prices = load_prices(env.prices)
    authorize(db, 350.0)
    run_id = repo.create_run(db, property_id="casa-01", method="fake", script_path="r", script_sha256="x")
    budget.reserve(
        db, prices, run_id=run_id, scene_index=1, target_s=5.0, method="fake", provider="fake", model="m",
        units=5.0, unit_kind="video_seconds", allow_paid=True,
    )  # fmt: skip
    code, out, _ = run_cli("budget", "show")
    assert code == 0
    assert "autorizado: R$ 350,00" in out
    assert "gasto: R$ 5,00" in out and "pending" in out and "R$ 5,00" in out
    assert "saldo: R$ 345,00" in out


def test_budget_show_after_run_matches_arithmetic(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    code, out, _ = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 0 and "12 ok" in out and "custo R$ 60,00" in out
    out = run_cli("budget", "show")[1]
    assert "gasto: R$ 60,00" in out and "saldo: R$ 290,00" in out


# --- run -------------------------------------------------------------------------------------


def test_run_selects_scenes_and_writes_clips_under_data_dir(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    code, _, _ = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1,3")
    assert code == 0
    rows = attempts(db)
    assert [r.scene_index for r in rows] == [1, 3]
    assert all(Path(r.clip_path or "").is_absolute() for r in rows)
    assert all(Path(r.clip_path or "").is_relative_to(env.data_dir) for r in rows)
    assert all(Path(r.clip_path or "").is_file() for r in rows)


def test_run_continue_existing_run(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    base = ("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    run_cli(*base, "--scenes", "1")
    code, out, _ = run_cli(*base, "--run", "1", "--scenes", "2")
    assert code == 0 and "run 1" in out
    assert {(r.run_id, r.scene_index) for r in attempts(db)} == {(1, 1), (1, 2)}
    assert len(repo.list_runs(db)) == 1


def test_run_with_other_method_for_existing_run_is_exit_2(
    env: Env, db: sqlite3.Connection, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1")
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--run", "1")
    assert code == 2 and "fake" in err


@pytest.mark.parametrize("scenes", ["", "a", "1,,2", "0", "13", "1,1"])
def test_run_rejects_invalid_scene_selection(
    env: Env, run_cli: Callable[..., tuple[int, str, str]], scenes: str
) -> None:
    code, _, _ = run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run", "--scenes", scenes)
    assert code == 2


def test_run_free_method_needs_no_allow_paid_nor_authorization(
    env: Env, db: sqlite3.Connection, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, out, _ = run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "1,2")
    assert code == 0 and "2 ok" in out and "custo R$ 0,00" in out
    assert [r.cost_brl for r in attempts(db)] == [0.0, 0.0]


def test_run_dry_run_shows_units_and_cost_without_reserving(
    env: Env, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, out, _ = run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run")
    assert code == 0  # sem --allow-paid e sem autorização
    scene_lines = [line for line in out.splitlines() if line.startswith("cena ")]
    assert len(scene_lines) == 12
    assert "5 video_seconds" in scene_lines[0] and "R$ 5,00" in scene_lines[0]
    assert "total: R$ 60,00" in out
    assert not env.db.exists()  # nada reservado, nem o banco foi criado


def test_run_dry_run_respects_scene_selection(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    code, out, _ = run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run", "--scenes", "2,5")
    assert code == 0 and "total: R$ 10,00" in out
    assert [line[:8] for line in out.splitlines() if line.startswith("cena ")] == ["cena 02:", "cena 05:"]


def test_run_creates_exactly_one_adapter_and_never_injects_a_client(
    env: Env, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    calls: list[tuple[Any, ...]] = []
    original = registry.create

    def spy(*args: Any, **kwargs: Any) -> SceneGenerator:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(registry, "create", spy)
    code, _, _ = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 0
    assert len(calls) == 1  # um adaptador por run: a sequência do fake vale por instância
    args, kwargs = calls[0]
    assert args[0] == "fake" and len(args) == 2 and kwargs == {}  # sem client injetado


def test_paid_real_adapter_is_built_without_reading_or_printing_the_api_key(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "CHAVE-FICTICIA-NAO-VAZAR")
    code, out, err = run_cli(
        "run", "--script", str(env.script), "--method", "gemini-omni-1.1-flash", "--dry-run", "--scenes", "1"
    )
    assert code == 0, err
    assert "CHAVE-FICTICIA-NAO-VAZAR" not in out + err
    assert "total: R$ 2,50" in out  # 5 s x US$ 0,10 x 5,0
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" not in source and "API_KEY" not in source


# --- ledger settle ---------------------------------------------------------------------------


@pytest.fixture
def orphan(env: Env, db: sqlite3.Connection) -> int:
    """Reserva ``pending`` órfã de R$ 5,00 (processo que morreu antes de liquidar)."""
    from verion.config import load_prices

    authorize(db, 350.0)
    run_id = repo.create_run(db, property_id="casa-01", method="fake", script_path="r", script_sha256="x")
    token = budget.reserve(
        db, load_prices(env.prices), run_id=run_id, scene_index=1, target_s=5.0, method="fake", provider="fake",
        model="m", units=5.0, unit_kind="video_seconds", allow_paid=True,
    )  # fmt: skip
    return token.attempt_id


def test_settle_defaults_to_the_reservation(
    orphan: int, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, out, _ = run_cli("ledger", "settle", "--attempt", str(orphan), "--note", "processo morreu")
    assert code == 0 and "R$ 5,00" in out
    row = repo.get_attempt(db, orphan)
    assert row is not None and (row.status, row.cost_brl) == ("error", 5.0)
    assert "processo morreu" in (row.error or "")


def test_settle_never_reduces_the_spend(
    orphan: int, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, out, _ = run_cli("ledger", "settle", "--attempt", str(orphan), "--cost-brl", "2", "--note", "n")
    assert code == 0 and "reserva" in out
    row = repo.get_attempt(db, orphan)
    assert row is not None and row.cost_brl == 5.0 and repo.spent_brl(db) == 5.0


def test_settle_accepts_higher_cost(
    orphan: int, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    assert run_cli("ledger", "settle", "--attempt", str(orphan), "--cost-brl", "7.5", "--note", "n")[0] == 0
    row = repo.get_attempt(db, orphan)
    assert row is not None and row.cost_brl == 7.5


def test_settle_errors_are_exit_2(orphan: int, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    assert run_cli("ledger", "settle", "--attempt", "999", "--note", "n")[0] == 2  # inexistente
    assert run_cli("ledger", "settle", "--attempt", str(orphan))[0] == 2  # sem --note (argparse)
    assert run_cli("ledger", "settle", "--attempt", str(orphan), "--note", "  ")[0] == 2
    assert run_cli("ledger", "settle", "--attempt", str(orphan), "--cost-brl", "-1", "--note", "n")[0] == 2
    assert run_cli("ledger", "settle", "--attempt", str(orphan), "--note", "n")[0] == 0
    assert run_cli("ledger", "settle", "--attempt", str(orphan), "--note", "n")[0] == 2  # já liquidada


# --- qc frames -------------------------------------------------------------------------------


def test_qc_frames_calls_extract_on_the_attempt_clip(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    authorize(db, 350.0)
    run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "3")
    (attempt,) = attempts(db)
    seen: list[tuple[Path, Path]] = []

    def fake_extract(clip: Path, out_dir: Path) -> qc_frames.QcFrames:
        seen.append((clip, out_dir))
        return qc_frames.QcFrames([out_dir / "qc_001.jpg", out_dir / "qc_002.jpg"], out_dir / "sheet.jpg", out_dir / "last.jpg")

    monkeypatch.setattr(qc_frames, "extract", fake_extract)
    code, out, _ = run_cli("qc", "frames", "--attempt", str(attempt.id))
    assert code == 0
    assert seen == [(Path(attempt.clip_path or ""), env.data_dir / "qc" / str(attempt.id))]
    assert "2 quadros" in out
    assert str(env.data_dir / "qc" / str(attempt.id) / "sheet.jpg") in out
    assert str(env.data_dir / "qc" / str(attempt.id) / "last.jpg") in out
    photo = env.root / "fotos-roteiro.toml" / "03.jpg"
    assert f"foto de origem da cena 3: {photo}" in out


def test_qc_frames_requires_an_ok_attempt(
    env: Env,
    orphan: int,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    monkeypatch.setattr(qc_frames, "extract", lambda clip, out_dir: pytest.fail("não deveria extrair"))
    assert run_cli("qc", "frames", "--attempt", str(orphan))[0] == 2  # pending: sem clipe
    assert run_cli("qc", "frames", "--attempt", "999")[0] == 2


# --- qc record -------------------------------------------------------------------------------


@pytest.fixture
def ok_attempt(env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]) -> int:
    authorize(db, 350.0)
    assert run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1")[0] == 0
    (row,) = attempts(db)
    return row.id


def test_qc_record_approved_by_human(
    ok_attempt: int,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY(f"APROVO tentativa {ok_attempt}\n"))
    code, out, _ = run_cli(
        "qc", "record", "--attempt", str(ok_attempt), "--verdict", "aprovada", "--reviewer", "humano"
    )
    assert code == 0 and "aprovada" in out
    qc = repo.get_qc(db, ok_attempt)
    assert qc is not None and (qc.verdict, qc.reviewer, qc.defects) == ("aprovada", "humano", ())
    assert repo.scene_status(db, 1, 1) == ("aprovada", None)


def test_qc_record_refuses_approval_by_agent(
    ok_attempt: int, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, _, err = run_cli(
        "qc", "record", "--attempt", str(ok_attempt), "--verdict", "aprovada", "--reviewer", "agente"
    )
    assert code == 2 and "humano" in err
    assert repo.get_qc(db, ok_attempt) is None


def test_qc_record_parses_defects(
    ok_attempt: int, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, _, _ = run_cli(
        "qc", "record", "--attempt", str(ok_attempt), "--verdict", "refazer", "--reviewer", "humano",
        "--defect", "2:3.0:janela ganhou folha: nova", "--defect", "leve:1.5:brilho",
    )  # fmt: skip
    assert code == 0
    qc = repo.get_qc(db, ok_attempt)
    assert qc is not None
    assert qc.defects == (
        repo.Defect(2, 3.0, "janela ganhou folha: nova"),  # a nota pode conter ":"
        repo.Defect("leve", 1.5, "brilho"),
    )


@pytest.mark.parametrize("defect", ["x:1:n", "2", "2:abc:n", "9:1:n", "0:1:n", "2:-1:n", ":1:n"])
def test_qc_record_rejects_malformed_defects(
    ok_attempt: int, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]], defect: str
) -> None:
    code, _, _ = run_cli(
        "qc", "record", "--attempt", str(ok_attempt), "--verdict", "refazer", "--reviewer", "humano",
        "--defect", defect,
    )  # fmt: skip
    assert code == 2
    assert repo.get_qc(db, ok_attempt) is None


def test_qc_record_rejects_unknown_attempt_and_bad_choices(
    ok_attempt: int, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    assert run_cli("qc", "record", "--attempt", "999", "--verdict", "aprovada", "--reviewer", "humano")[0] == 2
    assert run_cli("qc", "record", "--attempt", "1", "--verdict", "talvez", "--reviewer", "humano")[0] == 2
    assert run_cli("qc", "record", "--attempt", "1", "--verdict", "aprovada", "--reviewer", "robo")[0] == 2
    assert run_cli("qc", "record", "--attempt", "1", "--verdict", "refazer", "--reviewer", "humano")[0] == 2


# --- assemble --------------------------------------------------------------------------------


@dataclass
class AssembleSpy:
    calls: list[tuple[list[assembly.ClipSpec], Path, bool]]
    short_clips: tuple[int, ...] = ()
    error: Exception | None = None

    def __call__(
        self, clips: Any, out: Path, *, transition_s: float = 0.5, demo: bool = False
    ) -> assembly.AssemblyResult:
        self.calls.append((list(clips), out, demo))
        if self.error is not None:
            raise self.error
        return assembly.AssemblyResult(out=out, duration_s=17.5, short_clips=self.short_clips)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> AssembleSpy:
    assemble_spy = AssembleSpy(calls=[])
    monkeypatch.setattr(assembly, "assemble", assemble_spy)
    return assemble_spy


@pytest.fixture
def two_runs(
    env: Env, db: sqlite3.Connection, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    """Run 1 (fake): cenas 1, 2, 3 (tentativas 1-3), aprovadas 1 e 3 e refazer na 2. Run 2 (ken-burns): 2 e 4."""
    authorize(db, 350.0)
    run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1,2,3")
    approve(db, 1)
    repo.record_qc(db, 2, verdict="refazer", reviewer="humano", defects=[repo.Defect(2, 1.0, "x")])
    approve(db, 3)
    run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "2,4")
    approve(db, 4)  # run 2, cena 2
    approve(db, 5)  # run 2, cena 4


def test_assemble_uses_approved_scenes_in_order_with_fallback_for_gaps(
    env: Env, two_runs: None, db: sqlite3.Connection, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    out_file = env.data_dir / "saida" / "tour.mp4"
    spy.short_clips = (1, 3)  # índices base 0 da lista de clipes: posições 1 e 3 = cenas 2 e 4
    code, out, _ = run_cli(
        "assemble", "--run", "1", "--fallback-run", "2", "--demo", "--out", str(out_file)
    )
    assert code == 0
    ((clips, out_arg, demo),) = spy.calls
    assert out_arg == out_file and demo is True

    att = {a.id: a for a in attempts(db)}
    # cena 1 e 3 do run 1; cena 2 (refazer no run 1) e cena 4 vêm do fallback (run 2)
    assert [c.path for c in clips] == [Path(att[i].clip_path or "") for i in (1, 4, 3, 5)]
    assert all(c.target_s == 5.0 for c in clips)
    # short_clips (base 0) vira número de cena: índice 1 -> cena 2, índice 3 -> cena 4
    short_lines = [line for line in out.splitlines() if "mais curta" in line]
    assert len(short_lines) == 2
    assert "cena 2" in short_lines[0] and "cena 4" in short_lines[1]
    assert "lacunas" in out and "5, 6, 7, 8, 9, 10, 11, 12" in out
    assert "ken-burns" in out and "fake" in out  # método usado por cena


def test_assemble_without_fallback_omits_and_lists_gaps(
    env: Env, two_runs: None, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, out, _ = run_cli("assemble", "--run", "1", "--out", str(env.data_dir / "t.mp4"))
    assert code == 0
    ((clips, _, demo),) = spy.calls
    assert len(clips) == 2 and demo is False
    assert "lacunas" in out and "2, 4, 5" in out


def test_assemble_with_no_approved_scene_is_exit_2(
    env: Env, two_runs: None, db: sqlite3.Connection, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    run_id = repo.create_run(db, property_id="casa-01", method="fake", script_path="r", script_sha256="x")
    code, _, err = run_cli("assemble", "--run", str(run_id), "--out", str(env.data_dir / "t.mp4"))
    assert code == 2 and "aprovada" in err
    assert spy.calls == []


def test_assemble_validates_runs(
    env: Env, two_runs: None, db: sqlite3.Connection, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    out_file = str(env.data_dir / "t.mp4")
    assert run_cli("assemble", "--run", "99", "--out", out_file)[0] == 2
    assert run_cli("assemble", "--run", "1", "--fallback-run", "99", "--out", out_file)[0] == 2
    assert run_cli("assemble", "--run", "1", "--fallback-run", "1", "--out", out_file)[0] == 2
    other = repo.create_run(db, property_id="casa-02", method="ken-burns", script_path="r", script_sha256="y")
    code, _, err = run_cli("assemble", "--run", "1", "--fallback-run", str(other), "--out", out_file)
    assert code == 2 and "casa-02" in err  # fallback de outro imóvel
    assert spy.calls == []


def test_assemble_ffmpeg_failure_is_exit_6(
    env: Env, two_runs: None, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    spy.error = FfmpegError("xfade falhou")
    code, _, err = run_cli("assemble", "--run", "1", "--out", str(env.data_dir / "t.mp4"))
    assert code == 6 and "xfade falhou" in err


# --- report e export -------------------------------------------------------------------------


def test_report_command_prints_markdown_and_csv(
    env: Env, db: sqlite3.Connection, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "1,2")
    approve(db, 1)
    code, out, _ = run_cli("report")
    assert code == 0
    row = next(line for line in out.splitlines() if line.startswith("| ken-burns"))
    assert "| 12 | 1 | 0 | 11 | 2 |" in row  # cenas, aprovadas, rejeitadas, abertas, tentativas
    code, out, _ = run_cli("report", "--format", "csv")
    assert code == 0 and out.splitlines()[0].startswith("method,scenes_total")
    assert out.splitlines()[1].startswith("ken-burns,12,1,0,11,2,")
    assert run_cli("report", "--format", "xml")[0] == 2


def test_report_ignores_blocked_run_without_attempts(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    # sem autorização: o run é criado e a trava de gasto bloqueia a cena 1 (0 tentativas)
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "gemini-omni-1.1-flash", "--allow-paid")
    assert code == 3 and "run_id=1" in err
    assert len(repo.list_runs(db)) == 1 and attempts(db) == []
    code, out, _ = run_cli("report")
    assert code == 0 and "gemini-omni" not in out


def test_ledger_export_command(
    env: Env, db: sqlite3.Connection, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "1")
    run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1")
    out_dir = env.data_dir / "evidence"
    code, out, _ = run_cli("ledger", "export", "--out", str(out_dir))
    assert code == 0
    for name in ("runs.jsonl", "attempts.jsonl", "scenes.jsonl", "authorizations.jsonl"):
        assert (out_dir / name).is_file() and name in out
    assert "1 run fake omitido" in out
    assert "fake" not in (out_dir / "attempts.jsonl").read_text(encoding="utf-8")


def test_ledger_export_default_directory(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.chdir(env.root)
    assert run_cli("db", "init")[0] == 0
    code, _, _ = run_cli("ledger", "export")
    assert code == 0
    assert (env.root / "docs" / "engineering" / "evidence" / "fase0" / "attempts.jsonl").is_file()


def test_help_exits_0(run_cli: Callable[..., tuple[int, str, str]]) -> None:
    code, out, _ = run_cli("--help")
    assert code == 0 and "verion" in out
    for command in ("db", "budget", "run", "ledger", "qc", "assemble", "report"):
        assert command in out


# =============================================================================================
# Rodada final da T4 (revisões de código e de segurança): B1-B3, I1-I7, D1-D4
# =============================================================================================

COMMANDS_NEEDING_DB = [
    ("budget", "show"),
    ("report",),
    ("ledger", "export", "--out", "{data}/export"),
    ("ledger", "settle", "--attempt", "1", "--note", "n"),
    ("qc", "frames", "--attempt", "1"),
    ("qc", "record", "--attempt", "1", "--verdict", "refazer", "--reviewer", "humano", "--defect", "2:1:x"),
    ("assemble", "--run", "1", "--out", "{data}/t.mp4"),
]


def pty_run(env: Env, argv: tuple[str, ...], typed: bytes) -> tuple[int, str, str]:
    """Roda ``python -m verion.cli`` com um pseudo-TTY como stdin e ``typed`` já digitado."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "verion.cli", *argv],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env(env),
        text=True,
    )
    os.close(slave)
    os.write(master, typed)
    try:
        out, err = proc.communicate(timeout=60)
    finally:
        os.close(master)
    return proc.returncode, out, err


# --- B1: argumentos numéricos ---------------------------------------------------------------


@pytest.mark.parametrize("bad", ["99999999999999999999", "9223372036854775808", "0", "-1", "１２", "1e3", "1_0", "abc", ""])
@pytest.mark.parametrize(
    "template",
    [
        ("run", "--script", "{script}", "--method", "fake", "--dry-run", "--run", "{x}"),
        ("ledger", "settle", "--attempt", "{x}", "--note", "n"),
        ("qc", "frames", "--attempt", "{x}"),
        ("qc", "record", "--attempt", "{x}", "--verdict", "refazer", "--reviewer", "humano"),
        ("assemble", "--run", "{x}", "--out", "{data}/t.mp4"),
        ("assemble", "--run", "1", "--fallback-run", "{x}", "--out", "{data}/t.mp4"),
    ],
)
def test_ids_outside_1_to_2_63_are_usage_errors_not_bugs(
    env: Env, run_cli: Callable[..., tuple[int, str, str]], template: tuple[str, ...], bad: str
) -> None:
    argv = [a.format(x=bad, script=env.script, data=env.data_dir) for a in template]
    code, _, err = run_cli(*argv)
    assert code == 2
    assert "Traceback" not in err and "erro interno" not in err and "OverflowError" not in err


def test_largest_valid_id_is_accepted_by_the_parser(env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    code, _, err = run_cli("ledger", "settle", "--attempt", str(2**63 - 1), "--note", "n")
    assert code == 2 and "inexistente" in err  # passou do parser; falhou por não existir


@pytest.mark.parametrize("scenes", ["１", "1_0", "+1", "1e0"])
def test_scenes_accept_only_ascii_digits(env: Env, run_cli: Callable[..., tuple[int, str, str]], scenes: str) -> None:
    assert run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run", "--scenes", scenes)[0] == 2


@pytest.mark.parametrize("cost", ["1e30", "nan", "inf", "-1", "１", "1_0"])
def test_settle_cost_rejects_exponents_and_non_ascii(
    orphan: int, run_cli: Callable[..., tuple[int, str, str]], cost: str
) -> None:
    assert run_cli("ledger", "settle", "--attempt", str(orphan), "--cost-brl", cost, "--note", "n")[0] == 2


def test_unexpected_exception_while_parsing_is_a_usage_error(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    def overflowing(text: str) -> int:
        raise OverflowError("int too large")

    parser = argparse.ArgumentParser(prog="verion")
    parser.add_argument("--n", type=overflowing)
    monkeypatch.setattr(cli, "_build_parser", lambda: parser)
    assert cli.main(["--n", "1"]) == 2
    assert "OverflowError" in capsys.readouterr().err


# --- B2: a chave de API nunca sai ------------------------------------------------------------

SYNTHETIC_LITERAL = "chave-sintetica-literal-0123456789"
SYNTHETIC_AIZA = "AIzaSyDUMMY-synthetic-key_0123456789abcd"


def leaky_message() -> str:
    return (
        f"HTTP 403 em https://api.example/v1/models?key={SYNTHETIC_AIZA}&alt=sse "
        f"headers x-goog-api-key: {SYNTHETIC_LITERAL} Authorization: Bearer {SYNTHETIC_LITERAL} "
        f"literal {SYNTHETIC_LITERAL}"
    )


def assert_no_key(text: str) -> None:
    for secret in (SYNTHETIC_LITERAL, SYNTHETIC_AIZA, "AIzaSyDUMMY"):
        assert secret not in text, secret


def test_api_key_in_provider_error_reaches_neither_stderr_nor_database_nor_jsonl(
    env: Env,
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[..., tuple[int, str, str]],
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_LITERAL)
    authorize(db, 350.0)

    class Leaky(FakeAdapter):
        def generate(self, req: SceneRequest, token: Any) -> SceneResult:
            token.consume(self.method)
            raise ProviderError(leaky_message(), transient=False)

    monkeypatch.setattr(registry, "create", lambda method, prices, client=None: Leaky(prices))
    code, out, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 4
    assert_no_key(out + err)
    assert "[REDACTED]" in err

    (row,) = attempts(db)
    assert row.error is not None
    assert_no_key(row.error)
    for file in env.data_dir.glob("verion.db*"):  # banco, -wal e -shm, byte a byte
        assert SYNTHETIC_LITERAL.encode() not in file.read_bytes(), file.name
        assert SYNTHETIC_AIZA.encode() not in file.read_bytes(), file.name

    export_dir = env.data_dir / "export"
    assert run_cli("ledger", "export", "--out", str(export_dir))[0] == 0
    assert_no_key("".join(p.read_text(encoding="utf-8") for p in export_dir.glob("*.jsonl")))


def test_api_key_in_unexpected_error_and_debug_traceback_is_redacted(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_LITERAL)
    message = leaky_message()

    def boom(method: str, prices: Any, client: object | None = None) -> SceneGenerator:
        try:
            raise ValueError(message)
        except ValueError as cause:
            raise RuntimeError(message) from cause

    monkeypatch.setattr(registry, "create", boom)
    for flags in ((), ("--debug",)):
        code, out, err = run_cli(*flags, "run", "--script", str(env.script), "--method", "fake", "--dry-run")
        assert code == 1
        assert_no_key(out + err)
    assert "Traceback" in err  # o --debug continua mostrando o traceback, só que redigido


def test_api_key_in_notes_of_an_exception_is_redacted(
    env: Env, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_LITERAL)
    authorize(db, 350.0)

    class Noted(FakeAdapter):
        def generate(self, req: SceneRequest, token: Any) -> SceneResult:
            token.consume(self.method)
            exc = ProviderError("falhou", transient=False)
            exc.add_note(f"detalhe: {SYNTHETIC_LITERAL}")
            raise exc

    monkeypatch.setattr(registry, "create", lambda method, prices, client=None: Noted(prices))
    code, out, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 4
    assert_no_key(out + err)


# --- B3: o export não leva dados pessoais (via CLI) -------------------------------------------


def test_cli_export_has_no_paths_notes_or_release_reference(
    env: Env, db: sqlite3.Connection, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "1")
    run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid", "--scenes", "1")
    out_dir = env.data_dir / "export"
    assert run_cli("ledger", "export", "--out", str(out_dir))[0] == 0
    blob = "".join(p.read_text(encoding="utf-8") for p in out_dir.glob("*.jsonl"))
    for forbidden in (str(env.root), "fotos-", "termo-001", "roteiro.toml", ".mp4", "teste"):
        assert forbidden not in blob, forbidden


# --- I1: banco só nasce em db init, run e budget authorize -------------------------------------


@pytest.mark.parametrize("template", COMMANDS_NEEDING_DB)
def test_commands_do_not_create_the_database(
    env: Env, run_cli: Callable[..., tuple[int, str, str]], template: tuple[str, ...]
) -> None:
    argv = [a.format(data=env.data_dir) for a in template]
    code, _, err = run_cli(*argv)
    assert code == 2
    assert "banco não encontrado" in err and str(env.db) in err and "verion db init" in err
    assert not env.data_dir.exists()  # nem o banco, nem a pasta


def test_run_and_authorize_still_create_the_database_and_print_its_path(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]], stub_free: None
) -> None:
    code, out, _ = run_cli("run", "--script", str(env.script), "--method", "ken-burns", "--scenes", "1")
    assert code == 0 and f"banco: {env.db}" in out and env.db.is_file()
    code, out, _ = run_cli("budget", "show")
    assert f"banco: {env.db}" in out
    monkeypatch.setattr(sys, "stdin", FakeTTY("AUTORIZO R$ 10,00\n"))
    code, out, _ = run_cli(*authorize_args("10"))
    assert code == 0 and f"banco: {env.db}" in out


def test_dry_run_creates_nothing(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    assert run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run")[0] == 0
    assert not env.data_dir.exists()


def test_export_with_no_runs_refuses_to_overwrite_existing_evidence(
    env: Env, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    out_dir = env.data_dir / "export"
    out_dir.mkdir(parents=True)
    evidence = out_dir / "attempts.jsonl"
    evidence.write_text('{"projeto": "imv-001"}\n', encoding="utf-8")
    assert run_cli("db", "init")[0] == 0  # banco vazio, p.ex. VERION_DATA_DIR errado
    code, _, err = run_cli("ledger", "export", "--out", str(out_dir))
    assert code == 2 and "--force" in err and "attempts.jsonl" in err
    assert evidence.read_text(encoding="utf-8") == '{"projeto": "imv-001"}\n'  # intacto
    assert run_cli("ledger", "export", "--out", str(out_dir), "--force")[0] == 0
    assert evidence.read_text(encoding="utf-8") == ""


def test_export_with_no_runs_and_no_previous_export_is_fine(
    env: Env, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    run_cli("db", "init")
    assert run_cli("ledger", "export", "--out", str(env.data_dir / "export"))[0] == 0


# --- I2: --out confinado ---------------------------------------------------------------------


@pytest.fixture
def repo_cwd(env: Env, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = env.root / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return cwd


def test_assemble_out_must_be_inside_data_dir_or_evidence(
    env: Env, two_runs: None, spy: AssembleSpy, repo_cwd: Path, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    other = env.root / "dados-de-outro-lugar"
    (env.data_dir / "link").symlink_to(other.with_name("alvo"), target_is_directory=True)
    (env.root / "alvo").mkdir()
    refused = [
        env.root / "fora.mp4",
        Path("../.git/config"),
        env.data_dir / "verion.db",
        env.data_dir / "verion.db-wal",
        env.data_dir / ".git" / "x.mp4",
        env.data_dir / ".claude" / "x.mp4",
        env.data_dir / ".." / "fora.mp4",
        env.data_dir / "link" / "x.mp4",  # link simbólico que escapa da pasta
        Path("docs/engineering/evidence/../../../x.mp4"),
        Path("/etc/passwd"),
    ]
    for target in refused:
        code, _, err = run_cli("assemble", "--run", "1", "--out", str(target), "--force")
        assert code == 2, target
        assert "Traceback" not in err
    assert spy.calls == []
    assert env.db.is_file()  # o banco sobreviveu


def test_assemble_out_allowed_locations(
    env: Env, two_runs: None, spy: AssembleSpy, repo_cwd: Path, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    assert run_cli("assemble", "--run", "1", "--out", str(env.data_dir / "sub" / "t.mp4"))[0] == 0
    assert run_cli("assemble", "--run", "1", "--out", "docs/engineering/evidence/demo/t.mp4")[0] == 0
    assert len(spy.calls) == 2


def test_assemble_refuses_existing_output_without_force(
    env: Env, two_runs: None, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    target = env.data_dir / "t.mp4"
    target.write_bytes(b"video antigo")
    code, _, err = run_cli("assemble", "--run", "1", "--out", str(target))
    assert code == 2 and "--force" in err and spy.calls == []
    assert run_cli("assemble", "--run", "1", "--out", str(target), "--force")[0] == 0
    assert len(spy.calls) == 1
    assert run_cli("assemble", "--run", "1", "--out", str(env.data_dir), "--force")[0] == 2  # uma pasta


def test_export_out_is_confined_too(
    env: Env, repo_cwd: Path, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    run_cli("db", "init")
    for target in (
        env.root / "fora",
        Path("../.git/hooks"),
        env.data_dir / "verion.db",
        env.data_dir / ".git" / "x",
        Path("/proc/self"),
    ):
        assert run_cli("ledger", "export", "--out", str(target), "--force")[0] == 2, target
    assert run_cli("ledger", "export", "--out", "docs/engineering/evidence/fase0")[0] == 0


# --- I3: teto de R$ 500 ----------------------------------------------------------------------


def seed_authorization_at(env: Env, amount: float, created_at: str) -> None:
    raw = sqlite3.connect(env.db)
    try:
        raw.execute(
            "INSERT INTO authorizations (amount_brl, reference, created_at) VALUES (?, 'semente', ?)",
            (amount, created_at),
        )
        raw.commit()
    finally:
        raw.close()


def freeze_now(monkeypatch: pytest.MonkeyPatch, iso: str) -> None:
    from datetime import UTC, datetime

    monkeypatch.setattr(cli, "_utcnow", lambda: datetime.fromisoformat(iso).replace(tzinfo=UTC))


def authorize_typed(
    monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]], brl: str, ref: str = "STATE.md"
) -> tuple[int, str, str]:
    amount = f"{float(brl.replace(',', '.')):.2f}".replace(".", ",")
    monkeypatch.setattr(sys, "stdin", FakeTTY(f"AUTORIZO R$ {amount}\n"))
    return run_cli("budget", "authorize", "--brl", brl, "--ref", ref)


def test_authorize_caps_a_single_authorization_at_500(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, _, err = authorize_typed(monkeypatch, run_cli, "500.01")
    assert code == 2 and "500" in err
    assert auth_rows(env.data_dir) == 0
    assert authorize_typed(monkeypatch, run_cli, "500")[0] == 0


def test_authorize_caps_the_calendar_month_in_sao_paulo(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    freeze_now(monkeypatch, "2026-11-20T12:00:00")
    assert authorize_typed(monkeypatch, run_cli, "300")[0] == 0
    code, _, err = authorize_typed(monkeypatch, run_cli, "300")
    assert code == 2 and "teto" in err and "R$ 300,00" in err
    assert auth_rows(env.data_dir) == 1
    assert authorize_typed(monkeypatch, run_cli, "200")[0] == 0  # 500 exatos cabem
    assert authorize_typed(monkeypatch, run_cli, "0.01")[0] == 2
    assert auth_rows(env.data_dir) == 2
    freeze_now(monkeypatch, "2026-12-05T12:00:00")  # mês novo: o teto zera
    assert authorize_typed(monkeypatch, run_cli, "500")[0] == 0


def test_month_boundary_uses_sao_paulo_time_not_utc(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    run_cli("db", "init")
    seed_authorization_at(env, 400.0, "2026-12-01T01:00:00Z")  # 30/11 22:00 em São Paulo: conta em novembro
    seed_authorization_at(env, 450.0, "2026-11-01T02:00:00Z")  # 31/10 23:00 em São Paulo: conta em outubro
    freeze_now(monkeypatch, "2026-11-20T12:00:00")
    assert authorize_typed(monkeypatch, run_cli, "100.01")[0] == 2  # novembro já tem 400
    assert authorize_typed(monkeypatch, run_cli, "100")[0] == 0


def test_authorize_strips_control_and_ansi_characters_from_the_reference(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    code, out, _ = authorize_typed(monkeypatch, run_cli, "10", ref="STATE.md\x1b[31m\nlinha2\x07\u202e fim")
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    ((_, _, reference, _),) = [row for row in _auth_rows(env)]
    assert reference == "STATE.md[31m linha2 fim"
    assert authorize_typed(monkeypatch, run_cli, "10", ref="\x1b\x07\n")[0] == 2  # vazia depois do filtro


def _auth_rows(env: Env) -> list[tuple[int, float, str, str]]:
    conn = repo.connect(env.db, create=False)
    try:
        return repo.list_authorizations(conn)
    finally:
        conn.close()


# --- I4: aprovar exige TTY e frase ------------------------------------------------------------


def test_qc_approval_without_tty_is_refused_and_writes_nothing(
    ok_attempt: int, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"APROVO tentativa {ok_attempt}\n"))  # frase certa, sem TTY
    code, _, err = run_cli("qc", "record", "--attempt", str(ok_attempt), "--verdict", "aprovada", "--reviewer", "humano")
    assert code == 2 and "TTY" in err
    assert repo.get_qc(db, ok_attempt) is None


def test_qc_approval_with_wrong_phrase_is_refused(
    ok_attempt: int, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    for wrong in ("sim\n", "APROVO tentativa 2\n", "aprovo tentativa 1\n", "APROVO\n", ""):
        monkeypatch.setattr(sys, "stdin", FakeTTY(wrong))
        code, _, _ = run_cli("qc", "record", "--attempt", str(ok_attempt), "--verdict", "aprovada", "--reviewer", "humano")
        assert code == 2, wrong
    assert repo.get_qc(db, ok_attempt) is None


def test_qc_refazer_and_rejeitada_still_work_without_tty(
    ok_attempt: int, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code, _, _ = run_cli(
        "qc", "record", "--attempt", str(ok_attempt), "--verdict", "refazer", "--reviewer", "humano", "--defect", "2:1:x"
    )
    assert code == 0
    qc = repo.get_qc(db, ok_attempt)
    assert qc is not None and qc.verdict == "refazer"


def test_qc_agent_approval_is_refused_without_prompting(
    ok_attempt: int, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY(""))
    code, out, err = run_cli("qc", "record", "--attempt", str(ok_attempt), "--verdict", "aprovada", "--reviewer", "agente")
    assert code == 2 and "humano" in err and "APROVO" not in out


def test_qc_approval_in_a_real_pty(
    env: Env, ok_attempt: int, db: sqlite3.Connection
) -> None:
    argv = ("qc", "record", "--attempt", str(ok_attempt), "--verdict", "aprovada", "--reviewer", "humano")
    code, _, err = pty_run(env, argv, f"APROVO tentativa {ok_attempt + 1}\n".encode())
    assert code == 2, err
    assert repo.get_qc(db, ok_attempt) is None
    code, out, err = pty_run(env, argv, f"APROVO tentativa {ok_attempt}\n".encode())
    assert code == 0, err
    qc = repo.get_qc(db, ok_attempt)
    assert qc is not None and (qc.verdict, qc.reviewer) == ("aprovada", "humano")
    assert f"APROVO tentativa {ok_attempt}" in out


def test_qc_approval_without_tty_in_a_real_process(env: Env, ok_attempt: int, db: sqlite3.Connection) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "verion.cli", "qc", "record", "--attempt", str(ok_attempt),
         "--verdict", "aprovada", "--reviewer", "humano"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_subprocess_env(env), timeout=60, check=False,
    )  # fmt: skip
    assert proc.returncode == 2 and "TTY" in proc.stderr
    assert repo.get_qc(db, ok_attempt) is None


# --- I5: autorização das fotos (LGPD) --------------------------------------------------------


def test_third_party_set_is_the_documented_one() -> None:
    assert cli.THIRD_PARTY_PHOTO_METHODS >= {"veo-3.1-fast", "gemini-omni-1.1-flash", "kling-3.0"}


@pytest.mark.parametrize("method", ["gemini-omni-1.1-flash", "veo-3.1-fast"])
def test_third_party_method_without_release_ref_is_refused(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]], method: str
) -> None:
    authorize(db, 350.0)
    script = write_script(env.root, "sem-termo.toml", release_ref=None)
    code, _, err = run_cli("run", "--script", str(script), "--method", method, "--allow-paid")
    assert code == 2 and "photo_release_ref" in err
    assert repo.list_runs(db) == [] and attempts(db) == []  # nenhuma foto saiu, nenhum run


def test_dry_run_of_third_party_method_does_not_need_the_ref(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    script = write_script(env.root, "sem-termo.toml", release_ref=None)
    assert run_cli("run", "--script", str(script), "--method", "gemini-omni-1.1-flash", "--dry-run")[0] == 0


def test_local_method_does_not_need_the_ref(
    env: Env, stub_free: None, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    script = write_script(env.root, "sem-termo.toml", release_ref=None)
    assert run_cli("run", "--script", str(script), "--method", "ken-burns", "--scenes", "1")[0] == 0


def test_release_ref_is_recorded_in_the_run_before_any_photo_is_sent(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    # sem autorização de gasto: a trava bloqueia a cena 1, mas o run já nasceu com a referência
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "gemini-omni-1.1-flash", "--allow-paid")
    assert code == 3 and "run_id=1" in err
    assert repo.get_run(db, 1).notes == "photo_release_ref=termo-001"
    assert attempts(db) == []


def test_release_ref_is_recorded_once_when_continuing_a_run(
    env: Env, db: sqlite3.Connection, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    authorize(db, 350.0)
    base = ("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    run_cli(*base, "--scenes", "1")
    run_cli(*base, "--run", "1", "--scenes", "2")
    assert repo.get_run(db, 1).notes == "photo_release_ref=termo-001"


def test_invalid_release_ref_in_script_is_exit_2(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    script = write_script(env.root, "ruim.toml", release_ref="termo da Maria")
    code, _, err = run_cli("run", "--script", str(script), "--method", "fake", "--dry-run")
    assert code == 2 and "photo_release_ref" in err


def test_example_script_documents_neutral_property_id_and_release_ref() -> None:
    text = (Path(cli.__file__).parents[2] / "examples" / "roteiro.example.toml").read_text(encoding="utf-8")
    assert "imv-001" in text and "photo_release_ref" in text


# --- I6: ambiente e arquivo não são bug -------------------------------------------------------


def test_data_dir_that_is_a_file_is_exit_2(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    blocker = env.root / "arquivo"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("VERION_DATA_DIR", str(blocker))
    code, _, err = run_cli("db", "init")
    assert code == 2 and "ambiente ou arquivo" in err and "erro interno" not in err


def test_corrupted_database_is_exit_2(env: Env, run_cli: Callable[..., tuple[int, str, str]]) -> None:
    env.data_dir.mkdir()
    env.db.write_bytes(b"isto nao e um banco sqlite " * 50)
    code, _, err = run_cli("budget", "show")
    assert code == 2 and "ambiente ou arquivo" in err and "erro interno" not in err


def test_export_to_unwritable_target_is_exit_2(
    env: Env, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    run_cli("db", "init")
    blocker = env.data_dir / "ocupado"
    blocker.write_text("x", encoding="utf-8")
    code, _, err = run_cli("ledger", "export", "--out", str(blocker / "sub"))
    assert code == 2 and "erro interno" not in err


def test_broken_pipe_on_stdout_is_exit_2(
    env: Env, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Closed:
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(sys, "stdout", Closed())
    code = cli.main(["report"])
    err = capsys.readouterr().err
    assert code == 2 and "erro interno" not in err


# --- I7: SIGTERM -----------------------------------------------------------------------------


def test_sigterm_settles_the_reservation_and_exits_130(
    env: Env, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    import signal

    authorize(db, 350.0)
    before = signal.getsignal(signal.SIGTERM)

    class Terminated(FakeAdapter):
        def generate(self, req: SceneRequest, token: Any) -> SceneResult:
            token.consume(self.method)
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError("o handler deveria ter interrompido")  # pragma: no cover

    monkeypatch.setattr(registry, "create", lambda method, prices, client=None: Terminated(prices))
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--allow-paid")
    assert code == 130
    assert "run_id=1" in err and "reservas liquidadas" in err and "KeyboardInterrupt:" not in err
    assert [(r.status, r.cost_brl) for r in attempts(db)] == [("error", 5.0)]
    assert signal.getsignal(signal.SIGTERM) == before  # o handler foi restaurado


def test_interrupt_before_any_run_does_not_claim_settled_reservations(
    env: Env, monkeypatch: pytest.MonkeyPatch, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    def interrupted(path: Path) -> PriceTable:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "load_prices", interrupted)
    code, _, err = run_cli("run", "--script", str(env.script), "--method", "fake", "--dry-run")
    assert code == 130 and "interrompido" in err
    assert "liquidadas" not in err and "run_id" not in err


# --- D2 e D4 ---------------------------------------------------------------------------------


def test_assemble_checks_every_approved_clip_exists_and_names_the_scene(
    env: Env, two_runs: None, db: sqlite3.Connection, spy: AssembleSpy, run_cli: Callable[..., tuple[int, str, str]]
) -> None:
    clip = repo.get_attempt(db, 3)
    assert clip is not None and clip.clip_path is not None
    Path(clip.clip_path).unlink()  # cena 3 do run 1
    code, _, err = run_cli("assemble", "--run", "1", "--out", str(env.data_dir / "t.mp4"))
    assert code == 2 and "cena 3" in err and spy.calls == []


def test_gitignore_covers_databases_and_ffmpeg_leftovers() -> None:
    lines = (Path(cli.__file__).parents[2] / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("*.db", "*.db-*", ".*.part*", ".*.work-*"):
        assert pattern in lines, pattern


def test_interrupt_message_claims_settlement_only_when_confirmed(capsys: pytest.CaptureFixture[str]) -> None:
    def report(**attrs: object) -> str:
        exc = KeyboardInterrupt()
        for name, value in attrs.items():
            setattr(exc, name, value)
        cli._report_interrupt(exc)
        return capsys.readouterr().err

    assert "reservas liquidadas" in report(verion_run_id=3, verion_pending=0)
    unconfirmed = report(verion_run_id=3, verion_pending=None)
    assert "liquidadas" not in unconfirmed and "não foi possível confirmar" in unconfirmed
    still_pending = report(verion_run_id=3, verion_pending=2)
    assert "liquidadas" not in still_pending and "2 tentativa(s) pending" in still_pending and "ledger settle" in still_pending
    assert "liquidadas" not in report()
