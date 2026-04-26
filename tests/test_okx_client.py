from pathlib import Path
import sys
from datetime import datetime

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import toktok.okx_client as okx_client_module
from toktok.exceptions import OKXConfigError, OKXError
from toktok.okx_client import OkxClient


class FakePublicAPI:
    def __init__(self, *, flag: str) -> None:
        self.flag = flag

    def get_instruments(self, **kwargs):
        return {"code": "0", "data": [{"api": "public", "method": "get_instruments", "kwargs": kwargs}]}

    def get_opt_summary(self, **kwargs):
        return {"code": "0", "data": [{"api": "public", "method": "get_opt_summary", "kwargs": kwargs}]}

    def get_mark_price(self, **kwargs):
        return {"code": "0", "data": [{"api": "public", "method": "get_mark_price", "kwargs": kwargs}]}


class FakeMarketAPI:
    def __init__(self, *, flag: str) -> None:
        self.flag = flag

    def get_index_tickers(self, **kwargs):
        return {"code": "0", "data": [{"api": "market", "method": "get_index_tickers", "kwargs": kwargs}]}


class FakeTradeAPI:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, use_server_time: bool, flag: str) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.use_server_time = use_server_time
        self.flag = flag

    def place_order(self, **kwargs):
        return {"code": "0", "data": [{"api": "trade", "method": "place_order", "kwargs": kwargs}]}


class FakeModule:
    def __init__(self, **attrs) -> None:
        self.__dict__.update(attrs)


def _patch_okx_modules(monkeypatch) -> None:
    def fake_import_module(name: str):
        if name == "okx.PublicData":
            return FakeModule(PublicAPI=FakePublicAPI)
        if name == "okx.MarketData":
            return FakeModule(MarketAPI=FakeMarketAPI)
        if name == "okx.Trade":
            return FakeModule(TradeAPI=FakeTradeAPI)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(okx_client_module.importlib, "import_module", fake_import_module)


def test_okx_client_public_methods_map_to_python_okx(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)

    client = OkxClient(flag="1")

    instruments = client.get_instruments(inst_type="OPTION", inst_family="BTC-USD")
    assert instruments["data"][0]["kwargs"] == {"instType": "OPTION", "instFamily": "BTC-USD"}

    summary = client.get_opt_summary(inst_family="BTC-USD", exp_time="260427")
    assert summary["data"][0]["kwargs"] == {"instFamily": "BTC-USD", "expTime": "260427"}

    mark_price = client.get_mark_price(inst_type="OPTION", inst_family="BTC-USD", inst_id="BTC-USD-260427-77750-C")
    assert mark_price["data"][0]["kwargs"] == {
        "instType": "OPTION",
        "instFamily": "BTC-USD",
        "instId": "BTC-USD-260427-77750-C",
    }

    index_tickers = client.get_index_tickers(inst_id="BTC-USD")
    assert index_tickers["data"][0]["kwargs"] == {"instId": "BTC-USD"}


def test_okx_client_place_order_requires_credentials(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)

    client = OkxClient(flag="0")

    with pytest.raises(OKXConfigError):
        client.place_order(
            inst_id="BTC-USD-260427-77750-P",
            td_mode="cross",
            cl_ord_id="b15",
            side="sell",
            ord_type="limit",
            px="0.0100",
            sz="1",
        )


