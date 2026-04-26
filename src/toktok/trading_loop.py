from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Callable, cast

from py_clob_client_v2 import AssetType, BalanceAllowanceParams, ClobClient, OpenOrderParams, OrderArgs, OrderType

CLOB_DEFAULT_HOST = "https://clob.polymarket.com"
CLOB_DEFAULT_CHAIN_ID = 137
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"


def _default_print_fn(message: str) -> None:
    print(message)


def _green(text: str) -> str:
    return f"{ANSI_GREEN}{text}{ANSI_RESET}"


def _yellow(text: str) -> str:
    return f"{ANSI_YELLOW}{text}{ANSI_RESET}"


def _red(text: str) -> str:
    return f"{ANSI_RED}{text}{ANSI_RESET}"


@dataclass
class TradingLoopConfig:
    private_key: str
    poll_interval_seconds: float = 10.0
    buy_price: float = 0.2
    buy_usd_amount: float = 1.0
    clob_host: str = CLOB_DEFAULT_HOST
    chain_id: int = CLOB_DEFAULT_CHAIN_ID
    signature_type: int | None = None
    funder: str | None = None


def create_authenticated_clob_client(config: TradingLoopConfig) -> ClobClient:
    client = ClobClient(
        host=config.clob_host,
        chain_id=config.chain_id,
        key=config.private_key,
        signature_type=config.signature_type,
        funder=config.funder,
    )
    client.set_api_creds(client.create_or_derive_api_key())
    return client


def run_live_trading_loop(
    polymarket_client: Any,
    config: TradingLoopConfig,
    *,
    print_fn: Callable[[str], None] = _default_print_fn,
) -> None:
    clob_client = create_authenticated_clob_client(config)
    run_trading_loop(polymarket_client, clob_client, config, print_fn=print_fn)


