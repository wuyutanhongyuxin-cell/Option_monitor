import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

import aiohttp

from .base import BaseCollector

logger = logging.getLogger("arb.collector.derive")


def parse_derive_instrument(name: str) -> Optional[dict]:
    """
    解析 Derive instrument name。
    格式: {ASSET}-{YYYYMMDD}-{STRIKE}-{C/P}
    例: ETH-20260320-2100-C
    """
    parts = name.split("-")
    if len(parts) != 4:
        return None

    asset = parts[0]
    date_str = parts[1]
    strike_str = parts[2]
    opt_type = parts[3]

    if opt_type not in ("C", "P"):
        return None

    try:
        strike = float(strike_str)
    except ValueError:
        return None

    try:
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        expiry = f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None

    return {
        "underlying": asset,
        "expiry": expiry,
        "strike": strike,
        "option_type": "call" if opt_type == "C" else "put",
    }


class DeriveCollector(BaseCollector):
    """
    Derive (Lyra V2) 期权数据采集器。
    使用 REST 轮询模式，直接参数格式（非 JSON-RPC）。
    域名: api.lyra.finance（api.derive.xyz SSL 连接不稳定）。
    """

    def __init__(
        self,
        config: dict,
        on_option_update: Optional[Callable] = None,
    ):
        super().__init__("derive", config, on_option_update)
        self._session: Optional[aiohttp.ClientSession] = None
        # 使用 api.lyra.finance 作为稳定域名
        self._base_url = config.get("base_url", "https://api.lyra.finance")
        self._supported_assets = config.get("supported_assets", ["BTC", "ETH"])
        self._poll_interval = 10  # REST 轮询间隔（秒）
        self._instruments: Dict[str, List[dict]] = {}  # asset -> [instrument_info]
        self._expiry_dates: Dict[str, List[str]] = {}  # asset -> [YYYYMMDD]

    async def _post(self, endpoint: str, params: dict) -> dict:
        """发送 REST POST 请求（直接参数格式）"""
        url = f"{self._base_url}{endpoint}"

        try:
            async with self._session.post(
                url, json=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[derive] HTTP {resp.status}: {text[:200]}")
                    return {}
                data = await resp.json()
                return data.get("result", data)
        except asyncio.TimeoutError:
            logger.warning(f"[derive] request timeout: {endpoint}")
            return {}
        except Exception as e:
            logger.error(f"[derive] request error {endpoint}: {e}")
            return {}

    async def connect(self):
        """初始化并开始 REST 轮询"""
        logger.info(f"[derive] initializing REST collector ({self._base_url})")

        self._session = aiohttp.ClientSession(trust_env=True)
        self.is_connected = True

        try:
            # 获取所有支持资产的期权列表
            for asset in self._supported_assets:
                await self._fetch_instruments(asset)

            total = sum(len(v) for v in self._instruments.values())
            logger.info(f"[derive] init done, total {total} options")

            # 进入轮询循环
            await self._poll_loop()
        finally:
            self.is_connected = False
            await self.disconnect()

    async def disconnect(self):
        """关闭连接"""
        self.is_connected = False
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        logger.info("[derive] disconnected")

    async def subscribe_options(self, asset: str):
        """获取指定资产的期权列表（REST 模式下实际是拉取）"""
        await self._fetch_instruments(asset)

    async def _fetch_instruments(self, asset: str):
        """获取指定资产的所有活跃期权合约"""
        logger.info(f"[derive] fetching {asset} instruments...")

        result = await self._post("/public/get_instruments", {
            "currency": asset,
            "expired": False,
            "instrument_type": "option",
        })

        if not result:
            logger.warning(f"[derive] {asset} instruments empty")
            return

        instruments = result if isinstance(result, list) else []
        active = []
        expiry_set = set()
        for inst in instruments:
            name = inst.get("instrument_name", "")
            if not name or not inst.get("is_active", False):
                continue
            parsed = parse_derive_instrument(name)
            if parsed is None:
                continue
            active.append({"name": name, **parsed})
            # 提取到期日 YYYYMMDD 用于批量 get_tickers
            parts = name.split("-")
            if len(parts) >= 2:
                expiry_set.add(parts[1])

        self._instruments[asset] = active
        self._expiry_dates[asset] = sorted(expiry_set)
        logger.info(f"[derive] {asset}: {len(active)} active options, {len(expiry_set)} expiries")

    async def _poll_loop(self):
        """REST 轮询主循环"""
        while self._should_run and self.is_connected:
            try:
                await self._fetch_all_tickers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[derive] poll error: {e}")

            await asyncio.sleep(self._poll_interval)

    async def _fetch_all_tickers(self):
        """按到期日顺序获取所有期权 ticker，每批之间有速率控制间隔"""
        for asset in self._supported_assets:
            expiries = self._expiry_dates.get(asset, [])
            for exp in expiries:
                await self._fetch_tickers_batch(asset, exp)
                await asyncio.sleep(0.25)  # 速率控制：4 TPS

    async def _fetch_tickers_batch(self, asset: str, expiry_date: str):
        """批量获取指定资产+到期日的所有期权 ticker"""
        result = await self._post("/public/get_tickers", {
            "instrument_type": "option",
            "currency": asset,
            "expiry_date": expiry_date,
        })

        if not result or not isinstance(result, dict):
            return

        # get_tickers 返回 {instrument_name: {compressed_ticker}, ...}
        # 第一层可能有 "tickers" key 或直接是数据
        tickers = result.get("tickers", result)
        if not isinstance(tickers, dict):
            return

        count = 0
        for inst_name, ticker_data in tickers.items():
            if isinstance(ticker_data, dict):
                self._process_compressed_ticker(inst_name, ticker_data)
                count += 1

        if count > 0:
            logger.debug(f"[derive] batch {asset} {expiry_date}: {count} tickers")

    @staticmethod
    def _to_float(val):
        """转换为 float，仅接受正数（用于价格字段）"""
        if val is None:
            return None
        try:
            v = float(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_float_allow_negative(val):
        """转换为 float，允许 0 和负数（用于 Greeks 等可为负的字段）"""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _process_compressed_ticker(self, instrument_name: str, data: dict):
        """
        处理 get_tickers 返回的压缩格式 ticker。
        字段映射: t=timestamp, b=best_bid_price, B=best_bid_amount,
                  a=best_ask_price, A=best_ask_amount, I=index_price, M=mark_price
        option_pricing: d=delta, g=gamma, t=theta, v=vega, i=iv, r=rho,
                       m=mark_price, f=forward_price, bi=bid_iv, ai=ask_iv
        """
        tf = self._to_float

        bid_usd = tf(data.get("b"))
        ask_usd = tf(data.get("a"))
        bid_amount = tf(data.get("B"))
        ask_amount = tf(data.get("A"))
        index_price = tf(data.get("I")) or 0
        mark_usd = tf(data.get("M"))

        op = data.get("option_pricing", {}) or {}
        iv = tf(op.get("i"))
        if mark_usd is None:
            mark_usd = tf(op.get("m"))

        option_data = {
            "exchange": "derive",
            "instrument_name": instrument_name,
            "best_bid_price": bid_usd,
            "best_ask_price": ask_usd,
            "best_bid_amount": bid_amount,
            "best_ask_amount": ask_amount,
            "bid_usd": bid_usd,
            "ask_usd": ask_usd,
            "mark_price": mark_usd,
            "mark_usd": mark_usd,
            "mark_iv": iv,
            "bid_iv": tf(op.get("bi")),
            "ask_iv": tf(op.get("ai")),
            "underlying_price": index_price,
            "timestamp": data.get("t"),
            "greeks": {
                "delta": self._to_float_allow_negative(op.get("d")),
                "gamma": self._to_float_allow_negative(op.get("g")),
                "theta": self._to_float_allow_negative(op.get("t")),
                "vega": self._to_float_allow_negative(op.get("v")),
                "rho": self._to_float_allow_negative(op.get("r")),
            } if op else None,
        }

        self._update_cache(instrument_name, option_data)
