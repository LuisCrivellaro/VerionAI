"""Testes do adaptador ``fake``, do ``FakeClient`` e do helper de contrato (T2).

Sem rede, sem chave de API, sem ffmpeg. Os preços são os fictícios de tests/conftest.py
(``fake`` = R$ 1,00 por video_second).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from verion import budget
from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import ConfigError, PriceMissing, ProviderError, TokenInvalid
from verion.ledger import repo
from verion.providers import registry
from verion.providers.base import (
    CostEstimate,
    Scene,
    SceneGenerator,
    SceneRequest,
    SceneResult,
)
from verion.providers.contract import SpyClient, assert_generator_contract
from verion.providers.fake import FakeAdapter, FakeClient
from verion.providers.registry import METHODS, create

NewRun = Callable[..., int]

# --- helpers ----------------------------------------------------------------------------------


def reserve_for(
    conn: sqlite3.Connection,
    prices: PriceTable,
    gen: SceneGenerator,
    scene: Scene,
    run_id: int,
) -> SpendToken:
    est = gen.estimate(scene)
    return budget.reserve(
        conn,
        prices,
        run_id=run_id,
        scene_index=scene.index,
        target_s=scene.duration_s,
        method=gen.method,
        provider=gen.provider,
        model=gen.model,
        units=est.units,
        unit_kind=est.unit_kind,
        allow_paid=True,
    )


def request(
    scene: Scene, token: SpendToken, run_id: int, out_dir: Path
) -> SceneRequest:
    return SceneRequest(
        scene=scene,
        run_id=run_id,
        attempt_no=token.attempt_no,
        out_path=out_dir / f"s{scene.index:02d}.mp4",
    )


@pytest.fixture
def funded(authorize: Callable[[float], int]) -> None:
    authorize(1000.0)


@pytest.fixture
def run_id(new_run: NewRun) -> int:
    return new_run("fake")


def scene_n(scene: Scene, index: int) -> Scene:
    return Scene(
        index=index,
        photo=scene.photo,
        room=scene.room,
        camera=scene.camera,
        duration_s=scene.duration_s,
        prompt=scene.prompt,
    )


# --- identidade e estimate --------------------------------------------------------------------


def test_fake_identity_and_protocol(prices: PriceTable) -> None:
    gen = FakeAdapter(prices, FakeClient())
    assert isinstance(gen, SceneGenerator)
    assert (gen.method, gen.provider, gen.model) == ("fake", "fake", gen.model)
    assert gen.model


def test_fake_is_registered_under_its_own_class() -> None:
    assert METHODS["fake"] == f"{FakeAdapter.__module__}:{FakeAdapter.__qualname__}"


def test_estimate_units_are_the_scene_duration_with_the_table_unit_kind(
    prices: PriceTable, scene: Scene
) -> None:
    est = FakeAdapter(prices, FakeClient()).estimate(scene)
    assert est == CostEstimate(units=5.0, unit_kind="video_seconds")


def test_estimate_without_a_price_fails_loudly(
    prices: PriceTable, scene: Scene
) -> None:
    empty = PriceTable(fx_usd_brl=5.0, methods={})
    with pytest.raises(PriceMissing):
        FakeAdapter(empty, FakeClient()).estimate(scene)


def test_constructor_touches_nothing(
    prices: PriceTable, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    FakeAdapter(prices, FakeClient())
    assert list(tmp_path.iterdir()) == []


# --- contrato (helper) ------------------------------------------------------------------------


def test_contract_passes_on_the_fake(
    prices: PriceTable, conn: sqlite3.Connection, scene: Scene, out_dir: Path
) -> None:
    assert_generator_contract(
        lambda client: create("fake", prices, client),
        conn=conn,
        prices=prices,
        scene=scene,
        good_client=FakeClient(),
        out_dir=out_dir,
    )


def test_contract_authorizes_1000_and_leaves_no_pending_rows(
    prices: PriceTable, conn: sqlite3.Connection, scene: Scene, out_dir: Path
) -> None:
    assert_generator_contract(
        lambda client: create("fake", prices, client),
        conn=conn,
        prices=prices,
        scene=scene,
        good_client=FakeClient(),
        out_dir=out_dir,
    )
    assert repo.authorized_brl(conn) == 1000.0
    attempts = repo.list_attempts(conn)
    assert attempts, "o helper deveria ter exercitado o ledger"
    assert all(a.status != "pending" for a in attempts)
    assert repo.spent_brl(conn) <= 1000.0


def test_contract_works_for_a_free_method_without_authorization(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Método grátis passa pelo helper sem exigir saldo (gerador mínimo que escreve um arquivo)."""

    class FreeGen:
        method, provider, model = "ken-burns", "local", "ffmpeg"

        def __init__(self, client: object | None) -> None:
            self.client = client

        def estimate(self, scene: Scene) -> CostEstimate:
            return CostEstimate(units=0.0, unit_kind="none")

        def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
            token.consume(self.method)
            req.out_path.parent.mkdir(parents=True, exist_ok=True)
            req.out_path.write_bytes(b"x")
            return SceneResult(req.out_path, 5.0, 1920, 1080, 30.0, 0.0, "none", False)

    _register(monkeypatch, FreeGen, method="ken-burns")
    assert_generator_contract(
        lambda client: FreeGen(client),  # type: ignore[arg-type,return-value]
        conn=conn,
        prices=prices,
        scene=scene,
        good_client=object(),
        out_dir=out_dir,
    )
    assert repo.authorized_brl(conn) == 0.0  # grátis: o helper não autoriza nada


