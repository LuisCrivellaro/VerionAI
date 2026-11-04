"""Testes do adaptador ``gemini-omni-1.1-flash`` (T9). Sem rede, sem chave, sem gasto.

O SDK aparece de dois jeitos, ambos locais:

- um mock aninhado (``FakeOmniClient``: ``files.upload/get/download`` e ``interactions.create``), usado
  em quase todos os testes e no helper de contrato;
- o ``genai.Client`` REAL com um ``httpx.MockTransport`` no lugar da rede, em um único teste, para provar
  que os nomes de métodos e o corpo da requisição são aceitos e serializados pelo SDK instalado.

Os clipes "gerados" são MP4 pequenos criados com o ffmpeg local (lavfi). As esperas usam ``sleep`` e
relógio injetados (``fake_clock``): nenhum teste dorme de verdade. Os preços são os fictícios de
tests/conftest.py.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from verion import budget
from verion.budget import SpendToken
from verion.config import PriceTable
from verion.errors import ConfigError, InputError, ProviderError, TokenInvalid
from verion.ledger import repo
from verion.providers.base import (
    CostEstimate,
    Scene,
    SceneGenerator,
    SceneRequest,
    SceneResult,
)
from verion.providers.contract import SpyClient, assert_generator_contract
from verion.providers.gemini_omni import GeminiOmniAdapter, make_http_options
from verion.providers.registry import METHODS, create

FileState = genai_types.FileState

IN_NAME = "files/in1"
IN_URI = "https://generativelanguage.googleapis.com/v1beta/files/in1"
OUT_URI = "https://generativelanguage.googleapis.com/v1beta/files/zz9:download?alt=media"
OUT_NAME = "files/zz9"
JOB_ID = "v1_job_abc"


# --- mídia sintética ------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_clip(tmp_path_factory: pytest.TempPathFactory) -> Callable[[int], Path]:
    """Devolve ``make_clip(segundos)``: MP4 H.264 de 640x360 a 30 fps, gerado pelo lavfi e guardado em cache."""
    if shutil.which("ffmpeg") is None:
        pytest.fail("ffmpeg não encontrado no PATH (os testes do Omni geram MP4 sintético)")
    root = tmp_path_factory.mktemp("clipes_omni")
    cache: dict[int, Path] = {}

    def _make(seconds: int) -> Path:
        if seconds not in cache:
            path = root / f"clip_{seconds}s.mp4"
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )  # fmt: skip
            if proc.returncode != 0 or not path.is_file():
                pytest.fail(f"não foi possível gerar o clipe sintético: {proc.stderr}")
            cache[seconds] = path
        return cache[seconds]

    return _make


@pytest.fixture
def clip5(make_clip: Callable[[int], Path]) -> Path:
    return make_clip(5)


# --- mock aninhado do SDK ----------------------------------------------------------------------


def api_error(code: int, status: str = "ERR") -> genai_errors.APIError:
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, {"error": {"code": code, "message": "falha simulada", "status": status}})


def interaction(
    *,
    status: str = "completed",
    id: str | None = JOB_ID,
    uri: str | None = OUT_URI,
    tokens: int | None = 1234,
) -> Any:
    video = SimpleNamespace(uri=uri, mime_type="video/mp4", data=None) if uri is not None else None
    usage = SimpleNamespace(total_output_tokens=tokens)
    return SimpleNamespace(id=id, status=status, output_video=video, usage=usage, errors=None)


class _Files:
    def __init__(self, owner: FakeOmniClient) -> None:
        self._o = owner

    def upload(self, *, file: Any, config: Any = None) -> genai_types.File:
        o = self._o
        o.events.append("upload")
        o.upload_kwargs.append({"file": file, "config": config})
        if o.upload_errors:
            error = o.upload_errors.pop(0)
            if error is not None:
                raise error
        return genai_types.File(
            name=IN_NAME, uri=IN_URI, mime_type="image/jpeg", state=_state(o.input_states[0])
        )

    def get(self, *, name: str, config: Any = None) -> genai_types.File:
        o = self._o
        if name == IN_NAME:
            o.events.append("get_in")
            queue, index = o.input_states, o.get_in_calls + 1
            o.get_in_calls += 1
        elif name == OUT_NAME:
            o.events.append("get_out")
            queue, index = o.output_states, o.get_out_calls
            o.get_out_calls += 1
            if o.get_out_errors:
                error = o.get_out_errors.pop(0)
                if error is not None:
                    raise error
        else:
            raise AssertionError(f"files.get com nome inesperado: {name!r}")
        state = queue[min(index, len(queue) - 1)]
        return genai_types.File(name=name, state=_state(state))

    def download(self, *, file: Any, destination: Any = None, config: Any = None) -> None:
        o = self._o
        o.events.append("download")
        o.download_kwargs.append({"file": file, "destination": destination})
        if o.on_download is not None:
            o.on_download()
        if o.download_errors:
            error = o.download_errors.pop(0)
            if error is not None:
                raise error
        if o.download_bytes is not None:
            Path(destination).write_bytes(o.download_bytes)
        else:
            shutil.copyfile(o.clip, destination)


class _Interactions:
    def __init__(self, owner: FakeOmniClient) -> None:
        self._o = owner

    def create(self, **kwargs: Any) -> Any:
        o = self._o
        o.events.append("create")
        o.create_kwargs.append(kwargs)
        if o.create_errors:
            error = o.create_errors.pop(0)
            if error is not None:
                raise error
        return o.create_response


def _state(value: str) -> genai_types.FileState:
    return FileState(value)


class FakeOmniClient:
    """Mock aninhado no formato do SDK: ``client.files.*`` e ``client.interactions.create``.

    ``input_states[0]`` é o estado devolvido pelo upload; os seguintes, os de cada ``files.get`` do arquivo
    de entrada. ``output_states`` vale para os ``files.get`` do arquivo gerado (o último estado se repete).
    """

    def __init__(self, clip: Path) -> None:
        self.clip = clip
        self.input_states: list[str] = ["ACTIVE"]
        self.output_states: list[str] = ["ACTIVE"]
        self.upload_errors: list[Exception | None] = []
        self.create_errors: list[Exception | None] = []
        self.get_out_errors: list[Exception | None] = []
        self.download_errors: list[Exception | None] = []
        self.create_response: Any = interaction()
        self.download_bytes: bytes | None = None
        self.on_download: Callable[[], None] | None = None
        self.events: list[str] = []
        self.upload_kwargs: list[dict[str, Any]] = []
        self.create_kwargs: list[dict[str, Any]] = []
        self.download_kwargs: list[dict[str, Any]] = []
        self.get_in_calls = 0
        self.get_out_calls = 0
        self.files = _Files(self)
        self.interactions = _Interactions(self)


@pytest.fixture
def omni(clip5: Path) -> FakeOmniClient:
    return FakeOmniClient(clip5)


# --- helpers --------------------------------------------------------------------------------------


def adapter_for(
    prices: PriceTable,
    client: object | None,
    fake_clock: Any,
    *,
    deadline_s: float = 900.0,
    sleeps: list[float] | None = None,
) -> GeminiOmniAdapter:
    def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        fake_clock.advance(seconds)

    return GeminiOmniAdapter(prices, client, sleep=sleep, clock=fake_clock, deadline_s=deadline_s)


def reserve_for(
    conn: sqlite3.Connection, prices: PriceTable, gen: SceneGenerator, scene: Scene, run_id: int
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


def with_duration(scene: Scene, duration_s: float) -> Scene:
    return Scene(
        index=scene.index,
        photo=scene.photo,
        room=scene.room,
        camera=scene.camera,
        duration_s=duration_s,
        prompt=scene.prompt,
        negative_prompt=scene.negative_prompt,
    )


@pytest.fixture
def funded(authorize: Callable[[float], int]) -> None:
    authorize(1000.0)


@pytest.fixture
def run_id(new_run: Callable[..., int]) -> int:
    return new_run("gemini-omni-1.1-flash")


_DEFAULT: Any = object()  # "use o mock do fixture"; ``None`` significa "sem client" (o real nasce no generate)


class Env:
    """Agrupa o necessário para gerar uma cena com o mock e inspecionar o ledger."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        prices: PriceTable,
        scene: Scene,
        run_id: int,
        out_dir: Path,
        fake_clock: Any,
        client: FakeOmniClient,
    ) -> None:
        self.conn, self.prices, self.scene, self.run_id = conn, prices, scene, run_id
        self.out_dir, self.clock, self.client = out_dir, fake_clock, client
        self.sleeps: list[float] = []

    def gen(self, *, client: object | None = _DEFAULT, deadline_s: float = 900.0) -> GeminiOmniAdapter:
        return adapter_for(
            self.prices,
            self.client if client is _DEFAULT else client,
            self.clock,
            deadline_s=deadline_s,
            sleeps=self.sleeps,
        )

    def run(
        self, *, scene: Scene | None = None, deadline_s: float = 900.0, client: object | None = _DEFAULT
    ) -> tuple[SceneResult, SpendToken]:
        scene = scene or self.scene
        gen = self.gen(client=client, deadline_s=deadline_s)
        token = reserve_for(self.conn, self.prices, gen, scene, self.run_id)
        req = SceneRequest(
            scene=scene,
            run_id=self.run_id,
            attempt_no=token.attempt_no,
            out_path=self.out_dir / "s01.mp4",
        )
        return gen.generate(req, token), token

    def fail(
        self, *, scene: Scene | None = None, deadline_s: float = 900.0, client: object | None = _DEFAULT
    ) -> tuple[ProviderError, SpendToken]:
        scene = scene or self.scene
        gen = self.gen(client=client, deadline_s=deadline_s)
        token = reserve_for(self.conn, self.prices, gen, scene, self.run_id)
        req = SceneRequest(
            scene=scene,
            run_id=self.run_id,
            attempt_no=token.attempt_no,
            out_path=self.out_dir / "s01.mp4",
        )
        with pytest.raises(ProviderError) as info:
            gen.generate(req, token)
        assert not (self.out_dir / "s01.mp4").exists(), "falha não pode deixar clipe em req.out_path"
        return info.value, token

    def job_id_in_ledger(self, token: SpendToken) -> str | None:
        row = repo.get_attempt(self.conn, token.attempt_id)
        assert row is not None
        return row.provider_job_id


