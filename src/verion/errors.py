"""Exceções de domínio e códigos de saída da CLI (spec 3.1).

Toda exceção de domínio herda de ``VerionError`` e declara ``exit_code``. A CLI captura
``VerionError``, imprime em stderr e devolve o código. Qualquer outra exceção é bug.

Códigos: 2 entrada/configuração, 3 gasto bloqueado, 4 provedor, 5 regra de tentativa, 6 ffmpeg.
(0 é sucesso e não tem exceção.)
"""

from __future__ import annotations


class VerionError(Exception):
    """Base de todos os erros de domínio. O código 1 fica reservado a bugs (traceback)."""

    exit_code: int = 1


# --- código 2: entrada e configuração -------------------------------------------------------


class InputError(VerionError):
    exit_code = 2


class ConfigError(InputError):
    """Arquivo de configuração ausente ou inválido (preços, variáveis de ambiente)."""


class SchemaMismatch(InputError):
    """Banco com ``user_version`` diferente do esperado."""


class ScriptError(InputError):
    """Roteiro inválido."""


class UnknownMethod(InputError):
    """Id de método fora do registry."""


class MethodUnavailable(InputError):
    """Módulo ou extra do método não está instalado."""


class InvalidVerdict(InputError):
    """Veredito de QC fora das regras da skill ``verion-fidelity-qc``."""


class AuthorizationRefused(InputError):
    """Autorização de gasto recusada (sem TTY ou frase de confirmação errada)."""


class DepthMissing(InputError):
    """Mapa de profundidade ausente para o parallax 2,5D."""


# --- código 3: gasto bloqueado --------------------------------------------------------------


class SpendBlocked(VerionError):
    exit_code = 3


class PaidCallBlocked(SpendBlocked):
    """Método pago sem ``--allow-paid``."""


class BudgetExceeded(SpendBlocked):
    """Saldo autorizado insuficiente para a reserva."""


class PriceMissing(SpendBlocked):
    """Método pago sem preço na tabela."""


class EstimateInvalid(SpendBlocked):
    """Unidades ou custo inválidos (zero, negativo, não finito, tipo de unidade divergente)."""


class TokenInvalid(SpendBlocked):
    """``SpendToken`` forjado, reusado, de outro método ou de linha não pendente."""


# --- código 4: provedor ---------------------------------------------------------------------


class ProviderError(VerionError):
    """Falha do provedor. ``billed_units`` é o consumo já faturado, se houver."""

    exit_code = 4

    def __init__(
        self,
        message: str,
        *,
        transient: bool,
        billed_units: float = 0.0,
        provider_job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.billed_units = billed_units
        self.provider_job_id = provider_job_id


# --- código 5: regras de tentativa ----------------------------------------------------------


class AttemptRuleError(VerionError):
    exit_code = 5


class AwaitingQC(AttemptRuleError):
    """A última tentativa está ``ok`` e ainda não tem veredito de QC."""


class SceneClosed(AttemptRuleError):
    """Cena já aprovada ou rejeitada (inclui o limite de 3 tentativas)."""


class PendingAttempt(AttemptRuleError):
    """Há tentativa ``pending`` sem liquidação (ver ``verion ledger settle``)."""


# --- código 6: ffmpeg -----------------------------------------------------------------------


class FfmpegError(VerionError):
    exit_code = 6

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr
