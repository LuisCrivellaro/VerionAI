"""CLI ``verion`` (spec seção 4): argparse, 10 comandos, códigos de saída 0 e 2 a 6.

Configuração: arquivo de preços (``VERION_PRICES``, padrão ``config/prices.toml``) e pasta de dados
(``VERION_DATA_DIR``, padrão ``./data``). Só este módulo lê essas variáveis; o ``FakeClient`` lê
``VERION_FAKE_MODE`` por conta própria (exceção da spec 3.2). Nenhuma chave de provedor é lida ou impressa
aqui: os adaptadores pagos criam o client real de forma preguiçosa, em ``generate``, e todo texto de erro
passa por ``verion.redact.redact_secrets`` antes de sair.

Códigos de saída: 0 sucesso; 2 entrada, configuração, ambiente ou arquivo (inclui ``OSError`` e
``sqlite3.Error``); 3 gasto bloqueado; 4 provedor; 5 regra de tentativa (nenhuma cena pedida foi tentada); 6
ffmpeg (``VerionError.exit_code``). Fora da tabela: 1 = erro interno (bug) com mensagem de uma linha, sem
traceback (``--debug`` o imprime, redigido) e 130 = interrompido por Ctrl-C ou SIGTERM, depois de o harness
liquidar a reserva. Erros que vêm de ``run_scenes`` trazem ``verion_run_id``, e o run é sempre reportado.

Só ``db init``, ``run`` e ``budget authorize`` criam o banco; os demais comandos exigem que ele exista.

``budget authorize`` é o ÚNICO ponto de produção que chama ``repo.add_authorization``; fora de ``src/``, o
helper de contrato de testes (``providers/contract.py``) também a chama, e testes podem semear via ``repo``
(allow-list do teste AST da T1).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import sqlite3
import sys
import traceback
import unicodedata
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from types import FrameType
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from verion.config import PriceTable, load_prices
from verion.errors import (
    AttemptRuleError,
    AuthorizationRefused,
    InputError,
    InvalidVerdict,
    PaidCallBlocked,
    ProviderError,
    ScriptError,
    VerionError,
)
from verion.harness import RunSummary, run_scenes
from verion.ledger import repo
from verion.ledger.export import export_jsonl
from verion.ledger.repo import AttemptRow, Defect, RunRow
from verion.local import assembly, qc_frames
from verion.providers import registry
from verion.providers.base import Scene, SceneGenerator
from verion.redact import redact_secrets
from verion.report import build_report, render
from verion.script import SCENE_COUNT, Script, load_script

__all__ = ["main"]

EXIT_BUG: Final = 1
EXIT_INTERRUPTED: Final = 130
DB_NAME: Final = "verion.db"
DEFAULT_PRICES: Final = "config/prices.toml"
DEFAULT_EXPORT_DIR: Final = "docs/engineering/evidence/fase0"
EVIDENCE_ROOT: Final = "docs/engineering/evidence"

# Teto do brief (R$ 500/mês): por autorização e acumulado no mês calendário de America/Sao_Paulo.
MAX_AUTHORIZATION_BRL: Final = 500
MAX_ID: Final = 2**63 - 1  # maior INTEGER do SQLite

# Métodos que ENVIAM a foto a um terceiro: exigem photo_release_ref no roteiro (LGPD).
THIRD_PARTY_PHOTO_METHODS: Final = frozenset(
    {"veo-3.1-fast", "gemini-omni-1.1-flash", "kling-3.0", "wan-2.2-5b"}
)
MAX_TEXT_CHARS: Final = 200



def _sao_paulo() -> tzinfo:
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:  # sem tzdata no sistema: UTC-3 fixo (o Brasil não tem horário de verão desde 2019)
        return timezone(timedelta(hours=-3), "America/Sao_Paulo")


# --- ambiente e utilidades ------------------------------------------------------------------


def _data_dir() -> Path:
    """``VERION_DATA_DIR`` (padrão ``./data``), sempre absoluto: os caminhos gravados no ledger valem de qualquer cwd."""
    return Path(os.environ.get("VERION_DATA_DIR") or "data").resolve()


def _db_path() -> Path:
    return _data_dir() / DB_NAME


def _prices_path() -> Path:
    return Path(os.environ.get("VERION_PRICES") or DEFAULT_PRICES)


@contextlib.contextmanager
def _db(*, create: bool = False) -> Iterator[sqlite3.Connection]:
    """Conexão com o ledger. Só ``db init``, ``run`` e ``budget authorize`` passam ``create=True``."""
    conn = repo.connect(_db_path(), create=create)
    try:
        yield conn
    finally:
        conn.close()


def _brl(value: float) -> str:
    return f"R$ {value:.2f}".replace(".", ",")


def _num(value: float) -> str:
    return f"{value:g}"


def _out(text: str = "") -> None:
    print(text)


def _clean_text(text: str, *, what: str) -> str:
    """Texto de uma linha para gravar e imprimir: sem caracteres de controle/ANSI/bidi, sem segredos, limitado."""
    kept = "".join(" " if ch.isspace() else ch for ch in text if not unicodedata.category(ch).startswith("C") or ch.isspace())
    cleaned = redact_secrets(" ".join(kept.split()))[:MAX_TEXT_CHARS].strip()
    if not cleaned:
        raise InputError(f"{what} vazio(a) (depois de remover caracteres de controle)")
    return cleaned


# --- tipos de argumento (ArgumentTypeError vira erro de uso: código 2) -----------------------
# Só dígitos ASCII, sem expoente: `Decimal("1E+400")`, `int("１２")` e `float("1e30")` não passam.

_BRL_RE: Final = re.compile(r"[0-9]{1,9}([.,][0-9]{1,2})?")
_COST_RE: Final = re.compile(r"[0-9]{1,12}([.,][0-9]{1,6})?")
_ID_RE: Final = re.compile(r"[0-9]{1,19}")
_SCENE_RE: Final = re.compile(r"[0-9]{1,2}")
_SECOND_RE: Final = re.compile(r"[0-9]{1,6}([.][0-9]{1,3})?")


def _brl_arg(text: str) -> Decimal:
    if _BRL_RE.fullmatch(text.strip()) is None:
        raise argparse.ArgumentTypeError(f"valor inválido: {text!r} (use 350, 350.50 ou 350,50)")
    value = Decimal(text.strip().replace(",", "."))
    if value <= 0:
        raise argparse.ArgumentTypeError(f"o valor deve ser positivo: {text!r}")
    return value


def _id_arg(text: str) -> int:
    if _ID_RE.fullmatch(text.strip()) is None or not 1 <= int(text) <= MAX_ID:
        raise argparse.ArgumentTypeError(f"identificador inválido: {text!r} (inteiro de 1 a {MAX_ID})")
    return int(text)


def _cost_arg(text: str) -> float:
    if _COST_RE.fullmatch(text.strip()) is None:
        raise argparse.ArgumentTypeError(f"custo inválido: {text!r} (número decimal sem expoente)")
    return float(text.strip().replace(",", "."))


def _scenes_arg(text: str) -> list[int]:
    parts = text.split(",")
    if any(_SCENE_RE.fullmatch(part.strip()) is None for part in parts):
        raise argparse.ArgumentTypeError(f"cenas inválidas: {text!r} (use '1,3,5')")
    scenes = [int(part) for part in parts]
    if len(set(scenes)) != len(scenes):
        raise argparse.ArgumentTypeError(f"cena repetida: {text!r}")
    if any(not 1 <= scene <= SCENE_COUNT for scene in scenes):
        raise argparse.ArgumentTypeError(f"as cenas vão de 1 a {SCENE_COUNT}: {text!r}")
    return scenes


def _defect_arg(text: str) -> Defect:
    """``tipo:segundo:nota`` com tipo 1..7 ou ``leve``. A nota é o resto da linha e pode conter ':'."""
    parts = text.split(":", 2)
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(f"defeito inválido: {text!r} (use tipo:segundo:nota)")
    kind, second, note = parts[0].strip(), parts[1].strip(), parts[2] if len(parts) == 3 else ""
    tipo: int | str
    if kind == "leve":
        tipo = "leve"
    elif re.fullmatch(r"[1-7]", kind):
        tipo = int(kind)
    else:
        raise argparse.ArgumentTypeError(f"tipo de defeito inválido: {kind!r} (1..7 ou 'leve')")
    if _SECOND_RE.fullmatch(second) is None:
        raise argparse.ArgumentTypeError(f"segundo inválido no defeito: {second!r}")
    cleaned = _clean_text(note, what="nota do defeito") if note.strip() else ""
    return Defect(tipo, float(second), cleaned)  # type: ignore[arg-type]  # tipo validado acima


# --- confirmação digitada (TTY) -------------------------------------------------------------


def _require_tty(action: str) -> None:
    """Atrito contra ação acidental, não fronteira de segurança (spec 3.3): exige terminal de verdade."""
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        raise AuthorizationRefused(
            f"{action} exige um terminal interativo (TTY); nada foi gravado. Rode o comando você mesmo, num terminal"
        )


def _confirm_typed(phrase: str) -> None:
    _out(f"Para confirmar, digite exatamente: {phrase}")
    sys.stdout.flush()
    assert sys.stdin is not None  # _require_tty já conferiu
    answer = sys.stdin.readline().rstrip("\r\n")
    if answer != phrase:
        raise AuthorizationRefused(f"frase de confirmação incorreta; esperado {phrase!r}. Nada foi gravado")


# --- db e orçamento -------------------------------------------------------------------------


def _cmd_db_init(args: argparse.Namespace) -> int:
    with _db(create=True):
        pass  # connect() cria o arquivo (0600) e aplica o esquema; idempotente
    _out(f"banco pronto: {_db_path()}")
    return 0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _month_authorized_brl(conn: sqlite3.Connection) -> float:
    """Soma das autorizações do mês calendário corrente em America/Sao_Paulo (data ilegível conta, por cautela)."""
    zone = _sao_paulo()
    now = _utcnow().astimezone(zone)
    this_month = (now.year, now.month)
    total = 0.0
    for _id, amount, _ref, created_at in repo.list_authorizations(conn):
        try:
            when = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone(zone)
        except ValueError:
            total += amount
            continue
        if (when.year, when.month) == this_month:
            total += amount
    return total


def _check_monthly_cap(conn: sqlite3.Connection, amount: Decimal) -> None:
    already = _month_authorized_brl(conn)
    if round(already + float(amount), 2) > MAX_AUTHORIZATION_BRL:
        raise InputError(
            f"o teto operacional é {_brl(MAX_AUTHORIZATION_BRL)}/mês: já autorizado neste mês {_brl(already)}, "
            f"mais {_brl(float(amount))} passaria do teto. Nada foi gravado"
        )


def _cmd_budget_authorize(args: argparse.Namespace) -> int:
    amount: Decimal = args.brl
    if amount > MAX_AUTHORIZATION_BRL:
        raise InputError(
            f"cada autorização vai até {_brl(MAX_AUTHORIZATION_BRL)} (teto do mês): {_brl(float(amount))} é demais"
        )
    reference = _clean_text(args.ref, what="a referência")
    phrase = f"AUTORIZO R$ {amount:.2f}".replace(".", ",")
    _require_tty("autorizar gasto")
    with _db(create=True) as conn:
        _out(f"banco: {_db_path()}")
        _check_monthly_cap(conn, amount)
        _out(f"Autorizar {_brl(float(amount))} (ref.: {reference}).")
        _confirm_typed(phrase)
        _check_monthly_cap(conn, amount)  # de novo: outro terminal pode ter autorizado enquanto você digitava
        authorization_id = repo.add_authorization(conn, float(amount), reference)
        balance = repo.authorized_brl(conn) - repo.spent_brl(conn)
    _out(f"autorização #{authorization_id} registrada: {_brl(float(amount))}. Saldo: {_brl(balance)}")
    return 0


def _cmd_budget_show(args: argparse.Namespace) -> int:
    with _db() as conn:
        authorized = repo.authorized_brl(conn)
        spent = repo.spent_brl(conn)
        pending = sum(a.cost_brl for a in repo.list_attempts(conn) if a.status == "pending")
        authorizations = repo.list_authorizations(conn)
    _out(f"banco: {_db_path()}")
    _out(f"autorizado: {_brl(authorized)}")
    _out(f"gasto: {_brl(spent)} (inclui reservas pending: {_brl(pending)})")
    _out(f"saldo: {_brl(authorized - spent)}")
    for auth_id, amount, reference, created_at in authorizations:
        _out(f"  #{auth_id} {_brl(amount)}  {reference}  ({created_at})")
    return 0


# --- run ------------------------------------------------------------------------------------


def _select_scenes(script: Script, wanted: Sequence[int] | None) -> list[Scene]:
    by_index = {scene.index: scene for scene in script.scenes}
    if wanted is None:
        return list(script.scenes)
    unknown = sorted(set(wanted) - set(by_index))
    if unknown:
        raise InputError(f"cena(s) fora do roteiro: {unknown}")
    return [by_index[index] for index in sorted(wanted)]


def _register_release(conn: sqlite3.Connection, script: Script, method: str, run_id: int | None) -> int | None:
    """Registra ``photo_release_ref`` em ``runs.notes`` ANTES de qualquer foto sair. Devolve o run a usar.

    Sem referência no roteiro, não faz nada. Run novo: é criado aqui (o harness o reutiliza via ``run_id``).
    Run existente: só anota se ele for do mesmo método e roteiro (o harness valida o resto).
    """
    ref = script.photo_release_ref
    if ref is None:
        return run_id
    note = f"photo_release_ref={ref}"
    if run_id is None:
        run_id = repo.create_run(
            conn,
            property_id=script.property_id,
            method=method,
            script_path=str(script.path),
            script_sha256=script.sha256,
        )
        repo.add_note(conn, run_id, note)
        return run_id
    run = repo.get_run(conn, run_id)  # InputError se inexistente
    same = (run.method, run.property_id, run.script_sha256) == (method, script.property_id, script.sha256)
    if same and note not in run.notes.splitlines():
        repo.add_note(conn, run_id, note)
    return run_id


def _cmd_run(args: argparse.Namespace) -> int:
    prices = load_prices(_prices_path())
    script = load_script(Path(args.script).resolve())
    gen = registry.create(args.method, prices)  # UM adaptador por run, sem client injetado
    scenes: list[int] | None = args.scenes
    selected = _select_scenes(script, scenes)

    if args.dry_run:
        return _dry_run(script, gen, prices, selected)

    if gen.method in THIRD_PARTY_PHOTO_METHODS and script.photo_release_ref is None:
        raise InputError(
            f"o método {gen.method!r} envia as fotos a um terceiro: informe photo_release_ref no roteiro "
            "(referência curta ao termo de uso das fotos, sem nome; ex.: termo-0001)"
        )
    if prices.is_paid(gen.method) and not args.allow_paid:
        raise PaidCallBlocked(f"método pago {gen.method!r} exige --allow-paid")

    with _db(create=True) as conn:
        _out(f"banco: {_db_path()}")
        run_id = _register_release(conn, script, gen.method, args.run)
        try:
            summary = run_scenes(
                conn,
                script,
                gen,
                prices,
                allow_paid=args.allow_paid,
                data_dir=_data_dir(),
                scenes=scenes,
                run_id=run_id,
            )
        except BaseException as exc:
            pending = _print_run_state(conn, getattr(exc, "verion_run_id", None))
            with contextlib.suppress(AttributeError):
                exc.verion_pending = pending  # type: ignore[attr-defined]
            raise
    _print_summary(summary)
    if summary.attempted == 0 and summary.skipped:
        reasons = "; ".join(f"cena {scene}: {reason}" for scene, reason in sorted(summary.skipped.items()))
        raise AttemptRuleError(f"run {summary.run_id}: nenhuma cena foi tentada ({reasons})")
    return 0


def _dry_run(script: Script, gen: SceneGenerator, prices: PriceTable, selected: Sequence[Scene]) -> int:
    _out(f"simulação (nada reservado, nada gasto): método {gen.method}, imóvel {script.property_id}")
    total = 0.0
    for scene in selected:
        estimate = gen.estimate(scene)
        cost = prices.cost_brl(gen.method, estimate.units)
        total += cost
        _out(f"cena {scene.index:02d}: {_num(estimate.units)} {estimate.unit_kind} = {_brl(cost)}")
    _out(f"total: {_brl(total)} em {len(selected)} cena(s)")
    return 0


def _print_summary(summary: RunSummary) -> None:
    _out(
        f"run {summary.run_id}: {summary.attempted} tentada(s), {summary.ok} ok, "
        f"{summary.errors} erro(s), custo {_brl(summary.cost_brl)}"
    )
    for scene, reason in sorted(summary.skipped.items()):
        _out(f"cena {scene} pulada: {reason}")


def _print_run_state(conn: sqlite3.Connection, run_id: object) -> int | None:
    """Estado do run no ledger depois de uma falha (melhor esforço). Devolve o nº de ``pending`` ou ``None``."""
    if not isinstance(run_id, int):
        return None
    try:
        rows = repo.list_attempts(conn, run_id=run_id)
    except Exception:  # noqa: BLE001  (nunca mascara o erro original)
        return None
    count = {status: sum(1 for r in rows if r.status == status) for status in ("ok", "error", "pending")}
    cost = sum(r.cost_brl for r in rows)
    _out(
        f"estado do run {run_id} no ledger: {count['ok']} ok, {count['error']} erro, "
        f"{count['pending']} pending; custo {_brl(cost)}"
    )
    return count["pending"]


# --- saída segura ---------------------------------------------------------------------------


def _checked_out(path: Path) -> Path:
    """``--out`` só dentro de ``VERION_DATA_DIR`` ou de ``docs/engineering/evidence/`` (após resolver ``..`` e links)."""
    resolved = Path(path).resolve()
    roots = (_data_dir(), Path(EVIDENCE_ROOT).resolve())
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise InputError(
            f"--out fora das pastas permitidas: use um caminho dentro de {roots[0]} ou de {roots[1]}"
        )
    if {".git", ".claude"} & set(resolved.parts):
        raise InputError(f"--out não pode ficar em .git nem em .claude: {resolved}")
    if resolved.name.startswith(DB_NAME):
        raise InputError(f"--out não pode apontar para o banco ({resolved.name})")
    return resolved


# --- ledger ---------------------------------------------------------------------------------


def _cmd_ledger_settle(args: argparse.Namespace) -> int:
    note = _clean_text(args.note, what="a nota")
    with _db() as conn:
        attempt = repo.get_attempt(conn, args.attempt)
        if attempt is None:
            raise InputError(f"tentativa inexistente: {args.attempt}")
        requested: float | None = args.cost_brl
        cost = attempt.reserved_brl if requested is None else max(requested, attempt.reserved_brl)
        row = repo.settle_pending(conn, attempt.id, cost_brl=cost, note=note)
    _out(f"tentativa {row.id} liquidada como erro: custo {_brl(row.cost_brl)} (reserva {_brl(row.reserved_brl)})")
    if requested is not None and requested < row.reserved_brl:
        _out("o custo informado era menor que a reserva: a liquidação nunca reduz o gasto registrado")
    return 0


_EXPORT_FILES: Final = ("runs.jsonl", "attempts.jsonl", "scenes.jsonl", "authorizations.jsonl")


def _cmd_ledger_export(args: argparse.Namespace) -> int:
    out_dir = _checked_out(args.out)
    if out_dir.exists() and not out_dir.is_dir():
        raise InputError(f"--out deve ser uma pasta, e {out_dir} é um arquivo")
    with _db() as conn:
        if not repo.list_runs(conn) and not args.force:
            existing = [n for n in _EXPORT_FILES if (out_dir / n).is_file() and (out_dir / n).stat().st_size > 0]
            if existing:
                raise InputError(
                    f"o banco não tem runs e {out_dir} já tem export ({', '.join(existing)}): "
                    "exportar apagaria a evidência. Confira o VERION_DATA_DIR ou use --force"
                )
        result = export_jsonl(conn, out_dir)
    for path in result.paths:
        _out(f"gravado: {path}")
    if result.skipped_fake_runs:
        _out(f"{result.skipped_fake_runs} run fake omitido(s)")
    return 0


# --- qc -------------------------------------------------------------------------------------


def _origin_photo(run: RunRow, scene_index: int) -> str:
    """Onde está a foto de origem da cena, conferindo que o roteiro é o mesmo do run (sha256)."""
    try:
        script = load_script(Path(run.script_path))
    except (ScriptError, OSError):
        return f"roteiro {run.script_path} indisponível; confira a cena {scene_index} nele"
    if script.sha256 != run.script_sha256:
        return f"o roteiro {run.script_path} mudou desde o run (sha256 diferente); não confie na foto da cena {scene_index}"
    photo = next(scene.photo for scene in script.scenes if scene.index == scene_index)
    return str(photo)


def _cmd_qc_frames(args: argparse.Namespace) -> int:
    with _db() as conn:
        attempt = repo.get_attempt(conn, args.attempt)
        if attempt is None:
            raise InputError(f"tentativa inexistente: {args.attempt}")
        if attempt.status != "ok" or not attempt.clip_path:
            raise InputError(f"só tentativa ok tem clipe para o QC (tentativa {attempt.id} está {attempt.status})")
        run = repo.get_run(conn, attempt.run_id)
    out_dir = _data_dir() / "qc" / str(attempt.id)
    frames = qc_frames.extract(Path(attempt.clip_path), out_dir)
    _out(
        f"tentativa {attempt.id} (run {run.id}, cena {attempt.scene_index}, método {attempt.method}): "
        f"{len(frames.frames)} quadros em {out_dir}"
    )
    _out(f"folha de contato: {frames.sheet}")
    _out(f"último quadro: {frames.last}")
    _out(f"foto de origem da cena {attempt.scene_index}: {_origin_photo(run, attempt.scene_index)}")
    return 0


def _cmd_qc_record(args: argparse.Namespace) -> int:
    defects: list[Defect] = args.defect or []
    approving = args.verdict == "aprovada" and args.reviewer == "humano"
    with _db() as conn:
        if approving:
            # Aprovar é o ato que libera a cena para o cliente: exige humano num terminal, como a autorização.
            attempt = repo.get_attempt(conn, args.attempt)
            if attempt is None or attempt.status != "ok":
                raise InvalidVerdict(f"só tentativa ok recebe QC (tentativa {args.attempt} inexistente ou não ok)")
            _require_tty("aprovar uma cena")
            _out(f"Aprovar a tentativa {attempt.id} (run {attempt.run_id}, cena {attempt.scene_index}).")
            _confirm_typed(f"APROVO tentativa {attempt.id}")
        qc_id = repo.record_qc(conn, args.attempt, verdict=args.verdict, reviewer=args.reviewer, defects=defects)
        row = repo.get_attempt(conn, args.attempt)
        assert row is not None  # record_qc já validou a existência
        status, reason = repo.scene_status(conn, row.run_id, row.scene_index)
    _out(f"QC #{qc_id} gravado: tentativa {args.attempt}, veredito {args.verdict} ({args.reviewer})")
    _out(f"cena {row.scene_index} do run {row.run_id}: {status}" + (f" ({reason})" if reason else ""))
    return 0


# --- assemble -------------------------------------------------------------------------------


def _approved_by_scene(conn: sqlite3.Connection, run_id: int) -> dict[int, AttemptRow]:
    """Tentativa com veredito ``aprovada`` de cada cena do run (só humano aprova: regra do ``record_qc``)."""
    approved: dict[int, AttemptRow] = {}
    for attempt in repo.list_attempts(conn, run_id=run_id):
        qc = repo.get_qc(conn, attempt.id)
        if qc is not None and qc.verdict == "aprovada" and attempt.clip_path:
            approved[attempt.scene_index] = attempt
    return approved


def _cmd_assemble(args: argparse.Namespace) -> int:
    out = _checked_out(args.out)
    if out.is_dir():
        raise InputError(f"--out deve ser um arquivo, e {out} é uma pasta")
    if out.exists() and not args.force:
        raise InputError(f"{out} já existe: use --force para sobrescrever")
    with _db() as conn:
        run = repo.get_run(conn, args.run)
        primary = _approved_by_scene(conn, run.id)
        fallback: dict[int, AttemptRow] = {}
        if args.fallback_run is not None:
            if args.fallback_run == run.id:
                raise InputError("o run de fallback deve ser diferente do run principal")
            fallback_run = repo.get_run(conn, args.fallback_run)
            if fallback_run.property_id != run.property_id:
                raise InputError(
                    f"o fallback é do imóvel {fallback_run.property_id!r}, o run principal é de {run.property_id!r}"
                )
            fallback = _approved_by_scene(conn, fallback_run.id)

    chosen: list[tuple[int, AttemptRow]] = []  # (cena, tentativa aprovada), em ordem de cena
    gaps: list[int] = []
    for scene in range(1, SCENE_COUNT + 1):
        attempt = primary.get(scene) or fallback.get(scene)
        if attempt is None:
            gaps.append(scene)
        else:
            chosen.append((scene, attempt))
    if not chosen:
        raise InputError(f"o run {run.id} não tem nenhuma cena aprovada (nem no fallback) para montar")
    for scene, attempt in chosen:
        if not Path(attempt.clip_path or "").is_file():
            raise InputError(
                f"cena {scene}: o clipe aprovado (run {attempt.run_id}, tentativa {attempt.attempt_no}) "
                f"não existe mais: {attempt.clip_path}"
            )

    clips = [assembly.ClipSpec(path=Path(a.clip_path or ""), target_s=a.target_s) for _, a in chosen]
    try:
        result = assembly.assemble(clips, out, demo=args.demo)
    except InputError as exc:
        raise InputError(f"montagem recusada: {exc} (cenas usadas: {[scene for scene, _ in chosen]})") from exc

    _out(f"montagem: {result.out} ({result.duration_s:.1f} s, {len(chosen)} cena(s))")
    for scene, attempt in chosen:
        _out(f"cena {scene:02d}: {attempt.method} (run {attempt.run_id}, tentativa {attempt.attempt_no})")
    if gaps:
        _out(f"lacunas (sem cena aprovada, omitidas): {', '.join(map(str, gaps))}")
    for position in result.short_clips:  # base 0 na lista de clipes -> número da cena
        scene = chosen[position][0]
        _out(f"cena {scene} mais curta que o alvo: usada inteira, sem esticar")
    return 0


# --- report ---------------------------------------------------------------------------------


def _cmd_report(args: argparse.Namespace) -> int:
    with _db() as conn:
        rows = build_report(conn)
    sys.stdout.write(render(rows, args.format))
    sys.stdout.flush()
    return 0


# --- parser e main --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verion",
        description="VÉRION, harness da Fase 0. Variáveis: VERION_DATA_DIR, VERION_PRICES, VERION_FAKE_MODE.",
    )
    parser.add_argument("--debug", action="store_true", help="imprime o traceback (redigido) em erros internos")
    top = parser.add_subparsers(dest="command", metavar="COMANDO", required=True)

    db = top.add_parser("db", help="banco de dados").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    db.add_parser("init", help="cria o banco em $VERION_DATA_DIR/verion.db").set_defaults(func=_cmd_db_init)

    budget = top.add_parser("budget", help="orçamento").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    authorize = budget.add_parser(
        "authorize", help=f"registra autorização de gasto (TTY e frase; até R$ {MAX_AUTHORIZATION_BRL}/mês)"
    )
    authorize.add_argument("--brl", type=_brl_arg, required=True, help="valor em R$ (até 2 casas)")
    authorize.add_argument("--ref", required=True, help='referência, ex.: "STATE.md 2026-11-02"')
    authorize.set_defaults(func=_cmd_budget_authorize)
    budget.add_parser("show", help="autorizado, gasto (com reservas pending) e saldo").set_defaults(func=_cmd_budget_show)

    run = top.add_parser("run", help="gera as cenas de um roteiro com um método")
    run.add_argument("--script", type=Path, required=True)
    run.add_argument("--method", required=True)
    run.add_argument("--scenes", type=_scenes_arg, help="ex.: 1,3 (padrão: todas)")
    run.add_argument("--run", type=_id_arg, help="continua o run ID (mesmo método e roteiro)")
    run.add_argument("--allow-paid", action="store_true", help="obrigatório para método pago")
    run.add_argument("--dry-run", action="store_true", help="mostra unidades e R$ por cena, sem reservar")
    run.set_defaults(func=_cmd_run)

    ledger = top.add_parser("ledger", help="ledger").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    settle = ledger.add_parser("settle", help="liquida uma tentativa pending órfã")
    settle.add_argument("--attempt", type=_id_arg, required=True)
    settle.add_argument("--cost-brl", type=_cost_arg, help="custo em R$ (nunca abaixo da reserva)")
    settle.add_argument("--note", required=True)
    settle.set_defaults(func=_cmd_ledger_settle)
    export = ledger.add_parser("export", help="exporta o ledger em JSONL (só campos estruturados)")
    export.add_argument("--out", type=Path, default=Path(DEFAULT_EXPORT_DIR), help=f"padrão: {DEFAULT_EXPORT_DIR}")
    export.add_argument("--force", action="store_true", help="exporta mesmo sem runs, sobrescrevendo um export existente")
    export.set_defaults(func=_cmd_ledger_export)

    qc = top.add_parser("qc", help="controle de fidelidade").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    frames = qc.add_parser("frames", help="extrai quadros, folha de contato e último quadro")
    frames.add_argument("--attempt", type=_id_arg, required=True)
    frames.set_defaults(func=_cmd_qc_frames)
    record = qc.add_parser("record", help="grava o veredito de QC (aprovar exige TTY e frase)")
    record.add_argument("--attempt", type=_id_arg, required=True)
    record.add_argument("--verdict", required=True, choices=["aprovada", "refazer", "rejeitada"])
    record.add_argument("--reviewer", required=True, choices=["humano", "agente"])
    record.add_argument("--defect", type=_defect_arg, action="append", metavar="TIPO:SEGUNDO:NOTA")
    record.set_defaults(func=_cmd_qc_record)

    assemble = top.add_parser("assemble", help="monta as cenas aprovadas em um vídeo")
    assemble.add_argument("--run", type=_id_arg, required=True)
    assemble.add_argument("--fallback-run", type=_id_arg, help="run que cobre as lacunas (mesmo imóvel)")
    assemble.add_argument("--demo", action="store_true", help="aplica a marca DEMONSTRAÇÃO")
    assemble.add_argument("--out", type=Path, required=True, help=f"dentro de $VERION_DATA_DIR ou de {EVIDENCE_ROOT}/")
    assemble.add_argument("--force", action="store_true", help="sobrescreve a saída se ela já existir")
    assemble.set_defaults(func=_cmd_assemble)

    report = top.add_parser("report", help="relatório por método")
    report.add_argument("--format", choices=["md", "csv"], default="md")
    report.set_defaults(func=_cmd_report)
    return parser


def _report_failure(exc: BaseException, header: str) -> None:
    """Mensagem em stderr: tipo, texto, run reportado e notas. Tudo passa por ``redact_secrets``."""
    print(redact_secrets(f"{header}: {type(exc).__name__}: {exc}"), file=sys.stderr)
    _report_run_and_notes(exc)


def _report_run_and_notes(exc: BaseException) -> None:
    run_id = getattr(exc, "verion_run_id", None)
    if run_id is not None:
        print(f"run_id={run_id}", file=sys.stderr)
    for note in getattr(exc, "__notes__", ()):
        if not str(note).startswith("run_id="):
            print(redact_secrets(f"  {note}"), file=sys.stderr)


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt  # o harness liquida a reserva e relança


def _silence_stdout() -> None:
    """Depois de um pipe quebrado (``report | head``), evita um segundo erro ao fechar o stdout."""
    try:
        fd = sys.stdout.fileno()
    except (OSError, ValueError, AttributeError):  # stdout substituído (testes): nada a redirecionar
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, fd)
    finally:
        os.close(devnull)


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a CLI e devolve o código de saída (nunca levanta, exceto ``SystemExit`` de fora)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) e erros de uso (2)
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else EXIT_BUG)
    except Exception as exc:  # noqa: BLE001  (um tipo de argumento que falhou de forma inesperada ainda é erro de uso)
        print(redact_secrets(f"erro de uso: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return InputError.exit_code

    previous: object = None
    try:
        previous = signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # fora da thread principal: sem handler
        previous = None
    try:
        return int(args.func(args))
    except KeyboardInterrupt as exc:
        _report_interrupt(exc)
        return EXIT_INTERRUPTED
    except VerionError as exc:
        _report_failure(exc, "erro")
        if isinstance(exc, ProviderError) and exc.transient:
            print("(falha transitória do provedor; a reserva foi mantida como custo mínimo)", file=sys.stderr)
        return exc.exit_code
    except (OSError, sqlite3.Error) as exc:
        if isinstance(exc, BrokenPipeError):
            _silence_stdout()
        _report_failure(exc, "erro de ambiente ou arquivo")
        return InputError.exit_code
    except Exception as exc:  # noqa: BLE001  (bug: sempre com código 1 e uma linha identificável)
        _report_failure(exc, "erro interno (bug, código 1)")
        if args.debug:
            print(redact_secrets(traceback.format_exc()), file=sys.stderr)
        else:
            print("use --debug para ver o traceback", file=sys.stderr)
        return EXIT_BUG
    finally:
        if previous is not None:
            with contextlib.suppress(ValueError, TypeError):
                signal.signal(signal.SIGTERM, previous)  # type: ignore[arg-type]


def _report_interrupt(exc: KeyboardInterrupt) -> None:
    print("interrompido (Ctrl-C ou SIGTERM)", file=sys.stderr)
    _report_run_and_notes(exc)
    run_id = getattr(exc, "verion_run_id", None)
    pending = getattr(exc, "verion_pending", None)
    if run_id is None:
        return  # nenhuma reserva foi aberta por este comando: nada a afirmar
    if pending == 0:
        print(f"reservas liquidadas: o run {run_id} não tem tentativa pending", file=sys.stderr)
    elif isinstance(pending, int):
        print(f"ATENÇÃO: o run {run_id} tem {pending} tentativa(s) pending; use `verion ledger settle`", file=sys.stderr)
    else:
        print(f"não foi possível confirmar o estado do run {run_id}; confira com `verion budget show`", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