@pytest.fixture
def env(
    funded: None,
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    run_id: int,
    out_dir: Path,
    fake_clock: Any,
    omni: FakeOmniClient,
) -> Env:
    return Env(conn, prices, scene, run_id, out_dir, fake_clock, omni)


# --- identidade, registry e estimate -----------------------------------------------------------


def test_identity_and_protocol(prices: PriceTable) -> None:
    gen = GeminiOmniAdapter(prices, object())
    assert isinstance(gen, SceneGenerator)
    assert (gen.method, gen.provider, gen.model) == (
        "gemini-omni-1.1-flash",
        "google",
        "gemini-omni-1.1-flash",
    )


def test_registry_points_to_the_adapter_and_create_works(prices: PriceTable) -> None:
    assert METHODS["gemini-omni-1.1-flash"] == "verion.providers.gemini_omni:GeminiOmniAdapter"
    gen = create("gemini-omni-1.1-flash", prices, object())
    assert type(gen) is GeminiOmniAdapter


def test_estimate_is_the_rounded_integer_seconds_with_the_table_unit_kind(
    prices: PriceTable, scene: Scene
) -> None:
    gen = GeminiOmniAdapter(prices, object())
    assert gen.estimate(scene) == CostEstimate(units=5.0, unit_kind="video_seconds")