def test_okx_client_from_env_builds_trade_api(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    monkeypatch.setenv("OKX_API_KEY", "k")
    monkeypatch.setenv("OKX_SECRET_KEY", "s")
    monkeypatch.setenv("OKX_PASS_PHRASE", "p")
    monkeypatch.setenv("OKX_FLAG", "1")

    client = OkxClient.from_env()
    result = client.place_order(
        inst_id="BTC-USD-260427-77750-P",
        td_mode="cross",
        cl_ord_id="b15",
        side="sell",
        ord_type="limit",
        px="0.0100",
        sz="1",
    )

    assert result["data"][0]["kwargs"] == {
        "instId": "BTC-USD-260427-77750-P",
        "tdMode": "cross",
        "clOrdId": "b15",
        "side": "sell",
        "ordType": "limit",
        "px": "0.0100",
        "sz": "1",
    }


def test_okx_client_raises_when_python_okx_missing(monkeypatch) -> None:
    monkeypatch.setattr(okx_client_module.importlib, "import_module", lambda _: (_ for _ in ()).throw(ModuleNotFoundError("okx")))

    with pytest.raises(OKXError):
        OkxClient()


def test_get_latest_btc_option_put_uses_t_plus_2_before_or_at_16(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    client = OkxClient(flag="0")

    monkeypatch.setattr(client, "get_index_tickers", lambda *, inst_id="BTC-USD": {"code": "0", "data": [{"idxPx": "77949.8"}]})
    monkeypatch.setattr(
        client,
        "get_instruments",
        lambda *, inst_type="OPTION", inst_family="BTC-USD": {
            "code": "0",
            "data": [
                {"instId": "BTC-USD-260428-77750-P"},
                {"instId": "BTC-USD-260428-78000-P"},
                {"instId": "BTC-USD-260427-77750-P"},
            ],
        },
    )
    monkeypatch.setattr(
        client,
        "get_mark_price",
        lambda *, inst_type="OPTION", inst_family="BTC-USD", inst_id: {
            "code": "0",
            "data": [{"instId": inst_id, "instType": "OPTION", "markPx": "0.0061", "ts": "1"}],
        },
    )

    result = client.get_latest_btc_option_put(now=datetime(2026, 4, 26, 16, 0, 0))
    assert result["instId"] == "BTC-USD-260428-78000-P"
    assert result["markPx"] == "0.0061"


def test_get_latest_btc_option_put_uses_t_plus_1_after_16(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    client = OkxClient(flag="0")

    monkeypatch.setattr(client, "get_index_tickers", lambda *, inst_id="BTC-USD": {"code": "0", "data": [{"idxPx": "77949.8"}]})
    monkeypatch.setattr(
        client,
        "get_instruments",
        lambda *, inst_type="OPTION", inst_family="BTC-USD": {
            "code": "0",
            "data": [
                {"instId": "BTC-USD-260427-77750-P"},
                {"instId": "BTC-USD-260427-78000-P"},
                {"instId": "BTC-USD-260428-78000-P"},
            ],
        },
    )
    monkeypatch.setattr(
        client,
        "get_mark_price",
        lambda *, inst_type="OPTION", inst_family="BTC-USD", inst_id: {
            "code": "0",
            "data": [{"instId": inst_id, "instType": "OPTION", "markPx": "0.0099", "ts": "2"}],
        },
    )

    result = client.get_latest_btc_option_put(now=datetime(2026, 4, 26, 17, 0, 0))
    assert result["instId"] == "BTC-USD-260427-78000-P"
    assert result["markPx"] == "0.0099"


def test_get_latest_btc_option_call_uses_t_plus_2_before_or_at_16(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    client = OkxClient(flag="0")

    monkeypatch.setattr(client, "get_index_tickers", lambda *, inst_id="BTC-USD": {"code": "0", "data": [{"idxPx": "77949.8"}]})
    monkeypatch.setattr(
        client,
        "get_instruments",
        lambda *, inst_type="OPTION", inst_family="BTC-USD": {
            "code": "0",
            "data": [
                {"instId": "BTC-USD-260428-77750-C"},
                {"instId": "BTC-USD-260428-78000-C"},
                {"instId": "BTC-USD-260427-78000-C"},
            ],
        },
    )
    monkeypatch.setattr(
        client,
        "get_mark_price",
        lambda *, inst_type="OPTION", inst_family="BTC-USD", inst_id: {
            "code": "0",
            "data": [{"instId": inst_id, "instType": "OPTION", "markPx": "0.0055", "ts": "3"}],
        },
    )

    result = client.get_latest_btc_option_call(now=datetime(2026, 4, 26, 16, 0, 0))
    assert result["instId"] == "BTC-USD-260428-78000-C"
    assert result["markPx"] == "0.0055"


def test_get_latest_btc_option_call_uses_t_plus_1_after_16(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    client = OkxClient(flag="0")

    monkeypatch.setattr(client, "get_index_tickers", lambda *, inst_id="BTC-USD": {"code": "0", "data": [{"idxPx": "77949.8"}]})
    monkeypatch.setattr(
        client,
        "get_instruments",
        lambda *, inst_type="OPTION", inst_family="BTC-USD": {
            "code": "0",
            "data": [
                {"instId": "BTC-USD-260427-77750-C"},
                {"instId": "BTC-USD-260427-78000-C"},
                {"instId": "BTC-USD-260428-77750-C"},
            ],
        },
    )
    monkeypatch.setattr(
        client,
        "get_mark_price",
        lambda *, inst_type="OPTION", inst_family="BTC-USD", inst_id: {
            "code": "0",
            "data": [{"instId": inst_id, "instType": "OPTION", "markPx": "0.0088", "ts": "4"}],
        },
    )

    result = client.get_latest_btc_option_call(now=datetime(2026, 4, 26, 17, 0, 0))
    assert result["instId"] == "BTC-USD-260427-78000-C"
    assert result["markPx"] == "0.0088"


def test_get_latest_btc_option_call_selects_strictly_above_index(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    client = OkxClient(flag="0")

    monkeypatch.setattr(client, "get_index_tickers", lambda *, inst_id="BTC-USD": {"code": "0", "data": [{"idxPx": "77949.8"}]})
    monkeypatch.setattr(
        client,
        "get_instruments",
        lambda *, inst_type="OPTION", inst_family="BTC-USD": {
            "code": "0",
            "data": [
                {"instId": "BTC-USD-260427-77900-C"},
                {"instId": "BTC-USD-260427-77949.8-C"},
                {"instId": "BTC-USD-260427-78000-C"},
            ],
        },
    )
    monkeypatch.setattr(
        client,
        "get_mark_price",
        lambda *, inst_type="OPTION", inst_family="BTC-USD", inst_id: {
            "code": "0",
            "data": [{"instId": inst_id, "instType": "OPTION", "markPx": "0.0077", "ts": "5"}],
        },
    )

    result = client.get_latest_btc_option_call(now=datetime(2026, 4, 26, 17, 0, 0))
    assert result["instId"] == "BTC-USD-260427-78000-C"


def test_get_latest_btc_option_call_raises_when_no_strike_above_index(monkeypatch) -> None:
    _patch_okx_modules(monkeypatch)
    client = OkxClient(flag="0")

    monkeypatch.setattr(client, "get_index_tickers", lambda *, inst_id="BTC-USD": {"code": "0", "data": [{"idxPx": "77949.8"}]})
    monkeypatch.setattr(
        client,
        "get_instruments",
        lambda *, inst_type="OPTION", inst_family="BTC-USD": {
            "code": "0",
            "data": [
                {"instId": "BTC-USD-260427-77750-C"},
                {"instId": "BTC-USD-260427-77949.8-C"},
            ],
        },
    )

    with pytest.raises(OKXError):
        client.get_latest_btc_option_call(now=datetime(2026, 4, 26, 17, 0, 0))


