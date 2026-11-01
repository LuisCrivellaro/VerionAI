"""Teste arquitetural por AST (spec 3.3 e T1, caso j). Sem importar o código verificado.

Escopo: ``src/verion/**`` (``tests/`` fica de fora de propósito: testes podem semear autorização
e reservas via ``repo``). Regras:

1. ``_SEAL``, ``_ISSUED`` e escrita em ``._used`` só em ``budget.py``.
2. ``insert_pending`` só em ``budget.py`` e ``ledger/repo.py``.
3. ``add_authorization`` só em ``cli.py`` e ``providers/contract.py`` (a definição em ``repo.py`` não
   conta como uso).
4. Todo ``generate`` de classe ``*Adapter`` em ``providers/*.py`` e ``local/{ken_burns,parallax}.py``
   recebe ``token`` e começa com ``token.consume(self.method)``. O Protocol de ``providers/base.py``
   é isento. A classe precisa definir ``generate`` no próprio corpo com ``def``, sem decoradores
   (herdado, atribuído ou decorado falha).
5. SQL de escrita (``INSERT INTO authorizations|attempts``, ``UPDATE attempts``) só em ``ledger/repo.py``.

As regras são funções puras sobre ``{caminho_relativo: fonte}``; os testes negativos alimentam fontes
sintéticas para provar que cada regra detecta violações (os diretórios providers/ e local/ ainda não
existem, então a varredura real passa vacuamente nas regras 3 e 4).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "verion"

SEAL_ALLOWED = frozenset({"budget.py"})
INSERT_PENDING_ALLOWED = frozenset({"budget.py", "ledger/repo.py"})
AUTHORIZATION_ALLOWED = frozenset({"cli.py", "providers/contract.py"})
LOCAL_ADAPTER_FILES = frozenset({"local/ken_burns.py", "local/parallax.py"})


def load_sources(root: Path) -> dict[str, str]:
    """Mapa ``caminho/relativo.py`` (POSIX) → fonte, para todo ``.py`` sob ``root``."""
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))}


def _references(tree: ast.AST, name: str) -> list[int]:
    """Linhas que usam ``name``: Name, Attribute, import (com ou sem alias), ``global`` e string exata."""
    lines: list[int] = []
    for node in ast.walk(tree):
        hit = (
            (isinstance(node, ast.Name) and node.id == name)
            or (isinstance(node, ast.Attribute) and node.attr == name)
            or (isinstance(node, ast.alias) and node.name.split(".")[-1] == name)
            or (isinstance(node, ast.Constant) and node.value == name)  # getattr(mod, "nome")
            or (isinstance(node, ast.Global | ast.Nonlocal) and name in node.names)
        )
        if hit:
            lines.append(getattr(node, "lineno", 0))
    return lines


def _restricted(sources: Mapping[str, str], name: str, allowed: frozenset[str]) -> list[str]:
    violations = []
    for rel, text in sources.items():
        if rel in allowed:
            continue
        for line in _references(ast.parse(text, filename=rel), name):
            violations.append(f"{rel}:{line}: uso proibido de {name!r} (permitido só em {sorted(allowed)})")
    return violations


def _used_writes(tree: ast.AST) -> list[int]:
    """Escritas na flag ``_used`` do token: ``x._used = ...``/``del``/``+=`` ou a string ``"_used"``."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "_used" and isinstance(node.ctx, ast.Store | ast.Del):
            lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value == "_used":  # setattr(t, "_used", ...)
            lines.append(node.lineno)
    return lines


def check_seal(sources: Mapping[str, str]) -> list[str]:
    """Selo, registro de tokens emitidos e flag ``_used``: só em budget.py."""
    violations = _restricted(sources, "_SEAL", SEAL_ALLOWED) + _restricted(sources, "_ISSUED", SEAL_ALLOWED)
    for rel, text in sources.items():
        if rel in SEAL_ALLOWED:
            continue
        for line in _used_writes(ast.parse(text, filename=rel)):
            violations.append(f"{rel}:{line}: escrita proibida em '_used' (permitida só em budget.py)")
    return violations


def check_insert_pending(sources: Mapping[str, str]) -> list[str]:
    return _restricted(sources, "insert_pending", INSERT_PENDING_ALLOWED)