@pytest.mark.parametrize(
    ("target", "expected"),
    [(3.0, 3.0), (2.5, 3.0), (4.4, 4.0), (4.5, 5.0), (4.6, 5.0), (7.49, 7.0), (10.0, 10.0), (10.49, 10.0)],
)
def test_estimate_rounds_to_the_nearest_integer_second_halves_up(
    prices: PriceTable, scene: Scene, target: float, expected: float
) -> None:
    assert GeminiOmniAdapter(prices, object()).estimate(with_duration(scene, target)).units == expected


@pytest.mark.parametrize("target", [0.0, 2.0, 2.49, 10.5, 11.0, 40.0, -1.0, float("nan"), float("inf")])
def test_estimate_rejects_durations_the_model_cannot_do(
    prices: PriceTable, scene: Scene, target: float
) -> None:
    with pytest.raises(InputError):
        GeminiOmniAdapter(prices, object()).estimate(with_duration(scene, target))


@pytest.mark.parametrize("name", ["casa.gif", "casa.heic", "casa.tiff", "casa"])
def test_estimate_rejects_a_photo_format_the_files_api_flow_does_not_cover(
    prices: PriceTable, scene: Scene, tmp_path: Path, name: str
) -> None:
    odd = Scene(
        index=1, photo=tmp_path / name, room="sala", camera="push-in", duration_s=5.0, prompt="x"
    )
    with pytest.raises(InputError):
        GeminiOmniAdapter(prices, object()).estimate(odd)


# --- construtor e client preguiçoso -------------------------------------------------------------


def test_constructor_does_not_touch_the_client(prices: PriceTable, omni: FakeOmniClient) -> None:
    spy = SpyClient(omni)
    GeminiOmniAdapter(prices, spy)
    assert (spy.accesses, spy.calls) == (0, 0)


