"""Adaptador ``gemini-omni-1.1-flash`` (T9): foto -> clipe pelo Gemini Omni Flash, via Files + Interactions API.

Fluxo de ``generate`` (cada passo depois do ``token.consume``, a primeira instrução):

1. valida a cena (duração inteira de 3 a 10 s, foto existente em jpg/png/webp);
2. cria o client do SDK de forma preguiçosa (chave de ``GEMINI_API_KEY``, sem reenvio automático do SDK);
3. ANTES do aceite (nada faturado): ``files.upload`` da foto e espera pelo estado ``ACTIVE``;
4. ``interactions.create`` com first-frame + prompt + ``duration`` + 1080p + entrega por URI (síncrono);
5. ``token.job_accepted(<id>)`` logo que o pedido volta, antes de qualquer outra coisa;
6. espera o arquivo gerado ficar ``ACTIVE``, baixa (``files.download``), mede com o ffprobe e grava
   em ``req.out_path``.

Regras de dinheiro (decisão do supervisor, evidência 09 e revisão da T2):

- falha ANTES do aceite (inclui upload): ``ProviderError(transient=True, billed_units=0,
  provider_job_id=None)``. O submit e o upload repetem com backoff só nesse trecho (``retry_before_accept``);
- falha DEPOIS do aceite: ``ProviderError(transient=False, billed_units=<segundos pedidos>,
  provider_job_id=<id>)``. Nunca se reenvia o pedido pago (o download, que é grátis, repete);
- resposta AMBÍGUA do ``create`` (timeout de leitura, reset de conexão, HTTP 408 e 5xx fora o 503,
  qualquer exceção desconhecida): o servidor pode ter gerado e faturado sem devolver o id. Trata-se como
  faturado, sem reenvio: ``ProviderError(transient=False, billed_units=<segundos pedidos>,
  provider_job_id=None)``. Só 429/503, recusas 4xx (validação, autenticação) e falhas de conexão
  anteriores ao envio (recusada, DNS, ``httpx.ConnectError``/``ConnectTimeout`` como causa) contam como
  "antes do aceite";
- O SDK NÃO pode reenviar o POST pago: com as opções padrão, ``interactions.create`` repete sozinho
  (``RetryConfig(max_retries=4)`` em ``_gaos``) em 408/409/429/5xx, medido em 4 POSTs. O client que o
  adaptador cria usa ``make_http_options`` (``HttpRetryOptions(attempts=1, http_status_codes=[599])``: 1 POST
  em 408/429/5xx; ``attempts=0`` ou ``1`` sozinhos ainda davam 2). **Um client INJETADO (``client=...``)
  tem de ser criado com as mesmas opções**, senão o dinheiro fica desprotegido;
- ``automatic_continuation=False`` em toda chamada (o padrão ``True`` poderia gerar interactions pagas
  extras) e ``requires_continuation`` é tratado como falha pós-aceite, nunca continuado.

Duração: o modelo aceita segundos INTEIROS de 3 a 10. ``Scene.duration_s`` é arredondado para o inteiro
mais próximo (meio sobe: 4,5 -> 5) e o resultado precisa cair em 3..10, senão ``InputError``
(2,49 e 10,5 já falham). ``estimate`` e ``generate`` usam a mesma regra.

``units`` = ``max(segundos medidos no arquivo baixado (ffprobe), segundos pedidos)``: o provedor pode
faturar o pedido mesmo entregando menos, então nunca se registra menos que o pedido. Os segundos medidos vão
em ``meta["measured_s"]`` e, se passarem do pedido em mais de 1 s, ``meta["over_requested"] = "true"``. O Omni
devolve tokens de saída, não segundos faturados, então ``cost_is_estimate=True`` (o preço em
``config/prices.toml`` também é HIPÓTESE).

Cliente real: ``genai.Client(vertexai=False, api_key=os.environ["GEMINI_API_KEY"], http_options=...)``, criado
só depois de ``token.consume``. Sem a variável: ``ConfigError``. ``GOOGLE_API_KEY`` e
``GOOGLE_GENAI_USE_VERTEXAI`` do ambiente são ignoradas de propósito.

Confirmado OFFLINE contra o SDK instalado (``google-genai`` 2.34.0, ver ``tests/providers/test_gemini_omni.py``,
onde o ``genai.Client`` REAL roda sobre um ``httpx.MockTransport``):

- ``client.files.upload(file=<caminho>, config=UploadFileConfig(mime_type=, display_name=))`` devolve ``File``
  (``name``, ``uri``, ``state``); ``client.files.get(name="files/<id>")``; ``FileState.ACTIVE|PROCESSING|FAILED``;
- ``client.files.download(file=<uri>, destination=<caminho>)`` grava em disco (e devolve ``None``);
- ``client.interactions.create(model=, input=[{"type": "image", "uri", "mime_type"}, {"type": "text", ...}],
  response_format={"type": "video", "aspect_ratio", "resolution", "duration": "5s", "delivery": "uri"},
  background=, store=, stream=, timeout=<segundos>, automatic_continuation=)`` serializa o corpo esperado;
  ``Interaction`` tem ``id``, ``status`` ("completed", "failed", "requires_continuation", ...),
  ``output_video.uri``, ``usage.total_output_tokens``, ``errors``;
- os erros de ``interactions.create`` vêm de ``google.genai._gaos.lib.compat_errors`` (``APIStatusError``
  com ``.status_code``, ``APIConnectionError``, ``APITimeoutError`` com a exceção do httpx em ``__cause__``),
  que NÃO herdam de ``genai_errors.APIError`` (esse é o dos ``files.*``, com ``.code``). Por isso a
  classificação é por duck typing (``status_code`` ou ``code``) e pela cadeia ``__cause__``.

Confirmado na documentação oficial (``ai.google.dev/gemini-api/docs/omni``): chamada síncrona com
``background=false, store=false, stream=false``; first-frame pela tag ``<FIRST_FRAME>`` no texto; resolução
``1080p`` (a página a descreve como "upscaled", ou seja, NÃO nativa); o arquivo gerado é baixado por
``files.download(file=video.uri)`` e a doc faz polling do ``files.get`` até ``ACTIVE``.

HIPÓTESES (só a primeira chamada paga autorizada vai confirmar):

- que o servidor honre ``duration`` em ``response_format`` (o SDK tipa o campo, a página da doc não o descreve);
- que ``store=False`` ainda devolva ``Interaction.id`` (se vier vazio, o id do job vira o nome do arquivo
  gerado, ``files/<id>``);
- o uso de ``file.uri`` como devolvido pelo SDK na entrada (o script de referência da skill o normaliza para
  ``https://generativelanguage.googleapis.com/files/<id>``);
- que ``google-genai>=2.19.0`` (mínimo do extra ``gemini`` em pyproject.toml) tenha tudo isso: só a 2.34.0 foi testada;
- o preço por segundo e que o faturamento seja por segundo gerado; que o Omni não tenha ``seed`` nem
  ``negative_prompt`` (nenhum dos dois existe nos tipos do SDK: ``req.seed`` é ignorado e o
  ``negative_prompt`` vai no texto do prompt).
"""

