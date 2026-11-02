"""Helper de contrato dos adaptadores (spec 3.4). Sem pytest: falhas viram ``AssertionError``.

Cada tarefa de adaptador chama ``assert_generator_contract`` no próprio teste, com o próprio mock:
sem rede e sem mock alheio. O helper escreve no ``conn`` recebido (autorização de R$ 1.000 para métodos
pagos, runs e tentativas de teste), então deve receber um banco descartável. Este é, junto com a CLI,
o único módulo de ``src/verion`` autorizado a chamar ``add_authorization`` (allow-list do teste AST).

O que o contrato verifica:

1. ``isinstance(gen, SceneGenerator)`` e ``METHODS[gen.method]`` apontando para a classe de ``gen``;
2. ``estimate`` com o ``unit_kind`` da tabela e ``units > 0`` se o método é pago;
3. token forjado, já usado ou de outro método → ``TokenInvalid``, com ``SpyClient.calls == 0`` e sem
   arquivo gravado (``token.consume`` é a primeira instrução de ``generate``);
4. com ``good_client`` e um token de ``budget.reserve``: ``SceneResult`` coerente, com o arquivo em
   ``req.out_path``, e liquidável por ``budget.settle_ok``.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from verion import budget
from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import TokenInvalid
from verion.ledger import repo
from verion.providers import registry
from verion.providers.base import Scene, SceneGenerator, SceneRequest, SceneResult

_AUTHORIZED_BRL = 1000.0
_OTHER_FREE_METHODS = (
    "ken-burns",
    "parallax-2.5d",
)  # grátis: reservam R$ 0,00, sem precisar de saldo


class SpyClient:
    """Embrulha um client e conta ``accesses`` (atributos lidos) e ``calls`` (chamadas, inclusive ao próprio client)."""

    def __init__(self, wrapped: object) -> None:
        self._wrapped = wrapped
        self.accesses = 0
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        # Só roda para nomes que a própria SpyClient não tem; `_wrapped`, `accesses` e `calls` não contam.
        if name.startswith("__") or name == "_wrapped":
            raise AttributeError(name)
        self.accesses += 1
        attr = getattr(self._wrapped, name)
        if not callable(attr):
            return attr

        @wraps(attr)
        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return attr(*args, **kwargs)

        return counted

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._wrapped(*args, **kwargs)  # type: ignore[operator]


def _check(condition: bool, message: str) -> None:
    """``assert`` que sobrevive a ``python -O``."""
    if not condition:
        raise AssertionError(f"contrato do adaptador: {message}")


def _class_path(obj: object) -> str:
    cls = type(obj)
    return f"{cls.__module__}:{cls.__qualname__}"


def assert_generator_contract(
    factory: Callable[[object | None], SceneGenerator],
    *,
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    good_client: object,
    out_dir: Path,
) -> None:
    """Verifica o contrato da spec 3.4. ``factory(client)`` cria o adaptador com o client injetado."""
    gen = factory(good_client)

    # (1) tipo e registry
    _check(
        isinstance(gen, SceneGenerator),
        f"{type(gen).__name__} não implementa SceneGenerator",
    )
    registered = registry.METHODS.get(gen.method)
    _check(registered is not None, f"método {gen.method!r} ausente do registry")
    _check(
        registered == _class_path(gen),
        f"registry aponta {gen.method!r} para {registered!r}, mas o adaptador é {_class_path(gen)!r}",
    )

    # (2) estimate
    paid = prices.is_paid(gen.method)
    est = gen.estimate(scene)
    expected_kind = prices.unit_kind_of(gen.method)
    _check(
        est.unit_kind == expected_kind,
        f"estimate.unit_kind={est.unit_kind!r}, a tabela diz {expected_kind!r}",
    )
    _check(
        math.isfinite(est.units) and est.units >= 0,
        f"estimate.units inválido: {est.units!r}",
    )
    _check(not paid or est.units > 0, f"método pago com estimate.units={est.units!r}")

    if paid:
        repo.add_authorization(conn, _AUTHORIZED_BRL, "assert_generator_contract")
    work = out_dir / "contract"
    work.mkdir(
        parents=True, exist_ok=True
    )  # o harness cria o diretório do clipe; o helper faz o mesmo

    def new_token(method: str, units: float, unit_kind: str) -> tuple[int, SpendToken]:
        run_id = repo.create_run(
            conn,
            property_id="contrato",
            method=method,
            script_path="contract",
            script_sha256="0" * 64,
        )
        token = budget.reserve(
            conn,
            prices,
            run_id=run_id,
            scene_index=scene.index,
            target_s=scene.duration_s,
            method=method,
            provider=gen.provider,
            model=gen.model,
            units=units,
            unit_kind=unit_kind,  # type: ignore[arg-type]
            allow_paid=True,
        )
        return run_id, token

    def release(token: SpendToken) -> None:
        """Liquida a reserva de um token que o adaptador recusou (custo = reserva, nada some do ledger)."""
        budget.settle_error(
            conn,
            token,
            billed_units=0.0,
            error="contrato: token recusado",
            wall_time_s=0.0,
            provider_job_id=None,
        )

    def expect_refusal(label: str, run_id: int, token: SpendToken) -> None:
        spy = SpyClient(good_client)
        out = work / f"{label}.mp4"
        req = SceneRequest(
            scene=scene,
            run_id=run_id,
            attempt_no=getattr(token, "attempt_no", 1),
            out_path=out,
        )
        try:
            factory(spy).generate(req, token)
        except TokenInvalid:
            pass
        except Exception as exc:
            raise AssertionError(
                f"contrato do adaptador: token {label}: esperado TokenInvalid, veio {exc!r}"
            ) from exc
        else:
            raise AssertionError(
                f"contrato do adaptador: token {label} foi aceito por generate"
            )
        _check(
            spy.calls == 0,
            f"token {label}: o client foi chamado {spy.calls}x antes de token.consume",
        )
        _check(not out.exists(), f"token {label}: arquivo gravado apesar da recusa")

    # (3a) forjado: sem passar por budget.reserve
    forged = SpendToken.__new__(SpendToken)
    expect_refusal("forjado", run_id=1, token=forged)

    # (3b) já usado
    run_id, used = new_token(gen.method, est.units, est.unit_kind)
    used.consume(gen.method)
    expect_refusal("reusado", run_id, used)
    release(used)

    # (3c) de outro método (grátis, para não depender de saldo nem de preço)
    other = next(m for m in _OTHER_FREE_METHODS if m != gen.method)
    run_id, foreign = new_token(other, 0.0, prices.unit_kind_of(other))
    expect_refusal("de_outro_metodo", run_id, foreign)
    release(foreign)

    # (4) caminho feliz
    spy = SpyClient(good_client)
    good = factory(spy)
    run_id, token = new_token(gen.method, est.units, est.unit_kind)
    req = SceneRequest(
        scene=scene,
        run_id=run_id,
        attempt_no=token.attempt_no,
        out_path=work / "ok.mp4",
    )
    result = good.generate(req, token)
    _check(
        isinstance(result, SceneResult),
        f"generate devolveu {type(result).__name__}, não SceneResult",
    )
    _check(
        result.clip_path == req.out_path,
        f"clip_path={result.clip_path} difere de req.out_path={req.out_path}",
    )
    _check(
        result.clip_path.is_file(), f"arquivo do clipe não existe: {result.clip_path}"
    )
    _check(
        result.unit_kind == expected_kind,
        f"result.unit_kind={result.unit_kind!r}, a tabela diz {expected_kind!r}",
    )
    _check(
        math.isfinite(result.units) and result.units >= 0,
        f"result.units inválido: {result.units!r}",
    )
    _check(
        not paid or result.units > 0, f"método pago com result.units={result.units!r}"
    )
    # O resultado precisa ser liquidável: é o que o harness fará logo em seguida.
    budget.settle_ok(
        conn,
        token,
        units=result.units,
        cost_is_estimate=result.cost_is_estimate,
        clip_path=result.clip_path,
        duration_s=result.duration_s,
        wall_time_s=0.0,
        provider_job_id=result.provider_job_id,
    )


__all__ = ["SpyClient", "assert_generator_contract"]
