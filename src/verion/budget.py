"""Orçamento: reserva, ``SpendToken`` e liquidação (spec 3.3).

Fluxo: ``reserve`` grava uma linha ``pending`` (custo = reserva) sob ``BEGIN IMMEDIATE``; o adaptador
recebe o token e o consome em ``generate``; o harness liquida com ``settle_ok`` ou ``settle_error``.
O custo vem só da tabela de preços; o adaptador informa unidades, nunca R$. Sem rede.
"""

from __future__ import annotations

import math
import sqlite3
import weakref
from pathlib import Path
from typing import Final

from verion.config import PriceTable, UnitKind
from verion.errors import (
    BudgetExceeded,
    EstimateInvalid,
    InputError,
    PaidCallBlocked,
    TokenInvalid,
)
from verion.ledger import repo
from verion.ledger.repo import AttemptRow

# Selo privado: só ``reserve`` o repassa ao construtor. O teste AST proíbe o nome fora deste módulo.
_SEAL: Final = object()

# Tokens emitidos por ``reserve`` neste processo. Cópias (copy, __new__ + __dict__) não entram aqui.
_ISSUED: weakref.WeakSet[SpendToken] = weakref.WeakSet()

_BALANCE_EPS: Final = 1e-9  # tolerância de ponto flutuante na comparação de saldo
_MIN_COST_BRL: Final = 1e-6  # custo de método pago abaixo disso é tratado como zero
MIN_SCENE, MAX_SCENE = 1, 12


class SpendToken:
    """Autorização de uso único para UMA chamada de ``generate``. Só ``reserve`` cria (exige ``_SEAL``).

    Exceção declarada à regra ``frozen``: guarda ``_conn`` e as flags mutáveis ``_used``/``_job_id``.
    """

    attempt_id: int
    attempt_no: int
    method: str
    reserved_brl: float

    def __init__(
        self,
        seal: object,
        *,
        conn: sqlite3.Connection,
        attempt_id: int,
        attempt_no: int,
        method: str,
        reserved_brl: float,
        prices: PriceTable,
    ) -> None:
        if seal is not _SEAL:
            raise TokenInvalid("SpendToken só pode ser criado por budget.reserve")
        self._conn = conn
        self._prices = prices
        self._used = False
        self._job_id: str | None = None
        self.attempt_id = attempt_id
        self.attempt_no = attempt_no
        self.method = method
        self.reserved_brl = reserved_brl
        _ISSUED.add(self)

    def _require_issued(self) -> None:
        # ``in`` usa hash por identidade; ``__new__`` sem init e cópias não estão em _ISSUED.
        if self not in _ISSUED:
            raise TokenInvalid("SpendToken não foi emitido por budget.reserve")

    def _require_pending(self) -> AttemptRow:
        row = repo.get_attempt(self._conn, self.attempt_id)
        if row is None or row.status != "pending":
            raise TokenInvalid(f"tentativa {self.attempt_id} não está pending")
        return row

    def consume(self, method: str) -> None:
        """Valida e queima o token. Chamado como primeira linha de ``generate``."""
        self._require_issued()
        if self._used:
            raise TokenInvalid("SpendToken já usado")
        if method != self.method:
            raise TokenInvalid(f"SpendToken é do método {self.method!r}, não de {method!r}")
        self._require_pending()
        self._used = True

    def job_accepted(self, job_id: str) -> None:
        """Grava ``provider_job_id`` na linha pending (autocommit). Exige ``consume`` antes."""
        self._require_issued()
        if not self._used:
            raise TokenInvalid("job_accepted antes de consume")
        if not isinstance(job_id, str) or not job_id.strip():
            raise InputError("job_id vazio")
        repo.set_job_id(self._conn, self.attempt_id, job_id.strip())
        self._job_id = job_id.strip()