from __future__ import annotations

import math
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import ConfigError, InputError, ProviderError
from verion.local import ffmpeg
from verion.providers.base import (
    CostEstimate,
    Scene,
    SceneRequest,
    SceneResult,
    retry_before_accept,
)

__all__ = ["GeminiOmniAdapter"]

MODEL: Final = "gemini-omni-1.1-flash"
MIN_DURATION_S: Final = 3
MAX_DURATION_S: Final = 10
RESOLUTION: Final = "1080p"
ASPECT_RATIO: Final = "16:9"

# Formatos de foto com MIME conhecido (os mesmos do script de referência da skill).
_MIME_BY_SUFFIX: Final = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

_DEFAULT_DEADLINE_S: Final = 900.0  # prazo total de um generate (relógio injetável)
_OVER_REQUESTED_TOLERANCE_S: Final = 1.001  # acima disso, o clipe é sinalizado em meta
_POLL_FIRST_S: Final = 2.0
_POLL_FACTOR: Final = 1.5
_POLL_MAX_S: Final = 10.0
_POLL_TOLERATED_ERRORS: Final = 2  # erros seguidos tolerados em files.get antes de desistir
_DOWNLOAD_ATTEMPTS: Final = 3
_DOWNLOAD_BACKOFF_S: Final = 2.0
_RETRYABLE_CODES: Final = frozenset({429, 503})  # recusa do provedor, sem job criado
# Falhas de stdlib/httpx com prova de que o pedido não saiu (como em providers/base.py).
_BEFORE_SEND: Final = (ConnectionRefusedError, socket.gaierror, httpx.ConnectError, httpx.ConnectTimeout)

