"""Registry de métodos (spec 3.4): id estável → ``modulo:Classe``, com importação sob demanda.

Cada adaptador mora em seu próprio arquivo e só é importado quando pedido. Assim, um extra ausente
(ex.: ``google-genai``) ou um método ainda não entregue afeta apenas o seu id (``MethodUnavailable``).
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from verion.config import PriceTable
from verion.errors import MethodUnavailable, UnknownMethod
from verion.providers.base import SceneGenerator

# Ids estáveis no banco, na CLI e no JSONL. O teste AST da T1 só confere classes ``*Adapter`` em
# providers/*.py e local/{ken_burns,parallax}.py: manter os caminhos dentro desse escopo.
METHODS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ken-burns": "verion.local.ken_burns:KenBurnsAdapter",
        "parallax-2.5d": "verion.local.parallax:ParallaxAdapter",
        "veo-3.1-fast": "verion.providers.veo:VeoAdapter",
        "gemini-omni-1.1-flash": "verion.providers.gemini_omni:GeminiOmniAdapter",
        "kling-3.0": "verion.providers.kling:KlingAdapter",
        "wan-2.2-5b": "verion.providers.wan:WanAdapter",
        "fake": "verion.providers.fake:FakeAdapter",
    }
)


def create(
    method: str, prices: PriceTable, client: object | None = None
) -> SceneGenerator:
    """Instancia o adaptador de ``method``.

    - id fora de ``METHODS`` → ``UnknownMethod`` (nada é importado);
    - módulo, extra ou classe ausente → ``MethodUnavailable`` (causa encadeada);
    - erros do construtor (ex.: ``ConfigError``) sobem sem disfarce.
    """
    path = METHODS.get(method) if isinstance(method, str) else None
    if path is None:
        known = ", ".join(sorted(METHODS))
        raise UnknownMethod(
            f"método desconhecido: {method!r}. Métodos válidos: {known}"
        )
    module_name, _, class_name = path.partition(":")
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except (ImportError, AttributeError) as exc:  # ModuleNotFoundError é ImportError
        raise MethodUnavailable(
            f"método {method!r} indisponível ({path}): {exc}"
        ) from exc
    adapter: SceneGenerator = cls(prices, client)
    return adapter