def test_constructor_without_client_does_not_build_the_sdk_client(
    prices: PriceTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("genai.Client criado no construtor")

    monkeypatch.setattr(genai, "Client", boom)
    GeminiOmniAdapter(prices)
    create("gemini-omni-1.1-flash", prices)


def test_real_client_is_built_lazily_after_consume_and_only_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeOmniClient:
        built.append(kwargs)
        return env.client

    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia-de-teste")
    monkeypatch.setattr(genai, "Client", factory)
    gen = adapter_for(env.prices, None, env.clock)
    token = reserve_for(env.conn, env.prices, gen, env.scene, env.run_id)
    assert built == []
    req = SceneRequest(env.scene, env.run_id, token.attempt_no, env.out_dir / "a.mp4")
    gen.generate(req, token)
    assert len(built) == 1
    kwargs = built[0]
    assert kwargs["vertexai"] is False  # nunca GOOGLE_GENAI_USE_VERTEXAI
    assert kwargs["api_key"] == "chave-ficticia-de-teste"
    assert set(kwargs) == {"vertexai", "api_key", "http_options"}
    http_options = kwargs["http_options"]
    assert http_options.timeout == 900 * 1000  # milissegundos
    assert http_options.retry_options.attempts == 1  # o SDK não reenvia o POST pago
    assert http_options.retry_options.http_status_codes == [599]


def test_client_uses_gemini_api_key_even_with_vertex_and_google_api_key_in_the_environment(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, Any]] = []
    monkeypatch.setenv("GEMINI_API_KEY", "chave-gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "outra-chave")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setattr(genai, "Client", lambda **kw: built.append(kw) or env.client)
    gen = adapter_for(env.prices, None, env.clock)
    token = reserve_for(env.conn, env.prices, gen, env.scene, env.run_id)
    gen.generate(SceneRequest(env.scene, env.run_id, token.attempt_no, env.out_dir / "a.mp4"), token)
    assert built[0]["api_key"] == "chave-gemini" and built[0]["vertexai"] is False


def test_real_client_is_not_built_when_the_token_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> object:
        raise AssertionError("genai.Client criado antes de token.consume")

    monkeypatch.setattr(genai, "Client", boom)
    gen = adapter_for(env.prices, None, env.clock)
    token = reserve_for(env.conn, env.prices, gen, env.scene, env.run_id)
    token.consume(gen.method)  # queima o token
    req = SceneRequest(env.scene, env.run_id, token.attempt_no, env.out_dir / "a.mp4")
    with pytest.raises(TokenInvalid):
        gen.generate(req, token)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_gemini_api_key_is_a_config_error_even_with_google_api_key_set(
    env: Env, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    def boom(**kwargs: Any) -> object:
        raise AssertionError("genai.Client não pode ser criado sem GEMINI_API_KEY")

    if value is None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GEMINI_API_KEY", value)
    monkeypatch.setenv("GOOGLE_API_KEY", "nao-vale")  # não pode ser usada no lugar
    monkeypatch.setattr(genai, "Client", boom)
    gen = adapter_for(env.prices, None, env.clock)
    token = reserve_for(env.conn, env.prices, gen, env.scene, env.run_id)
    req = SceneRequest(env.scene, env.run_id, token.attempt_no, env.out_dir / "a.mp4")
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        gen.generate(req, token)


# --- contrato ----------------------------------------------------------------------------------


def contract_client(clip: Path) -> FakeOmniClient:
    """Upload PROCESSING→ACTIVE e arquivo gerado PROCESSING→ACTIVE: exercita os dois polls."""
    client = FakeOmniClient(clip)
    client.input_states = ["PROCESSING", "ACTIVE"]
    client.output_states = ["PROCESSING", "ACTIVE"]
    return client


# upload (1) + get da entrada (1) + create (1) + get da saída (2) + download (1)
HAPPY_CALLS = 6


def run_contract(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    out_dir: Path,
    fake_clock: Any,
    clip: Path,
    max_client_calls: int,
) -> None:
    assert_generator_contract(
        lambda client: adapter_for(prices, client, fake_clock),
        conn=conn,
        prices=prices,
        scene=scene,
        good_client=contract_client(clip),
        out_dir=out_dir,
        max_client_calls=max_client_calls,
    )


def test_contract_passes_with_the_exact_client_call_budget(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    out_dir: Path,
    fake_clock: Any,
    clip5: Path,
) -> None:
    run_contract(conn, prices, scene, out_dir, fake_clock, clip5, HAPPY_CALLS)


def test_contract_call_budget_is_exact_not_loose(
    conn: sqlite3.Connection,
    prices: PriceTable,
    scene: Scene,
    out_dir: Path,
    fake_clock: Any,
    clip5: Path,
) -> None:
    # Uma chamada a menos no limite faz o contrato acusar: o adaptador faz exatamente HAPPY_CALLS.
    with pytest.raises(AssertionError, match="chamadas"):
        run_contract(conn, prices, scene, out_dir, fake_clock, clip5, HAPPY_CALLS - 1)


# --- caminho feliz -----------------------------------------------------------------------------


def test_happy_path_sequence_and_result(env: Env) -> None:
    result, token = env.run()
    assert env.client.events == ["upload", "create", "get_out", "download"]
    assert result.clip_path == env.out_dir / "s01.mp4"
    assert result.clip_path.is_file()
    assert list(env.out_dir.iterdir()) == [result.clip_path]  # sem .part esquecido
    assert result.provider_job_id == JOB_ID
    assert env.job_id_in_ledger(token) == JOB_ID
    assert result.unit_kind == "video_seconds"
    assert result.units == pytest.approx(5.0, abs=0.1)  # segundos medidos no arquivo baixado
    assert result.duration_s == pytest.approx(result.units, abs=0.01)
    assert (result.width, result.height) == (640, 360)
    assert result.fps == pytest.approx(30.0)
    assert result.cost_is_estimate is True  # o provedor devolve tokens, não segundos faturados
    assert result.meta["resolution_requested"] == "1080p"
    assert result.meta["duration_requested_s"] == "5"
    assert result.meta["interaction_id"] == JOB_ID
    assert result.meta["output_tokens"] == "1234"
    assert result.meta["output_file"] == OUT_NAME


def test_request_carries_first_frame_prompt_duration_and_1080p(env: Env) -> None:
    env.run()
    (upload,) = env.client.upload_kwargs
    assert upload["file"] == str(env.scene.photo)
    assert upload["config"].mime_type == "image/jpeg"
    assert env.scene.photo.name not in upload["config"].display_name  # nada do nome do imóvel vai ao provedor

    (kwargs,) = env.client.create_kwargs
    assert kwargs["model"] == "gemini-omni-1.1-flash"
    image, text = kwargs["input"]
    assert image == {"type": "image", "uri": IN_URI, "mime_type": "image/jpeg"}
    assert text["type"] == "text"
    assert text["text"].startswith("<FIRST_FRAME> ")
    assert env.scene.prompt in text["text"]
    assert "no scene cuts" in text["text"]
    assert kwargs["response_format"] == {
        "type": "video",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "duration": "5s",
        "delivery": "uri",
    }
    assert (kwargs["background"], kwargs["store"], kwargs["stream"]) == (False, False, False)
    assert 0 < kwargs["timeout"] <= 900


def test_negative_prompt_goes_into_the_text_because_the_api_has_no_such_field(env: Env, scene: Scene) -> None:
    with_negative = Scene(
        index=scene.index,
        photo=scene.photo,
        room=scene.room,
        camera=scene.camera,
        duration_s=scene.duration_s,
        prompt=scene.prompt,
        negative_prompt="pessoas, texto",
    )
    env.run(scene=with_negative)
    text = env.client.create_kwargs[0]["input"][1]["text"]
    assert "pessoas, texto" in text


@pytest.mark.parametrize(("target", "expected"), [(3.0, "3s"), (4.6, "5s"), (10.0, "10s")])
def test_requested_duration_is_an_integer_string_in_3_to_10(
    env: Env, make_clip: Callable[[int], Path], scene: Scene, target: float, expected: str
) -> None:
    env.client.clip = make_clip(int(expected[:-1]))
    result, _ = env.run(scene=with_duration(scene, target))
    assert env.client.create_kwargs[0]["response_format"]["duration"] == expected
    assert result.units == pytest.approx(float(expected[:-1]), abs=0.1)


def test_units_never_drop_below_the_requested_seconds_and_keep_the_measured_ones_in_meta(
    env: Env, make_clip: Callable[[int], Path]
) -> None:
    env.client.clip = make_clip(4)  # pediu 5 s, o provedor entregou 4 s: o faturamento por segundo é incerto
    result, token = env.run()
    assert result.units == 5.0  # max(medido, pedido)
    assert float(result.meta["measured_s"]) == pytest.approx(4.0, abs=0.1)
    assert result.duration_s == pytest.approx(4.0, abs=0.1)  # a duração do clipe continua a medida
    assert "over_requested" not in result.meta
    assert result.meta["duration_requested_s"] == "5"
    assert env.job_id_in_ledger(token) == JOB_ID


def test_units_follow_the_measured_seconds_when_longer_and_flag_a_big_excess(
    env: Env, make_clip: Callable[[int], Path]
) -> None:
    env.client.clip = make_clip(7)  # pediu 5 s, veio 7 s (> pedido + 1 s)
    result, _ = env.run()
    assert result.units == pytest.approx(7.0, abs=0.1)
    assert float(result.meta["measured_s"]) == pytest.approx(7.0, abs=0.1)
    assert result.meta["over_requested"] == "true"


def test_a_one_second_excess_is_not_flagged(env: Env, make_clip: Callable[[int], Path]) -> None:
    env.client.clip = make_clip(6)  # exatamente pedido + 1 s
    result, _ = env.run()
    assert result.units == pytest.approx(6.0, abs=0.1)
    assert "over_requested" not in result.meta


def test_input_and_output_files_are_polled_until_active_with_backoff(env: Env) -> None:
    env.client.input_states = ["PROCESSING", "PROCESSING", "ACTIVE"]
    env.client.output_states = ["PROCESSING", "ACTIVE"]
    env.run()
    assert env.client.events == [
        "upload", "get_in", "get_in", "create", "get_out", "get_out", "download",
    ]  # fmt: skip
    assert all(0 < s <= 10 for s in env.sleeps)  # intervalo limitado
    assert env.sleeps[:2] == sorted(env.sleeps[:2]) and env.sleeps[0] < env.sleeps[1]  # cresce entre os polls
    assert len(env.sleeps) == 3  # 2 esperas pelo upload + 1 pelo arquivo gerado


def test_output_file_name_is_taken_from_the_uri(env: Env) -> None:
    env.client.create_response = interaction(uri="https://generativelanguage.googleapis.com/v1beta/files/zz9")
    env.run()
    assert env.client.events.count("get_out") == 1
    assert env.client.download_kwargs[0]["file"].endswith("files/zz9")


def test_job_accepted_is_recorded_before_the_download(env: Env, conn: sqlite3.Connection) -> None:
    seen: list[str | None] = []

    def snapshot() -> None:
        (attempt,) = repo.list_attempts(conn)
        seen.append(attempt.provider_job_id)

    env.client.on_download = snapshot
    env.run()
    assert seen == [JOB_ID]


def test_download_goes_to_a_part_file_and_out_dir_is_created(env: Env, tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b"
    env.out_dir = deep
    result, _ = env.run()
    assert result.clip_path.parent == deep
    destination = Path(env.client.download_kwargs[0]["destination"])
    assert destination != result.clip_path and destination.parent == deep


def test_missing_interaction_id_falls_back_to_the_output_file_name(env: Env) -> None:
    env.client.create_response = interaction(id="")
    result, token = env.run()
    assert result.provider_job_id == OUT_NAME
    assert env.job_id_in_ledger(token) == OUT_NAME


# --- falhas ANTES do aceite (nada faturado, pode repetir) ---------------------------------------


def assert_pre_accept(exc: ProviderError, token: SpendToken, env: Env) -> None:
    assert (exc.transient, exc.billed_units, exc.provider_job_id) == (True, 0.0, None)
    assert env.job_id_in_ledger(token) is None


def test_upload_failure_is_transient_unbilled_and_never_reaches_create(env: Env) -> None:
    env.client.upload_errors = [ConnectionResetError("rede")] * 3
    exc, token = env.fail()
    assert_pre_accept(exc, token, env)
    assert env.client.events == ["upload"] * 3  # retry com backoff, só do upload
    assert len(env.sleeps) == 2 and all(s >= 1.0 for s in env.sleeps)  # backoff do retry_before_accept
    assert "create" not in env.client.events


def test_upload_recovers_after_a_transient_failure(env: Env) -> None:
    env.client.upload_errors = [api_error(503, "UNAVAILABLE"), None]
    result, token = env.run()
    assert env.client.events == ["upload", "upload", "create", "get_out", "download"]
    assert env.job_id_in_ledger(token) == result.provider_job_id == JOB_ID


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_upload_rejected_by_the_provider_is_not_retried(env: Env, code: int) -> None:
    env.client.upload_errors = [api_error(code)] * 3
    exc, token = env.fail()
    assert_pre_accept(exc, token, env)
    assert env.client.events == ["upload"]
    assert env.sleeps == []


def test_input_file_in_failed_state_is_a_pre_accept_failure(env: Env) -> None:
    env.client.input_states = ["PROCESSING", "FAILED"]
    exc, token = env.fail()
    assert_pre_accept(exc, token, env)
    assert env.client.events == ["upload", "get_in"]


def test_input_file_that_never_gets_active_hits_the_deadline_before_any_submission(env: Env) -> None:
    env.client.input_states = ["PROCESSING"]
    exc, token = env.fail(deadline_s=30.0)
    assert_pre_accept(exc, token, env)
    assert "create" not in env.client.events
    assert env.clock() <= 30.0 + 1e-9  # a espera respeita o prazo
    assert "prazo" in str(exc).lower()


def test_polling_survives_a_blip_in_files_get_for_the_input(env: Env) -> None:
    # um erro isolado de rede no get não derruba o upload já feito
    env.client.input_states = ["PROCESSING", "ACTIVE"]
    original = env.client.files.get
    calls = {"n": 0}

    def flaky(*, name: str, config: Any = None) -> genai_types.File:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("blip")
        return original(name=name, config=config)

    env.client.files.get = flaky  # type: ignore[method-assign]
    result, _ = env.run()
    assert env.client.events.count("upload") == 1
    assert result.provider_job_id == JOB_ID


# (os erros do ``interactions.create`` com o SDK real estão na seção "SDK real", mais abaixo)


def test_connection_refused_on_create_is_pre_accept(env: Env) -> None:
    env.client.create_errors = [ConnectionRefusedError("recusada")] * 3
    exc, token = env.fail()
    assert_pre_accept(exc, token, env)
    assert env.client.events.count("create") == 3


def test_no_submission_when_the_deadline_is_already_spent(env: Env) -> None:
    env.client.input_states = ["PROCESSING", "ACTIVE"]
    exc, token = env.fail(deadline_s=1.0)  # a 1ª espera (2 s) estoura o prazo
    assert_pre_accept(exc, token, env)
    assert "create" not in env.client.events


# --- falhas DEPOIS do aceite (faturado de forma conservadora, nunca reenviar) -------------------------


def assert_post_accept(exc: ProviderError, token: SpendToken, env: Env, job_id: str | None = JOB_ID) -> None:
    assert exc.transient is False
    assert exc.billed_units == 5.0  # segundos pedidos: o conservador
    assert exc.provider_job_id == job_id
    assert env.job_id_in_ledger(token) == job_id
    assert env.client.events.count("create") == 1, "o pedido pago nunca é reenviado"


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("sem resposta"),
        httpx.ReadTimeout("sem resposta"),
        ConnectionResetError("reset no meio"),
        RuntimeError("inesperado"),
    ],
    ids=lambda e: f"{type(e).__name__}",
)
def test_ambiguous_create_failure_is_billed_conservatively_without_resending(
    env: Env, error: Exception
) -> None:
    env.client.create_errors = [error, None]  # o 2º create (bem-sucedido) NÃO pode acontecer
    exc, token = env.fail()
    assert_post_accept(exc, token, env, job_id=None)
    assert exc.__cause__ is error
    assert "get_out" not in env.client.events and "download" not in env.client.events


@pytest.mark.parametrize(
    "status", ["failed", "cancelled", "incomplete", "budget_exceeded", "in_progress", "queued", "requires_action", "requires_continuation"]
)
def test_interaction_not_completed_is_post_accept_failure_with_the_job_id(env: Env, status: str) -> None:
    env.client.create_response = interaction(status=status)
    exc, token = env.fail()
    assert_post_accept(exc, token, env)
    assert status in str(exc)
    assert "download" not in env.client.events


def test_completed_without_video_is_post_accept_failure(env: Env) -> None:
    env.client.create_response = interaction(uri=None)
    exc, token = env.fail()
    assert_post_accept(exc, token, env)


def test_no_interaction_id_and_no_video_leaves_no_job_id_but_still_bills(env: Env) -> None:
    env.client.create_response = interaction(id=None, uri=None)
    exc, token = env.fail()
    assert_post_accept(exc, token, env, job_id=None)


def test_output_file_in_failed_state_is_post_accept(env: Env) -> None:
    env.client.output_states = ["PROCESSING", "FAILED"]
    exc, token = env.fail()
    assert_post_accept(exc, token, env)
    assert "download" not in env.client.events


def test_output_file_that_never_gets_active_hits_the_deadline_after_accept(env: Env) -> None:
    env.client.output_states = ["PROCESSING"]
    exc, token = env.fail(deadline_s=60.0)
    assert_post_accept(exc, token, env)
    assert env.clock() <= 60.0 + 1e-9


def test_output_polling_gives_up_after_repeated_get_errors(env: Env) -> None:
    env.client.get_out_errors = [ConnectionResetError("a")] * 10
    exc, token = env.fail()
    assert_post_accept(exc, token, env)
    assert env.client.events.count("get_out") <= 4  # tolerância limitada, não laço infinito


def test_download_is_retried_with_backoff_without_resubmitting(env: Env) -> None:
    env.client.download_errors = [ConnectionResetError("x"), api_error(503), None]
    result, token = env.run()
    assert env.client.events.count("download") == 3
    assert env.client.events.count("create") == 1
    assert len(env.sleeps) == 2 and env.sleeps[0] < env.sleeps[1]
    assert env.job_id_in_ledger(token) == result.provider_job_id == JOB_ID


def test_download_failing_forever_is_post_accept_with_the_job_id(env: Env) -> None:
    env.client.download_errors = [ConnectionResetError("x")] * 10
    exc, token = env.fail()
    assert_post_accept(exc, token, env)
    assert env.client.events.count("download") == 3
    assert list(env.out_dir.glob("*")) == []  # nem o .part fica


def test_download_that_is_not_a_video_is_post_accept_and_leaves_nothing(env: Env) -> None:
    env.client.download_bytes = b"isto nao e um mp4"
    exc, token = env.fail()
    assert_post_accept(exc, token, env)
    assert list(env.out_dir.glob("*")) == []


def test_download_that_writes_nothing_is_post_accept(env: Env) -> None:
    env.client.download_bytes = b""
    exc, token = env.fail()
    assert_post_accept(exc, token, env)


def test_post_accept_error_is_settleable_with_billed_units_and_job_id(env: Env) -> None:
    env.client.create_response = interaction(status="failed")
    exc, token = env.fail()
    budget.settle_error(
        env.conn,
        token,
        billed_units=exc.billed_units,
        error=str(exc),
        wall_time_s=0.0,
        provider_job_id=exc.provider_job_id,
    )
    (attempt,) = repo.list_attempts(env.conn)
    assert attempt.status == "error"
    assert attempt.provider_job_id == JOB_ID
    assert attempt.cost_brl == pytest.approx(2.5)  # 5 s x US$ 0,10 x 5,00 (preços de teste)


def test_pre_accept_error_is_settleable_without_a_job_id(env: Env) -> None:
    env.client.upload_errors = [api_error(403)]
    exc, token = env.fail()
    budget.settle_error(
        env.conn,
        token,
        billed_units=exc.billed_units,
        error=str(exc),
        wall_time_s=0.0,
        provider_job_id=exc.provider_job_id,
    )
    (attempt,) = repo.list_attempts(env.conn)
    assert attempt.status == "error"
    assert attempt.provider_job_id is None
    # T1: o erro custa max(faturado, reserva); o ledger nunca registra menos que a reserva.
    assert attempt.cost_brl == pytest.approx(attempt.reserved_brl)


# --- entradas inválidas depois do consume: nenhuma chamada ao provedor ---------------------------


def test_missing_photo_is_an_input_error_without_any_client_call(env: Env, scene: Scene) -> None:
    scene.photo.unlink()
    gen = env.gen()
    token = reserve_for(env.conn, env.prices, gen, scene, env.run_id)
    req = SceneRequest(scene, env.run_id, token.attempt_no, env.out_dir / "a.mp4")
    with pytest.raises(InputError):
        gen.generate(req, token)
    assert env.client.events == []


def test_generate_revalidates_the_duration_without_any_client_call(env: Env, scene: Scene) -> None:
    gen = env.gen()
    token = reserve_for(env.conn, env.prices, gen, scene, env.run_id)
    bad = with_duration(scene, 12.0)
    req = SceneRequest(bad, env.run_id, token.attempt_no, env.out_dir / "a.mp4")
    with pytest.raises(InputError):
        gen.generate(req, token)
    assert env.client.events == []


def test_error_messages_never_leak_an_api_key(env: Env) -> None:
    secret = "AIza" + "x" * 35
    env.client.upload_errors = [RuntimeError(f"falhou com key={secret} no URL")] * 3
    exc, _ = env.fail()
    assert secret not in str(exc)
    assert "[REDACTED_KEY]" in str(exc)


# --- SDK real, rede substituída por um transporte local ------------------------------------------


class SdkServer:
    """Servidor fictício (``httpx.MockTransport``) de Files + Interactions para o ``genai.Client`` REAL.

    ``create_script`` define a resposta de cada POST em ``/v1beta/interactions`` (status HTTP, ``"connect"``
    ou ``"read"``); esgotado, responde sucesso. ``posts`` conta os POSTs que chegaram ao "provedor". As
    respostas de sucesso são escritas à mão a partir da documentação: o que o servidor real faz é HIPÓTESE.
    """

    def __init__(self, clip: Path) -> None:
        self.clip_bytes = clip.read_bytes()
        self.create_script: list[int | str] = []
        self.upload_script: list[int] = []
        self.posts = 0
        self.upload_starts = 0
        self.bodies: list[bytes] = []

    def client(self, *, options: genai_types.HttpOptions | None = None) -> genai.Client:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        opts = make_http_options(900.0, httpx_client=http) if options is None else options
        return genai.Client(vertexai=False, api_key="chave-ficticia-de-teste", http_options=opts)

    @staticmethod
    def _error(status: int) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": status, "message": "falha simulada", "status": "ERR"}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/upload/v1beta/files" in url and request.headers.get("x-goog-upload-command") == "start":
            self.upload_starts += 1
            if self.upload_script and (status := self.upload_script.pop(0)) != 200:
                return self._error(status)
            return httpx.Response(
                200,
                headers={
                    "x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/v1beta/files/up1?upload_id=u",
                    "x-goog-upload-status": "active",
                },
            )
        if "upload_id=" in url:
            return httpx.Response(
                200,
                headers={"x-goog-upload-status": "final"},
                json={"file": {"name": IN_NAME, "uri": IN_URI, "mimeType": "image/jpeg", "state": "PROCESSING"}},
            )
        if request.method == "GET" and url.endswith("files/in1"):
            return httpx.Response(200, json={"name": IN_NAME, "uri": IN_URI, "state": "ACTIVE"})
        if request.method == "POST" and url.endswith("/v1beta/interactions"):
            self.posts += 1
            self.bodies.append(request.content)
            step = self.create_script.pop(0) if self.create_script else 200
            if step == "connect":
                raise httpx.ConnectError("sem rota", request=request)
            if step == "connect_timeout":
                raise httpx.ConnectTimeout("sem rota (timeout)", request=request)
            if step == "read":
                raise httpx.ReadTimeout("lento demais", request=request)
            assert isinstance(step, int)
            if step != 200:
                return self._error(step)
            return httpx.Response(
                200,
                json={
                    "id": JOB_ID,
                    "status": "completed",
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "video", "uri": OUT_URI, "mime_type": "video/mp4"}],
                        }
                    ],
                },
            )
        if request.method == "GET" and url.endswith("files/zz9"):
            return httpx.Response(200, json={"name": OUT_NAME, "state": "ACTIVE"})
        if ":download" in url:
            return httpx.Response(200, content=self.clip_bytes)
        return self._error(404)


