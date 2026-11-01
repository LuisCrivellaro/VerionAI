"""Testes de errors.py e config.py (preços)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from verion import errors
from verion.config import FREE_METHODS, MethodPrice, PriceTable, load_prices

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_prices(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "prices.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- errors.py -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.InputError, 2),
        (errors.ConfigError, 2),
        (errors.SchemaMismatch, 2),
        (errors.ScriptError, 2),
        (errors.UnknownMethod, 2),
        (errors.MethodUnavailable, 2),
        (errors.InvalidVerdict, 2),
        (errors.AuthorizationRefused, 2),
        (errors.DepthMissing, 2),
        (errors.SpendBlocked, 3),
        (errors.PaidCallBlocked, 3),
        (errors.BudgetExceeded, 3),
        (errors.PriceMissing, 3),
        (errors.EstimateInvalid, 3),
        (errors.TokenInvalid, 3),
        (errors.ProviderError, 4),
        (errors.AttemptRuleError, 5),
        (errors.AwaitingQC, 5),
        (errors.SceneClosed, 5),
        (errors.PendingAttempt, 5),
        (errors.FfmpegError, 6),
    ],
)
def test_exit_codes(exc: type[errors.VerionError], code: int) -> None:
    assert issubclass(exc, errors.VerionError)
    assert exc.exit_code == code


def test_hierarchy_bases() -> None:
    assert issubclass(errors.ConfigError, errors.InputError)
    assert issubclass(errors.PaidCallBlocked, errors.SpendBlocked)
    assert issubclass(errors.SceneClosed, errors.AttemptRuleError)


def test_provider_error_fields() -> None:
    err = errors.ProviderError("falhou", transient=True, billed_units=4.0, provider_job_id="job-1")
    assert err.transient is True
    assert err.billed_units == 4.0
    assert err.provider_job_id == "job-1"
    assert "falhou" in str(err)
    default = errors.ProviderError("x", transient=False)
    assert default.billed_units == 0.0
    assert default.provider_job_id is None


def test_ffmpeg_error_carries_stderr() -> None:
    err = errors.FfmpegError("ffmpeg falhou", stderr="Invalid data")
    assert err.stderr == "Invalid data"


# --- config.py -------------------------------------------------------------------------------


def test_free_methods_is_fixed() -> None:
    assert frozenset({"ken-burns", "parallax-2.5d"}) == FREE_METHODS


def test_is_paid(prices: PriceTable) -> None:
    assert not prices.is_paid("ken-burns")
    assert not prices.is_paid("parallax-2.5d")
    for method in ("veo-3.1-fast", "gemini-omni-1.1-flash", "kling-3.0", "wan-2.2-5b", "fake"):
        assert prices.is_paid(method)
    # Método desconhecido é pago por padrão (nunca grátis por omissão).
    assert prices.is_paid("qualquer-outro")


def test_cost_brl_converts_usd_and_keeps_brl(prices: PriceTable) -> None:
    assert prices.cost_brl("veo-3.1-fast", 8.0) == pytest.approx(8.0 * 0.10 * 5.0)
    assert prices.cost_brl("fake", 3.0) == pytest.approx(3.0)


def test_cost_brl_free_is_zero_even_if_absent() -> None:
    table = PriceTable(fx_usd_brl=5.0, methods={})
    assert table.cost_brl("ken-burns", 10.0) == 0.0


def test_cost_brl_paid_absent_raises_price_missing() -> None:
    table = PriceTable(fx_usd_brl=5.0, methods={})
    with pytest.raises(errors.PriceMissing):
        table.cost_brl("veo-3.1-fast", 5.0)


@pytest.mark.parametrize("units", [-1.0, math.nan, math.inf])
def test_cost_brl_rejects_invalid_units(prices: PriceTable, units: float) -> None:
    with pytest.raises(errors.EstimateInvalid):
        prices.cost_brl("veo-3.1-fast", units)


def test_unit_kind_of(prices: PriceTable) -> None:
    assert prices.unit_kind_of("wan-2.2-5b") == "gpu_seconds"
    assert prices.unit_kind_of("ken-burns") == "none"
    assert PriceTable(5.0, {}).unit_kind_of("parallax-2.5d") == "none"
    with pytest.raises(errors.PriceMissing):
        PriceTable(5.0, {}).unit_kind_of("kling-3.0")


def test_load_prices_real_file() -> None:
    table = load_prices(REPO_ROOT / "config" / "prices.toml")
    assert table.fx_usd_brl > 0
    assert not table.is_paid("ken-burns")
    assert table.cost_brl("ken-burns", 5.0) == 0.0
    assert table.cost_brl("veo-3.1-fast", 5.0) > 0.0
    assert table.cost_brl("wan-2.2-5b", 60.0) > 0.0


def test_load_prices_example_file() -> None:
    table = load_prices(REPO_ROOT / "config" / "prices.example.toml")
    assert table.fx_usd_brl > 0
    assert table.cost_brl("veo-3.1-fast", 5.0) > 0.0
    assert table.methods["ken-burns"].price_per_unit == 0.0


def test_load_prices_missing_file(tmp_path: Path) -> None:
    with pytest.raises(errors.ConfigError):
        load_prices(tmp_path / "nao-existe.toml")


def test_load_prices_malformed_toml(tmp_path: Path) -> None:
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, "fx_usd_brl = ["))


_PAID_OK = """
fx_usd_brl = 5.0
[methods."veo-3.1-fast"]
unit_kind = "video_seconds"
price_per_unit = 0.1
currency = "USD"
source = "teste"
"""


def test_load_prices_ok_roundtrip(tmp_path: Path) -> None:
    table = load_prices(write_prices(tmp_path, _PAID_OK))
    assert table.methods["veo-3.1-fast"] == MethodPrice("video_seconds", 0.1, "USD", "teste")
    assert table.cost_brl("veo-3.1-fast", 10.0) == pytest.approx(5.0)


@pytest.mark.parametrize("fx", ["0", "-1.0", "nan", '"5"'])
def test_load_prices_invalid_fx(tmp_path: Path, fx: str) -> None:
    body = _PAID_OK.replace("fx_usd_brl = 5.0", f"fx_usd_brl = {fx}")
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, body))


def test_load_prices_missing_fx(tmp_path: Path) -> None:
    body = _PAID_OK.replace("fx_usd_brl = 5.0", "")
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, body))


@pytest.mark.parametrize("price", ["0", "0.0", "-0.5", "nan", "inf"])
def test_load_prices_paid_needs_positive_finite_price(tmp_path: Path, price: str) -> None:
    body = _PAID_OK.replace("price_per_unit = 0.1", f"price_per_unit = {price}")
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, body))


def test_load_prices_free_must_be_zero(tmp_path: Path) -> None:
    body = """
fx_usd_brl = 5.0
[methods."ken-burns"]
unit_kind = "none"
price_per_unit = 0.5
currency = "BRL"
source = "teste"
"""
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, body))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('unit_kind = "video_seconds"', 'unit_kind = "frames"'),
        ('currency = "USD"', 'currency = "EUR"'),
        ('source = "teste"', ""),
        ('source = "teste"', 'source = ""'),
        ('price_per_unit = 0.1', 'price_per_unit = "0.1"'),
        ('currency = "USD"', ""),
    ],
)
def test_load_prices_invalid_fields(tmp_path: Path, old: str, new: str) -> None:
    body = _PAID_OK.replace(old, new)
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, body))


def test_load_prices_paid_with_unit_kind_none_rejected(tmp_path: Path) -> None:
    body = _PAID_OK.replace('unit_kind = "video_seconds"', 'unit_kind = "none"')
    with pytest.raises(errors.ConfigError):
        load_prices(write_prices(tmp_path, body))
