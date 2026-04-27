from __future__ import annotations

import importlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from toktok.exceptions import OKXConfigError, OKXError


class OkxClient:
    """Thin wrapper around python-okx APIs used by this project."""

    def __init__(
        self,
        *,
        flag: str = "0",
        api_key: str | None = None,
        secret_key: str | None = None,
        passphrase: str | None = None,
        use_server_time: bool = False,
    ) -> None:
        self._flag = flag
        self._public_data_module = self._load_module("okx.PublicData")
        self._market_data_module = self._load_module("okx.MarketData")
        self._trade_module = self._load_module("okx.Trade")

        self._public_api = self._public_data_module.PublicAPI(flag=flag)
        self._market_api = self._market_data_module.MarketAPI(flag=flag)
        self._trade_api = None

        if any(value is not None for value in (api_key, secret_key, passphrase)):
            if not all(value is not None for value in (api_key, secret_key, passphrase)):
                raise OKXConfigError("初始化交易 API 需要同时提供 api_key、secret_key、passphrase。")
            self._trade_api = self._trade_module.TradeAPI(api_key, secret_key, passphrase, use_server_time, flag)

    @classmethod
    def from_env(cls, *, flag: str | None = None, use_server_time: bool = False, enable_trade: bool = True) -> "OkxClient":
        resolved_flag = flag if flag is not None else os.getenv("OKX_FLAG", "0")

        if not enable_trade:
            return cls(flag=resolved_flag, use_server_time=use_server_time)

        api_key = os.getenv("OKX_API_KEY")
        secret_key = os.getenv("OKX_SECRET_KEY")
        passphrase = os.getenv("OKX_PASS_PHRASE")

        missing = [
            env_name
            for env_name, env_value in (
                ("OKX_API_KEY", api_key),
                ("OKX_SECRET_KEY", secret_key),
                ("OKX_PASS_PHRASE", passphrase),
            )
            if not env_value
        ]
        if missing:
            raise OKXConfigError(f"缺少 OKX 环境变量: {', '.join(missing)}")

        return cls(
            flag=resolved_flag,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            use_server_time=use_server_time,
        )

    def get_instruments(self, *, inst_type: str = "OPTION", inst_family: str = "BTC-USD") -> dict[str, Any]:
        response = self._public_api.get_instruments(instType=inst_type, instFamily=inst_family)
        return self._ensure_okx_response(response, action="get_instruments")

    def get_opt_summary(self, *, inst_family: str = "BTC-USD", exp_time: str) -> dict[str, Any]:
        response = self._public_api.get_opt_summary(instFamily=inst_family, expTime=exp_time)
        return self._ensure_okx_response(response, action="get_opt_summary")

    def get_mark_price(
        self,
        *,
        inst_type: str = "OPTION",
        inst_family: str = "BTC-USD",
        inst_id: str,
    ) -> dict[str, Any]:
        response = self._public_api.get_mark_price(instType=inst_type, instFamily=inst_family, instId=inst_id)
        return self._ensure_okx_response(response, action="get_mark_price")

    def get_index_tickers(self, *, inst_id: str = "BTC-USD") -> dict[str, Any]:
        response = self._market_api.get_index_tickers(instId=inst_id)
        return self._ensure_okx_response(response, action="get_index_tickers")

    def get_latest_btc_option_put(self, *, now: datetime | None = None) -> dict[str, Any]:
        return self._get_latest_btc_option_by_type(opt_type="P", now=now)

    def get_latest_btc_option_call(self, *, now: datetime | None = None) -> dict[str, Any]:
        return self._get_latest_btc_option_by_type(opt_type="C", now=now)

    def _get_latest_btc_option_by_type(self, *, opt_type: str, now: datetime | None = None) -> dict[str, Any]:
        ticker_response = self.get_index_tickers(inst_id="BTC-USD")
        ticker_items = ticker_response.get("data")
        if not isinstance(ticker_items, list) or not ticker_items:
            raise OKXError("get_index_tickers 返回数据为空。")

        index_item = ticker_items[0]
        if not isinstance(index_item, dict):
            raise OKXError("get_index_tickers 返回的数据格式异常。")

        raw_index_price = index_item.get("idxPx")
        if raw_index_price is None:
            raise OKXError("get_index_tickers 缺少 idxPx 字段。")

        try:
            index_price = float(raw_index_price)
        except (TypeError, ValueError) as exc:
            raise OKXError(f"idxPx 不是有效数字: {raw_index_price}") from exc

        current_time = now or datetime.now(timezone(timedelta(hours=8)))
        target_days = 2 if 0 <= current_time.hour < 16 else 1
        exp_time = (current_time + timedelta(days=target_days)).strftime("%y%m%d")

        instruments_response = self.get_instruments(inst_type="OPTION", inst_family="BTC-USD")
        instrument_items = instruments_response.get("data")
        if not isinstance(instrument_items, list) or not instrument_items:
            raise OKXError("get_instruments 返回数据为空。")

        candidate_puts: list[tuple[float, str]] = []
        for item in instrument_items:
            if not isinstance(item, dict):
                continue
            inst_id = item.get("instId")
            if not isinstance(inst_id, str):
                continue

            parts = inst_id.split("-")
            if len(parts) < 5:
                continue
            if parts[2] != exp_time or parts[-1] != opt_type:
                continue

            try:
                strike = float(parts[-2])
            except ValueError:
                continue

            # put 取小于指数价格的，call 取大于指数价格的
            if opt_type == "P" and strike >= index_price:
                continue
            if opt_type == "C" and strike <= index_price:
                continue

            candidate_puts.append((strike, inst_id))

        if not candidate_puts:
            label = "Put" if opt_type == "P" else "Call"
            raise OKXError(f"未找到到期日 {exp_time} 的 BTC {label} 期权。")

        # 选择距离指数价格最近的期权。
        _, atm_inst_id = min(candidate_puts, key=lambda x: abs(x[0] - index_price))

        mark_price_response = self.get_mark_price(inst_type="OPTION", inst_family="BTC-USD", inst_id=atm_inst_id)
        mark_items = mark_price_response.get("data")
        if not isinstance(mark_items, list) or not mark_items:
            raise OKXError(f"get_mark_price 返回数据为空: {atm_inst_id}")
        mark_item = mark_items[0]
        if not isinstance(mark_item, dict):
            raise OKXError(f"get_mark_price 返回的数据格式异常: {atm_inst_id}")

        return mark_item

    def place_order(
        self,
        *,
        inst_id: str,
        td_mode: str,
        cl_ord_id: str,
        side: str,
        ord_type: str,
        px: str,
        sz: int,
    ) -> dict[str, Any]:
        if self._trade_api is None:
            raise OKXConfigError("尚未配置交易 API 凭证，无法 place_order。")

        response = self._trade_api.place_order(
            instId=inst_id,
            tdMode=td_mode,
            clOrdId=cl_ord_id,
            side=side,
            ordType=ord_type,
            px=px,
            sz=sz,
        )
        return self._ensure_okx_response(response, action="place_order")

    def _load_module(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise OKXError("缺少依赖 python-okx，请先安装 `python-okx`。") from exc

    def _ensure_okx_response(self, response: Any, *, action: str) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise OKXError(f"{action} 返回了非 dict 响应。")

        response_code = response.get("code")
        if response_code not in (None, "0", 0):
            response_msg = response.get("msg", "")
            raise OKXError(f"{action} 调用失败: code={response_code}, msg={response_msg}")

        return response

