"""Adaptador ``veo-3.1-fast``: imagem para vídeo com o Veo 3.1 Fast pelo SDK ``google-genai`` (T8).

Fluxo de ``generate`` (síncrono, com polling interno):

1. ``token.consume`` (primeira instrução); valida a foto; cria o client real só agora, se nenhum foi
   injetado (a chave vem do ambiente, lida pelo próprio SDK; o construtor não lê chave nem client).
2. ``client.models.generate_videos(model=, source=GenerateVideosSource(prompt, image), config=)``
   dentro de ``retry_before_accept``: retry com backoff SÓ enquanto nada foi aceito.
3. ``token.job_accepted(operation.name)`` logo que o provedor devolve a operação.
4. Polling: ``client.operations.get(operation)`` a cada ``poll_interval_s`` até ``operation.done``
   ou até o prazo ``timeout_s`` (``sleep`` e ``clock`` injetáveis).
5. ``client.files.download(file=video, destination=<out>.part)``, ``ffprobe`` e ``os.replace`` para
   ``req.out_path``. O MP4 é o do provedor, sem normalização (a montagem faz R2).

Classificação de falhas (decisão do supervisor, evidência 09):

- ANTES do aceite: 429 e 503 do provedor e falhas de conexão com prova de envio não realizado
  (``httpx.ConnectError``/``ConnectTimeout``, ``ConnectionRefusedError``, DNS) viram
  ``ProviderError(transient=True, billed_units=0, provider_job_id=None)``, repetíveis por
  ``retry_before_accept``. Erros permanentes (4xx) e ambíguos (timeout de leitura, 500/502/504, em
  que o POST pode ter chegado) viram ``transient=False, billed_units=0``: não são repetidos. O ledger
  mantém a reserva inteira em qualquer erro (``settle_error``: custo = max(faturado, reserva)).
- DEPOIS do aceite (prazo de polling, operação com erro, vídeo filtrado, download ou arquivo
  inválido, qualquer exceção): NUNCA transient e nunca reenvia. ``billed_units`` = segundos
  PEDIDOS (conservador) e ``provider_job_id`` = nome da operação. A doc oficial diz que vídeo
  bloqueado não é cobrado: o adaptador ainda assim declara o pior caso.
- Falhas transitórias de CONSULTA (GET idempotente: 429, 5xx, rede) são toleradas até
  ``_MAX_POLL_FAILURES`` seguidas; não duplicam cobrança.

``units`` do resultado = duração do MP4 baixado (``ffprobe``), em ``video_seconds``;
``cost_is_estimate=True`` porque a API não devolve o consumo faturado.

CONFIRMADO no SDK instalado (google-genai 2.34.0, inspeção offline; ver ``tests/providers/test_veo.py``):
assinaturas de ``models.generate_videos``, ``operations.get`` e ``files.download``; campos da operação;
no modo Gemini Developer API o SDK REJEITA ``seed``, ``fps``, ``generate_audio`` e outros (``ValueError``),
por isso ``SceneRequest.seed`` é ignorado aqui (o Veo não é reprodutível por semente nesse modo).

CONFIRMADO na doc oficial (ai.google.dev/gemini-api/docs/veo, lida em 2026-11-02): IDs
``veo-3.1-fast-generate-preview``; ``durationSeconds`` 4, 6 ou 8; 1080p e 4k exigem 8 s; aspect 16:9
ou 9:16; ``personGeneration`` só ``allow_adult`` em imagem para vídeo; 24 fps; áudio sempre ligado;
vídeo fica 2 dias no servidor; latência de ~11 s a 6 min.

ATENÇÃO (doc oficial de descontinuação, lida em 2026-11-02): ``veo-3.1-fast-generate-preview`` consta
como descontinuado em 22/10/2026 no Gemini API, com recomendação de migrar para ``gemini-omni-1.1-flash``
(T9) ou para modelos GA da "Gemini Enterprise Agent Platform". O ID GA do Veo 3.1 Fast NÃO foi confirmado
em fonte oficial. Por isso ``model`` é configurável (``VeoAdapter(prices, model=...)``) e o padrão pode
estar morto: uma chamada real exige confirmar o ID antes. Hipóteses não verificáveis offline: formato
exato do erro da operação, JPEG/PNG como únicos MIME aceitos, tamanho máximo da foto inline e o
tratamento de foto em retrato no vídeo 16:9 (o QC humano deve conferir).
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as gtypes

from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import ConfigError, FfmpegError, InputError, ProviderError
from verion.local import ffmpeg
from verion.providers.base import (
    CostEstimate,
    Scene,
    SceneRequest,
    SceneResult,
    retry_before_accept,
)

DEFAULT_MODEL: Final = "veo-3.1-fast-generate-preview"  # ver ATENÇÃO no docstring do módulo
SUPPORTED_DURATIONS_S: Final = (4, 6, 8)
_RESOLUTIONS: Final = ("720p", "1080p")  # a tabela de preços cobre até 1080p; 4k fica de fora
_FIXED_DURATION_RESOLUTIONS: Final = ("1080p",)  # exigem 8 s (doc oficial)
_ASPECT_RATIO: Final = "16:9"
_PERSON_GENERATION: Final = "allow_adult"  # único valor aceito em imagem para vídeo (doc oficial)
_CALL_TIMEOUT_MS: Final = 120_000  # timeout HTTP de cada chamada do client criado aqui
_MAX_POLL_FAILURES: Final = 3
_MIME_BY_SUFFIX: Final = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_TRANSIENT_CODES: Final = (429, 503)


class VeoAdapter:
    """Método ``veo-3.1-fast``: R$ por ``video_seconds`` da tabela de preços, grava o MP4 do provedor."""

    method = "veo-3.1-fast"
    provider = "google"

    def __init__(
        self,
        prices: PriceTable,
        client: object | None = None,
        *,
        model: str = DEFAULT_MODEL,
        resolution: str = "720p",
        poll_interval_s: float = 10.0,
        timeout_s: float = 600.0,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Só guarda configuração e a referência ao client (nunca lê atributo dele nem a chave)."""
        if resolution not in _RESOLUTIONS:
            raise ConfigError(f"resolution inválida {resolution!r}; válidas: {', '.join(_RESOLUTIONS)}")
        if not (poll_interval_s > 0 and timeout_s > 0):
            raise ConfigError("poll_interval_s e timeout_s devem ser > 0")
        self._prices = prices
        self._client = client
        self.model = model
        self._resolution = resolution
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        self._sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

    # --- contrato ------------------------------------------------------------------------

    def estimate(self, scene: Scene) -> CostEstimate:
        return CostEstimate(
            units=float(self._duration_for(scene)),
            unit_kind=self._prices.unit_kind_of(self.method),
        )

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        """Gera o clipe em ``req.out_path``. Falhas são ``ProviderError`` (ver o docstring do módulo)."""
        token.consume(self.method)
        scene = req.scene
        requested = self._duration_for(scene)
        image = _load_image(scene.photo)
        client = self._get_client()
        config = gtypes.GenerateVideosConfig(
            duration_seconds=requested,
            aspect_ratio=_ASPECT_RATIO,
            resolution=self._resolution,
            person_generation=_PERSON_GENERATION,
            negative_prompt=scene.negative_prompt or None,
        )
        source = gtypes.GenerateVideosSource(prompt=scene.prompt, image=image)

        def submit() -> Any:
            return self._submit(client, source, config)

        operation = retry_before_accept(submit, sleep=self._sleep)

        job_id = getattr(operation, "name", None)
        if not isinstance(job_id, str) or not job_id.strip():
            raise ProviderError(
                "Veo devolveu a operação sem nome: o job pode ter sido aceito e faturado, mas não há id "
                "para consultá-lo. Verifique no console do provedor.",
                transient=False,
                billed_units=float(requested),
            )
        token.job_accepted(job_id)
        try:
            done = self._await(client, operation, job_id, requested)
            video = _extract_video(done, job_id, requested)
            return self._fetch(client, video, req.out_path, job_id, requested)
        except ProviderError:
            raise
        except Exception as exc:  # qualquer outra falha depois do aceite já pode estar faturada
            raise _after_accept(f"falha inesperada ({exc!r})", job_id, requested) from exc

    # --- internos ------------------------------------------------------------------------

    def _duration_for(self, scene: Scene) -> int:
        """Menor duração suportada >= alvo da cena (teto de 8 s; 1080p sempre 8 s)."""
        if self._resolution in _FIXED_DURATION_RESOLUTIONS:
            return SUPPORTED_DURATIONS_S[-1]
        for seconds in SUPPORTED_DURATIONS_S:
            if seconds >= scene.duration_s - 1e-9:
                return seconds
        return SUPPORTED_DURATIONS_S[-1]

    def _get_client(self) -> Any:
        """O client real nasce aqui (depois do ``consume``), com a chave do ambiente lida pelo SDK."""
        if self._client is None:
            try:
                self._client = genai.Client(http_options=gtypes.HttpOptions(timeout=_CALL_TIMEOUT_MS))
            except ValueError as exc:
                raise ConfigError(
                    "chave de API do Gemini ausente ou inválida: defina GEMINI_API_KEY no ambiente"
                ) from exc
        return self._client

    def _submit(self, client: Any, source: Any, config: Any) -> Any:
        try:
            return client.models.generate_videos(model=self.model, source=source, config=config)
        except genai_errors.APIError as exc:
            transient = exc.code in _TRANSIENT_CODES
            unsure = "" if exc.code < 500 or transient else " (o pedido pode ter sido aceito)"
            raise ProviderError(
                f"Veo recusou o pedido: HTTP {exc.code} {exc.status}: {exc.message}{unsure}",
                transient=transient,
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError(f"sem conexão com o Veo antes do envio: {exc}", transient=True) from exc
        except (httpx.TransportError, TimeoutError) as exc:
            raise ProviderError(
                f"falha de rede ao enviar o pedido ao Veo; o pedido pode ter sido aceito e faturado: {exc}",
                transient=False,
            ) from exc

    def _await(self, client: Any, operation: Any, job_id: str, requested: int) -> Any:
        """Polling até ``done`` ou até o prazo. Toda falha aqui já é pós-aceite."""
        started = self._clock()
        failures = 0
        while not operation.done:
            if self._clock() - started >= self._timeout_s:
                raise _after_accept(f"prazo de espera esgotado ({self._timeout_s:.0f} s)", job_id, requested)
            self._sleep(self._poll_interval_s)
            try:
                operation = client.operations.get(operation)
                failures = 0
            except Exception as exc:
                failures += 1
                if not _transient_poll_error(exc) or failures >= _MAX_POLL_FAILURES:
                    raise _after_accept(f"falha ao consultar a operação ({exc!r})", job_id, requested) from exc
        return operation

    def _fetch(self, client: Any, video: Any, out_path: Path, job_id: str, requested: int) -> SceneResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        part = out_path.with_name(out_path.name + ".part")
        try:
            if video.video_bytes:  # HIPÓTESE: modo Vertex/Enterprise pode devolver os bytes na resposta
                part.write_bytes(video.video_bytes)
            else:
                client.files.download(file=video, destination=str(part))
            info = ffmpeg.probe(part)
            fps = info.fps_value
            if not all(math.isfinite(v) and v > 0 for v in (info.duration_s, info.width, info.height, fps)):
                raise FfmpegError(f"vídeo baixado com metadados inválidos: {info}")
            os.replace(part, out_path)
        except Exception as exc:
            part.unlink(missing_ok=True)
            raise _after_accept(f"falha ao baixar ou validar o vídeo ({exc!r})", job_id, requested) from exc
        return SceneResult(
            clip_path=out_path,
            duration_s=info.duration_s,
            width=info.width,
            height=info.height,
            fps=fps,
            units=info.duration_s,
            unit_kind=self._prices.unit_kind_of(self.method),
            cost_is_estimate=True,  # a API não devolve o consumo faturado
            provider_job_id=job_id,
            meta={
                "model": self.model,
                "resolution": self._resolution,
                "aspect_ratio": _ASPECT_RATIO,
                "requested_s": str(requested),
            },
        )


# --- helpers sem estado ----------------------------------------------------------------------


def _load_image(photo: Path) -> Any:
    mime = _MIME_BY_SUFFIX.get(photo.suffix.lower())
    if mime is None:
        raise InputError(f"formato de foto não suportado pelo adaptador Veo: {photo.name} (use .jpg ou .png)")
    try:
        data = photo.read_bytes()
    except OSError as exc:
        raise InputError(f"foto ilegível: {photo} ({exc})") from exc
    if not data:
        raise InputError(f"foto vazia: {photo}")
    return gtypes.Image(image_bytes=data, mime_type=mime)


def _after_accept(reason: str, job_id: str, requested: int) -> ProviderError:
    """Falha com o job já aceito: sem retry, faturada pelo pior caso (segundos pedidos), com o id."""
    return ProviderError(
        f"Veo, job {job_id}: {reason}. Já aceito pelo provedor: tratado como faturado "
        f"({requested} s). O vídeo, se existir, fica 2 dias no servidor; confira no console.",
        transient=False,
        billed_units=float(requested),
        provider_job_id=job_id,
    )


def _transient_poll_error(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in (429,) or exc.code >= 500
    return isinstance(exc, httpx.TransportError | TimeoutError)


def _extract_video(operation: Any, job_id: str, requested: int) -> Any:
    error = getattr(operation, "error", None)
    if error:
        detail = error.get("message", error) if isinstance(error, dict) else error
        raise _after_accept(f"a operação terminou com erro: {detail}", job_id, requested)
    response = getattr(operation, "response", None) or getattr(operation, "result", None)
    videos = getattr(response, "generated_videos", None) if response is not None else None
    if not videos or getattr(videos[0], "video", None) is None:
        reasons = getattr(response, "rai_media_filtered_reasons", None) or []
        detail = f" (filtrado: {'; '.join(reasons)})" if reasons else ""
        raise _after_accept(f"a operação terminou sem vídeo{detail}", job_id, requested)
    return videos[0].video
