import json
import os
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toktok import OkxClient


def test_okx_public_instruments_print_only() -> None:
    pytest.importorskip("okx.PublicData")

    client = OkxClient(flag="0")

    # 获取交易产品基础信息，仅打印返回用于人工验证
    result = client.get_instruments(inst_type="OPTION", inst_family="BTC-USD")
    print(json.dumps(result, indent=2))

def test_okx_public_summary_print_only() -> None:
    pytest.importorskip("okx.PublicData")

    client = OkxClient(flag="0")

    # 获取交易产品基础信息，仅打印返回用于人工验证
    result = client.get_opt_summary(inst_family="BTC-USD", exp_time="260427")
    print(json.dumps(result, indent=2))

def test_okx_public_mark_price_print_only() -> None:
    pytest.importorskip("okx.PublicData")

    client = OkxClient(flag="0")

    # 获取交易产品基础信息，仅打印返回用于人工验证
    result = client.get_mark_price(
        inst_type="OPTION",
        inst_family="BTC-USD",
        inst_id="BTC-USD-260427-77750-C",
    )
    print(json.dumps(result, indent=2))

def test_okx_public_index_tickers_print_only() -> None:
    pytest.importorskip("okx.MarketData")

    client = OkxClient(flag="0")

    # 获取指数行情
    result = client.get_index_tickers(inst_id="BTC-USD")
    print(json.dumps(result, indent=2))


def test_okx_public_latest_btc_option_put_print_only() -> None:
    pytest.importorskip("okx.PublicData")
    pytest.importorskip("okx.MarketData")

    client = OkxClient(flag="0")

    # 获取最近 ATM put 期权的标记价格对象，仅打印返回用于人工验证
    result = client.get_latest_btc_option_put()
    print(json.dumps(result, indent=2))


def test_okx_public_latest_btc_option_call_print_only() -> None:
    pytest.importorskip("okx.PublicData")
    pytest.importorskip("okx.MarketData")

    client = OkxClient(flag="0")

    # 获取最近 ATM call 期权的标记价格对象，仅打印返回用于人工验证
    result = client.get_latest_btc_option_call()
    print(json.dumps(result, indent=2))

def test_okx_public_trade_order_print_only() -> None:
    pytest.importorskip("okx.Trade")

    required_env = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASS_PHRASE")
    missing = [name for name in required_env if not os.getenv(name)]
    if missing:
        pytest.skip(f"missing env for trade test: {', '.join(missing)}")

    client = OkxClient.from_env(flag="1")

    # 现货模式限价单 逐仓isolated 全仓cross
    result = client.place_order(
        inst_id="BTC-USD-260427-77750-P",
        td_mode="cross",
        cl_ord_id="b15",
        side="sell",
        ord_type="limit",
        px="0.0100",
        sz="1",
    )
    print(json.dumps(result, indent=2))
