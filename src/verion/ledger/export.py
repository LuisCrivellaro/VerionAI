"""Export JSONL do ledger (spec 3.8): evidência versionada, porque a sessão em nuvem é efêmera.

Reescreve ``runs.jsonl``, ``attempts.jsonl``, ``scenes.jsonl`` e ``authorizations.jsonl`` (ordenados por ``id``,
para o diff ser estável). Regras:

- runs ``fake`` (pago fictício, só testes) e suas tentativas são omitidos;
- nunca sai caminho de foto, de clipe ou do roteiro: as colunas ``clip_path`` e ``script_path`` não são lidas
  para o arquivo, e caminhos absolutos dentro de mensagens de erro viram ``<caminho>``. Limite: caminhos
  relativos em texto livre não são reconhecíveis. O ``provider_job_id`` também fica fora;
- ``attempts.jsonl`` traz as chaves do registro da skill ``verion-fidelity-qc`` (``projeto``, ``cena``,
  ``metodo``, ``tentativa``, ``veredito``, ``defeitos``, ``custo_brl``, ``revisor``) sempre presentes, mais
  extensões. Sem QC: ``veredito`` e ``revisor`` são ``null`` e ``defeitos`` é ``[]``;
- ``metodo`` sai literal, como está no banco.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from verion.ledger import repo
from verion.ledger.repo import AttemptRow
from verion.script import SCENE_COUNT

__all__ = ["ExportResult", "export_jsonl"]

_EXCLUDED_METHODS: Final = frozenset({"fake"})
_RUNS: Final = "runs.jsonl"
_ATTEMPTS: Final = "attempts.jsonl"
_SCENES: Final = "scenes.jsonl"
_AUTHORIZATIONS: Final = "authorizations.jsonl"

# Caminho absoluto POSIX com 2+ segmentos, no início do texto ou depois de espaço, aspas, parêntese, "=" ou ":".
_ABS_PATH: Final = re.compile(r"""(?:(?<=^)|(?<=[\s'"(=:\[]))/[^\s'"():;,\]]+(?:/[^\s'"():;,\]]+)+""")
_PLACEHOLDER: Final = "<caminho>"


@dataclass(frozen=True)
class ExportResult:
    paths: list[Path]
    skipped_fake_runs: int


def export_jsonl(conn: sqlite3.Connection, out_dir: Path) -> ExportResult:
    """Grava os 4 arquivos em ``out_dir`` (criado se preciso) e devolve os caminhos, na ordem acima."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_runs = repo.list_runs(conn)
    runs = [r for r in all_runs if r.method not in _EXCLUDED_METHODS]
    skipped = len(all_runs) - len(runs)

    run_lines: list[Mapping[str, Any]] = []
    attempt_lines: list[Mapping[str, Any]] = []
    scene_lines: list[Mapping[str, Any]] = []
    for run in runs:
        run_lines.append(
            {
                "run_id": run.id,
                "projeto": run.property_id,
                "metodo": run.method,
                "script_sha256": run.script_sha256,
                "iniciado_em": run.started_at,
                "finalizado_em": run.finished_at,
                "notas": run.notes,
            }
        )
        attempts = repo.list_attempts(conn, run_id=run.id)
        for attempt in attempts:
            attempt_lines.append(_attempt_line(conn, run.property_id, attempt))
        for scene in range(1, SCENE_COUNT + 1):
            status, reason = repo.scene_status(conn, run.id, scene)
            scene_attempts = [a for a in attempts if a.scene_index == scene]
            scene_lines.append(
                {
                    "projeto": run.property_id,
                    "metodo": run.method,
                    "run_id": run.id,
                    "cena": scene,
                    "status": status,
                    "motivo": reason,
                    "tentativas": len(scene_attempts),
                    "custo_brl": round(sum((a.cost_brl for a in scene_attempts), 0.0), 6),
                }
            )

    authorization_lines = [
        {"id": i, "valor_brl": amount, "referencia": reference, "criado_em": created_at}
        for i, amount, reference, created_at in repo.list_authorizations(conn)
    ]

    paths = [
        _write(out_dir / _RUNS, run_lines),
        _write(out_dir / _ATTEMPTS, attempt_lines),
        _write(out_dir / _SCENES, scene_lines),
        _write(out_dir / _AUTHORIZATIONS, authorization_lines),
    ]
    return ExportResult(paths=paths, skipped_fake_runs=skipped)


def _attempt_line(conn: sqlite3.Connection, property_id: str, attempt: AttemptRow) -> dict[str, Any]:
    qc = repo.get_qc(conn, attempt.id)
    return {
        # chaves da skill verion-fidelity-qc
        "projeto": property_id,
        "cena": attempt.scene_index,
        "metodo": attempt.method,
        "tentativa": attempt.attempt_no,
        "veredito": None if qc is None else qc.verdict,
        "defeitos": [] if qc is None else [{"tipo": d.tipo, "segundo": d.segundo, "nota": d.nota} for d in qc.defects],
        "custo_brl": round(attempt.cost_brl, 6),
        "revisor": None if qc is None else qc.reviewer,
        # extensões
        "run_id": attempt.run_id,
        "attempt_id": attempt.id,
        "status": attempt.status,
        "custo_estimado": attempt.cost_is_estimate,
        "reservado_brl": round(attempt.reserved_brl, 6),
        "unidades": attempt.units,
        "tipo_unidade": attempt.unit_kind,
        "duracao_s": attempt.duration_s,
        "tempo_s": attempt.wall_time_s,
        "erro": None if attempt.error is None else _redact_paths(attempt.error),
    }


def _redact_paths(text: str) -> str:
    return _ABS_PATH.sub(_PLACEHOLDER, text)


def _write(path: Path, lines: Iterable[Mapping[str, Any]]) -> Path:
    """Escrita atômica (arquivo temporário + ``os.replace``): uma falha não deixa o export pela metade."""
    body = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    tmp = path.with_name(f".{path.name}.part")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
