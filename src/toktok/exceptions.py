from __future__ import annotations


class PolymarketError(Exception):
    """Base exception for Polymarket client errors."""


class PolymarketRequestError(PolymarketError):
    """Raised when the request could not be completed."""


class PolymarketAPIError(PolymarketError):
    """Raised when the API returns an unexpected response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolymarketNotFoundError(PolymarketAPIError):
    """Raised when a market cannot be found for the given slug."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"未找到 slug 为 '{slug}' 的市场。", status_code=404)
        self.slug = slug


class OKXError(Exception):
    """Base exception for OKX client errors."""


class OKXConfigError(OKXError):
    """Raised when required OKX configuration is missing or invalid."""