@pytest.fixture
def server(clip5: Path, monkeypatch: pytest.MonkeyPatch) -> SdkServer:
    # Rede de segurança: se o SDK tentar dormir entre reenvios, o teste não espera de verdade.
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    return SdkServer(clip5)


def test_http_options_disable_the_sdk_retries_of_the_paid_post() -> None:
    options = make_http_options(120.0)
    assert options.timeout == 120_000
    retry = options.retry_options
    assert retry is not None
    # attempts=0 ou 1 sozinhos ainda dão 2 POSTs; a lista [599] fecha as brechas (medido com o client real)
    assert (retry.attempts, retry.http_status_codes) == (1, [599])


CREATE_KWARGS: dict[str, Any] = {
    "model": "gemini-omni-1.1-flash",
    "input": "x",
    "store": False,
    "background": False,
    "stream": False,
    "automatic_continuation": False,
    "timeout": 5,
}


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 500, 502, 503, 504, "connect", "connect_timeout", "read"])
def test_real_sdk_sends_exactly_one_post_per_create_with_our_http_options(
    server: SdkServer, status: int | str
) -> None:
    """O SDK reenviava o POST sozinho (4 POSTs em 408/429/5xx). Com ``make_http_options`` é 1, medido aqui."""
    server.create_script = [status] * 5
    with pytest.raises(Exception):  # noqa: B017 - a classe exata é conferida nos testes de classificação
        server.client().interactions.create(**CREATE_KWARGS)
    assert server.posts == 1


