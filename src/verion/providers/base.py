"""Contrato do adaptador de geração de cena (spec 3.4).

Todo método (local ou pago) implementa o Protocol ``SceneGenerator``: ``estimate`` informa UNIDADES
(nunca R$; o custo vem só da tabela de preços) e ``generate`` é bloqueante, recebe um ``SpendToken`` e
o consome na primeira instrução. Este módulo não faz I/O além do retry com ``time.sleep`` injetável.

Retry com backoff só vale ANTES do aceite do job (``retry_before_accept``). Depois do aceite, a falha
vira ``ProviderError(billed_units=...)``, porque repetir duplicaria a cobrança.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol, get_args, runtime_checkable

from verion.budget import SpendToken
from verion.config import UnitKind  # definido em config.py (T1); não redefinir aqui
from verion.errors import ProviderError

__all__ = [
    "CAMERAS",
    "Camera",
    "CostEstimate",
    "Scene",
    "SceneGenerator",
    "SceneRequest",
    "SceneResult",
    "UnitKind",
    "retry_before_accept",
]

Camera = Literal["push-in", "pull-out", "pan-left", "pan-right", "static"]
CAMERAS: Final = frozenset(get_args(Camera))

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scene:
    """Uma cena do roteiro. ``duration_s`` é o alvo (3,0 a 10,0; validado por ``script.load_script``).

    O ``prompt`` descreve só o movimento de câmera, nunca objeto ausente da foto.
    """

    index: int
    photo: Path
    room: str
    camera: Camera
    duration_s: float
    prompt: str
    negative_prompt: str = ""


@dataclass(frozen=True)
class SceneRequest:
    scene: Scene
    run_id: int
    attempt_no: int
    out_path: Path
    seed: int | None = None


@dataclass(frozen=True)
class CostEstimate:
    """Consumo previsto em unidades do provedor. O R$ sai de ``PriceTable.cost_brl``."""

    units: float
    unit_kind: UnitKind


@dataclass(frozen=True)
class SceneResult:
    clip_path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    units: float
    unit_kind: UnitKind
    cost_is_estimate: bool  # True se o provedor não devolve o consumo
    provider_job_id: str | None = None
    meta: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class SceneGenerator(Protocol):
    """Interface comum de todos os métodos.

    ``provider``: "google" | "kling" | "gpu-rental" | "local" | "fake".
    Construtor (por convenção, o Protocol não o impõe): ``__init__(self, prices, client=None)``, sem
    ler chave nem abrir conexão.
    """

    method: str
    provider: str
    model: str

    def estimate(self, scene: Scene) -> CostEstimate: ...

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        """Bloqueante, com polling interno. A primeira instrução é ``token.consume(self.method)``;
        após o aceite do job, ``token.job_accepted(job_id)``."""
        ...


# Falhas de rede da stdlib que um adaptador pode deixar escapar antes do aceite.
_NETWORK_ERRORS: Final = (ConnectionError, TimeoutError)


def retry_before_accept[T](
    call: Callable[[], T],
    *,
    attempts: int = 3,
    initial_s: float = 1.0,
    max_s: float = 30.0,
    jitter_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Executa ``call`` (a submissão do job) com backoff exponencial e jitter, só enquanto nada foi aceito.

    Repete apenas:
    - ``ProviderError`` com ``transient=True``, ``billed_units == 0`` e sem ``provider_job_id``
      (se algo foi faturado ou o job existe, repetir duplicaria a cobrança: relança na hora);
    - ``ConnectionError`` e ``TimeoutError`` da stdlib. Esgotadas as tentativas, viram
      ``ProviderError(transient=True)`` com a causa encadeada.

    Qualquer outra exceção (bug, chave inválida, erro permanente) sobe imediatamente. Espera entre
    tentativas: ``min(max_s, initial_s * 2**(n-1)) + rng() * jitter_s``. O total fica limitado por
    ``attempts`` e ``max_s``; o timeout de cada chamada é responsabilidade do adaptador. Cada nova
    tentativa é registrada em log (nível WARNING).
    """
    if attempts < 1:
        raise ValueError(f"attempts deve ser >= 1, veio {attempts!r}")
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except ProviderError as exc:
            retryable = (
                exc.transient and exc.billed_units == 0 and exc.provider_job_id is None
            )
            if not retryable or attempt == attempts:
                raise
            reason: BaseException = exc
        except _NETWORK_ERRORS as exc:
            if attempt == attempts:
                raise ProviderError(
                    f"falha de rede após {attempts} tentativas: {exc}", transient=True
                ) from exc
            reason = exc
        delay = min(max_s, initial_s * 2 ** (attempt - 1)) + rng() * jitter_s
        _log.warning(
            "tentativa %d/%d falhou antes do aceite (%s); nova tentativa em %.1fs",
            attempt,
            attempts,
            reason,
            delay,
        )
        sleep(delay)
    raise AssertionError(
        "inalcançável: o laço sempre retorna ou levanta"
    )  # pragma: no cover
