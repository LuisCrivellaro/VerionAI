"""Helper de contrato dos adaptadores (spec 3.4). Sem pytest: falhas viram ``AssertionError``.

Cada tarefa de adaptador chama ``assert_generator_contract`` no próprio teste, com o próprio mock:
sem rede e sem mock alheio. ``good_client`` TEM de ser um mock/fake local (o helper chama o adaptador
de verdade, então um client real geraria chamadas e custo). O helper escreve no ``conn`` recebido
(autorização de R$ 1.000 para métodos pagos, runs e tentativas de teste) e por isso EXIGE um banco
descartável: com autorização, run ou tentativa prévios ele recusa. Este é, junto com a CLI, o único
módulo de ``src/verion`` autorizado a chamar ``add_authorization`` (allow-list do teste AST).

O que o contrato verifica:

1. ``isinstance(gen, SceneGenerator)`` e ``METHODS[gen.method]`` apontando para a classe de ``gen``;
2. ``estimate`` com o ``unit_kind`` da tabela e ``units > 0`` se o método é pago;
3. token forjado, já usado ou de outro método → ``TokenInvalid``, sem NENHUM acesso ao client
   (``SpyClient.accesses == 0`` e ``calls == 0``, inclusive em atributos aninhados como
   ``client.models.generate_videos``; vale também para o construtor) e sem arquivo gravado
   (``token.consume`` é a primeira instrução de ``generate``);
4. com ``good_client`` e um token de ``budget.reserve``: ``SceneResult`` coerente (arquivo em
   ``req.out_path``; largura, altura, fps e duração > 0; ``units`` dentro de ``units_tolerance`` do
   ``estimate``), no máximo ``max_client_calls`` chamadas ao client, e liquidável por
   ``budget.settle_ok``. Em método pago, ``token.job_accepted`` precisa ter gravado no ledger o
   mesmo ``provider_job_id`` do resultado (sem isso, um crash deixaria o job faturado sem id).

``max_client_calls`` vale 1 por padrão (uma submissão). Adaptadores com polling ou upload passam o
limite exato do próprio mock (ex.: submit + 2 consultas = 3).

Fora deste helper (item 6 da revisão 08): as falhas ``transient`` (antes do aceite) e pós-aceite
(``ProviderError(billed_units=...)``) são requisito dos testes de cada adaptador pago (T8, T9, T10, T11);
o helper cobre apenas o caminho feliz e a recusa de token.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
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


_PRIMITIVES = (str, bytes, int, float, bool, type(None))


class _Counts:
    """Contadores compartilhados entre o ``SpyClient`` raiz e todos os proxies filhos."""

    def __init__(self) -> None:
        self.accesses = 0
        self.calls = 0


class SpyClient:
    """Embrulha um client e conta, em qualquer profundidade, ``accesses`` (atributos lidos) e ``calls``
    (chamadas).

    Atributos não primitivos (objetos, métodos) voltam embrulhados no mesmo contador, então
    ``spy.models.generate_videos(...)`` conta 2 acessos e 1 chamada, o formato do SDK google-genai.
    Primitivos (str, bytes, números, bool, None) e o retorno das chamadas voltam sem embrulho. É um
    proxy de mock: dunders (``__iter__``, ``__enter__``...) e escrita de atributos não são repassados.
    """

    def __init__(self, wrapped: object, _counts: _Counts | None = None) -> None:
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(
            self, "_counts", _counts if _counts is not None else _Counts()
        )

    @property
    def accesses(self) -> int:
        counts: _Counts = object.__getattribute__(self, "_counts")
        return counts.accesses

    @property
    def calls(self) -> int:
        counts: _Counts = object.__getattribute__(self, "_counts")
        return counts.calls

    def __getattr__(self, name: str) -> Any:
        # Só roda para nomes que o proxy não tem (``_wrapped``, ``_counts``, ``accesses`` e ``calls`` não contam).
        if name.startswith("__") or name in ("_wrapped", "_counts"):
            raise AttributeError(name)
        counts: _Counts = object.__getattribute__(self, "_counts")
        counts.accesses += 1
        attr = getattr(object.__getattribute__(self, "_wrapped"), name)
        if isinstance(attr, _PRIMITIVES):
            return attr
        return SpyClient(attr, counts)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        counts: _Counts = object.__getattribute__(self, "_counts")
        counts.calls += 1
        return object.__getattribute__(self, "_wrapped")(*args, **kwargs)


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
    max_client_calls: int = 1,
    units_tolerance: tuple[float, float] = (0.5, 2.0),
) -> None:
    """Verifica o contrato da spec 3.4. ``factory(client)`` cria o adaptador com o client injetado.

    ``conn`` deve ser um banco descartável e ``good_client`` um mock (ver o docstring do módulo).
    ``result.units`` precisa ficar entre ``units_tolerance[0]`` e ``units_tolerance[1]`` vezes o ``estimate``.
    """
    _check(
        not repo.list_authorizations(conn)
        and not repo.list_runs(conn)
        and not repo.list_attempts(conn),
        "conn precisa ser um banco descartável (sem autorizações, runs ou tentativas): o helper grava nele",
    )
    _check(max_client_calls >= 0, f"max_client_calls inválido: {max_client_calls!r}")
    low, high = units_tolerance
    _check(0 < low <= high, f"units_tolerance inválida: {units_tolerance!r}")
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
            spy.calls == 0 and spy.accesses == 0,
            f"token {label}: o client foi usado antes de token.consume "
            f"({spy.accesses} acessos, {spy.calls} chamadas)",
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
    _check(
        low * est.units <= result.units <= high * est.units,
        f"result.units={result.units!r} fora de {low}x a {high}x do estimate.units={est.units!r}",
    )
    for field_name in ("duration_s", "width", "height", "fps"):
        value = getattr(result, field_name)
        _check(
            isinstance(value, int | float) and math.isfinite(value) and value > 0,
            f"result.{field_name}={value!r} deve ser > 0",
        )
    _check(
        spy.calls <= max_client_calls,
        f"o client recebeu {spy.calls} chamadas no caminho feliz (máximo {max_client_calls}); "
        "submissão duplicada fatura duas vezes. Com polling, aumente max_client_calls",
    )
    if paid:
        # Se o processo cair depois do aceite, só o ledger sabe qual job foi faturado.
        row = repo.get_attempt(conn, token.attempt_id)
        recorded = None if row is None else row.provider_job_id
        _check(
            recorded is not None and recorded == result.provider_job_id,
            f"token.job_accepted não gravou o id do job no ledger "
            f"(ledger={recorded!r}, result.provider_job_id={result.provider_job_id!r})",
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