_PROMPT_SUFFIX: Final = (
    "Single continuous shot, no scene cuts. Keep the room, furniture, decor and architecture exactly as in "
    "the starting image: do not add, remove or change any object. No dialogue."
)

_FILE_ID_RE: Final = re.compile(r"files/([^/:?#\s]+)")
_SECRET_RE: Final = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{20,}|ya29\.[0-9A-Za-z_\-]+")
_BEARER_RE: Final = re.compile(r"Bearer\s+[A-Za-z0-9_\-.]+", re.IGNORECASE)
_QUERY_SECRET_RE: Final = re.compile(
    r"([?&](?:key|api_key|apiKey|access_token|auth|signature)=)[^&\s\"'<>()]+"
)


class _Rejected(Exception):
    """Recusa definitiva ANTES do aceite (4xx, arquivo FAILED, prazo): não adianta repetir."""


class _Ambiguous(Exception):
    """O ``create`` falhou sem prova de que o pedido NÃO foi aceito: tratar como faturado, sem reenviar."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


class _FileFailed(Exception):
    """A Files API marcou o arquivo como ``FAILED``."""


class _DeadlineExceeded(Exception):
    """O prazo total do ``generate`` acabou."""


@dataclass(frozen=True)
class _Uploaded:
    name: str
    uri: str
    mime_type: str


def make_http_options(
    deadline_s: float, *, httpx_client: httpx.Client | None = None
) -> genai_types.HttpOptions:
    """Opções HTTP do client do Omni: timeout total e SEM reenvio automático do SDK (ver o docstring do módulo).

    ``httpx_client`` existe para os testes trocarem a rede por um ``httpx.MockTransport``.
    """
    return genai_types.HttpOptions(
        timeout=int(deadline_s * 1000),
        retry_options=genai_types.HttpRetryOptions(attempts=1, http_status_codes=[599]),
        httpx_client=httpx_client,
    )


def _status_of(exc: BaseException) -> int | None:
    """Status HTTP da exceção por duck typing: ``status_code`` (``_gaos``) ou ``code`` (``genai.errors``)."""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _failed_before_send(exc: BaseException) -> bool:
    """Conexão que comprovadamente falhou antes de enviar o pedido (a causa pode estar encadeada pelo SDK)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, _BEFORE_SEND):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


