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
        self._spread_trading_module = self._load_module("okx.SpreadTrading")

        self._public_api = self._public_data_module.PublicAPI(flag=flag)
        self._market_api = self._market_data_module.MarketAPI(flag=flag)
        self._trade_api = None
        self._spread_api = self._spread_trading_module.SpreadTradingAPI(flag=flag)

        if any(value is not None for value in (api_key, secret_key, passphrase)):
            if not all(value is not None for value in (api_key, secret_key, passphrase)):
                raise OKXConfigError("初始化交易 API 需要同时提供 api_key、secret_key、passphrase。")
            self._trade_api = self._trade_module.TradeAPI(api_key, secret_key, passphrase, use_server_time, flag)
            self._spread_api = self._spread_trading_module.SpreadTradingAPI(api_key, secret_key, passphrase, use_server_time, flag)

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
        exp_time = (current_time + timedelta(days=1)).strftime("%y%m%d")

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

    def _find_nearest_put_inst_id(
        self,
        *,
        instrument_items: list[Any],
        exp_time: str,
        target_strike: float,
    ) -> tuple[str, float]:
        """从合约列表中找到到期日为 exp_time、行权价最接近 target_strike 的 Put 合约。"""
        candidates: list[tuple[float, str]] = []
        for item in instrument_items:
            if not isinstance(item, dict):
                continue
            inst_id = item.get("instId")
            if not isinstance(inst_id, str):
                continue
            parts = inst_id.split("-")
            if len(parts) < 5:
                continue
            if parts[2] != exp_time or parts[-1] != "P":
                continue
            try:
                strike = float(parts[-2])
            except ValueError:
                continue
            candidates.append((strike, inst_id))

        if not candidates:
            raise OKXError(f"未找到到期日 {exp_time} 的 BTC Put 期权合约。")

        strike, inst_id = min(candidates, key=lambda x: abs(x[0] - target_strike))
    return inst_id, strike

    def place_put_spread(
        self,
        *,
        td_mode: str = "cross",
        ord_type: str = "limit",
        sz: int = 1,
        sell_cl_ord_id: str,
        buy_cl_ord_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """下一组 Put 价差单：卖出当前指数价格最近的 Put，买入指数价格 -2000 的 Put。

        两腿均使用各自的标记价格作为限价单报价。

        Returns:
            {"sell": <place_order response>, "buy": <place_order response>}
        """
        if self._trade_api is None:
            raise OKXConfigError("尚未配置交易 API 凭证，无法 place_put_spread。")

        # 1. 获取 BTC 现货指数价格
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

        # 2. 计算到期日
        current_time = now or datetime.now(timezone(timedelta(hours=8)))
        exp_time = (current_time + timedelta(days=1)).strftime("%y%m%d")

        # 3. 获取合约列表
        instruments_response = self.get_instruments(inst_type="OPTION", inst_family="BTC-USD")
        instrument_items = instruments_response.get("data")
        if not isinstance(instrument_items, list) or not instrument_items:
            raise OKXError("get_instruments 返回数据为空。")

        # 4. 找到 sell leg（最近 ATM Put）和 buy leg（指数 -2000 Put）
        sell_inst_id, sell_strike = self._find_nearest_put_inst_id(
            instrument_items=instrument_items,
            exp_time=exp_time,
            target_strike=index_price,
        )
        buy_inst_id, buy_strike = self._find_nearest_put_inst_id(
            instrument_items=instrument_items,
            exp_time=exp_time,
            target_strike=index_price - 2000,
        )

        # 5. 分别获取标记价格作为报价
        def _get_mark_px(inst_id: str) -> str:
            resp = self.get_mark_price(inst_type="OPTION", inst_family="BTC-USD", inst_id=inst_id)
            items = resp.get("data")
            if not isinstance(items, list) or not items:
                raise OKXError(f"get_mark_price 返回数据为空: {inst_id}")
            mark_item = items[0]
            if not isinstance(mark_item, dict):
                raise OKXError(f"get_mark_price 返回数据格式异常: {inst_id}")
            mark_px = mark_item.get("markPx")
            if mark_px is None:
                raise OKXError(f"get_mark_price 缺少 markPx 字段: {inst_id}")
            return str(mark_px)

        sell_px = f"{float(_get_mark_px(sell_inst_id)) * 0.9:.4f}"
        buy_px = f"{float(_get_mark_px(buy_inst_id)) * 1.1:.4f}"

        # 6. 下单：先卖（sell put），再买（buy put）
        sell_response = self.place_order(
            inst_id=sell_inst_id,
            td_mode=td_mode,
            cl_ord_id=sell_cl_ord_id,
            side="sell",
            ord_type=ord_type,
            px=sell_px,
            sz=sz,
        )
        buy_response = self.place_order(
            inst_id=buy_inst_id,
            td_mode=td_mode,
            cl_ord_id=buy_cl_ord_id,
            side="buy",
            ord_type=ord_type,
            px=buy_px,
            sz=sz,
        )

        return {
            "mode": "leg",
            "sell": sell_response,
            "sell_inst_id": sell_inst_id,
            "sell_strike": sell_strike,
            "buy": buy_response,
            "buy_inst_id": buy_inst_id,
            "buy_strike": buy_strike,
            "index_price": index_price,
        }

    def get_put_spread_id(
        self,
        *,
        sell_inst_id: str,
        buy_inst_id: str,
    ) -> str | None:
        """查询是否存在由 sell_inst_id 和 buy_inst_id 组成的官方价差产品，返回 sprdId 或 None。"""
        # 先用 sell leg 查，再过滤 buy leg
        for inst_id in (sell_inst_id, buy_inst_id):
            response = self._spread_api.get_spreads(instId=inst_id, state="live")
            if not isinstance(response, dict) or response.get("code") not in (None, "0", 0):
                continue
            data = response.get("data") or []
            for item in data:
                if not isinstance(item, dict):
                    continue
                # 价差产品的 legs 字段包含两个合约，确认两腿都匹配
                legs = item.get("legs") or []
                inst_ids_in_legs = {leg.get("instId") for leg in legs if isinstance(leg, dict)}
                if sell_inst_id in inst_ids_in_legs and buy_inst_id in inst_ids_in_legs:
                    return item.get("sprdId")
        return None

    def place_put_spread_via_spread_api(
        self,
        *,
        sprd_id: str,
        cl_ord_id: str,
        side: str = "sell",
        ord_type: str = "limit",
        sz: int = 1,
        px: str,
    ) -> dict[str, Any]:
        """通过 OKX SpreadTrading 接口下价差单（原子成交，无腿风险）。"""
        if self._spread_api is None:
            raise OKXConfigError("尚未配置交易 API 凭证，无法使用 SpreadTrading 接口。")
        response = self._spread_api.place_order(
            sprdId=sprd_id,
            clOrdId=cl_ord_id,
            side=side,
            ordType=ord_type,
            sz=str(sz),
            px=px,
        )
        return self._ensure_okx_response(response, action="spread_place_order")

    def place_put_spread_smart(
        self,
        *,
        td_mode: str = "cross",
        ord_type: str = "limit",
        sz: int = 1,
        sell_cl_ord_id: str,
        buy_cl_ord_id: str,
        spread_cl_ord_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """智能下 Put 价差单。

        优先查询 OKX 是否有对应的官方价差产品（SpreadTrading），有则走原子成交接口；
        否则回退为两腿分别独立下单。

        Returns:
            dict，包含 mode（"spread" 或 "leg"）及下单结果。
        """
        # 1. 确定两腿合约
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
        exp_time = (current_time + timedelta(days=1)).strftime("%y%m%d")

        instruments_response = self.get_instruments(inst_type="OPTION", inst_family="BTC-USD")
        instrument_items = instruments_response.get("data")
        if not isinstance(instrument_items, list) or not instrument_items:
            raise OKXError("get_instruments 返回数据为空。")

        sell_inst_id, sell_strike = self._find_nearest_put_inst_id(
            instrument_items=instrument_items,
            exp_time=exp_time,
            target_strike=index_price,
        )
        buy_inst_id, buy_strike = self._find_nearest_put_inst_id(
            instrument_items=instrument_items,
            exp_time=exp_time,
            target_strike=index_price - 2000,
        )

        # 2. 查询官方价差产品
        sprd_id = self.get_put_spread_id(sell_inst_id=sell_inst_id, buy_inst_id=buy_inst_id)
        print(f"查询官方价差产品: sell_inst_id={sell_inst_id}, buy_inst_id={buy_inst_id}, sprd_id={sprd_id}")  # 调试输出查询结果

        # if sprd_id:
        #     # 查询价差 ticker 获取报价
        #     ticker_resp = self._spread_api.get_ticker(sprdId=sprd_id)
        #     spread_px = ""
        #     if isinstance(ticker_resp, dict) and ticker_resp.get("code") in (None, "0", 0):
        #         t_data = ticker_resp.get("data") or []
        #         if t_data and isinstance(t_data[0], dict):
        #             print(f"Spread ticker data: {t_data[0]}")  # 调试输出价差 ticker 数据
        #             spread_px = str(t_data[0].get("askPx") or t_data[0].get("last") or "")

        #     spread_response = self.place_put_spread_via_spread_api(
        #         sprd_id=sprd_id,
        #         cl_ord_id=spread_cl_ord_id,
        #         side="sell",
        #         ord_type=ord_type,
        #         sz=sz,
        #         px=spread_px,
        #     )
        #     return {
        #         "mode": "spread",
        #         "sprd_id": sprd_id,
        #         "spread": spread_response,
        #         "sell_inst_id": sell_inst_id,
        #         "sell_strike": sell_strike,
        #         "buy_inst_id": buy_inst_id,
        #         "buy_strike": buy_strike,
        #         "index_price": index_price,
        #     }

        # 3. 回退：两腿分别下单
        return self.place_put_spread(
            td_mode=td_mode,
            ord_type=ord_type,
            sz=sz,
            sell_cl_ord_id=sell_cl_ord_id,
            buy_cl_ord_id=buy_cl_ord_id,
            now=now,
        )

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