# --- contrato: o helper precisa REPROVAR geradores defeituosos --------------------------------


def _register(monkeypatch: pytest.MonkeyPatch, cls: type, method: str = "fake") -> None:
    monkeypatch.setattr(
        registry, "METHODS", {**METHODS, method: f"{cls.__module__}:{cls.__qualname__}"}
    )


class GoodBase:
    """Gerador mínimo correto; os defeituosos herdam e quebram uma regra por vez."""

    method, provider, model = "fake", "fake", "fake-1"

    def __init__(self, client: object | None = None) -> None:
        self.client = client

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=scene.duration_s, unit_kind="video_seconds")

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        token.consume(self.method)
        self.client.submit(req.scene)  # type: ignore[attr-defined]
        return self._write(req)

    def _write(self, req: SceneRequest) -> SceneResult:
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        req.out_path.write_bytes(b"bytes")
        return SceneResult(
            req.out_path,
            req.scene.duration_s,
            1920,
            1080,
            30.0,
            req.scene.duration_s,
            "video_seconds",
            False,
        )


class SpyableClient:
    def submit(self, scene: Scene) -> str:
        return "job"


class UsesClientBeforeConsume(GoodBase):
    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        self.client.submit(req.scene)  # type: ignore[attr-defined]
        token.consume(self.method)
        return self._write(req)


class NeverConsumes(GoodBase):
    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        return self._write(req)


class WrongUnitKind(GoodBase):
    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=scene.duration_s, unit_kind="gpu_seconds")


class ZeroUnitsWhenPaid(GoodBase):
    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=0.0, unit_kind="video_seconds")


class NoFileWritten(GoodBase):
    def _write(self, req: SceneRequest) -> SceneResult:
        return SceneResult(
            req.out_path, 5.0, 1920, 1080, 30.0, 5.0, "video_seconds", False
        )


class WritesElsewhere(GoodBase):
    def _write(self, req: SceneRequest) -> SceneResult:
        other = req.out_path.with_name("outro.mp4")
        other.write_bytes(b"x")
        return SceneResult(other, 5.0, 1920, 1080, 30.0, 5.0, "video_seconds", False)