def run_trading_loop(
    polymarket_client: Any,
    clob_client: Any,
    config: TradingLoopConfig,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] | None = None,
    print_fn: Callable[[str], None] = _default_print_fn,
    max_cycles: int | None = None,
) -> None:
    tracked_filled_sizes: dict[str, float] = {}
    placed_slugs: set[str] = set()
    cycle = 0

    current_time_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def emit(message: str) -> None:
        print_fn(f"[{current_time_fn().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

    emit(
        "[STARTUP] "
        f"signer={_get_signer_address(clob_client) or 'unknown'} "
        f"funder={_get_funder_address(clob_client) or 'unknown'} "
        f"signature_type={_get_signature_type(clob_client) or 'unknown'}"
    )

    _emit_balance_allowance(clob_client, emit, context="before refresh")

    try:
        allowance_resp = clob_client.update_balance_allowance(
            BalanceAllowanceParams(asset_type=cast(Any, AssetType.COLLATERAL))
        )
        emit(f"[ALLOWANCE] collateral allowance refreshed: {allowance_resp}")
    except Exception as exc:
        emit(f"[WARN] allowance refresh failed: {exc}")

    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        try:
            utc_now = current_time_fn()
            slug = polymarket_client.build_btc_updown_5m_slug()
            down_token = polymarket_client.get_down_token_by_slug(slug)
            token_id = str(down_token["token_id"])

            open_orders = clob_client.get_open_orders(OpenOrderParams(asset_id=token_id))
            for open_order in open_orders:
                order_id = _extract_order_id(open_order)
                if order_id and order_id not in tracked_filled_sizes:
                    tracked_filled_sizes[order_id] = _extract_filled_size(open_order)

            if (
                slug not in placed_slugs
                and _is_in_first_four_minutes_of_5m_cycle(utc_now)
                and not _has_matching_buy_order(open_orders, token_id)
            ):
                size = config.buy_usd_amount / config.buy_price
                expiration = _get_cycle_expiration_after_minute_four(utc_now)
                response = clob_client.create_and_post_order(
                    OrderArgs(
                        token_id=token_id,
                        price=config.buy_price,
                        size=size,
                        side="BUY",
                        expiration=expiration,
                    ),
                    order_type=OrderType.GTD,
                )
                order_id = _extract_order_id(response)
                if order_id:
                    tracked_filled_sizes.setdefault(order_id, 0.0)
                placed_slugs.add(slug)
                emit(
                    _green(
                        f"[PLACE] slug={slug} token_id={token_id} price={config.buy_price} usd={config.buy_usd_amount} order_id={order_id or 'unknown'}"
                    )
                )
                _emit_balance_allowance(
                    clob_client,
                    emit,
                    context=f"after place slug={slug} order_id={order_id or 'unknown'}",
                )
            elif slug not in placed_slugs and not _is_in_first_four_minutes_of_5m_cycle(utc_now):
                emit(f"[SKIP] slug={slug} outside first 4 minutes of current 5m cycle")

            for order_id in list(tracked_filled_sizes.keys()):
                order = clob_client.get_order(order_id)
                status = str(_get_first(order, "status", "orderStatus", "order_status", default="UNKNOWN"))
                emit(f"[STATUS] order_id={order_id} status={status} filled={_extract_filled_size(order)}")
                new_filled_size = _extract_filled_size(order)
                old_filled_size = tracked_filled_sizes.get(order_id, 0.0)
                normalized_status = status.lower()

                if "matched" in normalized_status:
                    emit(_red(f"[MATCHED] order_id={order_id} matched={new_filled_size} detail={order}"))

                if new_filled_size > old_filled_size:
                    emit(_yellow(f"[FILLED] order_id={order_id} filled={new_filled_size} detail={order}"))
                tracked_filled_sizes[order_id] = max(old_filled_size, new_filled_size)

                if any(keyword in normalized_status for keyword in ("matched", "canceled", "cancelled", "expired", "rejected")):
                    tracked_filled_sizes.pop(order_id, None)
        except Exception as exc:
            emit(f"[WARN] trading loop cycle failed: {exc}")

        sleep_fn(config.poll_interval_seconds)


def _has_matching_buy_order(open_orders: list[dict[str, Any]], token_id: str) -> bool:
    for order in open_orders:
        order_token_id = str(_get_first(order, "asset_id", "assetId", "token_id", "tokenId", default=""))
        side = str(_get_first(order, "side", default="")).upper()
        if order_token_id == token_id and side == "BUY":
            return True
    return False


def _emit_balance_allowance(clob_client: Any, emit: Callable[[str], None], *, context: str) -> None:
    try:
        balance_allowance = clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=cast(Any, AssetType.COLLATERAL))
        )
        balance = int(balance_allowance.get('balance')) / 1000000
        emit(f"[BALANCE] collateral balance_allowance {balance} : {context}")
    except Exception as exc:
        emit(f"[WARN] balance fetch failed ({context}): {exc}")


def _get_signer_address(clob_client: Any) -> str | None:
    get_address = getattr(clob_client, "get_address", None)
    if callable(get_address):
        try:
            return str(get_address())
        except Exception:
            return None

    signer = getattr(clob_client, "signer", None)
    address_fn = getattr(signer, "address", None)
    if callable(address_fn):
        try:
            return str(address_fn())
        except Exception:
            return None

    return None


def _get_funder_address(clob_client: Any) -> str | None:
    builder = getattr(clob_client, "builder", None)
    funder = getattr(builder, "funder", None)
    return str(funder) if funder else None


def _get_signature_type(clob_client: Any) -> str | None:
    builder = getattr(clob_client, "builder", None)
    signature_type = getattr(builder, "signature_type", None)
    return str(signature_type) if signature_type is not None else None


def _is_in_first_four_minutes_of_5m_cycle(utc_now: datetime) -> bool:
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=timezone.utc)
    return int(utc_now.timestamp()) % 300 < 240


def _get_cycle_expiration_after_minute_four(utc_now: datetime) -> int:
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=timezone.utc)
    current_ts = int(utc_now.timestamp())
    cycle_start = (current_ts // 300) * 300
    return cycle_start + 310


def _extract_order_id(payload: Any) -> str | None:
    candidate = _get_first(payload, "orderID", "orderId", "id")
    if candidate is None:
        return None
    text = str(candidate).strip()
    return text or None


def _extract_filled_size(payload: Any) -> float:
    value = _get_first(
        payload,
        "filled_size",
        "filledSize",
        "size_matched",
        "sizeMatched",
        "matched_size",
        "matchedSize",
        default=0.0,
    )
    return _to_float(value)


def _get_first(payload: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(payload, dict):
        return default
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