def reserve(
    conn: sqlite3.Connection,
    prices: PriceTable,
    *,
    run_id: int,
    scene_index: int,
    target_s: float,
    method: str,
    provider: str,
    model: str,
    units: float,
    unit_kind: UnitKind,
    allow_paid: bool,
) -> SpendToken:
    """Reserva custo e devolve o token. Ordem da spec 3.3; nada é gravado em caso de erro."""
    paid = prices.is_paid(method)
    if paid and not allow_paid:
        raise PaidCallBlocked(f"método pago {method!r} exige --allow-paid")
    cost = prices.cost_brl(method, units)  # PriceMissing / EstimateInvalid (não finito, negativo)
    if unit_kind != prices.unit_kind_of(method):
        raise EstimateInvalid(f"unit_kind {unit_kind!r} diverge da tabela de preços para {method!r}")
    if paid and (units <= 0 or round(cost, 6) < _MIN_COST_BRL):
        raise EstimateInvalid(f"estimativa inválida para {method!r}: units={units!r}, custo=R$ {cost!r}")
    if isinstance(scene_index, bool) or not MIN_SCENE <= scene_index <= MAX_SCENE:
        raise InputError(f"cena fora de {MIN_SCENE}..{MAX_SCENE}: {scene_index!r}")

    with repo.transaction(conn):  # BEGIN IMMEDIATE; rollback em qualquer exceção
        repo.get_run(conn, run_id)  # InputError se inexistente
        attempt_no = repo.next_attempt_no(conn, run_id, scene_index)
        if paid:
            available = repo.authorized_brl(conn) - repo.spent_brl(conn)
            if available + _BALANCE_EPS < cost:
                raise BudgetExceeded(
                    f"saldo R$ {max(available, 0.0):.2f} insuficiente para reserva de R$ {cost:.2f}"
                )
        attempt_id = repo.insert_pending(
            conn,
            run_id=run_id,
            scene_index=scene_index,
            attempt_no=attempt_no,
            target_s=target_s,
            method=method,
            provider=provider,
            model=model,
            units=units,
            unit_kind=unit_kind,
            reserved_brl=cost,
        )
    return SpendToken(
        _SEAL,
        conn=conn,
        attempt_id=attempt_id,
        attempt_no=attempt_no,
        method=method,
        reserved_brl=cost,
        prices=prices,
    )


def settle_ok(
    conn: sqlite3.Connection,
    token: SpendToken,
    *,
    units: float,
    cost_is_estimate: bool,
    clip_path: Path,
    duration_s: float,
    wall_time_s: float,
    provider_job_id: str | None,
) -> AttemptRow:
    """Liquida com sucesso, gravando o custo real calculado pela tabela a partir de ``units``."""
    token._require_issued()
    paid = token._prices.is_paid(token.method)
    cost = token._prices.cost_brl(token.method, units)  # EstimateInvalid se não finito/negativo
    if paid and (units <= 0 or round(cost, 6) < _MIN_COST_BRL):
        raise EstimateInvalid(f"unidades inválidas na liquidação de {token.method!r}: {units!r}")
    with repo.transaction(conn):
        token._require_pending()
        return repo.finish_attempt(
            conn,
            token.attempt_id,
            status="ok",
            cost_brl=cost,
            cost_is_estimate=cost_is_estimate,
            units=units,
            clip_path=str(clip_path),
            duration_s=duration_s,
            wall_time_s=wall_time_s,
            provider_job_id=provider_job_id,
            error=None,
        )


def settle_error(
    conn: sqlite3.Connection,
    token: SpendToken,
    *,
    billed_units: float,
    error: str,
    wall_time_s: float,
    provider_job_id: str | None,
) -> AttemptRow:
    """Liquida com erro: custo = max(faturado, reserva). Funciona mesmo sem ``consume`` prévio."""
    token._require_issued()
    billed = billed_units
    if isinstance(billed, bool) or not isinstance(billed, int | float) or not math.isfinite(billed) or billed < 0:
        billed = 0.0  # lixo do provedor não pode reduzir a reserva
    billed_cost = token._prices.cost_brl(token.method, billed)
    message = error if isinstance(error, str) and error.strip() else "erro sem mensagem"
    with repo.transaction(conn):
        row = token._require_pending()
        if billed_cost > row.reserved_brl:
            cost, units = billed_cost, billed
        else:
            cost, units = row.reserved_brl, row.units  # unidades consistentes com o custo gravado
        return repo.finish_attempt(
            conn,
            token.attempt_id,
            status="error",
            cost_brl=cost,
            cost_is_estimate=True,
            units=units,
            clip_path=None,
            duration_s=None,
            wall_time_s=wall_time_s,
            provider_job_id=provider_job_id,
            error=message,
        )
