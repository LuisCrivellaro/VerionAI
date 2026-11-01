"""Configuração e tabela de preços (spec 3.2).

Só stdlib. A tabela de preços é a ÚNICA fonte de custo: o adaptador informa unidades, nunca R$.
Variáveis de ambiente são lidas apenas pelo ``cli.py``, que repassa os valores por argumento.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, get_args

from verion.errors import ConfigError, EstimateInvalid, PriceMissing

UnitKind = Literal["video_seconds", "gpu_seconds", "credits", "none"]  # base.py importa daqui
Currency = Literal["USD", "BRL"]

# Métodos locais sem custo variável. Mudar esta lista exige ADR.
FREE_METHODS: Final = frozenset({"ken-burns", "parallax-2.5d"})

_UNIT_KINDS: Final = frozenset(get_args(UnitKind))
_CURRENCIES: Final = frozenset(get_args(Currency))


@dataclass(frozen=True)
class MethodPrice:
    unit_kind: UnitKind
    price_per_unit: float
    currency: Currency
    source: str


@dataclass(frozen=True)
class PriceTable:
    fx_usd_brl: float
    methods: Mapping[str, MethodPrice]

    def is_paid(self, method: str) -> bool:
        """False só para ``FREE_METHODS``; qualquer outro id (inclusive desconhecido) é pago."""
        return method not in FREE_METHODS

    def unit_kind_of(self, method: str) -> UnitKind:
        """Tipo de unidade esperado para o método. Grátis ausente da tabela: ``none``."""
        price = self.methods.get(method)
        if price is not None:
            return price.unit_kind
        if not self.is_paid(method):
            return "none"
        raise PriceMissing(f"método pago sem preço em prices.toml: {method!r}")

    def cost_brl(self, method: str, units: float) -> float:
        """Custo em R$ de ``units``. 0.0 se grátis; ``PriceMissing`` se pago e ausente."""
        if not math.isfinite(units) or units < 0:
            raise EstimateInvalid(f"unidades inválidas para {method!r}: {units!r}")
        if not self.is_paid(method):
            return 0.0
        price = self.methods.get(method)
        if price is None:
            raise PriceMissing(f"método pago sem preço em prices.toml: {method!r}")
        rate = self.fx_usd_brl if price.currency == "USD" else 1.0
        return units * price.price_per_unit * rate


def _number(value: object, what: str) -> float:
    """Aceita int/float finito (bool e str não); caso contrário ``ConfigError``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{what}: esperado número, veio {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{what}: valor não finito ({value!r})")
    return number


def _parse_method(method: str, raw: object) -> MethodPrice:
    where = f"methods.{method}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: esperado tabela TOML")
    for key in ("unit_kind", "price_per_unit", "currency", "source"):
        if key not in raw:
            raise ConfigError(f"{where}: campo ausente {key!r}")
    unit_kind, currency, source = raw["unit_kind"], raw["currency"], raw["source"]
    if unit_kind not in _UNIT_KINDS:
        raise ConfigError(f"{where}.unit_kind inválido: {unit_kind!r}")
    if currency not in _CURRENCIES:
        raise ConfigError(f"{where}.currency inválido: {currency!r}")
    if not isinstance(source, str) or not source.strip():
        raise ConfigError(f"{where}.source: informe a fonte do preço")
    price = _number(raw["price_per_unit"], f"{where}.price_per_unit")
    if method in FREE_METHODS:
        if price != 0.0:
            raise ConfigError(f"{where}: método grátis exige price_per_unit = 0")
    else:
        if price <= 0.0:
            raise ConfigError(f"{where}: método pago exige price_per_unit > 0")
        if unit_kind == "none":
            raise ConfigError(f"{where}: método pago não pode ter unit_kind = 'none'")
    return MethodPrice(unit_kind, price, currency, source)  # type: ignore[arg-type]  # validado acima


def load_prices(path: Path) -> PriceTable:
    """Lê a tabela de preços. ``ConfigError``: ausente, TOML inválido, pago <= 0, grátis != 0, fx <= 0."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"tabela de preços ilegível: {path} ({exc.strerror})") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML inválido em {path}: {exc}") from exc

    if "fx_usd_brl" not in data:
        raise ConfigError("fx_usd_brl ausente")
    fx = _number(data["fx_usd_brl"], "fx_usd_brl")
    if fx <= 0:
        raise ConfigError("fx_usd_brl deve ser > 0")

    raw_methods = data.get("methods", {})
    if not isinstance(raw_methods, dict):
        raise ConfigError("'methods' deve ser uma tabela TOML")
    methods = {name: _parse_method(name, raw) for name, raw in raw_methods.items()}
    return PriceTable(fx_usd_brl=fx, methods=methods)
