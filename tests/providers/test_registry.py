"""Testes do registry (``providers/registry.py``) e dos tipos/retry de ``providers/base.py`` (T2).

O arquivo de teste de ``base.py`` não está na lista da T2; por isso os testes de ``base`` ficam aqui,
na segunda metade. Sem rede e sem relógio real: o ``sleep`` do retry é injetado.
"""

from __future__ import annotations

import ast
import dataclasses
import logging
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import pytest

from verion.config import PriceTable
from verion.errors import MethodUnavailable, ProviderError, UnknownMethod
from verion.providers import registry
from verion.providers.base import (
    CAMERAS,
    CostEstimate,
    Scene,
    SceneGenerator,
    SceneRequest,
    SceneResult,
    retry_before_accept,
)
from verion.providers.fake import FakeAdapter, FakeClient
from verion.providers.registry import METHODS, create

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "verion"

EXPECTED_METHODS = {
    "ken-burns": "verion.local.ken_burns:KenBurnsAdapter",
    "parallax-2.5d": "verion.local.parallax:ParallaxAdapter",
    "veo-3.1-fast": "verion.providers.veo:VeoAdapter",
    "gemini-omni-1.1-flash": "verion.providers.gemini_omni:GeminiOmniAdapter",
    "kling-3.0": "verion.providers.kling:KlingAdapter",
    "wan-2.2-5b": "verion.providers.wan:WanAdapter",
    "fake": "verion.providers.fake:FakeAdapter",
}

# --- METHODS ----------------------------------------------------------------------------------


def test_methods_are_exactly_the_seven_ids_with_the_spec_paths() -> None:
    assert dict(METHODS) == EXPECTED_METHODS
    assert len(METHODS) == 7


def test_methods_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        METHODS["novo"] = "verion.providers.novo:NovoAdapter"  # type: ignore[index]
    assert isinstance(METHODS, MappingProxyType)


def test_every_registered_class_is_inside_the_scope_of_the_ast_rule() -> None:
    """Pendência da regra 4 da T1: o teste AST só confere classes ``*Adapter`` em ``providers/*.py``
    (menos ``base.py``) e em ``local/{ken_burns,parallax}.py``. Um id do registry fora desse escopo
    escaparia da exigência de ``token.consume(self.method)``."""
    for method, path in METHODS.items():
        module, _, cls = path.partition(":")
        assert module.startswith("verion."), method
        rel = module.removeprefix("verion.").replace(".", "/") + ".py"
        parts = PurePosixPath(rel).parts
        in_providers = (
            len(parts) == 2 and parts[0] == "providers" and parts[1] != "base.py"
        )
        in_local = rel in {"local/ken_burns.py", "local/parallax.py"}
        assert in_providers or in_local, f"{method}: {rel} fora do escopo da regra AST"
        assert cls.endswith("Adapter"), (
            f"{method}: a regra AST só confere classes *Adapter ({cls})"
        )


def test_registered_class_is_defined_in_its_file_when_the_file_exists() -> None:
    for method, path in METHODS.items():
        module, _, cls = path.partition(":")
        file = SRC_ROOT / (module.removeprefix("verion.").replace(".", "/") + ".py")
        if not file.exists():  # T5, T6, T8-T11 ainda não entregaram o arquivo
            continue
        classes = {
            n.name
            for n in ast.walk(ast.parse(file.read_text("utf-8")))
            if isinstance(n, ast.ClassDef)
        }
        assert cls in classes, f"{method}: {cls} não definida em {file.name}"


def test_importing_the_registry_does_not_import_any_adapter() -> None:
    code = (
        "import sys\n"
        "import verion.providers.registry, verion.providers.base\n"
        "loaded = [m for m in sys.modules if m.startswith(('verion.providers.', 'verion.local.'))]\n"
        "bad = [m for m in loaded if m not in ('verion.providers.registry', 'verion.providers.base')]\n"
        "assert not bad, bad\n"
    )
    subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- create -----------------------------------------------------------------------------------


def test_create_returns_the_registered_adapter(prices: PriceTable) -> None:
    gen = create("fake", prices, FakeClient())
    assert isinstance(gen, FakeAdapter)
    assert isinstance(gen, SceneGenerator)
    assert gen.method == "fake"


