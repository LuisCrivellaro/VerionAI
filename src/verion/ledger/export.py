"""Export JSONL do ledger (spec 3.8): evidência versionada, porque a sessão em nuvem é efêmera.

Reescreve ``runs.jsonl``, ``attempts.jsonl``, ``scenes.jsonl`` e ``authorizations.jsonl`` (ordenados por ``id``,
para o diff ser estável). O export usa LISTA DE PERMITIDOS: só campos estruturados saem; texto livre nunca.

- runs ``fake`` (pago fictício, só testes) e suas tentativas são omitidos; runs sem tentativa não geram linhas
  de cena (como no relatório);
- ficam só no banco local, fora do export: ``script_path``, ``clip_path``, ``provider_job_id``, ``runs.notes``,
  a ``nota`` de cada defeito, o motivo textual da cena e a ``referencia`` das autorizações. São campos de texto
  livre que podem conter endereço, nome de pessoa ou caminho (LGPD);
- ``erro`` sai só como a CLASSE da exceção (ex.: ``ProviderError``), com o ``erro_codigo`` de saída;
- defeitos saem como ``tipo``, ``segundo`` e ``gravidade`` (``bloqueante`` para 1..7, ``leve``);
- ``motivo_codigo`` (cena) é um código fixo: ``limite_3_tentativas``, ``rejeitada_qc``, ``pendente``,
  ``aguardando_qc`` ou ``null``;
- por último, cada linha passa por ``redact_secrets`` (defesa em profundidade contra chaves de API);
- ``attempts.jsonl`` traz as chaves do registro da skill ``verion-fidelity-qc`` (``projeto``, ``cena``,
  ``metodo``, ``tentativa``, ``veredito``, ``defeitos``, ``custo_brl``, ``revisor``) sempre presentes, mais
  extensões. Sem QC: ``veredito`` e ``revisor`` são ``null`` e ``defeitos`` é ``[]``;
- ``metodo`` sai literal, como está no banco. ``projeto`` é o ``property_id``: use um código sem relação com o
  imóvel (ex.: ``imv-001``).
"""

from __future__ import annotations

import builtins
import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from verion import errors
from verion.ledger import repo
from verion.ledger.repo import AttemptRow, Defect, QcRow
from verion.redact import redact_secrets
from verion.script import SCENE_COUNT

__all__ = ["ExportResult", "export_jsonl"]

_EXCLUDED_METHODS: Final = frozenset({"fake"})
_RUNS: Final = "runs.jsonl"
_ATTEMPTS: Final = "attempts.jsonl"
_SCENES: Final = "scenes.jsonl"
_AUTHORIZATIONS: Final = "authorizations.jsonl"

_ERROR_NAME: Final = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):")
_VERION_ERRORS: Final = {
    name: obj
    for name, obj in vars(errors).items()
    if isinstance(obj, type) and issubclass(obj, errors.VerionError)
}
_BUILTIN_ERRORS: Final = frozenset(
    name for name, obj in vars(builtins).items() if isinstance(obj, type) and issubclass(obj, BaseException)
)


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
            }
        )
        attempts = repo.list_attempts(conn, run_id=run.id)
        if not attempts:
            continue  # run sem nenhuma tentativa (ex.: bloqueado na cena 1): sem linhas de cena, como no relatório
        reviews = {a.id: repo.get_qc(conn, a.id) for a in attempts}
        for attempt in attempts:
            attempt_lines.append(_attempt_line(run.property_id, attempt, reviews[attempt.id]))
        for scene in range(1, SCENE_COUNT + 1):
            status, _reason = repo.scene_status(conn, run.id, scene)  # o motivo textual não sai
            scene_attempts = [a for a in attempts if a.scene_index == scene]
            scene_lines.append(
                {
                    "projeto": run.property_id,
                    "metodo": run.method,
                    "run_id": run.id,
                    "cena": scene,
                    "status": status,
                    "motivo_codigo": _reason_code(status, [reviews[a.id] for a in scene_attempts]),
                    "tentativas": len(scene_attempts),
                    "custo_brl": round(sum((a.cost_brl for a in scene_attempts), 0.0), 6),
                }
            )

    authorization_lines = [
        {"id": i, "valor_brl": amount, "criado_em": created_at}  # a referência (texto livre) fica no banco
        for i, amount, _reference, created_at in repo.list_authorizations(conn)
    ]

    paths = [
        _write(out_dir / _RUNS, run_lines),
        _write(out_dir / _ATTEMPTS, attempt_lines),
        _write(out_dir / _SCENES, scene_lines),
        _write(out_dir / _AUTHORIZATIONS, authorization_lines),
    ]
    return ExportResult(paths=paths, skipped_fake_runs=skipped)


def _reason_code(status: str, reviews: Sequence[QcRow | None]) -> str | None:
    if status == "rejeitada":
        return "rejeitada_qc" if any(r is not None and r.verdict == "rejeitada" for r in reviews) else "limite_3_tentativas"
    if status in ("pendente", "aguardando_qc"):
        return status
    return None


def _error_class(text: str) -> tuple[str, int | None]:
    """Classe da exceção e código de saída, a partir do texto gravado pelo harness (``Classe: mensagem``).

    Lista de permitidos: só nomes de ``verion.errors`` e de exceções embutidas saem; qualquer outro texto
    (inclusive o de liquidação manual) vira um rótulo fixo, e a mensagem nunca é exportada.
    """
    match = _ERROR_NAME.match(text)
    name = match.group(1) if match else ""
    if name in _VERION_ERRORS:
        return name, _VERION_ERRORS[name].exit_code
    if name in _BUILTIN_ERRORS:
        return name, None
    if text.startswith("liquidada manualmente"):
        return "LiquidacaoManual", None
    return "Erro", None


def _attempt_line(property_id: str, attempt: AttemptRow, qc: QcRow | None) -> dict[str, Any]:
    error_class, error_code = (None, None) if attempt.error is None else _error_class(attempt.error)
    return {
        # chaves da skill verion-fidelity-qc
        "projeto": property_id,
        "cena": attempt.scene_index,
        "metodo": attempt.method,
        "tentativa": attempt.attempt_no,
        "veredito": None if qc is None else qc.verdict,
        "defeitos": [] if qc is None else [_defect_line(d) for d in qc.defects],
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
        "erro": error_class,
        "erro_codigo": error_code,
    }


def _defect_line(defect: Defect) -> dict[str, Any]:
    # sem a ``nota`` (texto livre): só o tipo, o segundo e a gravidade
    return {
        "tipo": defect.tipo,
        "segundo": defect.segundo,
        "gravidade": "leve" if defect.tipo == "leve" else "bloqueante",
    }


def _write(path: Path, lines: Iterable[Mapping[str, Any]]) -> Path:
    """Escrita atômica (arquivo temporário + ``os.replace``). Cada linha passa por ``redact_secrets``."""
    body = "".join(redact_secrets(json.dumps(line, ensure_ascii=False)) + "\n" for line in lines)
    tmp = path.with_name(f".{path.name}.part")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
