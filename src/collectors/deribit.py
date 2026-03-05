import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

import aiohttp

from .base import BaseCollector

logger = logging.getLogger("arb.collector.deribit")

# Deribit instrument name 日期中的月份缩写
MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_deribit_instrument(name: str) -> Optional[dict]:
    """
    解析 Deribit instrument name。
    格式: {ASSET}-{DDMMMYY}-{STRIKE}-{C/P}
    例: BTC-28MAR26-90000-C
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

    # 解析日期: DDMMMYY
    try:
        day = int(date_str[:2])
        month_str = date_str[2:5].upper()
        year_short = int(date_str[5:])
        month = MONTH_MAP.get(month_str)
        if month is None:
            return None
        year = 2000 + year_short
        expiry = f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None

    return {
        "underlying": asset,
        "expiry": expiry,
        "strike": strike,
        "option_type": "call" if opt_type == "C" else "put",
    }


class DeribitCollector(BaseCollector):
    """Deribit 期权数据采集器 (WebSocket JSON-RPC 2.0)"""

    def __init__(
        self,
        config: dict,
        on_option_update: Optional[Callable] = None,
    ):
        super().__init__("deribit", config, on_option_update)
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._heartbeat_interval = 30
        self._subscribed_channels: List[str] = []

        # 根据配置选择正式/测试网
        use_testnet = config.get("use_testnet", True)
        if use_testnet:
            self._ws_url = config.get("testnet_ws_url", "wss://test.deribit.com/ws/api/v2")
        else:
            self._ws_url = config.get("ws_url", "wss://www.deribit.com/ws/api/v2")

        self._supported_assets = config.get("supported_assets", ["BTC", "ETH"])

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send(self, method: str, params: dict = None) -> dict:
        """发送 JSON-RPC 请求并等待响应"""
        if self._ws is None or self._ws.closed:
            raise ConnectionError("WebSocket 未连接")

        msg_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[msg_id] = future

        await self._ws.send_json(msg)
        try:
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(msg_id, None)
            raise TimeoutError(f"请求 {method} 超时 (id={msg_id})")

    async def _send_no_wait(self, method: str, params: dict = None):
        """发送 JSON-RPC 请求，不等待响应"""
        if self._ws is None or self._ws.closed:
            return
        msg_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        await self._ws.send_json(msg)

    async def connect(self):
        """建立 WebSocket 连接并开始接收数据"""
        logger.info(f"[deribit] 正在连接 {self._ws_url}")

        self._session = aiohttp.ClientSession(trust_env=True)
        try:
            self._ws = await self._session.ws_connect(
                self._ws_url,
                heartbeat=None,  # 让 Deribit 的心跳机制处理
            )
        except Exception as e:
            logger.error(f"[deribit] WebSocket 连接失败: {e}")
            await self._session.close()
            self._session = None
            raise

        self.is_connected = True
        logger.info("[deribit] WebSocket 已连接")

        try:
            # 先启动消息循环（后台），再发初始化请求
            loop_task = asyncio.create_task(self._message_loop())

            # 启用心跳
            await self._send("public/set_heartbeat", {"interval": self._heartbeat_interval})
            logger.info(f"[deribit] 心跳已启用 (间隔 {self._heartbeat_interval}s)")

            # 订阅所有支持资产的期权
            for asset in self._supported_assets:
                await self.subscribe_options(asset)

            # 等待消息循环结束（断线时）
            await loop_task
        finally:
            self.is_connected = False
            await self.disconnect()

    async def disconnect(self):
        """断开连接"""
        self.is_connected = False
        # 取消所有未完成的 pending requests
        for msg_id, future in self._pending_requests.items():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()
        if self._ws and not self._ws.closed:
            await self._ws.close()
            logger.info("[deribit] WebSocket 已关闭")
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None
        self._subscribed_channels.clear()

    async def subscribe_options(self, asset: str):
        """获取指定资产的活跃期权并订阅 ticker"""
        logger.info(f"[deribit] 获取 {asset} 期权合约列表...")

        result = await self._send("public/get_instruments", {
            "currency": asset,
            "kind": "option",
            "expired": False,
        })

        instruments = result.get("result", [])
        logger.info(f"[deribit] {asset} 共有 {len(instruments)} 个活跃期权")

        if not instruments:
            return

        # 获取当前标的价格（通过 index）
        try:
            index_result = await self._send("public/get_index_price", {
                "index_name": f"{asset.lower()}_usd",
            })
            spot_price = index_result.get("result", {}).get("index_price", 0)
        except Exception:
            spot_price = 0

        # 过滤策略：只订阅行权价在现货 ±50% 范围内的合约
        # （测试网数据可能偏差大，放宽到 ±50%）
        filtered = []
        for inst in instruments:
            name = inst.get("instrument_name", "")
            parsed = parse_deribit_instrument(name)
            if parsed is None:
                continue

            if spot_price > 0:
                strike = parsed["strike"]
                ratio = strike / spot_price
                if ratio < 0.5 or ratio > 1.5:
                    continue

            filtered.append(name)

        logger.info(
            f"[deribit] {asset} 过滤后订阅 {len(filtered)}/{len(instruments)} 个期权"
        )

        # 分批订阅（每批最多 500 个频道）
        channels = [f"ticker.{name}.100ms" for name in filtered]
        batch_size = 400  # 留点余量
        for i in range(0, len(channels), batch_size):
            batch = channels[i : i + batch_size]
            try:
                await self._send("public/subscribe", {"channels": batch})
                self._subscribed_channels.extend(batch)
                logger.info(f"[deribit] 已订阅 {len(batch)} 个 {asset} 期权频道")
            except Exception as e:
                logger.error(f"[deribit] 订阅失败: {e}")

    async def _message_loop(self):
        """WebSocket 消息接收主循环"""
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.warning(f"[deribit] 无法解析消息: {msg.data[:100]}")
                except Exception as e:
                    logger.error(f"[deribit] 消息处理异常: {e}", exc_info=True)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"[deribit] WebSocket 错误: {self._ws.exception()}")
                break
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                logger.warning("[deribit] WebSocket 连接关闭")
                break

    async def _handle_message(self, data: dict):
        """处理收到的 JSON-RPC 消息"""
        # 检查是否是对请求的响应
        if "id" in data and data["id"] in self._pending_requests:
            future = self._pending_requests.pop(data["id"])
            if not future.done():
                if "error" in data:
                    future.set_exception(
                        Exception(f"Deribit RPC 错误: {data['error']}")
                    )
                else:
                    future.set_result(data)
            return

        method = data.get("method", "")

        # 心跳处理
        if method == "heartbeat":
            hb_type = data.get("params", {}).get("type", "")
            if hb_type == "test_request":
                # 必须回复 public/test，否则服务器会断开连接
                await self._send_no_wait("public/test", {})
            return

        # 订阅推送
        if method == "subscription":
            params = data.get("params", {})
            channel = params.get("channel", "")
            ticker_data = params.get("data", {})

            if channel.startswith("ticker.") and ticker_data:
                self._process_ticker(ticker_data)

    def _process_ticker(self, data: dict):
        """处理 ticker 推送数据"""
        instrument_name = data.get("instrument_name", "")
        if not instrument_name:
            return

        # 提取关键字段
        underlying_price = data.get("underlying_price") or data.get("index_price", 0)
        best_bid = data.get("best_bid_price")
        best_ask = data.get("best_ask_price")

        # Deribit 期权价格单位是 BTC/ETH，需要转换为 USD
        bid_usd = None
        ask_usd = None
        mark_usd = None

        if best_bid is not None and best_bid > 0 and underlying_price > 0:
            bid_usd = best_bid * underlying_price
        if best_ask is not None and best_ask > 0 and underlying_price > 0:
            ask_usd = best_ask * underlying_price

        mark_price = data.get("mark_price")
        if mark_price is not None and underlying_price > 0:
            mark_usd = mark_price * underlying_price

        option_data = {
            "exchange": "deribit",
            "instrument_name": instrument_name,
            "best_bid_price": best_bid,
            "best_ask_price": best_ask,
            "best_bid_amount": data.get("best_bid_amount"),
            "best_ask_amount": data.get("best_ask_amount"),
            "bid_usd": bid_usd,
            "ask_usd": ask_usd,
            "mark_price": mark_price,
            "mark_usd": mark_usd,
            "mark_iv": data.get("mark_iv"),
            "underlying_price": underlying_price,
            "timestamp": data.get("timestamp"),
            "greeks": data.get("greeks"),
        }

        self._update_cache(instrument_name, option_data)
