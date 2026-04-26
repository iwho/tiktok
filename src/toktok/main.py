from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from toktok.client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, PolymarketClient
from toktok.exceptions import PolymarketError
from toktok.trading_loop import TradingLoopConfig, run_live_trading_loop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 Polymarket Gamma API 按 slug 获取市场数据。",
    )
    parser.add_argument("slug", nargs="?", help="要查询的市场 slug。")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Polymarket API 基础地址，默认：{DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"请求超时时间（秒），默认：{DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="输出紧凑 JSON，而不是格式化 JSON。",
    )
    parser.add_argument(
        "--trade-loop",
        action="store_true",
        help="启动交易循环：自动计算 BTC 5m slug 并按固定参数下单买 DOWN。",
    )
    parser.add_argument(
        "--clob-host",
        default=os.getenv("TOKTOK_CLOB_HOST", "https://clob.polymarket.com"),
        help="CLOB API 地址，默认：https://clob.polymarket.com",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.getenv("TOKTOK_CHAIN_ID", "137")),
        help="链 ID，默认：137",
    )
    parser.add_argument(
        "--private-key",
        default=os.getenv("TOKTOK_PRIVATE_KEY"),
        help="钱包私钥（也可通过环境变量 TOKTOK_PRIVATE_KEY 提供）。",
    )
    parser.add_argument(
        "--signature-type",
        type=int,
        default=int(os.getenv("TOKTOK_SIGNATURE_TYPE")) if os.getenv("TOKTOK_SIGNATURE_TYPE") else 1,
        help="可选签名类型（代理钱包场景可用）。",
    )
    parser.add_argument(
        "--funder",
        default=os.getenv("TOKTOK_FUNDER"),
        help="可选 funder 地址（代理钱包场景可用）。",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("TOKTOK_POLL_INTERVAL", "5")),
        help="轮询间隔秒数，默认：5",
    )
    parser.add_argument(
        "--buy-price",
        type=float,
        default=float(os.getenv("TOKTOK_BUY_PRICE", "0.2")),
        help="买入 DOWN 的限价，默认：0.2",
    )
    parser.add_argument(
        "--buy-usd",
        type=float,
        default=float(os.getenv("TOKTOK_BUY_USD", "1.0")),
        help="每次下单总美元金额，默认：1.0",
    )
    return parser


def format_payload(payload: object, *, compact: bool = False) -> str:
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.trade_loop:
        if not args.private_key:
            print("错误：交易循环模式需要 --private-key 或 TOKTOK_PRIVATE_KEY。", file=sys.stderr)
            return 1

        if args.buy_price <= 0 or args.buy_usd <= 0 or args.poll_interval <= 0:
            print("错误：--buy-price、--buy-usd、--poll-interval 必须为正数。", file=sys.stderr)
            return 1

        config = TradingLoopConfig(
            private_key=args.private_key,
            poll_interval_seconds=args.poll_interval,
            buy_price=args.buy_price,
            buy_usd_amount=args.buy_usd,
            clob_host=args.clob_host,
            chain_id=args.chain_id,
            signature_type=args.signature_type,
            funder=args.funder,
        )

        try:
            with PolymarketClient(base_url=args.base_url, timeout=args.timeout) as client:
                run_live_trading_loop(client, config)
        except KeyboardInterrupt:
            print("交易循环已停止。", file=sys.stderr)
            return 130
        except (ValueError, PolymarketError) as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"错误：交易循环启动失败：{exc}", file=sys.stderr)
            return 1
        return 0

    if not args.slug:
        parser.print_help(sys.stderr)
        return 2

    try:
        with PolymarketClient(base_url=args.base_url, timeout=args.timeout) as client:
            payload = client.get_market_by_slug(args.slug)
    except (ValueError, PolymarketError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(format_payload(payload, compact=args.compact))
    return 0