def check_add_authorization(sources: Mapping[str, str]) -> list[str]:
    # A definição em ledger/repo.py é um FunctionDef (não Name/Attribute), logo não é uso.
    return _restricted(sources, "add_authorization", AUTHORIZATION_ALLOWED)


RAW_SQL_ALLOWED = frozenset({"ledger/repo.py"})
_RAW_LEDGER_WRITE = re.compile(
    r"\b(insert\s+into\s+(authorizations|attempts)|update\s+attempts)\b", re.IGNORECASE
)


def check_raw_ledger_sql(sources: Mapping[str, str]) -> list[str]:
    """SQL de escrita em ``authorizations``/``attempts`` só em ledger/repo.py (inclui partes de f-string)."""
    violations = []
    for rel, text in sources.items():
        if rel in RAW_SQL_ALLOWED:
            continue
        for node in ast.walk(ast.parse(text, filename=rel)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _RAW_LEDGER_WRITE.search(node.value):
                violations.append(f"{rel}:{node.lineno}: SQL de escrita no ledger fora de ledger/repo.py")
    return violations


def _is_adapter_file(rel: str) -> bool:
    path = PurePosixPath(rel)
    in_providers = len(path.parts) == 2 and path.parts[0] == "providers" and path.name != "base.py"
    return in_providers or rel in LOCAL_ADAPTER_FILES


def _is_consume_call(stmt: ast.stmt) -> bool:
    """``token.consume(self.method)`` como instrução de expressão (``await`` não vale)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "consume"
        and isinstance(func.value, ast.Name)
        and func.value.id == "token"
    ):
        return False
    if call.keywords or len(call.args) != 1:
        return False
    arg = call.args[0]
    return (
        isinstance(arg, ast.Attribute)
        and arg.attr == "method"
        and isinstance(arg.value, ast.Name)
        and arg.value.id == "self"
    )


def _first_statement(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # docstring
    return body[0] if body else None


def check_adapters(sources: Mapping[str, str]) -> list[str]:
    violations = []
    for rel, text in sources.items():
        if not _is_adapter_file(rel):
            continue
        for cls in ast.walk(ast.parse(text, filename=rel)):
            if not (isinstance(cls, ast.ClassDef) and cls.name.endswith("Adapter")):
                continue
            defs = [
                item
                for item in cls.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "generate"
            ]
            for item in cls.body:  # generate = _gen / generate: T = _gen
                targets = item.targets if isinstance(item, ast.Assign) else [getattr(item, "target", None)]
                if isinstance(item, ast.Assign | ast.AnnAssign) and any(
                    isinstance(t, ast.Name) and t.id == "generate" for t in targets
                ):
                    violations.append(f"{rel}:{item.lineno}: {cls.name}.generate atribuído, não definido com def")
            if not defs:
                violations.append(f"{rel}:{cls.lineno}: {cls.name} não define generate no próprio corpo")
            for item in defs:
                where = f"{rel}:{item.lineno}: {cls.name}.generate"
                if item.decorator_list:
                    violations.append(f"{where} não pode ter decoradores")
                    continue
                args = item.args
                names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
                if "token" not in names:
                    violations.append(f"{where} não recebe o parâmetro 'token'")
                    continue
                first = _first_statement(item)
                if first is None or not _is_consume_call(first):
                    violations.append(f"{where} não começa com token.consume(self.method)")
    return violations


ALL_RULES = {
    "_SEAL": check_seal,
    "insert_pending": check_insert_pending,
    "add_authorization": check_add_authorization,
    "adapter.generate": check_adapters,
    "raw_ledger_sql": check_raw_ledger_sql,
}


# --- varredura real de src/verion/** ------------------------------------------------------------


@pytest.fixture(scope="module")
def real_sources() -> dict[str, str]:
    sources = load_sources(SRC_ROOT)
    # Garante que a varredura não é vazia nas regras que já têm alvo hoje.
    assert "budget.py" in sources and "ledger/repo.py" in sources
    assert not any(rel.startswith("tests/") for rel in sources)
    return sources


@pytest.mark.parametrize("rule", sorted(ALL_RULES))
def test_real_tree_respects_rule(real_sources: dict[str, str], rule: str) -> None:
    assert ALL_RULES[rule](real_sources) == []


def test_real_tree_defines_the_guarded_symbols(real_sources: dict[str, str]) -> None:
    """Se alguém renomear os símbolos protegidos, as regras viram vácuo: este teste avisa."""
    assert _references(ast.parse(real_sources["budget.py"]), "_SEAL")
    assert _references(ast.parse(real_sources["budget.py"]), "insert_pending")
    repo_tree = ast.parse(real_sources["ledger/repo.py"])
    defined = {n.name for n in ast.walk(repo_tree) if isinstance(n, ast.FunctionDef)}
    assert {"insert_pending", "add_authorization"} <= defined


# --- testes negativos e positivos com fontes sintéticas ------------------------------------------

GOOD_ADAPTER = '''
class VeoAdapter:
    method = "veo-3.1-fast"

    def generate(self, request, token):
        """Gera o clipe."""
        token.consume(self.method)
        return None
'''


def test_seal_is_flagged_outside_budget() -> None:
    for src in (
        "from verion.budget import _SEAL\n",
        "from verion.budget import _SEAL as s\n",
        "import verion.budget as b\nx = b._SEAL\n",
        "x = getattr(budget, '_SEAL')\n",
    ):
        assert check_seal({"providers/evil.py": src}), src
    assert check_seal({"budget.py": "_SEAL = object()\n"}) == []


def test_insert_pending_is_flagged_outside_budget_and_repo() -> None:
    for src in (
        "from verion.ledger.repo import insert_pending\n",
        "from verion.ledger import repo\nrepo.insert_pending(c)\n",
        "f = getattr(repo, 'insert_pending')\n",
    ):
        assert check_insert_pending({"harness.py": src}), src
        assert check_insert_pending({"local/ken_burns.py": src}), src
    ok = {"budget.py": "repo.insert_pending(c)\n", "ledger/repo.py": "def insert_pending(c): ...\n"}
    assert check_insert_pending(ok) == []


def test_add_authorization_is_flagged_outside_allow_list() -> None:
    src = "from verion.ledger import repo\nrepo.add_authorization(c, 1.0, 'x')\n"
    assert check_add_authorization({"harness.py": src})
    assert check_add_authorization({"budget.py": src})  # nem o budget pode
    assert check_add_authorization({"report.py": "from verion.ledger.repo import add_authorization\n"})
    assert check_add_authorization({"providers/fake.py": src})  # só contract.py, não o resto de providers/
    assert check_add_authorization({"cli.py": src, "providers/contract.py": src}) == []
    # a definição em repo.py não é um uso
    assert check_add_authorization({"ledger/repo.py": "def add_authorization(c, a, r): ...\n"}) == []


def test_adapter_generate_without_token_param_is_flagged() -> None:
    src = GOOD_ADAPTER.replace("request, token", "request")
    found = check_adapters({"providers/veo.py": src})
    assert len(found) == 1 and "token" in found[0]


def test_adapter_generate_without_consume_is_flagged() -> None:
    for body in (
        "        return None\n",
        "        pass\n",
        "        x = 1\n        token.consume(self.method)\n",  # consume não é a primeira instrução
        "        token.consume('veo-3.1-fast')\n",  # método literal em vez de self.method
        "        other.consume(self.method)\n",
        "        token.job_accepted(self.method)\n",
        "        token.consume(self.method, 1)\n",
        "        token.consume()\n",
        "        if token:\n            token.consume(self.method)\n",
    ):
        src = f"class VeoAdapter:\n    def generate(self, request, token):\n{body}"
        assert check_adapters({"providers/veo.py": src}), body


def test_adapter_generate_empty_body_after_docstring_is_flagged() -> None:
    src = 'class VeoAdapter:\n    def generate(self, request, token):\n        """So doc."""\n'
    assert check_adapters({"providers/veo.py": src})


def test_async_adapter_generate_is_checked() -> None:
    bad = "class KlingAdapter:\n    async def generate(self, request, token):\n        return None\n"
    assert check_adapters({"providers/kling.py": bad})
    good = "class KlingAdapter:\n    async def generate(self, request, token):\n        token.consume(self.method)\n"
    assert check_adapters({"providers/kling.py": good}) == []


def test_keyword_only_token_param_is_accepted() -> None:
    src = "class WanAdapter:\n    def generate(self, request, *, token):\n        token.consume(self.method)\n"
    assert check_adapters({"providers/wan.py": src}) == []


def test_good_adapter_passes_in_every_covered_file() -> None:
    covered = {
        "providers/veo.py": GOOD_ADAPTER,
        "providers/fake.py": GOOD_ADAPTER,
        "local/ken_burns.py": GOOD_ADAPTER,
        "local/parallax.py": GOOD_ADAPTER,
    }
    assert check_adapters(covered) == []


def test_adapter_rule_covers_the_declared_files_only() -> None:
    bad = GOOD_ADAPTER.replace("        token.consume(self.method)\n", "")
    for rel in ("providers/veo.py", "providers/fake.py", "local/ken_burns.py", "local/parallax.py"):
        assert check_adapters({rel: bad}), rel
    # base.py (Protocol), outros módulos e subpastas não são cobertos
    protocol = "class SceneGenerator(Protocol):\n    def generate(self, request, token): ...\n"
    assert check_adapters({"providers/base.py": protocol}) == []
    assert check_adapters({"providers/base.py": bad}) == []
    assert check_adapters({"local/ffmpeg.py": bad, "harness.py": bad}) == []


def test_only_classes_named_adapter_are_checked() -> None:
    src = "class Helper:\n    def generate(self, request):\n        return None\n"
    assert check_adapters({"providers/veo.py": src}) == []
    assert check_adapters({"providers/veo.py": src.replace("Helper", "VeoAdapter")})


def test_violations_in_multiple_classes_are_all_reported() -> None:
    bad = "class AAdapter:\n    def generate(self, r):\n        pass\n"
    bad += "class BAdapter:\n    def generate(self, r, token):\n        pass\n"
    assert len(check_adapters({"providers/x.py": bad})) == 2


def test_tests_directory_is_out_of_scope() -> None:
    """Rótulo de escopo: a varredura parte de src/verion e não enxerga tests/."""
    assert SRC_ROOT.name == "verion" and SRC_ROOT.parent.name == "src"
    assert "tests" not in {p.name for p in SRC_ROOT.iterdir() if p.is_dir()}


# --- revisão T1 rodada 1 -------------------------------------------------------------------------


def test_adapter_must_define_generate_in_its_own_body() -> None:
    inherited = "class VeoAdapter(Base):\n    method = 'veo-3.1-fast'\n"
    assigned = "def _gen(self, request, token):\n    token.consume(self.method)\n\n"
    assigned += "class VeoAdapter:\n    generate = _gen\n"
    annotated = "class VeoAdapter:\n    generate: object = _gen\n"
    decorated = "class VeoAdapter:\n    @wrap\n    def generate(self, request, token):\n        token.consume(self.method)\n"
    for src in (inherited, assigned, annotated, decorated):
        assert check_adapters({"providers/veo.py": src}), src
        assert check_adapters({"local/parallax.py": src}), src


def test_issued_registry_and_used_flag_are_restricted_to_budget() -> None:
    for src in (
        "from verion.budget import _ISSUED\n",
        "from verion import budget\nbudget._ISSUED.add(t)\n",
        "token._used = False\n",
        "setattr(token, '_used', False)\n",
    ):
        assert check_seal({"harness.py": src}), src
    assert check_seal({"budget.py": "_ISSUED = set()\nself._used = True\n"}) == []
    assert check_seal({"harness.py": "if token._used:\n    pass\n"}) == []  # leitura não é escrita


def test_raw_ledger_writes_are_restricted_to_repo() -> None:
    for sql in (
        "INSERT INTO authorizations (amount_brl) VALUES (1)",
        "insert   into\n attempts (id) values (1)",
        "Update Attempts SET cost_brl = 0",
    ):
        src = f"conn.execute({sql!r})\n"
        assert check_raw_ledger_sql({"harness.py": src}), sql
        assert check_raw_ledger_sql({"budget.py": src}), sql
        assert check_raw_ledger_sql({"ledger/repo.py": src}) == []
    fstring = "conn.execute(f'UPDATE attempts SET cost_brl = {x}')\n"
    assert check_raw_ledger_sql({"report.py": fstring})
    assert check_raw_ledger_sql({"report.py": "conn.execute('SELECT * FROM attempts')\n"}) == []
    assert check_raw_ledger_sql({"report.py": "conn.execute('INSERT INTO runs VALUES (1)')\n"}) == []


def test_real_tree_has_no_raw_ledger_writes_outside_repo(real_sources: dict[str, str]) -> None:
    assert check_raw_ledger_sql(real_sources) == []
    assert "raw_ledger_sql" in ALL_RULES
