from pathlib import Path
import io
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toktok.client import PolymarketClient
from toktok.exceptions import (
    PolymarketAPIError,
    PolymarketNotFoundError,
    PolymarketRequestError,
)


class FakeResponse:
    def __init__(self, payload: str, *, status: int = 200) -> None:
        self.status = status
        self._payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_get_market_by_slug_returns_market_payload() -> None:
    expected = {
        "slug": "fed-cut-2026",
        "question": "Will the Fed cut rates in 2026?",
    }

    def fake_urlopen(request, timeout):
        assert request.get_method() == "GET"
        assert request.full_url.endswith("/markets/slug/fed-cut-2026")
        return FakeResponse('{"slug": "fed-cut-2026", "question": "Will the Fed cut rates in 2026?"}')

    with patch("toktok.client.request.urlopen", side_effect=fake_urlopen):
        with PolymarketClient() as client:
            assert client.get_market_by_slug("fed-cut-2026") == expected


def test_get_market_by_slug_raises_not_found_error() -> None:
    http_error = HTTPError(
        url="https://gamma-api.polymarket.com/markets/slug/missing-market",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=io.BytesIO(b'{"detail": "missing"}'),
    )

    with patch("toktok.client.request.urlopen", side_effect=http_error):
        with PolymarketClient() as client:
            with pytest.raises(PolymarketNotFoundError):
                client.get_market_by_slug("missing-market")


def test_get_market_by_slug_raises_timeout_error() -> None:
    with patch("toktok.client.request.urlopen", side_effect=TimeoutError("timed out")):
        with PolymarketClient() as client:
            with pytest.raises(PolymarketRequestError):
                client.get_market_by_slug("slow-market")


def test_get_market_by_slug_rejects_blank_slug() -> None:
    with PolymarketClient() as client:
        with pytest.raises(ValueError):
            client.get_market_by_slug("   ")


def test_get_market_by_slug_rejects_non_object_json() -> None:
    with patch("toktok.client.request.urlopen", return_value=FakeResponse('[{"slug": "unexpected-list"}]')):
        with PolymarketClient() as client:
            with pytest.raises(PolymarketAPIError):
                client.get_market_by_slug("unexpected-list")


def test_get_market_by_slug_raises_request_error_for_url_error() -> None:
    with patch("toktok.client.request.urlopen", side_effect=URLError("dns failure")):
        with PolymarketClient() as client:
            with pytest.raises(PolymarketRequestError):
                client.get_market_by_slug("dns-failure")


def test_get_market_by_token_id_returns_market_payload() -> None:
    expected = {
        "market": "fed-cut-2026",
        "token_id": "12345",
    }

    def fake_urlopen(request, timeout):
        assert request.get_method() == "GET"
        assert request.full_url.endswith("/markets-by-token/12345")
        return FakeResponse('{"market": "fed-cut-2026", "token_id": "12345"}')

    with patch("toktok.client.request.urlopen", side_effect=fake_urlopen):
        with PolymarketClient() as client:
            assert client.get_market_by_token_id("12345") == expected


def test_get_market_by_token_id_rejects_blank_token_id() -> None:
    with PolymarketClient() as client:
        with pytest.raises(ValueError):
            client.get_market_by_token_id("   ")


def test_get_market_by_token_id_raises_not_found_error() -> None:
    http_error = HTTPError(
        url="https://clob.polymarket.com/markets-by-token/missing-token",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=io.BytesIO(b'{"detail": "missing"}'),
    )

    with patch("toktok.client.request.urlopen", side_effect=http_error):
        with PolymarketClient() as client:
            with pytest.raises(PolymarketNotFoundError):
                client.get_market_by_token_id("missing-token")


def test_get_down_token_by_slug_from_tokens_list() -> None:
    market_payload = {
        "conditionId": "0xabc",
        "tokens": [
            {"tokenId": "100", "outcome": "Yes"},
            {"tokenId": "101", "outcome": "No"},
        ],
    }

    with patch.object(PolymarketClient, "get_market_by_slug", return_value=market_payload):
        with PolymarketClient() as client:
            result = client.get_down_token_by_slug("fed-cut-2026")

    assert result == {"condition_id": "0xabc", "token_id": "101", "outcome": "No"}


def test_get_down_token_by_slug_from_outcomes_and_clob_token_ids() -> None:
    market_payload = {
        "conditionId": "0xdef",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["200", "201"]',
    }

    with patch.object(PolymarketClient, "get_market_by_slug", return_value=market_payload):
        with PolymarketClient() as client:
            result = client.get_down_token_by_slug("fed-cut-2026")

    assert result == {"condition_id": "0xdef", "token_id": "201", "outcome": "No"}


def test_get_down_token_by_slug_raises_api_error_when_no_outcome_missing() -> None:
    market_payload = {
        "conditionId": "0xghi",
        "tokens": [
            {"tokenId": "300", "outcome": "Yes"},
        ],
    }

    with patch.object(PolymarketClient, "get_market_by_slug", return_value=market_payload):
        with PolymarketClient() as client:
            with pytest.raises(PolymarketAPIError):
                client.get_down_token_by_slug("fed-cut-2026")


def test_build_btc_updown_5m_slug_uses_previous_5m_boundary() -> None:
    now = datetime(2026, 4, 26, 12, 7, 11, tzinfo=timezone.utc)

    with PolymarketClient() as client:
        slug = client.build_btc_updown_5m_slug(now=now)

    assert slug == "btc-updown-5m-1777205100"


def test_build_btc_updown_5m_slug_on_exact_boundary_still_uses_previous_boundary() -> None:
    now = datetime(2026, 4, 26, 12, 10, 0, tzinfo=timezone.utc)

    with PolymarketClient() as client:
        slug = client.build_btc_updown_5m_slug(now=now)

    assert slug == "btc-updown-5m-1777205100"


def test_get_down_token_by_latest_btc_updown_5m_slug_and_print() -> None:
    with PolymarketClient() as client:
        latest_slug = client.build_btc_updown_5m_slug()
        down_token = client.get_down_token_by_slug(latest_slug)
        print(f"latest_slug={latest_slug} down_token={down_token}")

    assert down_token != {}


