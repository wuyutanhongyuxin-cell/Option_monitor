import abc
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("arb.collector")


class BaseCollector(abc.ABC):
    """交易所期权数据采集器抽象基类"""

    def __init__(
        self,
        exchange_name: str,
        config: dict,
        on_option_update: Optional[Callable] = None,
    ):
        self.exchange_name = exchange_name
        self.config = config
        self.is_connected = False
        self.last_update_time: Optional[datetime] = None
        self._on_option_update = on_option_update

        # 内部缓存：instrument_name -> ticker data
        self._options_cache: Dict[str, dict] = {}

        # 重连参数
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._base_reconnect_delay = 5  # 秒
        self._max_reconnect_delay = 60
        self._should_run = False

        # 心跳参数（子类可重写）
        self._heartbeat_interval = 30  # 秒

    @abc.abstractmethod
    async def connect(self):
        """建立连接（WebSocket 或 REST 初始化）"""

    @abc.abstractmethod
    async def disconnect(self):
        """断开连接"""

    @abc.abstractmethod
    async def subscribe_options(self, asset: str):
        """订阅某资产的所有期权行情"""

    def _cleanup_expired(self):
        """清理缓存中已过期（DTE < 0）的合约"""
        now = datetime.now(timezone.utc)
        expired_keys = []
        for key, data in self._options_cache.items():
            # 尝试从 instrument name 解析到期日
            parts = key.split("-")
            if len(parts) >= 2:
                try:
                    # 支持 YYYYMMDD 和 DDMMMYY 格式
                    date_part = parts[1]
                    if len(date_part) == 8 and date_part.isdigit():
                        expiry_dt = datetime(
                            int(date_part[:4]), int(date_part[4:6]),
                            int(date_part[6:8]), 8, tzinfo=timezone.utc,
                        )
                    else:
                        continue  # 其他格式由 normalizer 处理
                    if expiry_dt < now:
                        expired_keys.append(key)
                except (ValueError, IndexError):
                    continue
        for key in expired_keys:
            del self._options_cache[key]
        if expired_keys:
            logger.info(f"[{self.exchange_name}] cleaned {len(expired_keys)} expired from cache")

    async def get_all_options(self) -> Dict[str, dict]:
        """返回当前所有活跃期权的报价快照"""
        self._cleanup_expired()
        return dict(self._options_cache)

    def get_option_count(self) -> int:
        """返回缓存中的期权数量"""
        return len(self._options_cache)

    def _update_cache(self, instrument_name: str, data: dict):
        """更新缓存并触发回调"""
        self._options_cache[instrument_name] = data
        self.last_update_time = datetime.utcnow()
        if self._on_option_update:
            try:
                self._on_option_update(data)
            except Exception as e:
                logger.error(f"[{self.exchange_name}] 回调错误: {e}")

    async def start(self):
        """启动采集器（含自动重连）"""
        self._should_run = True
        while self._should_run:
            try:
                await self.connect()
                self._reconnect_attempts = 0
                # connect 内部应该会阻塞直到断线
            except asyncio.CancelledError:
                logger.info(f"[{self.exchange_name}] 采集器被取消")
                break
            except Exception as e:
                logger.error(f"[{self.exchange_name}] 连接异常: {e}")

            if not self._should_run:
                break

            # 自动重连（指数退避，永不放弃）
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                # 达到最大重连次数后进入"长等待"模式，重置计数器
                logger.warning(
                    f"[{self.exchange_name}] 达到最大重连次数"
                    f" ({self._max_reconnect_attempts})，进入长等待模式（5分钟后重试）"
                )
                await asyncio.sleep(300)
                self._reconnect_attempts = 0
                continue

            delay = min(
                self._base_reconnect_delay * (2 ** (self._reconnect_attempts - 1)),
                self._max_reconnect_delay,
            )
            logger.warning(
                f"[{self.exchange_name}] {delay}秒后第{self._reconnect_attempts}次重连..."
            )
            await asyncio.sleep(delay)

        self.is_connected = False

    async def stop(self):
        """停止采集器"""
        self._should_run = False
        await self.disconnect()