def _describe(exc: BaseException) -> str:
    """Texto curto e sem segredos de uma exceção, para mensagens de erro e ledger."""
    status = _status_of(exc)
    message = getattr(exc, "message", None) or str(exc)
    text = f"HTTP {status}: {message}" if status is not None else f"{type(exc).__name__}: {message}"
    text = _SECRET_RE.sub("[REDACTED_KEY]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= 300 else text[:297] + "..."


def _is_client_rejection(exc: BaseException) -> bool:
    """4xx que o provedor devolveu por recusar o pedido (chave, política, formato); 408 e 429 ficam de fora."""
    status = _status_of(exc)
    return status is not None and 400 <= status < 500 and status not in (408, 429)


def _file_id(uri: str | None) -> str | None:
    """Id do arquivo na URI do vídeo gerado (``.../files/<id>:download?alt=media``)."""
    found = _FILE_ID_RE.search(uri) if uri else None
    return found.group(1) if found else None


def _state_name(state: object) -> str:
    return str(getattr(state, "name", None) or state).upper()


def _requested_seconds(duration_s: float) -> int:
    """Duração inteira pedida ao modelo: arredonda para o inteiro mais próximo (meio sobe) e exige 3..10."""
    if isinstance(duration_s, bool) or not isinstance(duration_s, int | float) or not math.isfinite(duration_s):
        raise InputError(f"duração inválida para o Omni: {duration_s!r}")
    seconds = math.floor(duration_s + 0.5)
    if not MIN_DURATION_S <= seconds <= MAX_DURATION_S:
        raise InputError(
            f"o Omni gera de {MIN_DURATION_S} a {MAX_DURATION_S} s inteiros; a cena pede {duration_s} s"
        )
    return seconds


def _mime_of(photo: Path) -> str:
    mime = _MIME_BY_SUFFIX.get(photo.suffix.lower())
    if mime is None:
        raise InputError(
            f"foto {photo.name!r}: formato sem MIME conhecido para o Omni (use {', '.join(sorted(_MIME_BY_SUFFIX))})"
        )
    return mime


def _prompt_text(scene: Scene) -> str:
    text = f"<FIRST_FRAME> {scene.prompt.strip()} {_PROMPT_SUFFIX}"
    if scene.negative_prompt.strip():
        text += f" Avoid: {scene.negative_prompt.strip()}."
    return text


class GeminiOmniAdapter:
    """Método ``gemini-omni-1.1-flash``: first-frame -> vídeo 1080p de 3 a 10 s (ver o docstring do módulo)."""

    method = MODEL
    provider = "google"
    model = MODEL

    def __init__(
        self,
        prices: PriceTable,
        client: object | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        deadline_s: float = _DEFAULT_DEADLINE_S,
    ) -> None:
        if not deadline_s > 0:
            raise ValueError(f"deadline_s deve ser > 0, veio {deadline_s!r}")
        self._prices = prices
        self._client = client  # nunca é lido aqui; o real nasce em _ensure_client
        self._sleep = sleep
        self._clock = clock
        self._deadline_s = deadline_s

    # --- estimate ---------------------------------------------------------------------------

    def estimate(self, scene: Scene) -> CostEstimate:
        _mime_of(scene.photo)
        return CostEstimate(
            units=float(_requested_seconds(scene.duration_s)),
            unit_kind=self._prices.unit_kind_of(self.method),
        )

    # --- generate ---------------------------------------------------------------------------

    def generate(self, req: SceneRequest, token: SpendToken) -> SceneResult:
        """Bloqueante. Falhas são ``ProviderError`` com a classificação de dinheiro do docstring do módulo."""
        token.consume(self.method)
        scene = req.scene
        seconds = _requested_seconds(scene.duration_s)
        mime = _mime_of(scene.photo)
        if not scene.photo.is_file():
            raise InputError(f"foto da cena {scene.index} não encontrada: {scene.photo}")
        deadline = self._clock() + self._deadline_s
        client = self._ensure_client()
        uploaded = self._upload_first_frame(client, scene, mime, deadline)
        interaction = self._submit(client, uploaded, scene, seconds, deadline)
        return self._finish(client, interaction, req, token, seconds, deadline)

    # --- client -----------------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                raise ConfigError("chave da Gemini API ausente: defina GEMINI_API_KEY no ambiente")
            self._client = genai.Client(
                vertexai=False, api_key=api_key, http_options=make_http_options(self._deadline_s)
            )
        return self._client

    # --- antes do aceite --------------------------------------------------------------------

    def _pre_accept[T](self, attempt: Callable[[], T]) -> T:
        """Repete ``attempt`` (só falhas transitórias) e converte recusas definitivas em ``transient=True``."""
        try:
            return retry_before_accept(attempt, sleep=self._sleep)
        except _Rejected as exc:
            raise ProviderError(str(exc), transient=True) from (exc.__cause__ or exc)

    def _upload_first_frame(
        self, client: Any, scene: Scene, mime: str, deadline: float
    ) -> _Uploaded:
        display = f"verion-scene-{scene.index:02d}{scene.photo.suffix.lower()}"  # nada do nome do imóvel

        def attempt() -> _Uploaded:
            try:
                file = client.files.upload(
                    file=str(scene.photo),
                    config=genai_types.UploadFileConfig(mime_type=mime, display_name=display),
                )
                name, uri = file.name, file.uri
                if not name or not uri:
                    raise _Rejected("upload sem name/uri na resposta")
                self._wait_active(client, name, _state_name(file.state), deadline)
                return _Uploaded(name=name, uri=uri, mime_type=mime)
            except (_Rejected, ProviderError):
                raise
            except (_FileFailed, _DeadlineExceeded) as exc:
                raise _Rejected(f"first-frame: {exc}") from exc
            except genai_errors.APIError as exc:
                if _is_client_rejection(exc):
                    raise _Rejected(f"upload do first-frame recusado: {_describe(exc)}") from exc
                raise ProviderError(
                    f"upload do first-frame falhou: {_describe(exc)}", transient=True
                ) from exc
            except Exception as exc:
                raise ProviderError(
                    f"upload do first-frame falhou: {_describe(exc)}", transient=True
                ) from exc

        return self._pre_accept(attempt)

    def _submit(
        self, client: Any, uploaded: _Uploaded, scene: Scene, seconds: int, deadline: float
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"type": "image", "uri": uploaded.uri, "mime_type": uploaded.mime_type},
                {"type": "text", "text": _prompt_text(scene)},
            ],
            "response_format": {
                "type": "video",
                "aspect_ratio": ASPECT_RATIO,
                "resolution": RESOLUTION,
                "duration": f"{seconds}s",
                "delivery": "uri",
            },
            "background": False,
            "store": False,
            "stream": False,
            "automatic_continuation": False,  # opção do SDK: sem interactions pagas extras
        }

        def attempt() -> Any:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _Rejected("prazo esgotado antes de submeter o pedido ao provedor")
            try:
                return client.interactions.create(**kwargs, timeout=remaining)
            except Exception as exc:
                status = _status_of(exc)
                if status is not None:
                    if status in _RETRYABLE_CODES:
                        raise ProviderError(
                            f"provedor recusou o pedido: {_describe(exc)}", transient=True
                        ) from exc
                    if _is_client_rejection(exc):
                        raise _Rejected(f"provedor recusou o pedido: {_describe(exc)}") from exc
                    raise _Ambiguous(exc) from exc
                if _failed_before_send(exc):
                    raise ProviderError(
                        f"sem conexão com o provedor: {_describe(exc)}", transient=True
                    ) from exc
                raise _Ambiguous(exc) from exc

        try:
            return self._pre_accept(attempt)
        except _Ambiguous as amb:
            raise ProviderError(
                f"resposta incerta do provedor ao gerar a cena {scene.index} (pode ter sido aceito e faturado; "
                f"o pedido NÃO será reenviado): {_describe(amb.original)}",
                transient=False,
                billed_units=float(seconds),
                provider_job_id=None,
            ) from amb.original

    # --- depois do aceite -------------------------------------------------------------------

    def _finish(
        self,
        client: Any,
        interaction: Any,
        req: SceneRequest,
        token: SpendToken,
        seconds: int,
        deadline: float,
    ) -> SceneResult:
        video = getattr(interaction, "output_video", None)
        uri = getattr(video, "uri", None) or None
        file_id = _file_id(uri)
        job_id = (getattr(interaction, "id", None) or "").strip() or (f"files/{file_id}" if file_id else None)

        def fail(message: str) -> ProviderError:
            return ProviderError(
                message, transient=False, billed_units=float(seconds), provider_job_id=job_id
            )

        part = req.out_path.with_name(req.out_path.name + ".part")
        try:
            if job_id is not None:
                token.job_accepted(job_id)  # antes de tudo: um crash daqui em diante não perde o id
            status = _state_name(getattr(interaction, "status", None))
            if status != "COMPLETED":
                raise fail(f"interação terminou como {status.lower()!r}{self._interaction_errors(interaction)}")
            if uri is None:
                raise fail(
                    "interação concluída sem vídeo na resposta (bloqueio de política ou restrição regional?)"
                )
            if file_id is not None:
                self._wait_active(client, f"files/{file_id}", None, deadline)
            req.out_path.parent.mkdir(parents=True, exist_ok=True)
            self._download(client, uri, part, deadline)
            if not part.is_file() or part.stat().st_size == 0:
                raise fail("download do vídeo gerado não gravou nenhum byte")
            probe = ffmpeg.probe(part)
            if not probe.duration_s > 0 or probe.fps_value <= 0:
                raise fail(f"vídeo baixado inválido: duração {probe.duration_s!r}, fps {probe.fps!r}")
            os.replace(part, req.out_path)
        except ProviderError:
            raise
        except Exception as exc:
            raise fail(f"falha depois do aceite do job: {_describe(exc)}") from exc
        finally:
            part.unlink(missing_ok=True)

        meta = {
            "model": self.model,
            "resolution_requested": RESOLUTION,
            "aspect_ratio": ASPECT_RATIO,
            "duration_requested_s": str(seconds),
            "delivered": f"{probe.width}x{probe.height}",
            "measured_s": f"{probe.duration_s:.3f}",
        }
        if probe.duration_s > seconds + _OVER_REQUESTED_TOLERANCE_S:
            meta["over_requested"] = "true"
        if getattr(interaction, "id", None):
            meta["interaction_id"] = str(interaction.id)
        if file_id is not None:
            meta["output_file"] = f"files/{file_id}"
        tokens = getattr(getattr(interaction, "usage", None), "total_output_tokens", None)
        if tokens is not None:
            meta["output_tokens"] = str(tokens)
        return SceneResult(
            clip_path=req.out_path,
            duration_s=probe.duration_s,
            width=probe.width,
            height=probe.height,
            fps=probe.fps_value,
            units=max(round(probe.duration_s, 3), float(seconds)),
            unit_kind=self._prices.unit_kind_of(self.method),
            cost_is_estimate=True,
            provider_job_id=job_id,
            meta=meta,
        )

    @staticmethod
    def _interaction_errors(interaction: Any) -> str:
        errors = getattr(interaction, "errors", None)
        if not errors:
            return ""
        parts = [
            f"{getattr(e, 'code', '')}: {getattr(e, 'message', '')}".strip(": ") for e in errors
        ]
        return " (" + _describe(RuntimeError("; ".join(parts))).removeprefix("RuntimeError: ") + ")"

    def _download(self, client: Any, uri: str, part: Path, deadline: float) -> None:
        """``files.download`` para ``part``. Grátis e idempotente: repete com backoff (o job já foi pago)."""
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            part.unlink(missing_ok=True)
            try:
                client.files.download(file=uri, destination=str(part))
                return
            except Exception:
                delay = _DOWNLOAD_BACKOFF_S * 2 ** (attempt - 1)
                if attempt == _DOWNLOAD_ATTEMPTS or self._clock() + delay > deadline:
                    raise
                self._sleep(delay)

    # --- polling da Files API ---------------------------------------------------------------

    def _wait_active(self, client: Any, name: str, state: str | None, deadline: float) -> None:
        """Espera ``files/<id>`` ficar ``ACTIVE``. ``state`` é o estado já conhecido (ex.: o do upload) ou
        ``None`` para consultar de imediato. ``FAILED`` -> ``_FileFailed``; prazo -> ``_DeadlineExceeded``.
        Tolera poucos erros seguidos de ``files.get``; passando disso, relança o último."""
        interval = _POLL_FIRST_S
        blips = 0
        while True:
            if state is None:
                try:
                    state = _state_name(client.files.get(name=name).state)
                    blips = 0
                except Exception:
                    blips += 1
                    if blips > _POLL_TOLERATED_ERRORS:
                        raise
                    state = "RETRY"
            if state == "ACTIVE":
                return
            if state == "FAILED":
                raise _FileFailed(f"a Files API marcou {name} como FAILED")
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _DeadlineExceeded(f"prazo de {self._deadline_s:g} s esgotado esperando {name} ficar ACTIVE")
            self._sleep(min(interval, remaining))
            interval = min(interval * _POLL_FACTOR, _POLL_MAX_S)
            state = None
