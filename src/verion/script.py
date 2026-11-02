"""Roteiro da Fase 0 (spec 3.5): TOML com ``property_id`` e exatamente 12 ``[[scenes]]``.

Só stdlib. O caminho de cada foto é relativo à pasta do roteiro (nunca ao diretório atual) ou absoluto.
Qualquer desvio gera ``ScriptError`` (código de saída 2). Chaves desconhecidas também são recusadas,
para que um erro de digitação (``duracao_s``) não vire um valor padrão silencioso.

Formato::

    property_id = "casa-01"            # ^[a-z0-9-]{1,32}$ (id neutro; nunca endereço ou nome)

    [[scenes]]
    index = 1                          # 1..12, sem repetição
    photo = "fotos/01-sala.jpg"
    room = "sala"
    camera = "push-in"                 # push-in | pull-out | pan-left | pan-right | static
    duration_s = 5.0                   # alvo, de 3.0 a 10.0
    prompt = "Push-in lento. Não adicionar objetos."   # só movimento de câmera
    negative_prompt = ""               # opcional
"""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from verion.errors import ScriptError
from verion.providers.base import CAMERAS, Camera, Scene

__all__ = ["MAX_DURATION_S", "MIN_DURATION_S", "SCENE_COUNT", "Script", "load_script"]

SCENE_COUNT: Final = 12
MIN_DURATION_S: Final = 3.0
MAX_DURATION_S: Final = 10.0

_PROPERTY_ID: Final = re.compile(r"[a-z0-9-]{1,32}")  # usado com fullmatch: `$` aceitaria "\n" final
_TOP_KEYS: Final = frozenset({"property_id", "scenes"})
_REQUIRED: Final = ("index", "photo", "room", "camera", "duration_s", "prompt")
_SCENE_KEYS: Final = frozenset({*_REQUIRED, "negative_prompt"})


@dataclass(frozen=True)
class Script:
    """Roteiro validado. ``scenes`` vem ordenado por ``index``; ``sha256`` é o hash dos bytes do arquivo."""

    property_id: str
    path: Path
    sha256: str
    scenes: tuple[Scene, ...]


def _text(value: object, field: str, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ScriptError(f"{where}: {field} deve ser texto{'' if allow_empty else ' não vazio'}")
    return value


def _parse_scene(raw: object, position: int, base_dir: Path) -> Scene:
    where = f"cena #{position}"
    if not isinstance(raw, dict):
        raise ScriptError(f"{where}: esperada uma tabela [[scenes]]")
    unknown = sorted(set(raw) - _SCENE_KEYS)
    if unknown:
        raise ScriptError(f"{where}: campo desconhecido {', '.join(map(repr, unknown))}")
    missing = [key for key in _REQUIRED if key not in raw]
    if missing:
        raise ScriptError(f"{where}: campo ausente {', '.join(map(repr, missing))}")

    index = raw["index"]
    if type(index) is not int or not 1 <= index <= SCENE_COUNT:  # `type is int` recusa bool
        raise ScriptError(f"{where}: índice deve ser inteiro de 1 a {SCENE_COUNT}, veio {index!r}")
    where = f"cena #{position} (índice {index})"

    camera = raw["camera"]
    if not isinstance(camera, str) or camera not in CAMERAS:
        raise ScriptError(f"{where}: camera inválida {camera!r}; válidas: {', '.join(sorted(CAMERAS))}")

    duration = raw["duration_s"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, int | float)
        or not math.isfinite(duration)
        or not MIN_DURATION_S <= duration <= MAX_DURATION_S
    ):
        raise ScriptError(
            f"{where}: duration_s deve ser número de {MIN_DURATION_S:g} a {MAX_DURATION_S:g}, veio {duration!r}"
        )

    photo_text = _text(raw["photo"], "foto (photo)", where)
    photo = Path(photo_text)
    if not photo.is_absolute():
        photo = base_dir / photo
    photo = photo.resolve()
    if not photo.is_file():
        raise ScriptError(f"{where}: foto não encontrada: {photo_text}")

    return Scene(
        index=index,
        photo=photo,
        room=_text(raw["room"], "room", where),
        camera=cast("Camera", camera),  # validado contra CAMERAS acima
        duration_s=float(duration),
        prompt=_text(raw["prompt"], "prompt", where),
        negative_prompt=_text(raw.get("negative_prompt", ""), "negative_prompt", where, allow_empty=True),
    )


def load_script(path: Path) -> Script:
    """Lê e valida o roteiro. ``ScriptError``: arquivo ilegível, TOML inválido, ``property_id`` fora de
    ``^[a-z0-9-]{1,32}$``, número de cenas != 12, índice repetido ou fora de 1..12, foto ausente, câmera
    inválida, duração fora de 3..10, campo ausente ou desconhecido."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ScriptError(f"roteiro ilegível: {path} ({exc.strerror})") from exc
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ScriptError(f"roteiro {path} não é UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ScriptError(f"TOML inválido em {path}: {exc}") from exc

    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        raise ScriptError(f"chave desconhecida no roteiro: {', '.join(map(repr, unknown))}")

    property_id = doc.get("property_id")
    if not isinstance(property_id, str) or _PROPERTY_ID.fullmatch(property_id) is None:
        raise ScriptError(f"property_id deve casar com ^[a-z0-9-]{{1,32}}$, veio {property_id!r}")

    raw_scenes = doc.get("scenes", [])  # ausente = 0 cenas (mesma mensagem de contagem)
    if not isinstance(raw_scenes, list):
        raise ScriptError("scenes deve ser uma lista de tabelas [[scenes]]")
    if len(raw_scenes) != SCENE_COUNT:
        raise ScriptError(f"o roteiro exige exatamente {SCENE_COUNT} cenas, veio {len(raw_scenes)}")

    scenes = [_parse_scene(raw, position, path.parent) for position, raw in enumerate(raw_scenes, start=1)]
    seen: set[int] = set()
    for scene in scenes:
        if scene.index in seen:
            raise ScriptError(f"índice repetido: {scene.index}")
        seen.add(scene.index)

    return Script(
        property_id=property_id,
        path=path,
        sha256=hashlib.sha256(data).hexdigest(),
        scenes=tuple(sorted(scenes, key=lambda s: s.index)),
    )
