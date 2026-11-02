"""CLI ``verion`` (spec seção 4): argparse, 10 comandos, códigos de saída 0 e 2 a 6.

Configuração: arquivo de preços (``VERION_PRICES``, padrão ``config/prices.toml``) e pasta de dados
(``VERION_DATA_DIR``, padrão ``./data``). Só este módulo lê essas variáveis; o ``FakeClient`` lê
``VERION_FAKE_MODE`` por conta própria (exceção da spec 3.2). Nenhuma chave de provedor é lida ou impressa
aqui: os adaptadores pagos criam o client real de forma preguiçosa, em ``generate``.

Códigos de saída: 0 sucesso; 2 entrada/configuração; 3 gasto bloqueado; 4 provedor; 5 regra de tentativa
(nenhuma cena pedida foi tentada); 6 ffmpeg (``VerionError.exit_code``). Fora da tabela: 1 = erro interno
(bug) com mensagem de uma linha, sem traceback (``--debug`` o imprime) e 130 = interrompido (Ctrl-C), depois de
o harness liquidar a reserva. Erros que vêm de ``run_scenes`` trazem ``verion_run_id``, e o run é sempre
reportado.

``budget authorize`` é o ÚNICO lugar de produção que chama ``repo.add_authorization`` (teste AST da T1).
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import sqlite3
import sys
import traceback
from collections.abc import Iterator, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from verion.config import PriceTable, load_prices
from verion.errors import (
    AttemptRuleError,
    AuthorizationRefused,
    InputError,
    PaidCallBlocked,
    ProviderError,
    VerionError,
)
from verion.harness import RunSummary, run_scenes
from verion.ledger import repo
from verion.ledger.export import export_jsonl
from verion.ledger.repo import AttemptRow, Defect
from verion.local import assembly, qc_frames
from verion.providers import registry
from verion.providers.base import Scene, SceneGenerator
from verion.report import build_report, render
from verion.script import SCENE_COUNT, Script, load_script

__all__ = ["main"]

EXIT_BUG: Final = 1
EXIT_INTERRUPTED: Final = 130
DB_NAME: Final = "verion.db"
DEFAULT_PRICES: Final = "config/prices.toml"
DEFAULT_EXPORT_DIR: Final = "docs/engineering/evidence/fase0"


# --- ambiente e utilidades ------------------------------------------------------------------


def _data_dir() -> Path:
    """``VERION_DATA_DIR`` (padrão ``./data``), sempre absoluto: os caminhos gravados no ledger valem de qualquer cwd."""
    return Path(os.environ.get("VERION_DATA_DIR") or "data").resolve()


def _prices_path() -> Path:
    return Path(os.environ.get("VERION_PRICES") or DEFAULT_PRICES)


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = repo.connect(_data_dir() / DB_NAME)
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


# --- tipos de argumento (ArgumentTypeError vira erro de uso: código 2) -----------------------


def _brl_arg(text: str) -> Decimal:
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"valor inválido: {text!r}") from None
    if not value.is_finite() or value <= 0:
        raise argparse.ArgumentTypeError(f"o valor deve ser positivo e finito: {text!r}")
    if value != value.quantize(Decimal("0.01")):
        raise argparse.ArgumentTypeError(f"use no máximo 2 casas decimais: {text!r}")
    return value.quantize(Decimal("0.01"))


def _cost_arg(text: str) -> float:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError(f"custo inválido: {text!r}") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"o custo deve ser finito e >= 0: {text!r}")
    return value


def _scenes_arg(text: str) -> list[int]:
    try:
        scenes = [int(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"cenas inválidas: {text!r} (use '1,3,5')") from None
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
    else:
        try:
            tipo = int(kind)
        except ValueError:
            raise argparse.ArgumentTypeError(f"tipo de defeito inválido: {kind!r} (1..7 ou 'leve')") from None
        if not 1 <= tipo <= 7:
            raise argparse.ArgumentTypeError(f"tipo de defeito fora de 1..7: {tipo}")
    try:
        seconds = float(second)
    except ValueError:
        raise argparse.ArgumentTypeError(f"segundo inválido no defeito: {second!r}") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError(f"segundo inválido no defeito: {second!r}")
    return Defect(tipo, seconds, note.strip())  # type: ignore[arg-type]  # tipo validado acima


# --- db e orçamento -------------------------------------------------------------------------


def _cmd_db_init(args: argparse.Namespace) -> int:
    path = _data_dir() / DB_NAME
    with _db():
        pass  # connect() cria o arquivo e aplica o esquema; idempotente
    _out(f"banco pronto: {path}")
    return 0


def _cmd_budget_authorize(args: argparse.Namespace) -> int:
    amount: Decimal = args.brl
    reference: str = args.ref
    if not reference.strip():
        raise InputError("a autorização exige uma referência não vazia (ex.: 'STATE.md AAAA-MM-DD')")
    phrase = f"AUTORIZO R$ {amount:.2f}".replace(".", ",")
    # Atrito contra ação acidental, não fronteira de segurança (spec 3.3): exige terminal de verdade.
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        raise AuthorizationRefused(
            "autorizar gasto exige um terminal interativo (TTY); nada foi gravado. "
            "Rode o comando você mesmo, num terminal"
        )
    _out(f"Autorizar {_brl(float(amount))} (ref.: {reference.strip()}).")
    _out(f"Para confirmar, digite exatamente: {phrase}")
    sys.stdout.flush()
    answer = stdin.readline().rstrip("\r\n")
    if answer != phrase:
        raise AuthorizationRefused(f"frase de confirmação incorreta; esperado {phrase!r}. Nada foi gravado")
    with _db() as conn:
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


def _cmd_run(args: argparse.Namespace) -> int:
    prices = load_prices(_prices_path())
    script = load_script(Path(args.script).resolve())
    gen = registry.create(args.method, prices)  # UM adaptador por run, sem client injetado
    scenes: list[int] | None = args.scenes

    if args.dry_run:
        return _dry_run(script, gen, prices, _select_scenes(script, scenes))

    if prices.is_paid(gen.method) and not args.allow_paid:
        raise PaidCallBlocked(f"método pago {gen.method!r} exige --allow-paid")

    with _db() as conn:
        try:
            summary = run_scenes(
                conn,
                script,
                gen,
                prices,
                allow_paid=args.allow_paid,
                data_dir=_data_dir(),
                scenes=scenes,
                run_id=args.run,
            )
        except BaseException as exc:
            _print_run_state(conn, getattr(exc, "verion_run_id", None))
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


def _print_run_state(conn: sqlite3.Connection, run_id: object) -> None:
    """Estado do run no ledger depois de uma falha (melhor esforço: nunca mascara o erro original)."""
    if not isinstance(run_id, int):
        return
    try:
        rows = repo.list_attempts(conn, run_id=run_id)
    except Exception:  # noqa: BLE001
        return
    count = {status: sum(1 for r in rows if r.status == status) for status in ("ok", "error", "pending")}
    cost = sum(r.cost_brl for r in rows)
    _out(
        f"estado do run {run_id} no ledger: {count['ok']} ok, {count['error']} erro, "
        f"{count['pending']} pending; custo {_brl(cost)}"
    )


# --- ledger ---------------------------------------------------------------------------------


def _cmd_ledger_settle(args: argparse.Namespace) -> int:
    note: str = args.note
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


def _cmd_ledger_export(args: argparse.Namespace) -> int:
    with _db() as conn:
        result = export_jsonl(conn, args.out)
    for path in result.paths:
        _out(f"gravado: {path}")
    if result.skipped_fake_runs:
        _out(f"{result.skipped_fake_runs} run fake omitido(s)")
    return 0


# --- qc -------------------------------------------------------------------------------------


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
    _out(f"tentativa {attempt.id} (run {run.id}, cena {attempt.scene_index}, método {attempt.method}): {len(frames.frames)} quadros em {out_dir}")
    _out(f"folha de contato: {frames.sheet}")
    _out(f"último quadro: {frames.last}")
    _out(f"foto de origem: ver o roteiro {run.script_path}, cena {attempt.scene_index}")
    return 0


def _cmd_qc_record(args: argparse.Namespace) -> int:
    defects: list[Defect] = args.defect or []
    with _db() as conn:
        qc_id = repo.record_qc(conn, args.attempt, verdict=args.verdict, reviewer=args.reviewer, defects=defects)
        attempt = repo.get_attempt(conn, args.attempt)
        assert attempt is not None  # record_qc já validou a existência
        status, reason = repo.scene_status(conn, attempt.run_id, attempt.scene_index)
    _out(f"QC #{qc_id} gravado: tentativa {args.attempt}, veredito {args.verdict} ({args.reviewer})")
    _out(f"cena {attempt.scene_index} do run {attempt.run_id}: {status}" + (f" ({reason})" if reason else ""))
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

    clips = [assembly.ClipSpec(path=Path(a.clip_path or ""), target_s=a.target_s) for _, a in chosen]
    result = assembly.assemble(clips, args.out, demo=args.demo)

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
    return 0


# --- parser e main --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verion",
        description="VÉRION, harness da Fase 0. Variáveis: VERION_DATA_DIR, VERION_PRICES, VERION_FAKE_MODE.",
    )
    parser.add_argument("--debug", action="store_true", help="imprime o traceback em erros internos")
    top = parser.add_subparsers(dest="command", metavar="COMANDO", required=True)

    db = top.add_parser("db", help="banco de dados").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    db.add_parser("init", help="cria o banco em $VERION_DATA_DIR/verion.db").set_defaults(func=_cmd_db_init)

    budget = top.add_parser("budget", help="orçamento").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    authorize = budget.add_parser("authorize", help="registra autorização de gasto (exige TTY e frase)")
    authorize.add_argument("--brl", type=_brl_arg, required=True, help="valor em R$ (até 2 casas)")
    authorize.add_argument("--ref", required=True, help='referência, ex.: "STATE.md 2026-11-02"')
    authorize.set_defaults(func=_cmd_budget_authorize)
    budget.add_parser("show", help="autorizado, gasto (com reservas pending) e saldo").set_defaults(func=_cmd_budget_show)

    run = top.add_parser("run", help="gera as cenas de um roteiro com um método")
    run.add_argument("--script", type=Path, required=True)
    run.add_argument("--method", required=True)
    run.add_argument("--scenes", type=_scenes_arg, help="ex.: 1,3 (padrão: todas)")
    run.add_argument("--run", type=int, help="continua o run ID (mesmo método e roteiro)")
    run.add_argument("--allow-paid", action="store_true", help="obrigatório para método pago")
    run.add_argument("--dry-run", action="store_true", help="mostra unidades e R$ por cena, sem reservar")
    run.set_defaults(func=_cmd_run)

    ledger = top.add_parser("ledger", help="ledger").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    settle = ledger.add_parser("settle", help="liquida uma tentativa pending órfã")
    settle.add_argument("--attempt", type=int, required=True)
    settle.add_argument("--cost-brl", type=_cost_arg, help="custo em R$ (nunca abaixo da reserva)")
    settle.add_argument("--note", required=True)
    settle.set_defaults(func=_cmd_ledger_settle)
    export = ledger.add_parser("export", help="exporta o ledger em JSONL")
    export.add_argument("--out", type=Path, default=Path(DEFAULT_EXPORT_DIR), help=f"padrão: {DEFAULT_EXPORT_DIR}")
    export.set_defaults(func=_cmd_ledger_export)

    qc = top.add_parser("qc", help="controle de fidelidade").add_subparsers(dest="sub", metavar="SUBCOMANDO", required=True)
    frames = qc.add_parser("frames", help="extrai quadros, folha de contato e último quadro")
    frames.add_argument("--attempt", type=int, required=True)
    frames.set_defaults(func=_cmd_qc_frames)
    record = qc.add_parser("record", help="grava o veredito de QC")
    record.add_argument("--attempt", type=int, required=True)
    record.add_argument("--verdict", required=True, choices=["aprovada", "refazer", "rejeitada"])
    record.add_argument("--reviewer", required=True, choices=["humano", "agente"])
    record.add_argument("--defect", type=_defect_arg, action="append", metavar="TIPO:SEGUNDO:NOTA")
    record.set_defaults(func=_cmd_qc_record)

    assemble = top.add_parser("assemble", help="monta as cenas aprovadas em um vídeo")
    assemble.add_argument("--run", type=int, required=True)
    assemble.add_argument("--fallback-run", type=int, help="run que cobre as lacunas (mesmo imóvel)")
    assemble.add_argument("--demo", action="store_true", help="aplica a marca DEMONSTRAÇÃO")
    assemble.add_argument("--out", type=Path, required=True)
    assemble.set_defaults(func=_cmd_assemble)

    report = top.add_parser("report", help="relatório por método")
    report.add_argument("--format", choices=["md", "csv"], default="md")
    report.set_defaults(func=_cmd_report)
    return parser


def _report_failure(exc: BaseException, header: str) -> None:
    """Mensagem em stderr: tipo, texto, run reportado e notas (sem duplicar o ``run_id=N`` do harness)."""
    print(f"{header}: {type(exc).__name__}: {exc}", file=sys.stderr)
    run_id = getattr(exc, "verion_run_id", None)
    if run_id is not None:
        print(f"run_id={run_id}", file=sys.stderr)
    for note in getattr(exc, "__notes__", ()):
        if not str(note).startswith("run_id="):
            print(f"  {note}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a CLI e devolve o código de saída (nunca levanta, exceto ``SystemExit`` de fora)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help (0) e erros de uso (2)
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else EXIT_BUG)

    try:
        return int(args.func(args))
    except KeyboardInterrupt as exc:
        _report_failure(exc, "interrompido")
        print("as reservas pendentes foram liquidadas; confira com `verion budget show`", file=sys.stderr)
        return EXIT_INTERRUPTED
    except VerionError as exc:
        _report_failure(exc, "erro")
        if isinstance(exc, ProviderError) and exc.transient:
            print("(falha transitória do provedor; a reserva foi mantida como custo mínimo)", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001  (bug: sempre com código 1 e uma linha identificável)
        _report_failure(exc, "erro interno (bug, código 1)")
        if args.debug:
            traceback.print_exc()
        else:
            print("use --debug para ver o traceback", file=sys.stderr)
        return EXIT_BUG


if __name__ == "__main__":
    sys.exit(main())
