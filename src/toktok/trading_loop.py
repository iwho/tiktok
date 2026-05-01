from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Callable, cast

from py_clob_client_v2 import AssetType, BalanceAllowanceParams, ClobClient, OpenOrderParams, OrderArgs, OrderType
from toktok.okx_client import OkxClient

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
    okx_delta_hedge_enabled: bool = True
    okx_sell_put_size: int = 1
    okx_td_mode: str = "cross"
    okx_order_type: str = "limit"


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
    okx_trade_client = _create_okx_trade_client_from_env(print_fn=print_fn)
    run_trading_loop(polymarket_client, clob_client, config, print_fn=print_fn, okx_client=okx_trade_client)


def run_trading_loop(
    polymarket_client: Any,
    clob_client: Any,
    config: TradingLoopConfig,
    *,
    okx_client: OkxClient | Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] | None = None,
    print_fn: Callable[[str], None] = _default_print_fn,
    max_cycles: int | None = None,
) -> None:
    tracked_filled_sizes: dict[str, float] = {}
    placed_slugs: set[str] = set()
    placed_down_order_ids: set[str] = set()
    hedged_down_order_ids: set[str] = set()
    order_slug_by_id: dict[str, str] = {}
    cycle = 0

    current_time_fn = now_fn or (lambda: datetime.now(timezone.utc))
    log_tz = timezone(timedelta(hours=8))

    def emit(message: str) -> None:
        log_time = current_time_fn()
        if log_time.tzinfo is None:
            log_time = log_time.replace(tzinfo=timezone.utc)
        log_time = log_time.astimezone(log_tz)
        print_fn(f"[{log_time.strftime('%Y-%m-%d %H:%M:%S')}] {message}")

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
                    placed_down_order_ids.add(order_id)
                    order_slug_by_id[order_id] = slug
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

                    # 只在“本策略自己下的 DOWN 单”成交后做一次 put 对冲。
                    if order_id in placed_down_order_ids and order_id not in hedged_down_order_ids:
                        _place_sell_put_delta_hedge(
                            okx_client,
                            config,
                            emit=emit,
                            now=utc_now,
                            trigger_slug=order_slug_by_id.get(order_id, slug),
                            down_order_id=order_id,
                        )
                        hedged_down_order_ids.add(order_id)

                if new_filled_size > old_filled_size:
                    emit(_yellow(f"[FILLED] order_id={order_id} filled={new_filled_size} detail={order}"))
                tracked_filled_sizes[order_id] = max(old_filled_size, new_filled_size)

                if any(keyword in normalized_status for keyword in ("matched", "canceled", "cancelled", "expired", "rejected", "invalid")):
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


def _create_okx_trade_client_from_env(*, print_fn: Callable[[str], None]) -> OkxClient | None:
    try:
        return OkxClient.from_env(enable_trade=True)
    except Exception as exc:
        print_fn(f"[OKX] 未启用 delta 对冲（无法初始化交易客户端）：{exc}")
        return None


def _place_sell_put_delta_hedge(
    okx_client: Any,
    config: TradingLoopConfig,
    *,
    emit: Callable[[str], None],
    now: datetime,
    trigger_slug: str,
    down_order_id: str | None,
) -> None:
    # 对冲是可选能力：未配置或主动关闭时，主交易流程继续执行。
    if not config.okx_delta_hedge_enabled:
        emit(f"[OKX-HEDGE] skip disabled slug={trigger_slug}")
        return

    if okx_client is None:
        emit(f"[OKX-HEDGE] skip no-okx-client slug={trigger_slug}")
        return

    try:
        # 优先使用 DOWN 的订单号作为 OKX cl_ord_id 的基础前缀，便于两边订单追踪关联。
        base_cl_ord_id = (down_order_id or "").strip() or _build_okx_hedge_client_order_id(now)
        sell_cl_ord_id = _build_okx_child_cl_ord_id(base_cl_ord_id, suffix="sell")
        buy_cl_ord_id = _build_okx_child_cl_ord_id(base_cl_ord_id, suffix="buy")
        spread_cl_ord_id = _build_okx_child_cl_ord_id(base_cl_ord_id, suffix="sprd")

        sz_int = int(config.okx_sell_put_size)
        emit(
            "[OKX-HEDGE] place_put_spread_smart params: "
            f"td_mode={config.okx_td_mode} ord_type={config.okx_order_type} sz={sz_int} "
            f"sell_cl_ord_id={sell_cl_ord_id} buy_cl_ord_id={buy_cl_ord_id} spread_cl_ord_id={spread_cl_ord_id}"
        )

        hedge_resp = okx_client.place_put_spread_smart(
            td_mode=config.okx_td_mode,
            ord_type=config.okx_order_type,
            sz=sz_int,
            sell_cl_ord_id=sell_cl_ord_id,
            buy_cl_ord_id=buy_cl_ord_id,
            spread_cl_ord_id=spread_cl_ord_id,
            now=now,
        )

        mode = str(_get_first(hedge_resp, "mode", default="unknown")).lower()
        sell_inst_id = str(_get_first(hedge_resp, "sell_inst_id", default=""))
        buy_inst_id = str(_get_first(hedge_resp, "buy_inst_id", default=""))

        if mode == "spread":
            sprd_id = _get_first(hedge_resp, "sprd_id", default="")
            emit(
                _green(
                    "[OKX-HEDGE] placed put-spread mode=spread "
                    f"sprd_id={sprd_id} sell_inst_id={sell_inst_id} buy_inst_id={buy_inst_id} "
                    f"slug={trigger_slug} resp={hedge_resp}"
                )
            )
        elif mode == "leg":
            emit(
                _yellow(
                    "[OKX-HEDGE] placed put-spread mode=leg "
                    f"sell_inst_id={sell_inst_id} buy_inst_id={buy_inst_id} "
                    f"slug={trigger_slug} resp={hedge_resp}"
                )
            )
        else:
            emit(f"[OKX-HEDGE] placed put-spread mode={mode} slug={trigger_slug} resp={hedge_resp}")
    except Exception as exc:
        emit(f"[WARN] okx hedge place failed slug={trigger_slug}: {exc}")


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


def _build_okx_hedge_client_order_id(now: datetime) -> str:
    # 用秒级时间戳生成幂等性较强的客户端订单号，便于排查日志。
    return f"toktok-{int(now.timestamp())}-sp"


def _build_okx_child_cl_ord_id(base_id: str, *, suffix: str) -> str:
    # OKX cl_ord_id 限制最多 32 位，预留连接符和后缀长度。
    clipped_base = (base_id or "toktok").strip() or "toktok"
    max_base_len = max(1, 32 - len(suffix))
    return f"{clipped_base[:max_base_len]}{suffix}"


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



