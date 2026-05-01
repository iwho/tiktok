from pathlib import Path
import sys
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toktok.trading_loop import TradingLoopConfig, run_trading_loop


class FakePolymarketClient:
    def build_btc_updown_5m_slug(self) -> str:
        return "btc-updown-5m-1777205100"

    def get_down_token_by_slug(self, slug: str) -> dict[str, str]:
        assert slug == "btc-updown-5m-1777205100"
        return {
            "condition_id": "0xabc",
            "token_id": "token-1",
            "outcome": "No",
        }


class FakeClobClient:
    def __init__(self) -> None:
        self.placed_orders: list[dict[str, object]] = []
        self.allowance_updated = 0
        self.balance_fetches = 0
        self.builder = type("Builder", (), {"funder": "0xfunder", "signature_type": 1})()

    def get_address(self):
        return "0xsigner"

    def get_balance_allowance(self, params=None):
        self.balance_fetches += 1
        return {"balance": "0", "allowance": "0"}

    def update_balance_allowance(self, params=None):
        self.allowance_updated += 1
        return {"status": "ok"}

    def get_open_orders(self, params):
        return []

    def create_and_post_order(self, order_args, order_type=None):
        self.placed_orders.append(
            {
                "token_id": order_args.token_id,
                "price": order_args.price,
                "size": order_args.size,
                "side": order_args.side,
                "expiration": order_args.expiration,
                "order_type": order_type,
            }
        )
        return {"orderID": "order-1"}

    def get_order(self, order_id: str):
        assert order_id == "order-1"
        return {
            "id": order_id,
            "status": "MATCHED",
            "filled_size": "5",
        }


class FakeOkxClient:
    def __init__(self) -> None:
        self.place_put_spread_smart_calls: list[dict[str, object]] = []

    def place_put_spread_smart(self, **kwargs):
        self.place_put_spread_smart_calls.append(kwargs)
        return {
            "mode": "spread",
            "sprd_id": "SPRD-BTC-PUT-1",
            "sell_inst_id": "BTC-USD-260428-78000-P",
            "buy_inst_id": "BTC-USD-260428-76000-P",
            "spread": {"code": "0", "data": [{"ordId": "okx-sprd-1"}]},
        }


def test_run_trading_loop_places_buy_down_order_and_prints_fill() -> None:
    config = TradingLoopConfig(private_key="dummy", buy_price=0.2, buy_usd_amount=1.0, poll_interval_seconds=0.0)
    fake_pm_client = FakePolymarketClient()
    fake_clob_client = FakeClobClient()
    outputs: list[str] = []

    run_trading_loop(
        fake_pm_client,
        fake_clob_client,
        config,
        max_cycles=1,
        sleep_fn=lambda _: None,
        now_fn=lambda: datetime(2026, 4, 26, 12, 2, 0, tzinfo=timezone.utc),
        print_fn=outputs.append,
    )

    assert len(fake_clob_client.placed_orders) == 1
    assert fake_clob_client.allowance_updated == 1
    assert fake_clob_client.balance_fetches == 2
    assert fake_clob_client.placed_orders[0]["side"] == "BUY"
    assert fake_clob_client.placed_orders[0]["price"] == 0.2
    assert fake_clob_client.placed_orders[0]["size"] == 5.0
    assert fake_clob_client.placed_orders[0]["expiration"] == 1777205110
    assert str(fake_clob_client.placed_orders[0]["order_type"]) == "GTD"
    assert all(line.startswith("[2026-04-26 20:02:00]") for line in outputs)
    assert any("[STARTUP] signer=0xsigner funder=0xfunder signature_type=1" in line for line in outputs)
    balance_lines = [line for line in outputs if "[BALANCE]" in line]
    assert len(balance_lines) >= 2
    assert any("before refresh" in line for line in balance_lines)
    assert any("after place" in line for line in balance_lines)
    assert any("[ALLOWANCE]" in line for line in outputs)
    assert any("[PLACE]" in line for line in outputs)
    assert any("[STATUS] order_id=order-1 status=MATCHED filled=5.0" in line for line in outputs)
    assert any("[FILLED]" in line for line in outputs)