def test_sdk_default_options_would_resend_the_paid_post(server: SdkServer) -> None:
    """Guarda do defeito: com as opções padrão do SDK instalado, um 408/429/5xx vira VÁRIOS POSTs."""
    server.create_script = [504] * 10
    http = httpx.Client(transport=httpx.MockTransport(server.handler))
    default = server.client(options=genai_types.HttpOptions(httpx_client=http))
    with pytest.raises(Exception):  # noqa: B017
        default.interactions.create(**CREATE_KWARGS)
    assert server.posts > 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_real_sdk_create_rejected_with_4xx_is_pre_accept_and_not_retried(
    env: Env, server: SdkServer, status: int
) -> None:
    server.create_script = [status] * 3
    exc, token = env.fail(client=server.client())
    assert (exc.transient, exc.billed_units, exc.provider_job_id) == (True, 0.0, None)
    assert env.job_id_in_ledger(token) is None
    assert server.posts == 1
    assert env.sleeps == [2.0]  # só a espera do arquivo de entrada (PROCESSING -> ACTIVE); nenhum backoff


@pytest.mark.parametrize("status", [429, 503])
def test_real_sdk_create_429_503_is_retried_by_us_then_succeeds(env: Env, server: SdkServer, status: int) -> None:
    server.create_script = [status, 200]
    result, token = env.run(client=server.client())
    assert server.posts == 2
    assert server.upload_starts == 1  # a foto não é reenviada
    assert result.provider_job_id == JOB_ID == env.job_id_in_ledger(token)
    assert len(env.sleeps) == 2  # espera do arquivo de entrada + 1 backoff do nosso retry


