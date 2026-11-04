"""Redação de segredos em texto livre (mensagens de erro, tracebacks, exports).

Duas camadas, aplicadas por ``redact_secrets``:

1. valores LITERAIS de ``GEMINI_API_KEY`` e ``GOOGLE_API_KEY`` presentes no ambiente (inclusive a forma
   com percent-encoding), porque uma chave pode ter qualquer formato e vazar dentro de uma exceção do SDK;
2. padrões conhecidos: ``AIza...``, ``AQ....``, ``ya29....``, ``Bearer <token>``, ``?key=``/``&api_key=``,
   cabeçalho ``x-goog-api-key`` e ``Authorization``.

Este módulo é o único lugar do código (fora dos adaptadores) que lê o valor dessas variáveis, e só para
removê-lo do texto; o valor nunca é impresso, gravado ou devolvido. Sem rede.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Final
from urllib.parse import quote

__all__ = ["REDACTED", "redact_secrets"]

REDACTED: Final = "[REDACTED]"
_SECRET_VARS: Final = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_MIN_LITERAL_CHARS: Final = 6  # abaixo disso não é chave real, e trocaria texto comum

_KEY_RE: Final = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-]{20,}|ya29\.[0-9A-Za-z_\-]+")
_BEARER_RE: Final = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-.~+/=]+", re.IGNORECASE)
_QUERY_RE: Final = re.compile(
    r"([?&;](?:key|api_key|apikey|access_token|auth|signature)=)[^&\s\"'<>()]+", re.IGNORECASE
)
_HEADER_RE: Final = re.compile(
    r"((?:x-goog-api-key|authorization)['\"]?\s*[:=]\s*['\"]?)[^\s,'\"}]+(?:\s+[A-Za-z0-9_\-.~+/=]{8,})?",
    re.IGNORECASE,
)


def _literals(environ: Mapping[str, str]) -> list[str]:
    values: set[str] = set()
    for name in _SECRET_VARS:
        value = environ.get(name, "").strip()
        if len(value) >= _MIN_LITERAL_CHARS:
            values.add(value)
            values.add(quote(value, safe=""))
    return sorted(values, key=len, reverse=True)  # o mais longo primeiro


def redact_secrets(text: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Devolve ``text`` sem chaves de API, tokens e cabeçalhos de autenticação."""
    env = os.environ if environ is None else environ
    for literal in _literals(env):
        text = text.replace(literal, REDACTED)
    text = _KEY_RE.sub(REDACTED, text)
    text = _HEADER_RE.sub(rf"\1{REDACTED}", text)
    text = _BEARER_RE.sub(rf"\1{REDACTED}", text)
    return _QUERY_RE.sub(rf"\1{REDACTED}", text)
