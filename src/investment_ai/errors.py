"""Typed application failures with machine-readable codes."""


class InvestmentAIError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ConfigError(InvestmentAIError):
    """Invalid public configuration or unavailable credentials."""


class ProviderError(InvestmentAIError):
    """Market-data provider failure."""


class StorageError(InvestmentAIError):
    """Persistence failure."""


class DataError(InvestmentAIError):
    """Invalid or unavailable market data."""