def test_create_passes_prices_and_client_to_the_adapter(
    prices: PriceTable, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Marker(FakeClient):
        pass

    marker = Marker()
    gen = create("fake", prices, marker)
    assert gen.estimate(scene).unit_kind == prices.unit_kind_of(
        "fake"
    )  # usou a tabela recebida
    monkeypatch.setenv(
        "VERION_FAKE_MODE", "billed_error"
    )  # ignorado: o client injetado vence
    assert isinstance(create("fake", prices, marker), FakeAdapter)


def test_create_unknown_method(prices: PriceTable) -> None:
    with pytest.raises(UnknownMethod) as exc:
        create("x", prices)
    assert "'x'" in str(exc.value)
    assert exc.value.exit_code == 2
    for known in EXPECTED_METHODS:  # a mensagem ajuda: lista os ids válidos
        assert known in str(exc.value)


def test_create_unknown_method_is_not_a_prefix_or_case_match(
    prices: PriceTable,
) -> None:
    for bad in ("", "FAKE", "fake ", "ken_burns", "veo"):
        with pytest.raises(UnknownMethod):
            create(bad, prices)


def _patched(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(registry, "METHODS", {**EXPECTED_METHODS, "fake": path})


def test_create_missing_module_is_method_unavailable(
    prices: PriceTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched(monkeypatch, "verion.providers.modulo_que_nao_existe:FakeAdapter")
    with pytest.raises(MethodUnavailable) as exc:
        create("fake", prices)
    assert exc.value.exit_code == 2
    assert "fake" in str(exc.value)
    assert isinstance(exc.value.__cause__, ImportError)


def test_create_missing_class_is_method_unavailable(
    prices: PriceTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched(monkeypatch, "verion.providers.fake:ClasseQueNaoExiste")
    with pytest.raises(MethodUnavailable):
        create("fake", prices)


def test_create_missing_extra_inside_the_module_is_method_unavailable(
    prices: PriceTable, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Módulo existe, mas importa um extra não instalado (ex.: ``google-genai``)."""
    (tmp_path / "adapter_com_extra_ausente.py").write_text(
        "import pacote_opcional_que_nao_esta_instalado\n\nclass X:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _patched(monkeypatch, "adapter_com_extra_ausente:X")
    with pytest.raises(MethodUnavailable) as exc:
        create("fake", prices)
    assert "pacote_opcional_que_nao_esta_instalado" in str(exc.value)


def test_constructor_errors_are_not_masked_as_unavailable(
    prices: PriceTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from verion.errors import ConfigError

    monkeypatch.setenv("VERION_FAKE_MODE", "modo-invalido")
    with pytest.raises(ConfigError):
        create("fake", prices)


# --- base.py: tipos ---------------------------------------------------------------------------


def test_cameras_are_the_five_of_the_spec() -> None:
    assert set(CAMERAS) == {"push-in", "pull-out", "pan-left", "pan-right", "static"}


def test_scene_defaults_and_immutability(scene: Scene) -> None:
    assert scene.negative_prompt == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        scene.index = 2  # type: ignore[misc]


def test_value_types_are_frozen_dataclasses(scene: Scene, tmp_path: Path) -> None:
    req = SceneRequest(scene=scene, run_id=1, attempt_no=1, out_path=tmp_path / "a.mp4")
    assert req.seed is None
    result = SceneResult(
        clip_path=tmp_path / "a.mp4",
        duration_s=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        units=5.0,
        unit_kind="video_seconds",
        cost_is_estimate=False,
    )
    assert result.provider_job_id is None
    assert dict(result.meta) == {}
    for obj, attr in (
        (req, "run_id"),
        (result, "units"),
        (CostEstimate(1.0, "none"), "units"),
    ):
        assert dataclasses.is_dataclass(obj)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, attr, 9)


def test_result_meta_default_is_not_shared_between_instances(tmp_path: Path) -> None:
    def make() -> SceneResult:
        return SceneResult(tmp_path / "a", 1.0, 1, 1, 1.0, 1.0, "none", False)

    a, b = make(), make()
    assert a.meta is not b.meta


def test_protocol_is_runtime_checkable_and_rejects_incomplete_objects() -> None:
    class Incomplete:
        method = "x"

    assert not isinstance(Incomplete(), SceneGenerator)
    assert not isinstance(object(), SceneGenerator)


# --- base.py: retry_before_accept -------------------------------------------------------------


class Flaky:
    """Falha ``failures`` vezes com ``error`` e depois devolve ``"ok"``."""

    def __init__(self, failures: int, error: Exception) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def transient() -> ProviderError:
    return ProviderError("indisponível", transient=True)


def test_retry_returns_immediately_on_success() -> None:
    sleeps: list[float] = []
    assert retry_before_accept(lambda: 42, sleep=sleeps.append) == 42
    assert sleeps == []


def test_retry_retries_transient_errors_with_exponential_backoff() -> None:
    flaky, sleeps = Flaky(2, transient()), []
    out = retry_before_accept(
        flaky, attempts=3, initial_s=1.0, max_s=30.0, jitter_s=0.0, sleep=sleeps.append
    )
    assert out == "ok"
    assert flaky.calls == 3
    assert sleeps == [1.0, 2.0]


def test_retry_backoff_is_capped_and_has_jitter() -> None:
    flaky, sleeps = Flaky(4, transient()), []
    retry_before_accept(
        flaky,
        attempts=5,
        initial_s=4.0,
        max_s=10.0,
        jitter_s=2.0,
        sleep=sleeps.append,
        rng=lambda: 0.5,
    )
    assert sleeps == [5.0, 9.0, 11.0, 11.0]  # min(10, 4*2**n) + 0.5*2


def test_retry_gives_up_after_the_attempt_limit_and_reraises_the_last_error() -> None:
    errors = [transient() for _ in range(3)]
    it = iter(errors)
    sleeps: list[float] = []

    def always_fail() -> str:
        raise next(it)

    with pytest.raises(ProviderError) as exc:
        retry_before_accept(always_fail, attempts=3, jitter_s=0.0, sleep=sleeps.append)
    assert exc.value is errors[-1]
    assert len(sleeps) == 2  # não dorme depois da última tentativa


def test_retry_never_retries_permanent_errors() -> None:
    flaky = Flaky(5, ProviderError("chave inválida", transient=False))
    sleeps: list[float] = []
    with pytest.raises(ProviderError):
        retry_before_accept(flaky, attempts=5, sleep=sleeps.append)
    assert flaky.calls == 1 and sleeps == []


def test_retry_never_retries_once_something_was_billed() -> None:
    """Depois do aceite (``billed_units > 0``) repetir duplicaria a cobrança."""
    flaky = Flaky(
        5, ProviderError("caiu", transient=True, billed_units=2.0, provider_job_id="j1")
    )
    with pytest.raises(ProviderError) as exc:
        retry_before_accept(flaky, attempts=5, sleep=lambda s: None)
    assert flaky.calls == 1
    assert exc.value.billed_units == 2.0


def test_retry_never_retries_when_a_job_id_already_exists() -> None:
    flaky = Flaky(5, ProviderError("caiu", transient=True, provider_job_id="j1"))
    with pytest.raises(ProviderError):
        retry_before_accept(flaky, attempts=5, sleep=lambda s: None)
    assert flaky.calls == 1


def test_retry_does_not_swallow_bugs() -> None:
    flaky = Flaky(5, ValueError("bug"))
    with pytest.raises(ValueError):
        retry_before_accept(flaky, attempts=5, sleep=lambda s: None)
    assert flaky.calls == 1


@pytest.mark.parametrize("error", [ConnectionError("reset"), TimeoutError("lento")])
def test_retry_retries_stdlib_network_errors(error: Exception) -> None:
    flaky = Flaky(1, error)
    assert (
        retry_before_accept(flaky, attempts=2, jitter_s=0.0, sleep=lambda s: None)
        == "ok"
    )
    assert flaky.calls == 2


def test_retry_converts_exhausted_network_errors_to_transient_provider_error() -> None:
    flaky = Flaky(9, TimeoutError("lento"))
    with pytest.raises(ProviderError) as exc:
        retry_before_accept(flaky, attempts=2, jitter_s=0.0, sleep=lambda s: None)
    assert exc.value.transient is True
    assert exc.value.billed_units == 0.0
    assert isinstance(exc.value.__cause__, TimeoutError)


def test_retry_logs_every_retry(caplog: pytest.LogCaptureFixture) -> None:
    flaky = Flaky(2, transient())
    with caplog.at_level(logging.WARNING, logger="verion.providers.base"):
        retry_before_accept(flaky, attempts=3, jitter_s=0.0, sleep=lambda s: None)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


@pytest.mark.parametrize("bad", [0, -1])
def test_retry_rejects_invalid_attempt_count(bad: int) -> None:
    called: list[int] = []
    with pytest.raises(ValueError):
        retry_before_accept(
            lambda: called.append(1), attempts=bad, sleep=lambda s: None
        )
    assert called == []


def test_retry_default_sleep_is_not_called_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setattr(time, "sleep", lambda s: pytest.fail("não deveria dormir"))
    assert retry_before_accept(lambda: 1) == 1