class ZeroUnitsInResult(GoodBase):
    def _write(self, req: SceneRequest) -> SceneResult:
        result = super()._write(req)
        return SceneResult(
            result.clip_path, 5.0, 1920, 1080, 30.0, 0.0, "video_seconds", False
        )


class WrongMethodAttribute(GoodBase):
    method = "metodo-nao-registrado"


class MissingGenerate:
    method, provider, model = "fake", "fake", "fake-1"

    def __init__(self, client: object | None = None) -> None: ...

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(units=1.0, unit_kind="video_seconds")


def run_contract(
    prices: PriceTable, conn: sqlite3.Connection, scene: Scene, out_dir: Path, cls: type
) -> None:
    assert_generator_contract(
        lambda client: cls(client),
        conn=conn,
        prices=prices,
        scene=scene,
        good_client=SpyableClient(),
        out_dir=out_dir,
    )


def test_contract_accepts_the_minimal_good_generator(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(monkeypatch, GoodBase)
    run_contract(prices, conn, scene, out_dir, GoodBase)


@pytest.mark.parametrize(
    "cls",
    [
        UsesClientBeforeConsume,
        NeverConsumes,
        WrongUnitKind,
        ZeroUnitsWhenPaid,
        NoFileWritten,
        WritesElsewhere,
        ZeroUnitsInResult,
        WrongMethodAttribute,
        MissingGenerate,
    ],
)
def test_contract_rejects_defective_generators(
    cls: type,
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(monkeypatch, cls)
    with pytest.raises(AssertionError):
        run_contract(prices, conn, scene, out_dir, cls)


def test_contract_rejects_the_wrong_class_for_a_registered_id(
    prices: PriceTable, conn: sqlite3.Connection, scene: Scene, out_dir: Path
) -> None:
    class Impostor(GoodBase):
        pass

    with pytest.raises(AssertionError, match="registry"):
        run_contract(
            prices, conn, scene, out_dir, Impostor
        )  # method="fake", mas o registry aponta p/ FakeAdapter


# --- SpyClient --------------------------------------------------------------------------------


def test_spy_client_counts_accesses_and_calls() -> None:
    class Target:
        value = 7

        def ping(self, x: int) -> int:
            return x + 1

    spy = SpyClient(Target())
    assert (spy.accesses, spy.calls) == (0, 0)
    assert spy.value == 7
    assert (spy.accesses, spy.calls) == (1, 0)
    assert spy.ping(1) == 2
    assert (spy.accesses, spy.calls) == (2, 1)
    assert spy.ping(2) == 3
    assert spy.calls == 2


def test_spy_client_counts_calls_of_a_callable_client() -> None:
    spy = SpyClient(lambda *a, **k: "ran")
    assert spy("x", y=1) == "ran"
    assert spy.calls == 1


def test_spy_client_propagates_missing_attributes() -> None:
    spy = SpyClient(object())
    with pytest.raises(AttributeError):
        spy.nope  # noqa: B018


# --- FakeAdapter.generate: modo ok ------------------------------------------------------------


def test_ok_writes_a_file_of_bytes_that_is_not_an_mp4(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient())
    token = reserve_for(conn, prices, gen, scene, run_id)
    result = gen.generate(request(scene, token, run_id, out_dir), token)

    data = result.clip_path.read_bytes()
    assert result.clip_path == out_dir / "s01.mp4"
    assert data, "arquivo vazio"
    assert data[4:8] != b"ftyp", "o fake não pode gravar um MP4"
    assert not data.startswith(b"\x00\x00\x00")  # nem cabeçalho de caixa ISO-BMFF
    assert b"FAKE" in data  # marcador legível: é claramente um clipe fictício


def test_ok_result_fields(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient())
    token = reserve_for(conn, prices, gen, scene, run_id)
    result = gen.generate(request(scene, token, run_id, out_dir), token)

    assert isinstance(result, SceneResult)
    assert (result.duration_s, result.units, result.unit_kind) == (
        5.0,
        5.0,
        "video_seconds",
    )
    assert result.cost_is_estimate is False
    assert (result.width, result.height, result.fps) == (1920, 1080, 30.0)
    assert result.provider_job_id and result.provider_job_id.startswith("fake-job-")
    assert result.meta["mode"] == "ok"


def test_ok_creates_missing_parent_directories(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    tmp_path: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient())
    token = reserve_for(conn, prices, gen, scene, run_id)
    deep = tmp_path / "a" / "b" / "c"
    req = SceneRequest(
        scene=scene, run_id=run_id, attempt_no=1, out_path=deep / "clip.mp4"
    )
    assert gen.generate(req, token).clip_path.exists()


def test_ok_records_the_job_id_in_the_ledger(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient())
    token = reserve_for(conn, prices, gen, scene, run_id)
    result = gen.generate(request(scene, token, run_id, out_dir), token)
    row = repo.get_attempt(conn, token.attempt_id)
    assert row is not None and row.status == "pending"
    assert row.provider_job_id == result.provider_job_id


def test_ok_settles_with_the_table_price(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient())
    token = reserve_for(conn, prices, gen, scene, run_id)
    result = gen.generate(request(scene, token, run_id, out_dir), token)
    row = budget.settle_ok(
        conn,
        token,
        units=result.units,
        cost_is_estimate=result.cost_is_estimate,
        clip_path=result.clip_path,
        duration_s=result.duration_s,
        wall_time_s=1.0,
        provider_job_id=result.provider_job_id,
    )
    assert row.status == "ok" and row.cost_brl == pytest.approx(5.0)


def test_generate_consumes_the_token_first(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient())
    token = reserve_for(conn, prices, gen, scene, run_id)
    gen.generate(request(scene, token, run_id, out_dir), token)
    with pytest.raises(TokenInvalid):
        gen.generate(request(scene, token, run_id, out_dir), token)


def test_token_of_another_method_is_refused_before_touching_the_client(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    new_run: NewRun,
) -> None:
    other_run = new_run("ken-burns")
    token = budget.reserve(
        conn, prices, run_id=other_run, scene_index=1, target_s=5.0, method="ken-burns",
        provider="local", model="ffmpeg", units=0.0, unit_kind="none", allow_paid=False,
    )  # fmt: skip
    spy = SpyClient(FakeClient())
    gen = FakeAdapter(prices, spy)
    req = SceneRequest(
        scene=scene, run_id=other_run, attempt_no=1, out_path=out_dir / "x.mp4"
    )
    with pytest.raises(TokenInvalid):
        gen.generate(req, token)
    assert spy.calls == 0 and not req.out_path.exists()


def test_forged_token_is_refused_before_touching_the_client(
    prices: PriceTable, scene: Scene, out_dir: Path
) -> None:
    forged = SpendToken.__new__(SpendToken)
    spy = SpyClient(FakeClient())
    gen = FakeAdapter(prices, spy)
    req = SceneRequest(scene=scene, run_id=1, attempt_no=1, out_path=out_dir / "x.mp4")
    with pytest.raises(TokenInvalid):
        gen.generate(req, forged)
    assert spy.calls == 0 and not req.out_path.exists()


# --- FakeAdapter.generate: modo transient -----------------------------------------------------


def test_transient_fails_before_acceptance_without_billing(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient(["transient"]))
    token = reserve_for(conn, prices, gen, scene, run_id)
    req = request(scene, token, run_id, out_dir)
    with pytest.raises(ProviderError) as exc:
        gen.generate(req, token)

    assert exc.value.transient is True
    assert exc.value.billed_units == 0.0
    assert exc.value.provider_job_id is None
    assert not req.out_path.exists()
    row = repo.get_attempt(conn, token.attempt_id)
    assert row is not None and row.provider_job_id is None  # nunca houve aceite


def test_transient_settles_at_the_reservation(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient(["transient"]))
    token = reserve_for(conn, prices, gen, scene, run_id)
    with pytest.raises(ProviderError) as exc:
        gen.generate(request(scene, token, run_id, out_dir), token)
    row = budget.settle_error(
        conn, token, billed_units=exc.value.billed_units, error=str(exc.value), wall_time_s=0.5,
        provider_job_id=exc.value.provider_job_id,
    )  # fmt: skip
    assert row.status == "error" and row.cost_brl == pytest.approx(
        token.reserved_brl
    ) == pytest.approx(5.0)


# --- FakeAdapter.generate: modo billed_error --------------------------------------------------


def test_billed_error_is_accepted_and_billed_for_the_whole_duration(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient(["billed_error"]))
    token = reserve_for(conn, prices, gen, scene, run_id)
    req = request(scene, token, run_id, out_dir)
    with pytest.raises(ProviderError) as exc:
        gen.generate(req, token)

    err = exc.value
    assert err.transient is False
    assert err.billed_units == scene.duration_s
    assert err.provider_job_id and err.provider_job_id.startswith("fake-job-")
    assert not req.out_path.exists()
    row = repo.get_attempt(conn, token.attempt_id)
    assert (
        row is not None and row.provider_job_id == err.provider_job_id
    )  # job_accepted foi chamado


def test_billed_error_settles_at_max_of_billed_and_reservation(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient(["billed_error"]))
    token = reserve_for(conn, prices, gen, scene, run_id)
    with pytest.raises(ProviderError) as exc:
        gen.generate(request(scene, token, run_id, out_dir), token)
    row = budget.settle_error(
        conn, token, billed_units=exc.value.billed_units, error=str(exc.value), wall_time_s=1.0,
        provider_job_id=exc.value.provider_job_id,
    )  # fmt: skip
    assert row.cost_brl == pytest.approx(5.0)
    assert repo.spent_brl(conn) == pytest.approx(5.0)


# --- sequência de VERION_FAKE_MODE ------------------------------------------------------------


def outcomes(
    gen: FakeAdapter,
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    run_id: int,
    out_dir: Path,
    n: int,
) -> list[str]:
    """Roda ``n`` cenas (índices 1..n) e devolve 'ok' | 'transient' | 'billed_error' por cena."""
    seen: list[str] = []
    for i in range(1, n + 1):
        s = scene_n(scene, i)
        token = reserve_for(conn, prices, gen, s, run_id)
        try:
            result = gen.generate(request(s, token, run_id, out_dir), token)
        except ProviderError as exc:
            seen.append("transient" if exc.transient else "billed_error")
        else:
            seen.append(result.meta["mode"])
    return seen


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ["ok", "ok", "ok"]),
        ("", ["ok", "ok", "ok"]),
        ("ok", ["ok", "ok", "ok"]),
        ("transient", ["transient", "transient", "transient"]),
        ("billed_error", ["billed_error", "billed_error", "billed_error"]),
        ("ok,transient", ["ok", "transient", "transient"]),  # o último se repete
        ("transient,ok", ["transient", "ok", "ok"]),
        ("ok, billed_error ,ok", ["ok", "billed_error", "ok"]),  # espaços tolerados
        ("billed_error,transient,ok", ["billed_error", "transient", "ok"]),
    ],
)
def test_fake_mode_sequence_from_the_environment(
    value: str | None,
    expected: list[str],
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if value is None:
        monkeypatch.delenv("VERION_FAKE_MODE", raising=False)
    else:
        monkeypatch.setenv("VERION_FAKE_MODE", value)
    gen = FakeAdapter(prices)  # client padrão: FakeClient.from_env()
    assert outcomes(gen, conn, prices, scene, run_id, out_dir, 3) == expected


def test_last_mode_repeats_beyond_the_sequence(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    run_id: int,
    funded: None,
) -> None:
    gen = FakeAdapter(prices, FakeClient(["transient", "ok"]))
    assert (
        outcomes(gen, conn, prices, scene, run_id, out_dir, 6)
        == ["transient"] + ["ok"] * 5
    )


def test_each_adapter_instance_starts_its_own_sequence(
    prices: PriceTable,
    conn: sqlite3.Connection,
    scene: Scene,
    out_dir: Path,
    new_run: NewRun,
    funded: None,
) -> None:
    first = outcomes(FakeAdapter(prices, FakeClient.from_env({"VERION_FAKE_MODE": "transient,ok"})),
                     conn, prices, scene, new_run("fake"), out_dir, 2)  # fmt: skip
    second = outcomes(FakeAdapter(prices, FakeClient.from_env({"VERION_FAKE_MODE": "transient,ok"})),
                      conn, prices, scene, new_run("fake"), out_dir, 2)  # fmt: skip
    assert first == second == ["transient", "ok"]


# --- FakeClient -------------------------------------------------------------------------------


def test_from_env_default_is_ok() -> None:
    assert FakeClient.from_env({}).modes == ("ok",)


def test_from_env_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERION_FAKE_MODE", "transient,ok")
    assert FakeClient.from_env().modes == ("transient", "ok")


@pytest.mark.parametrize(
    "bad", ["nope", "OK", "ok,,ok", "ok,", ",ok", "ok;transient", "billed-error", ","]
)
def test_invalid_mode_is_a_config_error(bad: str) -> None:
    with pytest.raises(ConfigError) as exc:
        FakeClient.from_env({"VERION_FAKE_MODE": bad})
    assert "VERION_FAKE_MODE" in str(exc.value)
    assert exc.value.exit_code == 2


def test_empty_mode_list_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        FakeClient([])


def test_invalid_mode_in_constructor_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        FakeClient(["ok", "explode"])


def test_job_ids_are_unique_and_deterministic(scene: Scene) -> None:
    client = FakeClient()
    assert [client.submit(scene_n(scene, i)) for i in (1, 2, 3)] == [
        "fake-job-0001",
        "fake-job-0002",
        "fake-job-0003",
    ]


def test_fetch_of_unknown_job_is_a_permanent_provider_error() -> None:
    with pytest.raises(ProviderError) as exc:
        FakeClient().fetch("fake-job-9999")
    assert exc.value.transient is False and exc.value.billed_units == 0.0


def test_latency_hook_advances_an_injected_clock_and_never_sleeps(
    prices: PriceTable, conn: sqlite3.Connection, scene: Scene, out_dir: Path, run_id: int, funded: None,
    fake_clock: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    import time

    monkeypatch.setattr(time, "sleep", lambda s: pytest.fail("sleep real"))
    gen = FakeAdapter(prices, FakeClient(latency_s=2.5, sleep=fake_clock.advance))
    token = reserve_for(conn, prices, gen, scene, run_id)
    start = fake_clock()
    gen.generate(request(scene, token, run_id, out_dir), token)
    assert fake_clock() - start == 2.5


def test_latency_applies_to_billed_error_but_not_to_transient(
    fake_clock: Any, scene: Scene
) -> None:
    client = FakeClient(
        ["transient", "billed_error"], latency_s=3.0, sleep=fake_clock.advance
    )
    with pytest.raises(ProviderError):
        client.submit(scene)
    assert fake_clock() == 0.0  # falhou antes do aceite: sem espera
    job = client.submit(scene)
    with pytest.raises(ProviderError):
        client.fetch(job)
    assert fake_clock() == 3.0


def test_default_latency_is_zero_and_default_sleep_is_inert(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    monkeypatch.setattr(time, "sleep", lambda s: pytest.fail("sleep real"))
    client = FakeClient()
    assert client.fetch(client.submit(scene))


def test_modes_are_exposed_as_an_immutable_tuple() -> None:
    client = FakeClient(["ok", "transient"])
    assert client.modes == ("ok", "transient")
    assert isinstance(client.modes, tuple)
