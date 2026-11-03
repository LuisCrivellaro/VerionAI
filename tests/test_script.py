"""Testes de ``verion.script.load_script`` (spec 3.5, T3). Sem rede; fotos são arquivos fictícios."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from verion.errors import InputError, ScriptError
from verion.providers.base import Scene
from verion.script import Script, load_script

REPO_ROOT = Path(__file__).resolve().parents[1]

_CAMERAS = ("push-in", "pull-out", "pan-left", "pan-right", "static")


def _scene_literals(index: int) -> dict[str, str | None]:
    """Campos de uma cena válida como LITERAIS TOML (um valor ``None`` remove a chave)."""
    return {
        "index": str(index),
        "photo": f'"fotos/{index:02d}.jpg"',
        "room": f'"comodo {index}"',
        "camera": f'"{_CAMERAS[index % len(_CAMERAS)]}"',
        "duration_s": "5.0",
        "prompt": '"Movimento de câmera lento. Não adicionar objetos."',
    }


def _render_scene(fields: dict[str, str | None]) -> str:
    lines = [f"{key} = {value}" for key, value in fields.items() if value is not None]
    return "[[scenes]]\n" + "\n".join(lines) + "\n"


def write_script(
    tmp_path: Path,
    *,
    property_id: str | None = '"casa-teste"',
    count: int = 12,
    overrides: dict[int, dict[str, str | None]] | None = None,
    make_photos: bool = True,
    extra_top: str = "",
    name: str = "roteiro.toml",
) -> Path:
    """Escreve ``tmp_path/name`` com ``count`` cenas e cria as fotos fictícias em ``tmp_path/fotos``."""
    overrides = overrides or {}
    parts = [] if property_id is None else [f"property_id = {property_id}\n"]
    parts.append(extra_top)
    for index in range(1, count + 1):
        fields = _scene_literals(index)
        fields.update(overrides.get(index, {}))
        parts.append("\n" + _render_scene(fields))
        if make_photos:
            photo = tmp_path / "fotos" / f"{index:02d}.jpg"
            photo.parent.mkdir(exist_ok=True)
            photo.write_bytes(b"foto-ficticia")
    path = tmp_path / name
    path.write_text("".join(parts), encoding="utf-8")
    return path


# --- caminho feliz --------------------------------------------------------------------------


def test_loads_twelve_scenes(tmp_path: Path) -> None:
    path = write_script(tmp_path)
    script = load_script(path)

    assert isinstance(script, Script)
    assert script.property_id == "casa-teste"
    assert script.path == path
    assert script.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(script.scenes) == 12
    assert all(isinstance(s, Scene) for s in script.scenes)
    assert [s.index for s in script.scenes] == list(range(1, 13))

    first = script.scenes[0]
    assert first.photo == (tmp_path / "fotos" / "01.jpg").resolve()
    assert first.photo.is_file()
    assert first.room == "comodo 1"
    assert first.camera == "pull-out"
    assert first.duration_s == 5.0
    assert first.prompt.startswith("Movimento de câmera")
    assert first.negative_prompt == ""


def test_scenes_are_sorted_by_index_regardless_of_file_order(tmp_path: Path) -> None:
    path = write_script(tmp_path)
    text = path.read_text(encoding="utf-8")
    head, *blocks = text.split("[[scenes]]")
    path.write_text(head + "".join("[[scenes]]" + b for b in reversed(blocks)), encoding="utf-8")

    script = load_script(path)

    assert [s.index for s in script.scenes] == list(range(1, 13))


def test_negative_prompt_and_integer_duration_are_accepted(tmp_path: Path) -> None:
    path = write_script(
        tmp_path, overrides={1: {"negative_prompt": '"pessoas, móveis novos"', "duration_s": "7"}}
    )

    scene = load_script(path).scenes[0]

    assert scene.negative_prompt == "pessoas, móveis novos"
    assert scene.duration_s == 7.0 and isinstance(scene.duration_s, float)


def test_photo_is_relative_to_the_script_directory_not_to_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_script(tmp_path)
    elsewhere = tmp_path / "outro"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    script = load_script(path)

    assert script.scenes[0].photo == (tmp_path / "fotos" / "01.jpg").resolve()


def test_absolute_photo_path_is_accepted(tmp_path: Path) -> None:
    other = tmp_path / "abs" / "sala.jpg"
    other.parent.mkdir()
    other.write_bytes(b"x")
    path = write_script(tmp_path, overrides={4: {"photo": f'"{other}"'}})

    assert load_script(path).scenes[3].photo == other.resolve()


@pytest.mark.parametrize("duration", ["3.0", "10.0", "3", "10"])
def test_duration_boundaries_are_inclusive(tmp_path: Path, duration: str) -> None:
    path = write_script(tmp_path, overrides={1: {"duration_s": duration}})

    assert load_script(path).scenes[0].duration_s == float(duration)


@pytest.mark.parametrize("property_id", ["a", "0", "a" * 32, "casa-01", "---"])
def test_property_id_boundaries_are_accepted(tmp_path: Path, property_id: str) -> None:
    path = write_script(tmp_path, property_id=f'"{property_id}"')

    assert load_script(path).property_id == property_id


def test_every_camera_is_accepted(tmp_path: Path) -> None:
    overrides = {i + 1: {"camera": f'"{cam}"'} for i, cam in enumerate(_CAMERAS)}
    path = write_script(tmp_path, overrides=overrides)

    assert [s.camera for s in load_script(path).scenes[:5]] == list(_CAMERAS)


# --- ScriptError: cada caso -----------------------------------------------------------------


def test_script_error_is_an_input_error_with_exit_code_2() -> None:
    assert issubclass(ScriptError, InputError)
    assert ScriptError.exit_code == 2


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ScriptError, match="roteiro"):
        load_script(tmp_path / "nao-existe.toml")


def test_directory_instead_of_file(tmp_path: Path) -> None:
    with pytest.raises(ScriptError):
        load_script(tmp_path)


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "r.toml"
    path.write_text("property_id = \n[[scenes", encoding="utf-8")

    with pytest.raises(ScriptError, match="TOML"):
        load_script(path)


def test_file_that_is_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "r.toml"
    path.write_bytes(b'property_id = "\xff\xfe"\n')

    with pytest.raises(ScriptError):
        load_script(path)


@pytest.mark.parametrize("count", [0, 1, 11, 13, 24])
def test_scene_count_must_be_exactly_twelve(tmp_path: Path, count: int) -> None:
    path = write_script(tmp_path, count=count)

    with pytest.raises(ScriptError, match="12"):
        load_script(path)


def test_scenes_must_be_an_array_of_tables(tmp_path: Path) -> None:
    path = tmp_path / "r.toml"
    path.write_text('property_id = "casa"\nscenes = 12\n', encoding="utf-8")

    with pytest.raises(ScriptError, match="scenes"):
        load_script(path)


def test_scene_entry_must_be_a_table(tmp_path: Path) -> None:
    path = tmp_path / "r.toml"
    path.write_text('property_id = "casa"\nscenes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]\n', encoding="utf-8")

    with pytest.raises(ScriptError, match="tabela"):
        load_script(path)


def test_repeated_index(tmp_path: Path) -> None:
    path = write_script(tmp_path, overrides={12: {"index": "11"}})

    with pytest.raises(ScriptError, match="repetido"):
        load_script(path)


@pytest.mark.parametrize("index", ["0", "13", "-1", "100"])
def test_index_out_of_range(tmp_path: Path, index: str) -> None:
    path = write_script(tmp_path, overrides={5: {"index": index}})

    with pytest.raises(ScriptError, match="índice"):
        load_script(path)


@pytest.mark.parametrize("index", ['"5"', "5.0", "true"])
def test_index_must_be_an_integer(tmp_path: Path, index: str) -> None:
    path = write_script(tmp_path, overrides={5: {"index": index}})

    with pytest.raises(ScriptError, match="índice"):
        load_script(path)


def test_missing_photo_file(tmp_path: Path) -> None:
    path = write_script(tmp_path)
    (tmp_path / "fotos" / "07.jpg").unlink()

    with pytest.raises(ScriptError, match="foto"):
        load_script(path)


def test_photo_that_is_a_directory(tmp_path: Path) -> None:
    path = write_script(tmp_path, overrides={2: {"photo": '"fotos"'}})

    with pytest.raises(ScriptError, match="foto"):
        load_script(path)


@pytest.mark.parametrize("photo", ['""', "3", '"  "'])
def test_photo_must_be_a_non_empty_string(tmp_path: Path, photo: str) -> None:
    path = write_script(tmp_path, overrides={2: {"photo": photo}})

    with pytest.raises(ScriptError, match="foto"):
        load_script(path)


@pytest.mark.parametrize("camera", ['"zoom-in"', '"PUSH-IN"', '""', "1"])
def test_invalid_camera(tmp_path: Path, camera: str) -> None:
    path = write_script(tmp_path, overrides={3: {"camera": camera}})

    with pytest.raises(ScriptError, match="camera"):
        load_script(path)


@pytest.mark.parametrize(
    "duration", ["2.99", "0", "-5.0", "10.01", "11", "nan", "inf", "-inf", '"5"', "true"]
)
def test_duration_out_of_range_or_invalid(tmp_path: Path, duration: str) -> None:
    path = write_script(tmp_path, overrides={6: {"duration_s": duration}})

    with pytest.raises(ScriptError, match="duration_s"):
        load_script(path)


@pytest.mark.parametrize(
    "property_id",
    ['"Casa"', '""', f'"{"a" * 33}"', '"casa_1"', '"casa 1"', '"casa\\n"', '"café"', '"../x"', "12"],
)
def test_invalid_property_id(tmp_path: Path, property_id: str) -> None:
    path = write_script(tmp_path, property_id=property_id)

    with pytest.raises(ScriptError, match="property_id"):
        load_script(path)


def test_missing_property_id(tmp_path: Path) -> None:
    path = write_script(tmp_path, property_id=None)

    with pytest.raises(ScriptError, match="property_id"):
        load_script(path)


@pytest.mark.parametrize("field", ["index", "photo", "room", "camera", "duration_s", "prompt"])
def test_missing_required_scene_field(tmp_path: Path, field: str) -> None:
    path = write_script(tmp_path, overrides={9: {field: None}})

    with pytest.raises(ScriptError, match=field):
        load_script(path)


@pytest.mark.parametrize("field", ["room", "prompt"])
@pytest.mark.parametrize("value", ['""', '"   "', "5"])
def test_room_and_prompt_must_be_non_blank_text(tmp_path: Path, field: str, value: str) -> None:
    path = write_script(tmp_path, overrides={9: {field: value}})

    with pytest.raises(ScriptError, match=field):
        load_script(path)


def test_negative_prompt_must_be_text(tmp_path: Path) -> None:
    path = write_script(tmp_path, overrides={9: {"negative_prompt": "5"}})

    with pytest.raises(ScriptError, match="negative_prompt"):
        load_script(path)


def test_unknown_scene_field_is_rejected_to_catch_typos(tmp_path: Path) -> None:
    path = write_script(tmp_path, overrides={9: {"duracao_s": "5.0"}})

    with pytest.raises(ScriptError, match="duracao_s"):
        load_script(path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = write_script(tmp_path, extra_top='owner = "x"\n')

    with pytest.raises(ScriptError, match="owner"):
        load_script(path)


def test_error_message_points_to_the_offending_scene(tmp_path: Path) -> None:
    path = write_script(tmp_path, overrides={8: {"camera": '"zoom-in"'}})

    with pytest.raises(ScriptError, match=r"cena #8|índice 8"):
        load_script(path)


# --- roteiro de exemplo ---------------------------------------------------------------------


def test_example_script_loads_with_existing_photos() -> None:
    script = load_script(REPO_ROOT / "examples" / "roteiro.example.toml")

    assert len(script.scenes) == 12
    assert [s.index for s in script.scenes] == list(range(1, 13))
    assert all(s.photo.is_file() for s in script.scenes)
    assert len({s.photo for s in script.scenes}) == 12  # uma foto por cena
    assert all(3.0 <= s.duration_s <= 10.0 for s in script.scenes)


def test_example_photos_are_small_valid_images() -> None:
    from PIL import Image  # dependência já declarada (numpy/Pillow, T1)

    script = load_script(REPO_ROOT / "examples" / "roteiro.example.toml")
    for scene in script.scenes:
        assert scene.photo.stat().st_size < 60_000, scene.photo
        with Image.open(scene.photo) as image:
            image.verify()


def test_example_prompts_describe_only_camera_motion() -> None:
    """Higiene mínima da regra 'nenhuma característica inventada': o exemplo não pede objetos novos."""
    script = load_script(REPO_ROOT / "examples" / "roteiro.example.toml")
    for scene in script.scenes:
        assert "não adicionar" in scene.prompt.lower(), scene.index


# --- photo_release_ref (LGPD, T4 rodada final) ----------------------------------------------


def test_photo_release_ref_is_optional_and_defaults_to_none(tmp_path: Path) -> None:
    assert load_script(write_script(tmp_path)).photo_release_ref is None


@pytest.mark.parametrize("ref", ["termo-0001", "A.b_c-9", "x" * 64])
def test_photo_release_ref_is_read(tmp_path: Path, ref: str) -> None:
    script = load_script(write_script(tmp_path, extra_top=f'photo_release_ref = "{ref}"\n'))
    assert script.photo_release_ref == ref


@pytest.mark.parametrize(
    "literal",
    ['""', '"x' + 'x' * 64 + '"', '"termo da Maria"', '"Rua das Flores 123"', '"a/b"', "42", "true", '"termo\\u00e9"'],
)
def test_photo_release_ref_rejects_invalid_values(tmp_path: Path, literal: str) -> None:
    path = write_script(tmp_path, extra_top=f"photo_release_ref = {literal}\n")
    with pytest.raises(ScriptError, match="photo_release_ref"):
        load_script(path)