@pytest.mark.parametrize("status", [429, 503, "connect", "connect_timeout"])
def test_real_sdk_create_refused_forever_is_transient_unbilled_after_bounded_retries(
    env: Env, server: SdkServer, status: int | str
) -> None:
    server.create_script = [status] * 10
    exc, token = env.fail(client=server.client())
    assert (exc.transient, exc.billed_units, exc.provider_job_id) == (True, 0.0, None)
    assert env.job_id_in_ledger(token) is None
    assert server.posts == 3  # o limite de retry_before_accept; o SDK contribui com 1 por tentativa


@pytest.mark.parametrize("status", [408, 500, 502, 504, "read"])
def test_real_sdk_ambiguous_create_failure_is_billed_and_posted_exactly_once(
    env: Env, server: SdkServer, status: int | str
) -> None:
    server.create_script = [status, 200]  # o 2º (sucesso) NÃO pode ser enviado
    exc, token = env.fail(client=server.client())
    assert (exc.transient, exc.billed_units, exc.provider_job_id) == (False, 5.0, None)
    assert env.job_id_in_ledger(token) is None
    assert server.posts == 1
    assert env.sleeps == [2.0]  # só a espera do arquivo de entrada (PROCESSING -> ACTIVE); nenhum backoff


def test_real_sdk_upload_rejected_with_403_is_pre_accept_and_not_retried(env: Env, server: SdkServer) -> None:
    server.upload_script = [403] * 3
    exc, _ = env.fail(client=server.client())
    assert (exc.transient, exc.billed_units, exc.provider_job_id) == (True, 0.0, None)
    assert server.upload_starts == 1 and server.posts == 0


