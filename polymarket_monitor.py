#!/usr/bin/env python3
"""
Polymarket Order Monitor
监听 Poly Market 上某个账号的订单，有订单成交后输出订单的详细信息

Usage:
    python polymarket_monitor.py <address> [--interval SECONDS] [--mode poll|ws]

Examples:
    python polymarket_monitor.py 0xYourAddress
    python polymarket_monitor.py 0xYourAddress --interval 10
    python polymarket_monitor.py 0xYourAddress --mode ws
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional  # noqa: F401

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CLOB_API_BASE = "https://clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DEFAULT_POLL_INTERVAL = 5  # seconds

# Sentinel cursor value that Polymarket CLOB returns when there are no more pages.
CLOB_CURSOR_END = "LTE="


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, timeout: int = 15) -> dict:
    """Perform a GET request and return the JSON body (must be a dict)."""
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected API response type {type(data).__name__} from {url}")
    return data


def fetch_trades(address: str, next_cursor: str | None = None) -> dict:
    """
    Fetch trades (filled orders) for *address*.

    The CLOB returns trades where the address is either maker or taker.
    Pagination is driven by ``next_cursor``.
    """
    params: dict = {
        "maker_address": address,
        "limit": 100,
    }
    if next_cursor and next_cursor != CLOB_CURSOR_END:
        params["next_cursor"] = next_cursor
    return _get(f"{CLOB_API_BASE}/trades", params=params)


def fetch_market_info(condition_id: str) -> dict:
    """
    Fetch human-readable market information from the Gamma API.

    The Gamma API returns a list; we return the first element.
    Returns an empty dict on any error so the monitor keeps running.
    """
    try:
        resp = requests.get(
            f"{GAMMA_API_BASE}/markets",
            params={"conditionIds": condition_id},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        if isinstance(results, list) and results:
            return results[0]
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _ts_to_dt(ts_str: str) -> str:
    """Convert a Unix-timestamp string to a human-readable UTC string."""
    try:
        ts = float(ts_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except (ValueError, TypeError):
        return ts_str


def print_trade(trade: dict, market_info: dict | None = None) -> None:
    """Print a single trade's details in a readable format."""
    sep = "=" * 60
    print(sep)
    print("🔔  新成交订单 / New Filled Order")
    print(sep)

    condition_id = trade.get("market", "N/A")
    market_question = (market_info or {}).get("question", "")
    market_slug = (market_info or {}).get("slug", "")

    print(f"  交易 ID       : {trade.get('id', 'N/A')}")
    print(f"  市场 ID       : {condition_id}")
    if market_question:
        print(f"  市场问题      : {market_question}")
    if market_slug:
        print(f"  市场链接      : https://polymarket.com/event/{market_slug}")
    print(f"  结果 (Outcome): {trade.get('outcome', 'N/A')}")
    print(f"  方向 (Side)   : {trade.get('side', 'N/A')}")
    print(f"  成交价 (Price): {trade.get('price', 'N/A')}")
    print(f"  数量 (Size)   : {trade.get('size', 'N/A')}")
    print(f"  Maker 地址    : {trade.get('maker_address', 'N/A')}")
    # The CLOB API uses 'taker_address' in REST responses and 'owner' in some
    # WebSocket event payloads — fall back gracefully to whichever is present.
    taker = trade.get("taker_address") or trade.get("owner", "N/A")
    print(f"  Taker 地址    : {taker}")
    print(f"  交易角色      : {trade.get('trader_side', 'N/A')}")
    print(f"  状态 (Status) : {trade.get('status', 'N/A')}")
    print(f"  成交时间      : {_ts_to_dt(trade.get('match_time', ''))}")
    tx_hash = trade.get("transaction_hash", "")
    if tx_hash:
        print(f"  交易哈希      : {tx_hash}")
        print(f"  Polygonscan   : https://polygonscan.com/tx/{tx_hash}")
    print(sep)
    print()


# ---------------------------------------------------------------------------
# Polling monitor
# ---------------------------------------------------------------------------

