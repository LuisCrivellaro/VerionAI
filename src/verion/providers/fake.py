"""Adaptador ``fake``: método PAGO FICTÍCIO, só para testes (spec 3.2 e 3.4).

Passa por toda a trava de gasto como um método pago de verdade (token, reserva, liquidação), mas não
faz rede nem usa ffmpeg: o "clipe" é um arquivo de bytes que NÃO é um MP4. O resultado de cada chamada
vem de ``VERION_FAKE_MODE`` (lista separada por vírgula, usada em ordem; o último valor se repete):

- ``ok``: aceita o job e entrega o arquivo;
- ``transient``: falha ANTES do aceite, sem faturamento (``ProviderError(transient=True)``). O fake
  simula o provedor depois de esgotados os retries; ele não repete por conta própria, para que a
  sequência dos modos seja determinística;
- ``billed_error``: aceita o job e falha depois, faturando a duração inteira da cena.

Só o ``FakeClient`` lê o ambiente (exceção declarada na spec 3.2). Nenhuma chave, nenhuma rede.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol, cast

from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import ConfigError, ProviderError
from verion.providers.base import CostEstimate, Scene, SceneRequest, SceneResult

ENV_VAR: Final = "VERION_FAKE_MODE"
MODES: Final = ("ok", "transient", "billed_error")

# Metadados do clipe fictício. Fixos de propósito: nada de MP4 de verdade.
_WIDTH, _HEIGHT, _FPS = 1920, 1080, 30.0
_MAGIC: Final = b"VERION-FAKE-CLIP v1"


def _parse_modes(raw: Sequence[str], where: str) -> tuple[str, ...]:
    modes = tuple(raw)
    if not modes:
        raise ConfigError(f"{where}: informe ao menos um modo ({', '.join(MODES)})")
    for mode in modes:
        if mode not in MODES:
            raise ConfigError(
                f"{where}: modo inválido {mode!r}; válidos: {', '.join(MODES)}"
            )
    return modes


class FakeClient:
    """Provedor fictício em memória. ``submit`` aceita (ou recusa) o job; ``fetch`` entrega o resultado.

    O modo de cada chamada de ``submit`` é ``modes[n]`` (n = submissões anteriores); o último repete.
    ``latency_s`` e ``sleep`` simulam a espera do provedor em ``fetch``: o ``sleep`` padrão é inerte
    (nunca dorme de verdade); os testes injetam ``fake_clock.advance`` para tempos exatos.
    """

    def __init__(
        self,
        modes: Sequence[str] = ("ok",),
        *,
        latency_s: float = 0.0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.modes: tuple[str, ...] = _parse_modes(modes, "FakeClient")
        self._latency_s = latency_s
        self._sleep: Callable[[float], None] = (
            sleep if sleep is not None else (lambda seconds: None)
        )
        self._submitted = 0
        self._jobs: dict[str, tuple[str, Scene]] = {}

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> FakeClient:
        """Lê ``VERION_FAKE_MODE``. Ausente ou vazio: ``ok``. Modo desconhecido ou item vazio: ``ConfigError``."""
        env = os.environ if environ is None else environ
        raw = env.get(ENV_VAR, "").strip()
        if not raw:
            return cls()
        return cls(_parse_modes([part.strip() for part in raw.split(",")], ENV_VAR))

    def submit(self, scene: Scene) -> str:
        """Aceita o job e devolve o id; no modo ``transient`` falha antes do aceite, sem faturar."""
        mode = self.modes[min(self._submitted, len(self.modes) - 1)]
        self._submitted += 1
        if mode == "transient":
            raise ProviderError(
                "fake: indisponível antes do aceite (transitório)", transient=True
            )
        job_id = f"fake-job-{self._submitted:04d}"
        self._jobs[job_id] = (mode, scene)
        return job_id

    def fetch(self, job_id: str) -> bytes:
        """Espera o provedor e entrega os bytes; no modo ``billed_error`` falha faturando a duração inteira."""
        if job_id not in self._jobs:
            raise ProviderError(f"fake: job desconhecido {job_id!r}", transient=False)
        mode, scene = self._jobs[job_id]
        if self._latency_s > 0:
            self._sleep(self._latency_s)
        if mode == "billed_error":
            raise ProviderError(
                "fake: falha após o aceite (faturada)",
                transient=False,
                billed_units=scene.duration_s,
                provider_job_id=job_id,
            )
        header = f"job={job_id} scene={scene.index} duration_s={scene.duration_s} mode={mode}\n"
        return _MAGIC + b"\n" + header.encode("utf-8")


class _Backend(Protocol):
    """O que o adaptador usa do client (o ``FakeClient`` ou um espião que o embrulha)."""

    def submit(self, scene: Scene) -> str: ...

    def fetch(self, job_id: str) -> bytes: ...


class FakeAdapter:
    """Método ``fake``: R$ 1,00 por ``video_second`` na tabela de testes; grava bytes, não MP4."""

    method = "fake"
    provider = "fake"
    model = "fake-1"

    def __init__(self, prices: PriceTable, client: object | None = None) -> None:
        self._prices = prices
        self._client = cast(
            "_Backend", client if client is not None else FakeClient.from_env()
        )

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(
            units=scene.duration_s, unit_kind=self._prices.unit_kind_of(self.method)
        )

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        """Grava um arquivo de bytes em ``req.out_path``. Falhas são ``ProviderError`` (ver o módulo)."""
        token.consume(self.method)
        scene = req.scene
        job_id = self._client.submit(scene)
        token.job_accepted(job_id)
        data = self._client.fetch(job_id)
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        req.out_path.write_bytes(data)
        return SceneResult(
            clip_path=req.out_path,
            duration_s=scene.duration_s,
            width=_WIDTH,
            height=_HEIGHT,
            fps=_FPS,
            units=scene.duration_s,
            unit_kind=self._prices.unit_kind_of(self.method),
            cost_is_estimate=False,
            provider_job_id=job_id,
            meta={
                "mode": "ok"
            },  # só o modo ok devolve resultado; os outros levantam ProviderError
        )