def test_real_sdk_upload_503_is_retried_and_never_reaches_create_until_it_works(env: Env, server: SdkServer) -> None:
    server.upload_script = [503, 200]
    result, _ = env.run(client=server.client())
    assert server.upload_starts == 2 and server.posts == 1
    assert result.provider_job_id == JOB_ID


def test_real_sdk_adapter_built_client_posts_once_per_attempt(
    env: Env, server: SdkServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pelo caminho preguiçoso do adaptador: as opções que ELE monta (não as do teste) valem 1 POST por tentativa."""
    original = genai.Client

    def factory(**kwargs: Any) -> genai.Client:
        http = httpx.Client(transport=httpx.MockTransport(server.handler))
        options = kwargs["http_options"].model_copy(update={"httpx_client": http})
        return original(**{**kwargs, "http_options": options})

    monkeypatch.setenv("GEMINI_API_KEY", "chave-ficticia-de-teste")
    monkeypatch.setattr(genai, "Client", factory)
    server.create_script = [504, 200]
    gen = env.gen(client=None)
    token = reserve_for(env.conn, env.prices, gen, env.scene, env.run_id)
    req = SceneRequest(env.scene, env.run_id, token.attempt_no, env.out_dir / "s01.mp4")
    with pytest.raises(ProviderError):
        gen.generate(req, token)
    assert server.posts == 1


def test_requires_continuation_is_a_post_accept_failure_and_is_never_continued(env: Env) -> None:
    env.client.create_response = interaction(status="requires_continuation")
    exc, token = env.fail()
    assert_post_accept(exc, token, env)
    assert "requires_continuation" in str(exc)
    assert env.client.events.count("create") == 1  # nenhuma 2ª interação paga
    assert env.client.create_kwargs[0]["automatic_continuation"] is False


def test_real_sdk_accepts_the_calls_and_serializes_the_request_offline(env: Env, server: SdkServer) -> None:
    """Prova de nomes e formatos contra o SDK instalado, sem rede: ``httpx.MockTransport`` no lugar do HTTP.

    Confirma que ``files.upload/get/download`` e ``interactions.create`` existem com estes parâmetros e que o
    corpo enviado ao provedor tem first-frame, prompt, duração, 1080p e entrega por URI.
    """
    result, _ = env.run(client=server.client())

    assert result.provider_job_id == JOB_ID
    assert result.units == pytest.approx(5.0, abs=0.1)
    assert server.posts == 1
    body = json.loads(server.bodies[0])
    assert body["model"] == "gemini-omni-1.1-flash"
    assert body["response_format"] == {
        "type": "video",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "duration": "5s",
        "delivery": "uri",
    }
    assert (body["background"], body["store"], body["stream"]) == (False, False, False)
    assert "automatic_continuation" not in body  # opção do SDK, não do corpo
    parts = body["input"][0]["content"]
    assert parts[0] == {"type": "image", "uri": IN_URI, "mime_type": "image/jpeg"}
    assert parts[1]["type"] == "text" and parts[1]["text"].startswith("<FIRST_FRAME> ")