class PollingMonitor:
    """Polls the CLOB REST API at a fixed interval to detect new fills."""

    def __init__(self, address: str, interval: int = DEFAULT_POLL_INTERVAL):
        self.address = address.lower()
        self.interval = interval
        # IDs of trades already seen so we only report genuinely new ones.
        self._seen_ids: set[str] = set()
        # Market info cache to avoid redundant Gamma API calls.
        self._market_cache: dict[str, dict] = {}

    def _get_market(self, condition_id: str) -> dict:
        if condition_id not in self._market_cache:
            self._market_cache[condition_id] = fetch_market_info(condition_id)
        return self._market_cache[condition_id]

    def _bootstrap(self) -> None:
        """
        Load existing trades on startup so we don't re-report old fills.
        """
        print(f"[{_now()}] 初始化，加载历史订单…")
        next_cursor: str | None = None
        while True:
            data = fetch_trades(self.address, next_cursor)
            for trade in data.get("data", []):
                self._seen_ids.add(trade["id"])
            next_cursor = data.get("next_cursor")
            if not next_cursor or next_cursor == CLOB_CURSOR_END:
                break
        print(
            f"[{_now()}] 初始化完成，已记录 {len(self._seen_ids)} 笔历史成交。"
        )
        print(f"[{_now()}] 开始监听新成交（每 {self.interval} 秒轮询一次）…\n")

    def run(self) -> None:
        self._bootstrap()
        while True:
            try:
                self._poll()
            except KeyboardInterrupt:
                print("\n监听已停止。")
                break
            except requests.RequestException as exc:
                print(f"[{_now()}] 请求错误: {exc}，{self.interval} 秒后重试…")
            time.sleep(self.interval)

    def _poll(self) -> None:
        data = fetch_trades(self.address)
        new_trades = [
            t for t in data.get("data", []) if t["id"] not in self._seen_ids
        ]
        for trade in new_trades:
            self._seen_ids.add(trade["id"])
            market_info = self._get_market(trade.get("market", ""))
            print_trade(trade, market_info)
        if not new_trades:
            print(f"[{_now()}] 无新成交。", end="\r")


# ---------------------------------------------------------------------------
# WebSocket monitor
# ---------------------------------------------------------------------------

class WebSocketMonitor:
    """
    Real-time monitor using the Polymarket WebSocket subscription API.

    Subscribes to the ``user`` channel for the given address.
    No API key is required for public trade data.
    """

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    def __init__(self, address: str):
        self.address = address
        self._market_cache: dict[str, dict] = {}

    def _get_market(self, condition_id: str) -> dict:
        if condition_id not in self._market_cache:
            self._market_cache[condition_id] = fetch_market_info(condition_id)
        return self._market_cache[condition_id]

    def run(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError:
            print(
                "WebSocket 依赖缺失，请运行: pip install websocket-client\n"
                "或改用轮询模式: --mode poll"
            )
            sys.exit(1)

        # Use a loop for reconnection instead of recursion to avoid stack overflow.
        while True:
            print(f"[{_now()}] 正在连接 Polymarket WebSocket…")
            should_reconnect = False

            def on_open(ws):
                print(f"[{_now()}] 已连接，订阅账户 {self.address} 的成交事件…")
                ws.send(
                    json.dumps(
                        {
                            "type": "subscribe",
                            "channel": "user",
                            "user": self.address,
                        }
                    )
                )

            def on_message(ws, message):
                try:
                    events = json.loads(message)
                    if not isinstance(events, list):
                        events = [events]
                    for event in events:
                        if event.get("event_type") == "trade":
                            market_info = self._get_market(event.get("market", ""))
                            print_trade(event, market_info)
                except (json.JSONDecodeError, AttributeError):
                    pass

            def on_error(ws, error):
                print(f"[{_now()}] WebSocket 错误: {error}")

            def on_close(ws, close_status_code, close_msg):
                nonlocal should_reconnect
                print(f"[{_now()}] WebSocket 连接关闭，5 秒后重连…")
                should_reconnect = True

            ws_app = websocket.WebSocketApp(
                self.WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            try:
                ws_app.run_forever(ping_interval=30, ping_timeout=10)
            except KeyboardInterrupt:
                print("\n监听已停止。")
                return

            if should_reconnect:
                time.sleep(5)
            else:
                break


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="监听 Polymarket 账户的成交订单并输出详细信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "address",
        help="要监听的 Polymarket 账户的以太坊地址 (0x…)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        metavar="SECONDS",
        help=f"轮询模式下的查询间隔（默认 {DEFAULT_POLL_INTERVAL} 秒，仅 --mode poll 时有效）",
    )
    parser.add_argument(
        "--mode",
        choices=["poll", "ws"],
        default="poll",
        help="监听模式：poll=轮询（默认），ws=WebSocket 实时推送",
    )

    args = parser.parse_args()

    # Validate Ethereum address: must be "0x" followed by exactly 40 hex characters.
    addr = args.address
    if (
        not addr.startswith("0x")
        or len(addr) != 42
        or not all(c in "0123456789abcdefABCDEF" for c in addr[2:])
    ):
        parser.error(
            "地址格式无效，请提供以 0x 开头、共 42 个字符的以太坊地址（例如：0xAbCd…1234）"
        )

    print(f"Polymarket Order Monitor")
    print(f"监听账户: {args.address}")
    print(f"模式    : {'WebSocket 实时' if args.mode == 'ws' else f'轮询（{args.interval}s）'}")
    print()

    if args.mode == "ws":
        WebSocketMonitor(address=args.address).run()
    else:
        PollingMonitor(address=args.address, interval=args.interval).run()


if __name__ == "__main__":
    main()
