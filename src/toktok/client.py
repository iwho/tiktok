from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib import error, request
from urllib.parse import quote

from toktok.exceptions import (
    PolymarketAPIError,
    PolymarketNotFoundError,
    PolymarketRequestError,
)

DEFAULT_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_TIMEOUT = 10.0


class PolymarketClient:
    """Small synchronous client for the Polymarket Gamma API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def __enter__(self) -> "PolymarketClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        return None

    def build_btc_updown_5m_slug(self, *, now: datetime | None = None) -> str:
        utc_now = now or datetime.now(timezone.utc)
        if utc_now.tzinfo is None:
            utc_now = utc_now.replace(tzinfo=timezone.utc)

        # "上一个" 5 分钟点位：即使恰好踩线，也回退到前一个 5 分钟点。
        current_ts = int(utc_now.timestamp())
        previous_5m_ts = ((current_ts - 1) // 300) * 300
        return f"btc-updown-5m-{previous_5m_ts}"

    def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        cleaned_slug = slug.strip()
        if not cleaned_slug:
            raise ValueError("slug 不能为空。")

        request_url = f"{self._base_url}/markets/slug/{quote(cleaned_slug, safe='')}"
        api_request = request.Request(
            request_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "toktok-polymarket-client",
            },
            method="GET",
        )

        try:
            with request.urlopen(api_request, timeout=self._timeout) as response:
                status_code = getattr(response, "status", None)
                if status_code is None:
                    status_code = response.getcode()
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            if exc.code == 404:
                raise PolymarketNotFoundError(cleaned_slug) from exc

            body_preview = exc.read().decode("utf-8", errors="replace").strip()
            message = f"Polymarket API 返回错误状态码 {exc.code}。"
            if body_preview:
                message = f"{message} 响应内容：{body_preview[:300]}"
            raise PolymarketAPIError(message, status_code=exc.code) from exc
        except TimeoutError as exc:
            raise PolymarketRequestError("请求 Polymarket 超时。") from exc
        except error.URLError as exc:
            raise PolymarketRequestError(f"请求 Polymarket 失败：{exc.reason}") from exc

        if status_code >= 400:
            raise PolymarketAPIError(f"Polymarket API 返回错误状态码 {status_code}。", status_code=status_code)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PolymarketAPIError("Polymarket API 返回了无效的 JSON。") from exc

        if not isinstance(payload, dict):
            raise PolymarketAPIError("Polymarket API 返回的数据不是对象类型。")

        return payload

    def get_market_by_token_id(self, token_id: str) -> dict[str, Any]:
        cleaned_token_id = token_id.strip()
        if not cleaned_token_id:
            raise ValueError("token_id 不能为空。")

        request_url = f"{self._base_url}/markets-by-token/{quote(cleaned_token_id, safe='')}"
        api_request = request.Request(
            request_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "toktok-polymarket-client",
            },
            method="GET",
        )

        try:
            with request.urlopen(api_request, timeout=self._timeout) as response:
                status_code = getattr(response, "status", None)
                if status_code is None:
                    status_code = response.getcode()
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            if exc.code == 404:
                raise PolymarketNotFoundError(cleaned_token_id) from exc

            body_preview = exc.read().decode("utf-8", errors="replace").strip()
            message = f"Polymarket API 返回错误状态码 {exc.code}。"
            if body_preview:
                message = f"{message} 响应内容：{body_preview[:300]}"
            raise PolymarketAPIError(message, status_code=exc.code) from exc
        except TimeoutError as exc:
            raise PolymarketRequestError("请求 Polymarket 超时。") from exc
        except error.URLError as exc:
            raise PolymarketRequestError(f"请求 Polymarket 失败：{exc.reason}") from exc

        if status_code >= 400:
            raise PolymarketAPIError(f"Polymarket API 返回错误状态码 {status_code}。", status_code=status_code)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PolymarketAPIError("Polymarket API 返回了无效的 JSON。") from exc

        if not isinstance(payload, dict):
            raise PolymarketAPIError("Polymarket API 返回的数据不是对象类型。")

        return payload

    def get_down_token_by_slug(self, slug: str) -> dict[str, str]:
        market = self.get_market_by_slug(slug)

        market_condition_id = market.get("condition_id") or market.get("conditionId")
        outcomes = json.loads(market.get("outcomes"))
        tokens = json.loads(market.get('clobTokenIds'))
        if not isinstance(tokens, list) or len(tokens) < 1:
            return {}
        if not isinstance(outcomes, list) or len(outcomes) < 1 or outcomes[1] != 'Down':
            return {}

        down_token_id = tokens[1]
        return {
            "condition_id": str(market_condition_id),
            "token_id": str(down_token_id),
            "outcome": "Down",
        }