def test_run_trading_loop_places_sell_put_hedge_after_down_matched() -> None:
    class DelayedMatchClobClient(FakeClobClient):
        def __init__(self) -> None:
            super().__init__()
            self._status_call_count = 0

        def get_order(self, order_id: str):
            assert order_id == "order-1"
            self._status_call_count += 1
            if self._status_call_count == 1:
                return {
                    "id": order_id,
                    "status": "LIVE",
                    "filled_size": "0",
                }
            return {
                "id": order_id,
                "status": "MATCHED",
                "filled_size": "5",
            }

    config = TradingLoopConfig(
        private_key="dummy",
        buy_price=0.2,
        buy_usd_amount=1.0,
        poll_interval_seconds=0.0,
        okx_delta_hedge_enabled=True,
        okx_sell_put_size=2,
    )
    fake_pm_client = FakePolymarketClient()
    fake_clob_client = DelayedMatchClobClient()
    fake_okx_client = FakeOkxClient()
    outputs: list[str] = []

    run_trading_loop(
        fake_pm_client,
        fake_clob_client,
        config,
        okx_client=fake_okx_client,
        max_cycles=2,
        sleep_fn=lambda _: None,
        now_fn=lambda: datetime(2026, 4, 26, 12, 2, 0, tzinfo=timezone.utc),
        print_fn=outputs.append,
    )

    assert len(fake_okx_client.place_put_spread_smart_calls) == 1
    call = fake_okx_client.place_put_spread_smart_calls[0]
    assert call["td_mode"] == "cross"
    assert call["ord_type"] == "limit"
    assert call["sz"] == 2
    assert call["sell_cl_ord_id"] == "order-1-sell"
    assert call["buy_cl_ord_id"] == "order-1-buy"
    assert call["spread_cl_ord_id"] == "order-1-sprd"
    assert call["now"] == datetime(2026, 4, 26, 12, 2, 0, tzinfo=timezone.utc)
    assert any("[STATUS] order_id=order-1 status=LIVE" in line for line in outputs)
    assert any("[MATCHED] order_id=order-1" in line for line in outputs)
    assert any("[OKX-HEDGE] place_put_spread_smart params:" in line for line in outputs)
    assert any("[OKX-HEDGE] placed put-spread mode=spread" in line for line in outputs)


def test_run_trading_loop_skips_order_after_first_4_minutes() -> None:
    config = TradingLoopConfig(private_key="dummy", buy_price=0.2, buy_usd_amount=1.0, poll_interval_seconds=0.0)
    fake_pm_client = FakePolymarketClient()
    fake_clob_client = FakeClobClient()
    outputs: list[str] = []

    run_trading_loop(
        fake_pm_client,
        fake_clob_client,
        config,
        max_cycles=1,
        sleep_fn=lambda _: None,
        now_fn=lambda: datetime(2026, 4, 26, 12, 4, 30, tzinfo=timezone.utc),
        print_fn=outputs.append,
    )

    assert fake_clob_client.allowance_updated == 1
    assert fake_clob_client.balance_fetches == 1
    assert len(fake_clob_client.placed_orders) == 0
    assert all(line.startswith("[2026-04-26 20:04:30]") for line in outputs)
    assert any("[SKIP]" in line for line in outputs)


def test_run_trading_loop_prints_existing_order_status_every_cycle() -> None:
    class ExistingOrderClobClient(FakeClobClient):
        def get_open_orders(self, params):
            return [{"id": "existing-order", "asset_id": "token-1", "side": "BUY", "price": "0.2"}]

        def get_order(self, order_id: str):
            assert order_id == "existing-order"
            return {
                "id": order_id,
                "status": "LIVE",
                "filled_size": "0",
            }

    config = TradingLoopConfig(private_key="dummy", buy_price=0.2, buy_usd_amount=1.0, poll_interval_seconds=0.0)
    fake_pm_client = FakePolymarketClient()
    fake_clob_client = ExistingOrderClobClient()
    timestamps = iter(
        [
            datetime(2026, 4, 26, 12, 2, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 2, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 3, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 4, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 5, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 6, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 7, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 8, tzinfo=timezone.utc),
            datetime(2026, 4, 26, 12, 2, 9, tzinfo=timezone.utc),
        ]
    )
    outputs: list[str] = []

    run_trading_loop(
        fake_pm_client,
        fake_clob_client,
        config,
        max_cycles=2,
        sleep_fn=lambda _: None,
        now_fn=lambda: next(timestamps),
        print_fn=outputs.append,
    )

    status_lines = [line for line in outputs if "[STATUS] order_id=existing-order status=LIVE filled=0.0" in line]
    assert len(status_lines) == 2


