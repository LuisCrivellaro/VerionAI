"""Harness da Fase 0 (spec 3.5): roda as cenas de um roteiro com UM adaptador, em sequência.

Ordem por cena: ``estimate`` -> ``budget.reserve`` -> ``generate`` (cronometrado por ``clock``) ->
``budget.settle_ok``. A reserva é gravada ANTES da chamada; qualquer exceção depois dela, em ``generate``
ou em ``settle_ok``, inclusive ``KeyboardInterrupt``, liquida a tentativa com ``settle_error`` (custo =
max(faturado, reserva)), encerra o run e é relançada. O primeiro erro encerra o run, para não queimar
reservas em série. ``SpendBlocked`` (``reserve`` recusou) sobe sem criar linha.

Regras de cena (``AttemptRuleError``: aguardando QC, aprovada/rejeitada, tentativa pendente) não são
erro: a cena entra em ``RunSummary.skipped`` e o run segue.

Um adaptador por run: o harness recebe a instância pronta e nunca a recria, porque a sequência de
``VERION_FAKE_MODE`` vale por instância do ``FakeClient``. O método do run é o do adaptador; continuar um
run (``run_id``) com outro método, roteiro ou ``property_id`` é ``InputError``.

Este módulo só gasta pela API pública de ``verion.budget``; não escreve no ledger de tentativas.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from verion import budget
from verion.config import PriceTable
from verion.errors import AttemptRuleError, EstimateInvalid, InputError, ProviderError
from verion.ledger import repo
from verion.providers.base import SceneGenerator, SceneRequest
from verion.script import Script

__all__ = ["RunSummary", "run_scenes"]

_MAX_ERROR_CHARS: Final = 500


@dataclass(frozen=True)
class RunSummary:
    """Resultado de uma chamada de ``run_scenes`` que terminou sem exceção.

    ``errors`` e ``stopped_by`` ficam em 0/``None`` no retorno normal: o primeiro erro é liquidado no
    ledger e RELANÇADO (a CLI converte em código de saída), então não há retorno com erro.
    """

    run_id: int
    attempted: int
    ok: int
    errors: int
    cost_brl: float
    skipped: Mapping[int, str]  # cena -> motivo
    stopped_by: str | None


def _describe(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= _MAX_ERROR_CHARS else text[: _MAX_ERROR_CHARS - 1] + "…"


def _select(script: Script, scenes: Sequence[int] | None) -> list[int]:
    """Índices a rodar, em ordem crescente (a do roteiro). ``InputError`` para seleção inválida."""
    available = [scene.index for scene in script.scenes]
    if scenes is None:
        return available
    wanted = list(scenes)
    if not wanted:
        raise InputError("seleção de cenas vazia: omita --scenes para rodar as 12")
    if len(set(wanted)) != len(wanted):
        raise InputError(f"cena repetida na seleção: {sorted(wanted)}")
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise InputError(f"cena(s) fora do roteiro: {unknown}")
    return [index for index in available if index in set(wanted)]


def _open_run(conn: sqlite3.Connection, script: Script, gen: SceneGenerator, run_id: int | None) -> int:
    """Cria o run ou valida o existente (método, roteiro e property_id). Nada é gravado em caso de erro."""
    if run_id is None:
        return repo.create_run(
            conn,
            property_id=script.property_id,
            method=gen.method,
            script_path=str(script.path),
            script_sha256=script.sha256,
        )
    run = repo.get_run(conn, run_id)  # InputError se inexistente
    if run.method != gen.method:
        raise InputError(f"o run {run_id} é do método {run.method!r}; o adaptador é {gen.method!r}")
    if run.property_id != script.property_id:
        raise InputError(
            f"o run {run_id} é do property_id {run.property_id!r}; o roteiro é {script.property_id!r}"
        )
    if run.script_sha256 != script.sha256:
        raise InputError(f"o roteiro mudou desde o run {run_id} (sha256 diferente): crie um novo run")
    return run_id


def run_scenes(
    conn: sqlite3.Connection,
    script: Script,
    gen: SceneGenerator,
    prices: PriceTable,
    *,
    allow_paid: bool,
    data_dir: Path,
    scenes: Sequence[int] | None = None,
    run_id: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RunSummary:
    """Roda as cenas pedidas (todas, se ``scenes`` for ``None``) em ordem crescente com o adaptador ``gen``.

    Os clipes vão para ``data_dir/runs/<run_id>/<método>/sNN_tN.mp4``. Com ``run_id``, continua um run
    existente do mesmo método e roteiro; sem ele, cria um. Exceções: ver o docstring do módulo.
    """
    selected = _select(script, scenes)
    by_index = {scene.index: scene for scene in script.scenes}
    run_id = _open_run(conn, script, gen, run_id)

    skipped: dict[int, str] = {}
    attempted = ok = 0
    cost_brl = 0.0
    try:
        for index in selected:
            scene = by_index[index]
            estimate = gen.estimate(scene)
            try:
                token = budget.reserve(
                    conn,
                    prices,
                    run_id=run_id,
                    scene_index=index,
                    target_s=scene.duration_s,
                    method=gen.method,
                    provider=gen.provider,
                    model=gen.model,
                    units=estimate.units,
                    unit_kind=estimate.unit_kind,
                    allow_paid=allow_paid,
                )
            except AttemptRuleError as exc:  # SpendBlocked e demais erros sobem sem criar linha
                skipped[index] = str(exc)
                continue

            # Tudo depois da reserva fica dentro do try: até o relógio pode falhar sem deixar pending órfã.
            started: float | None = None
            try:
                attempted += 1
                started = clock()
                out_path = data_dir / "runs" / str(run_id) / gen.method / f"s{index:02d}_t{token.attempt_no}.mp4"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                req = SceneRequest(scene=scene, run_id=run_id, attempt_no=token.attempt_no, out_path=out_path)
                result = gen.generate(req, token)
                wall_time_s = clock() - started
                if result.unit_kind != estimate.unit_kind:
                    raise EstimateInvalid(
                        f"unit_kind do resultado ({result.unit_kind!r}) diverge da estimativa ({estimate.unit_kind!r})"
                    )
                row = budget.settle_ok(
                    conn,
                    token,
                    units=result.units,
                    cost_is_estimate=result.cost_is_estimate,
                    clip_path=result.clip_path,
                    duration_s=result.duration_s,
                    wall_time_s=wall_time_s,
                    provider_job_id=result.provider_job_id,
                )
            except BaseException as exc:  # inclui KeyboardInterrupt/SystemExit: a reserva nunca fica órfã
                billed = exc.billed_units if isinstance(exc, ProviderError) else 0.0
                job_id = exc.provider_job_id if isinstance(exc, ProviderError) else None
                try:
                    budget.settle_error(
                        conn,
                        token,
                        billed_units=billed,
                        error=_describe(exc),
                        wall_time_s=_elapsed(clock, started),
                        provider_job_id=job_id,
                    )
                except Exception as settle_exc:  # noqa: BLE001  (não pode mascarar a causa original)
                    # Não mascara a causa. Só orienta `ledger settle` se a linha realmente seguir pending.
                    if _still_pending(conn, token.attempt_id):
                        exc.add_note(
                            f"liquidação falhou ({_describe(settle_exc)}); a tentativa {token.attempt_id} "
                            "segue pending com a reserva: use `verion ledger settle`"
                        )
                raise
            ok += 1
            cost_brl += row.cost_brl
    except BaseException as exc:
        # O chamador (CLI, T4) precisa do run para reportar e liquidar: anexa o id à exceção.
        try:
            exc.verion_run_id = run_id  # type: ignore[attr-defined]
        except AttributeError:
            pass
        exc.add_note(f"run_id={run_id}")
        raise
    finally:
        repo.finish_run(conn, run_id)

    return RunSummary(
        run_id=run_id,
        attempted=attempted,
        ok=ok,
        errors=0,
        cost_brl=cost_brl,
        skipped=MappingProxyType(skipped),
        stopped_by=None,
    )


def _elapsed(clock: Callable[[], float], started: float | None) -> float:
    """Tempo desde ``started``; 0.0 se o relógio falhou (a liquidação não pode depender dele)."""
    if started is None:
        return 0.0
    try:
        return max(0.0, clock() - started)
    except Exception:  # noqa: BLE001
        return 0.0


def _still_pending(conn: sqlite3.Connection, attempt_id: int) -> bool:
    try:
        row = repo.get_attempt(conn, attempt_id)
    except Exception:  # noqa: BLE001
        return True
    return row is not None and row.status == "pending"

