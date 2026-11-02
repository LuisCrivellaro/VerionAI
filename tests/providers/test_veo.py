"""Testes do adaptador ``veo-3.1-fast`` (T8). Sem rede, sem chave de API, sem espera real.

O SDK ``google-genai`` é substituído por um mock aninhado (``client.models``, ``client.operations``,
``client.files``). O MP4 "entregue pelo provedor" é gerado com ffmpeg/lavfi, uma vez por sessão.

CONFIRMADO no SDK instalado (google-genai 2.34.0, por inspeção offline): assinatura de
``models.generate_videos(model, source, config)``, ``operations.get(operation)`` (devolve a operação
atualizada), ``files.download(file=<Video>, destination=...)``, campos ``name``/``done``/``error``/
``response.generated_videos[i].video`` e a conversão para o corpo do Gemini API
(``instances[0].image.bytesBase64Encoded``, ``parameters.durationSeconds`` etc.).

HIPÓTESE (só verificável com chamada paga, não autorizada): os nomes dos campos de erro no corpo real
da operação, o ID de modelo ainda aceito pelo servidor (o ``veo-3.1-fast-generate-preview`` consta como
descontinuado em 22/10/2026 na doc oficial) e o comportamento com foto em retrato. Os mocks abaixo
codificam a doc oficial e o SDK, não respostas reais do servidor.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import types as pytypes
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types as gtypes

from verion import budget
from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import ConfigError, InputError, ProviderError
from verion.ledger import repo
from verion.providers import registry, veo
from verion.providers.base import Scene, SceneRequest
from verion.providers.contract import SpyClient, assert_generator_contract
from verion.providers.veo import VeoAdapter

JOB = "models/veo-3.1-fast-generate-preview/operations/op-123"
VIDEO_URI = "https://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media"

NewRun = Callable[..., int]


# --- mídia sintética -------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_mp4(tmp_path_factory: pytest.TempPathFactory) -> Callable[[float], Path]:
    """Fábrica de MP4 pequenos (160x90, 24 fps, com áudio) por duração; um por duração e sessão."""
    if shutil.which("ffmpeg") is None:
        pytest.fail("ffmpeg não encontrado no PATH (os testes do Veo geram o MP4 do mock)")
    root = tmp_path_factory.mktemp("veo-mp4")
    cache: dict[float, Path] = {}

    def _make(seconds: float) -> Path:
        if seconds not in cache:
            path = root / f"clip-{seconds}.mp4"
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=24:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )  # fmt: skip
            if proc.returncode != 0 or not path.is_file():
                pytest.fail(f"não foi possível gerar o MP4 do mock: {proc.stderr}")
            cache[seconds] = path
        return cache[seconds]

    return _make


# --- mock do SDK -----------------------------------------------------------------------------


def pending_op(name: str = JOB) -> gtypes.GenerateVideosOperation:
    return gtypes.GenerateVideosOperation(name=name, done=False)


def done_op(name: str = JOB) -> gtypes.GenerateVideosOperation:
    return gtypes.GenerateVideosOperation(
        name=name,
        done=True,
        response=gtypes.GenerateVideosResponse(
            generated_videos=[gtypes.GeneratedVideo(video=gtypes.Video(uri=VIDEO_URI))]
        ),
    )


class MockVeo:
    """Imita ``genai.Client`` só no que o adaptador usa. Cada script é consumido em ordem; o último
    item de ``polls`` se repete. Exceções do script são levantadas; ``Operation`` é devolvida."""

    def __init__(
        self,
        mp4: Path,
        *,
        submits: list[Any] | None = None,
        polls: list[Any] | None = None,
        download_error: Exception | None = None,
        on_poll: Callable[[], None] | None = None,
    ) -> None:
        self._mp4 = mp4
        self._submits: deque[Any] = deque(submits if submits is not None else [pending_op()])
        self._polls: list[Any] = list(polls if polls is not None else [pending_op(), done_op()])
        self._download_error = download_error
        self._on_poll = on_poll
        self.submit_kwargs: list[dict[str, Any]] = []
        self.poll_count = 0
        self.download_calls: list[dict[str, Any]] = []
        self.models = pytypes.SimpleNamespace(generate_videos=self._generate_videos)
        self.operations = pytypes.SimpleNamespace(get=self._get)
        self.files = pytypes.SimpleNamespace(download=self._download)

    def _generate_videos(self, **kwargs: Any) -> Any:
        self.submit_kwargs.append(kwargs)
        item = self._submits.popleft() if len(self._submits) > 1 else self._submits[0]
        if isinstance(item, Exception):
            raise item
        return item

    def _get(self, operation: Any, **kwargs: Any) -> Any:
        if self._on_poll is not None:
            self._on_poll()
        item = self._polls[min(self.poll_count, len(self._polls) - 1)]
        self.poll_count += 1
        if isinstance(item, Exception):
            raise item
        return item

    def _download(self, *, file: Any, destination: Any = None, **kwargs: Any) -> None:
        self.download_calls.append({"file": file, "destination": destination})
        if self._download_error is not None:
            raise self._download_error
        shutil.copyfile(self._mp4, destination)

    @property
    def n_calls(self) -> int:
        return len(self.submit_kwargs) + self.poll_count + len(self.download_calls)


# O fluxo feliz padrão: 1 submissão + 2 consultas (pendente, pronta) + 1 download.
HAPPY_CALLS = 4


def api_error(code: int, status: str = "ERRO", message: str = "falha simulada") -> Exception:
    body = {"error": {"code": code, "message": message, "status": status}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


# --- fixtures e helpers ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """O módulo do adaptador nunca dorme nem lê o relógio de verdade: tudo é injetado."""

    def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("time real usado pelo adaptador; injete sleep/clock")

    monkeypatch.setattr(veo, "time", pytypes.SimpleNamespace(sleep=boom, monotonic=boom))


@pytest.fixture
def funded(authorize: Callable[[float], int]) -> None:
    authorize(1000.0)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def adapter_factory(
    prices: PriceTable, fake_clock: Any, sleeps: list[float]
) -> Callable[..., VeoAdapter]:
    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_clock.advance(seconds)

    def _make(client: object | None, **kwargs: Any) -> VeoAdapter:
        kwargs.setdefault("sleep", _sleep)
        kwargs.setdefault("clock", fake_clock)
        return VeoAdapter(prices, client, **kwargs)

    return _make


@pytest.fixture
def mock_veo(make_mp4: Callable[[float], Path]) -> MockVeo:
    return MockVeo(make_mp4(6.0))


def token_for(
    conn: sqlite3.Connection, prices: PriceTable, gen: VeoAdapter, scene: Scene, run_id: int
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


def req_for(scene: Scene, token: SpendToken, run_id: int, out_dir: Path, seed: int | None = None) -> SceneRequest:
    return SceneRequest(
        scene=scene,
        run_id=run_id,
        attempt_no=token.attempt_no,
        out_path=out_dir / "veo" / "s01.mp4",
        seed=seed,
    )


@pytest.fixture
def run_id(new_run: NewRun) -> int:
    return new_run("veo-3.1-fast")


@pytest.fixture
def go(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    run_id: int,
    out_dir: Path,
    funded: None,
    adapter_factory: Callable[..., VeoAdapter],
) -> Callable[..., Any]:
    """Cria o adaptador, reserva e chama ``generate``. Devolve ``(resultado, token, req)``."""

    def _go(client: object, *, scene_: Scene | None = None, seed: int | None = None, **kwargs: Any) -> Any:
        sc = scene_ or scene
        gen = adapter_factory(client, **kwargs)
        token = token_for(conn, prices, gen, sc, run_id)
        req = req_for(sc, token, run_id, out_dir, seed)
        return gen.generate(req, token), token, req

    return _go


def provider_error(go: Callable[..., Any], client: object, **kwargs: Any) -> ProviderError:
    with pytest.raises(ProviderError) as info:
        go(client, **kwargs)
    return info.value


# --- contrato (T2) ---------------------------------------------------------------------------


def test_assert_generator_contract_with_polling_mock(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    out_dir: Path,
    mock_veo: MockVeo,
    adapter_factory: Callable[..., VeoAdapter],
) -> None:
    # max_client_calls EXATO do fluxo: 1 submissão + 2 consultas + 1 download.
    assert_generator_contract(
        adapter_factory,
        conn=conn,
        prices=prices,
        scene=scene,
        good_client=mock_veo,
        out_dir=out_dir,
        max_client_calls=HAPPY_CALLS,
    )
    assert mock_veo.n_calls == HAPPY_CALLS  # só o caminho feliz chegou ao mock; as recusas não


def test_contract_fails_if_max_client_calls_is_one_less(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    out_dir: Path,
    mock_veo: MockVeo,
    adapter_factory: Callable[..., VeoAdapter],
) -> None:
    # Guarda contra um limite frouxo demais: o limite do helper é de fato o do fluxo.
    with pytest.raises(AssertionError, match="chamadas"):
        assert_generator_contract(
            adapter_factory,
            conn=conn,
            prices=prices,
            scene=scene,
            good_client=mock_veo,
            out_dir=out_dir,
            max_client_calls=HAPPY_CALLS - 1,
        )


def test_registry_creates_adapter_without_client_or_key(prices: PriceTable) -> None:
    gen = registry.create("veo-3.1-fast", prices)
    assert isinstance(gen, VeoAdapter)
    assert (gen.method, gen.provider) == ("veo-3.1-fast", "google")


def test_constructor_never_touches_the_client(prices: PriceTable) -> None:
    class Hostile:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"construtor leu client.{name}")

    VeoAdapter(prices, Hostile())
    spy = SpyClient(MockVeo(Path("nao-usado.mp4")))
    VeoAdapter(prices, spy)
    assert spy.accesses == 0 and spy.calls == 0


def test_invalid_resolution_is_a_config_error(prices: PriceTable) -> None:
    with pytest.raises(ConfigError, match="resolution"):
        VeoAdapter(prices, None, resolution="480p")


# --- duração e estimate ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [(3.0, 4), (4.0, 4), (4.1, 6), (6.0, 6), (6.5, 8), (8.0, 8), (10.0, 8)],
)
def test_duration_is_smallest_supported_not_below_target(
    prices: PriceTable, scene: Scene, target: float, expected: int
) -> None:
    sc = Scene(**{**scene.__dict__, "duration_s": target})
    est = VeoAdapter(prices).estimate(sc)
    assert est.units == float(expected)
    assert est.unit_kind == "video_seconds"


def test_1080p_forces_eight_seconds(prices: PriceTable, scene: Scene) -> None:
    # Doc oficial: 1080p exige durationSeconds == 8 (hipótese: regra vigente também no GA).
    assert VeoAdapter(prices, resolution="1080p").estimate(scene).units == 8.0


# --- conteúdo do pedido ----------------------------------------------------------------------


def test_request_carries_photo_prompt_and_supported_duration(
    go: Callable[..., Any], mock_veo: MockVeo, scene: Scene
) -> None:
    go(mock_veo)
    assert len(mock_veo.submit_kwargs) == 1
    kw = mock_veo.submit_kwargs[0]
    assert kw["model"] == veo.DEFAULT_MODEL
    source = kw["source"]
    assert source.prompt == scene.prompt
    assert source.image.image_bytes == scene.photo.read_bytes()
    assert source.image.mime_type == "image/jpeg"
    cfg = kw["config"]
    assert cfg.duration_seconds == 6  # cena de 5,0 s → menor duração suportada >= 5
    assert cfg.aspect_ratio == "16:9"
    assert cfg.resolution == "720p"
    assert cfg.person_generation == "allow_adult"  # único valor aceito em imagem→vídeo (doc)
    assert cfg.negative_prompt is None


def test_negative_prompt_is_sent_when_present(
    go: Callable[..., Any], mock_veo: MockVeo, scene: Scene
) -> None:
    sc = Scene(**{**scene.__dict__, "negative_prompt": "móveis novos, pessoas"})
    go(mock_veo, scene_=sc)
    assert mock_veo.submit_kwargs[0]["config"].negative_prompt == "móveis novos, pessoas"


def test_seed_is_never_sent(go: Callable[..., Any], mock_veo: MockVeo) -> None:
    # CONFIRMADO no SDK: seed levanta ValueError no modo Gemini Developer API.
    go(mock_veo, seed=42)
    assert mock_veo.submit_kwargs[0]["config"].seed is None


def test_request_converts_with_the_installed_sdk_for_gemini_api(
    go: Callable[..., Any], mock_veo: MockVeo
) -> None:
    # Nenhum campo do config é rejeitado pelo SDK real no modo Gemini API (vertexai=False).
    import google.genai.models as sdk_models

    go(mock_veo)
    kw = mock_veo.submit_kwargs[0]
    body = sdk_models._GenerateVideosParameters_to_mldev(
        pytypes.SimpleNamespace(vertexai=False),
        {
            "model": kw["model"],
            "source": kw["source"].model_dump(exclude_none=True),
            "config": kw["config"].model_dump(exclude_none=True),
        },
    )
    assert body["parameters"]["durationSeconds"] == 6
    assert body["instances"][0]["image"]["mimeType"] == "image/jpeg"


def test_png_photo_uses_png_mime(
    go: Callable[..., Any], mock_veo: MockVeo, scene: Scene, tmp_path: Path
) -> None:
    png = tmp_path / "sala.png"
    png.write_bytes(b"png")
    go(mock_veo, scene_=Scene(**{**scene.__dict__, "photo": png}))
    assert mock_veo.submit_kwargs[0]["source"].image.mime_type == "image/png"


def test_unsupported_photo_format_is_an_input_error_without_client_use(
    go: Callable[..., Any], mock_veo: MockVeo, scene: Scene, tmp_path: Path
) -> None:
    gif = tmp_path / "sala.gif"
    gif.write_bytes(b"gif")
    with pytest.raises(InputError, match="formato"):
        go(mock_veo, scene_=Scene(**{**scene.__dict__, "photo": gif}))
    assert mock_veo.n_calls == 0


def test_missing_photo_is_an_input_error_without_client_use(
    go: Callable[..., Any], mock_veo: MockVeo, scene: Scene, tmp_path: Path
) -> None:
    with pytest.raises(InputError, match="foto"):
        go(mock_veo, scene_=Scene(**{**scene.__dict__, "photo": tmp_path / "nao-existe.jpg"}))
    assert mock_veo.n_calls == 0


# --- caminho feliz ---------------------------------------------------------------------------


def test_happy_path_result_and_file(
    go: Callable[..., Any], mock_veo: MockVeo
) -> None:
    result, _token, req = go(mock_veo)
    assert result.clip_path == req.out_path and req.out_path.is_file()
    assert not list(req.out_path.parent.glob("*.part"))
    assert result.provider_job_id == JOB
    assert result.unit_kind == "video_seconds"
    assert (result.width, result.height) == (160, 90)
    assert result.fps == pytest.approx(24.0)
    assert result.duration_s == pytest.approx(6.0, abs=0.2)
    assert result.cost_is_estimate is True  # a API não devolve o consumo
    assert result.meta["model"] == veo.DEFAULT_MODEL
    assert result.meta["requested_s"] == "6"
    assert result.meta["resolution"] == "720p"
    assert mock_veo.download_calls[0]["file"].uri == VIDEO_URI


def test_units_are_the_generated_seconds_not_the_requested(
    go: Callable[..., Any], make_mp4: Callable[[float], Path]
) -> None:
    client = MockVeo(make_mp4(4.0))  # pediu 6, o provedor entregou 4
    result, _t, _r = go(client)
    assert result.units == pytest.approx(4.0, abs=0.2)
    assert result.duration_s == pytest.approx(result.units)


def test_polls_until_done_with_injected_sleep(
    go: Callable[..., Any], make_mp4: Callable[[float], Path], sleeps: list[float]
) -> None:
    client = MockVeo(make_mp4(6.0), polls=[pending_op(), pending_op(), pending_op(), done_op()])
    go(client, poll_interval_s=7.0)
    assert client.poll_count == 4
    assert sleeps == [7.0] * 4  # uma espera antes de cada consulta; nenhuma real


def test_operation_already_done_on_submit_skips_polling(
    go: Callable[..., Any], make_mp4: Callable[[float], Path], sleeps: list[float]
) -> None:
    client = MockVeo(make_mp4(6.0), submits=[done_op()])
    result, _t, _r = go(client)
    assert client.poll_count == 0 and sleeps == []
    assert result.provider_job_id == JOB


def test_inline_video_bytes_are_written_without_download(
    go: Callable[..., Any], make_mp4: Callable[[float], Path]
) -> None:
    # HIPÓTESE: no modo Vertex/Enterprise o vídeo pode vir em video_bytes. Não é o caminho do Gemini API.
    mp4 = make_mp4(6.0)
    op = gtypes.GenerateVideosOperation(
        name=JOB,
        done=True,
        response=gtypes.GenerateVideosResponse(
            generated_videos=[gtypes.GeneratedVideo(video=gtypes.Video(video_bytes=mp4.read_bytes()))]
        ),
    )
    client = MockVeo(mp4, submits=[op])
    result, _t, _r = go(client)
    assert client.download_calls == []
    assert result.clip_path.read_bytes() == mp4.read_bytes()


def test_job_id_is_in_the_ledger_before_the_first_poll(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    run_id: int,
    out_dir: Path,
    funded: None,
    make_mp4: Callable[[float], Path],
    adapter_factory: Callable[..., VeoAdapter],
) -> None:
    gen = adapter_factory(None)
    token = token_for(conn, prices, gen, scene, run_id)
    seen: list[str | None] = []

    def on_poll() -> None:
        row = repo.get_attempt(conn, token.attempt_id)
        seen.append(None if row is None else row.provider_job_id)

    client = MockVeo(make_mp4(6.0), on_poll=on_poll)
    adapter_factory(client).generate(req_for(scene, token, run_id, out_dir), token)
    assert seen and set(seen) == {JOB}


# --- falhas ANTES do aceite ------------------------------------------------------------------


@pytest.mark.parametrize("code", [429, 503])
def test_quota_or_unavailable_before_accept_is_transient_and_retried(
    go: Callable[..., Any], mock_veo: MockVeo, sleeps: list[float], code: int
) -> None:
    client = MockVeo(mock_veo._mp4, submits=[api_error(code, "RESOURCE_EXHAUSTED")])
    err = provider_error(go, client)
    assert (err.transient, err.billed_units, err.provider_job_id) == (True, 0.0, None)
    assert len(client.submit_kwargs) == 3  # retry_before_accept: 3 tentativas
    assert len(sleeps) == 2 and 1.0 <= sleeps[0] < 2.0 and 2.0 <= sleeps[1] < 3.0
    assert client.poll_count == 0 and client.download_calls == []


def test_429_then_success_submits_twice_and_charges_once(
    go: Callable[..., Any], make_mp4: Callable[[float], Path]
) -> None:
    client = MockVeo(make_mp4(6.0), submits=[api_error(429), pending_op()])
    result, _t, _r = go(client)
    assert len(client.submit_kwargs) == 2
    assert result.provider_job_id == JOB


def test_connect_error_is_transient_before_accept(go: Callable[..., Any], mock_veo: MockVeo) -> None:
    client = MockVeo(mock_veo._mp4, submits=[httpx.ConnectError("sem rota")])
    err = provider_error(go, client)
    assert (err.transient, err.billed_units, err.provider_job_id) == (True, 0.0, None)
    assert len(client.submit_kwargs) == 3


def test_connection_refused_is_transient_before_accept(go: Callable[..., Any], mock_veo: MockVeo) -> None:
    client = MockVeo(mock_veo._mp4, submits=[ConnectionRefusedError("recusada")])
    err = provider_error(go, client)
    assert (err.transient, err.billed_units, err.provider_job_id) == (True, 0.0, None)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_client_errors_are_not_transient_nor_retried(
    go: Callable[..., Any], mock_veo: MockVeo, code: int
) -> None:
    client = MockVeo(mock_veo._mp4, submits=[api_error(code, message="model is no longer available")])
    err = provider_error(go, client)
    assert (err.transient, err.billed_units, err.provider_job_id) == (False, 0.0, None)
    assert len(client.submit_kwargs) == 1
    assert "no longer available" in str(err)


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("lento"), TimeoutError("lento"), api_error(500), api_error(504)]
)
def test_ambiguous_submit_failures_are_not_retried(
    go: Callable[..., Any], mock_veo: MockVeo, exc: Exception
) -> None:
    # O POST pode ter chegado e sido faturado: repetir duplicaria a cobrança. Sem id, billed_units = 0
    # (o ledger mantém a reserva inteira em qualquer erro).
    client = MockVeo(mock_veo._mp4, submits=[exc])
    err = provider_error(go, client)
    assert (err.transient, err.billed_units, err.provider_job_id) == (False, 0.0, None)
    assert len(client.submit_kwargs) == 1
    assert "pode ter sido aceito" in str(err)


def test_operation_without_name_is_billed_conservatively_without_id(
    go: Callable[..., Any], mock_veo: MockVeo
) -> None:
    client = MockVeo(mock_veo._mp4, submits=[gtypes.GenerateVideosOperation(done=False)])
    err = provider_error(go, client)
    assert (err.transient, err.billed_units, err.provider_job_id) == (False, 6.0, None)
    assert len(client.submit_kwargs) == 1


# --- cliente preguiçoso e chave --------------------------------------------------------------


def test_real_client_is_created_lazily_after_consume(
    monkeypatch: pytest.MonkeyPatch,
    go: Callable[..., Any],
    mock_veo: MockVeo,
    prices: PriceTable,
) -> None:
    created: list[dict[str, Any]] = []

    def fake_client(**kwargs: Any) -> MockVeo:
        created.append(kwargs)
        return mock_veo

    monkeypatch.setattr(veo.genai, "Client", fake_client)
    VeoAdapter(prices)  # construir não cria client
    assert created == []
    go(None)
    assert len(created) == 1
    assert created[0]["http_options"].timeout == 120_000  # timeout por chamada, em ms


def test_missing_api_key_is_a_config_error_before_any_call(
    monkeypatch: pytest.MonkeyPatch, go: Callable[..., Any]
) -> None:
    def no_key(**_: Any) -> Any:
        raise ValueError("Missing key inputs argument!")

    monkeypatch.setattr(veo.genai, "Client", no_key)
    with pytest.raises(ConfigError, match="chave"):
        go(None)


# --- falhas DEPOIS do aceite -----------------------------------------------------------------


def assert_post_accept(err: ProviderError, client: MockVeo) -> None:
    assert err.transient is False
    assert err.billed_units == 6.0  # conservador: segundos pedidos
    assert err.provider_job_id == JOB
    assert len(client.submit_kwargs) == 1  # nunca reenvia depois do aceite
    assert JOB in str(err)


def test_polling_deadline_is_post_accept_failure(
    go: Callable[..., Any], mock_veo: MockVeo, sleeps: list[float]
) -> None:
    client = MockVeo(mock_veo._mp4, polls=[pending_op()])
    err = provider_error(go, client, poll_interval_s=10.0, timeout_s=35.0)
    assert_post_accept(err, client)
    assert "prazo" in str(err)
    assert client.download_calls == []
    assert sum(sleeps) >= 35.0 and len(sleeps) == 4  # 4 esperas simuladas de 10 s, nenhuma real


def test_operation_error_is_post_accept_failure(go: Callable[..., Any], mock_veo: MockVeo) -> None:
    failed = gtypes.GenerateVideosOperation(
        name=JOB, done=True, error={"code": 13, "message": "internal error"}
    )
    client = MockVeo(mock_veo._mp4, polls=[pending_op(), failed])
    err = provider_error(go, client)
    assert_post_accept(err, client)
    assert "internal error" in str(err)


def test_filtered_video_is_post_accept_failure(go: Callable[..., Any], mock_veo: MockVeo) -> None:
    # A doc diz que vídeo bloqueado não é cobrado; o adaptador ainda assim declara o pior caso.
    filtered = gtypes.GenerateVideosOperation(
        name=JOB,
        done=True,
        response=gtypes.GenerateVideosResponse(
            rai_media_filtered_count=1, rai_media_filtered_reasons=["política de segurança"]
        ),
    )
    client = MockVeo(mock_veo._mp4, polls=[filtered])
    err = provider_error(go, client)
    assert_post_accept(err, client)
    assert "política de segurança" in str(err)


def test_download_failure_is_post_accept_failure(
    go: Callable[..., Any], mock_veo: MockVeo
) -> None:
    client = MockVeo(mock_veo._mp4, download_error=httpx.ReadTimeout("download lento"))
    err = provider_error(go, client)
    assert_post_accept(err, client)
    assert len(client.download_calls) == 1  # sem retry de download dentro do adaptador


def test_corrupt_download_is_post_accept_failure_and_leaves_no_clip(
    go: Callable[..., Any], tmp_path: Path, out_dir: Path
) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"isto nao e um mp4")
    client = MockVeo(bad)
    err = provider_error(go, client)
    assert_post_accept(err, client)
    assert not (out_dir / "veo" / "s01.mp4").exists()
    assert not list((out_dir / "veo").glob("*.part"))


def test_single_poll_error_is_tolerated(
    go: Callable[..., Any], make_mp4: Callable[[float], Path]
) -> None:
    client = MockVeo(make_mp4(6.0), polls=[api_error(503), pending_op(), done_op()])
    result, _t, _r = go(client)
    assert client.poll_count == 3 and result.provider_job_id == JOB


def test_repeated_poll_errors_are_post_accept_failure(
    go: Callable[..., Any], mock_veo: MockVeo
) -> None:
    client = MockVeo(mock_veo._mp4, polls=[api_error(503)])
    err = provider_error(go, client)
    assert_post_accept(err, client)
    assert client.poll_count == 3


def test_permanent_poll_error_fails_at_once(go: Callable[..., Any], mock_veo: MockVeo) -> None:
    client = MockVeo(mock_veo._mp4, polls=[api_error(404)])
    err = provider_error(go, client)
    assert_post_accept(err, client)
    assert client.poll_count == 1


def test_post_accept_failure_keeps_job_id_in_ledger(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    run_id: int,
    out_dir: Path,
    funded: None,
    mock_veo: MockVeo,
    adapter_factory: Callable[..., VeoAdapter],
) -> None:
    gen = adapter_factory(MockVeo(mock_veo._mp4, polls=[api_error(404)]))
    token = token_for(conn, prices, gen, scene, run_id)
    with pytest.raises(ProviderError):
        gen.generate(req_for(scene, token, run_id, out_dir), token)
    row = repo.get_attempt(conn, token.attempt_id)
    assert row is not None and row.provider_job_id == JOB
    # E o harness consegue liquidar com os dados do erro.
    budget.settle_error(
        conn, token, billed_units=6.0, error="x", wall_time_s=0.0, provider_job_id=JOB
    )
