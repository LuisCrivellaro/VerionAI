"""Testes de ``verion.redact.redact_secrets``. Todas as chaves aqui são SINTÉTICAS."""

from __future__ import annotations

import pytest

from verion.redact import REDACTED, redact_secrets

SYNTHETIC_KEY = "AIzaSyDUMMY-synthetic-key_0123456789abcd"


@pytest.mark.parametrize(
    "text",
    [
        f"falha: {SYNTHETIC_KEY}",
        f"GET https://x.example/v1/models?key={SYNTHETIC_KEY}&alt=sse",
        f"POST https://x.example/v1?alt=sse&api_key={SYNTHETIC_KEY}",
        f"headers={{'x-goog-api-key': '{SYNTHETIC_KEY}'}}",
        f"x-goog-api-key: {SYNTHETIC_KEY}",
        f"Authorization: Bearer {SYNTHETIC_KEY}",
        "Bearer ya29.a0AfH6SMBsynthetic-token",
        "token AQ.Ab8RN6Isynthetic_token_value_12345",
    ],
)
def test_known_patterns_are_removed(text: str) -> None:
    out = redact_secrets(text, environ={})
    assert SYNTHETIC_KEY not in out and "ya29.a0" not in out and "AQ.Ab8" not in out
    assert REDACTED in out


def test_literal_environment_values_are_removed_in_any_format() -> None:
    env = {"GEMINI_API_KEY": "chave-sem-padrao-conhecido/42+x", "GOOGLE_API_KEY": "outra chave 9999"}
    text = "erro com chave-sem-padrao-conhecido/42+x e chave-sem-padrao-conhecido%2F42%2Bx e outra chave 9999"
    out = redact_secrets(text, environ=env)
    assert "chave-sem-padrao" not in out and "outra chave" not in out
    assert out.count(REDACTED) == 3


def test_short_literals_are_ignored() -> None:
    assert redact_secrets("abc é comum", environ={"GEMINI_API_KEY": "abc"}) == "abc é comum"


def test_uses_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "valor-literal-do-ambiente")
    assert "valor-literal" not in redact_secrets("x valor-literal-do-ambiente y")


def test_plain_text_is_untouched() -> None:
    text = "ProviderError: timeout após 30 s na cena 3 (run_id=2)"
    assert redact_secrets(text, environ={}) == text
