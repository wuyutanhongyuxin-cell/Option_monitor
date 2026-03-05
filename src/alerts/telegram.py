import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("arb.alerts.telegram")

# 尝试导入 python-telegram-bot (httpx-based v20+)
try:
    from telegram import Bot
    from telegram.error import TelegramError

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    logger.warning("python-telegram-bot not installed, alerts disabled")


class TelegramAlerter:
    """Telegram Bot 报警模块"""

    def __init__(self, bot_token: str, chat_id: str, config: dict = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._config = config or {}

        # 冷却机制
        self._cooldown_seconds = self._config.get("cooldown_seconds", 300)
        self._max_alerts_per_hour = self._config.get("max_alerts_per_hour", 20)
        self._last_alert_time: Dict[str, float] = {}  # key -> timestamp
        self._hourly_count = 0
        self._hour_start = time.time()

        self._bot: Optional["Bot"] = None
        self._enabled = False

    async def initialize(self) -> bool:
        """初始化 Bot 并发送测试消息"""
        if not HAS_TELEGRAM:
            logger.warning("telegram module not available")
            return False

        if not self._bot_token or self._bot_token.startswith("your_"):
            logger.warning("telegram bot token not configured, alerts disabled")
            return False

        if not self._chat_id or self._chat_id.startswith("your_"):
            logger.warning("telegram chat_id not configured, alerts disabled")
            return False

        try:
            self._bot = Bot(token=self._bot_token)
            # 验证 token
            me = await self._bot.get_me()
            logger.info(f"telegram bot connected: @{me.username}")

            # 发送启动消息
            await self._send_message(
                "[Arb Monitor] System started\n"
                f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            )
            self._enabled = True
            return True

        except Exception as e:
            logger.error(f"telegram init failed: {e}")
            return False

    async def _send_message(self, text: str):
        """发送消息到 Telegram"""
        if self._bot is None:
            return

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"telegram send failed: {e}")

    def _check_cooldown(self, key: str) -> bool:
        """检查冷却：返回 True 表示可以发送"""
        now = time.time()

        # 小时计数重置
        if now - self._hour_start > 3600:
            self._hourly_count = 0
            self._hour_start = now

        # 每小时上限
        if self._hourly_count >= self._max_alerts_per_hour:
            return False

        # 同一机会冷却
        last = self._last_alert_time.get(key, 0)
        if now - last < self._cooldown_seconds:
            return False

        return True

    def _make_alert_key(self, opp) -> str:
        """生成冷却键（含交易所方向，方向翻转的机会有独立冷却）"""
        return f"{opp.underlying}_{opp.strike}_{opp.expiry}_{opp.option_type}_{opp.buy_exchange}_{opp.sell_exchange}"

    async def send_opportunities(self, opportunities: list):
        """发送套利机会报警"""
        if not self._enabled:
            return

        # 过滤冷却中的机会
        sendable = []
        for opp in opportunities:
            key = self._make_alert_key(opp)
            if self._check_cooldown(key):
                sendable.append(opp)

        if not sendable:
            return

        # 构建消息
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"<b>Arb Opportunities</b>\n{now_str}\n"]

        for i, opp in enumerate(sendable[:5], 1):  # 最多 5 个
            opt_type = opp.option_type.upper()
            dte_str = f"{opp.dte_days:.1f}d"

            warning = ""
            if opp.dte_days < 3:
                warning = "\n   [!] DTE&lt;3d HIGH RISK"

            depth_ok = "OK" if opp.max_tradable_size >= 3 else "LOW"

            lines.append(
                f"<b>{i}) {opp.underlying} {opt_type} | "
                f"${opp.strike:,.0f} | exp {opp.expiry}</b>\n"
                f"   Buy  @ {opp.buy_exchange}: ${opp.buy_price_usd:,.2f}\n"
                f"   Sell @ {opp.sell_exchange}: ${opp.sell_price_usd:,.2f}\n"
                f"   Gross: ${opp.raw_spread_usd:,.2f} | "
                f"Net: ${opp.net_spread_usd:,.2f}\n"
                f"   Profit: ${opp.estimated_profit_usd:,.2f} "
                f"({opp.max_tradable_size:.1f} contracts)\n"
                f"   APR: {opp.net_apr_percent:,.1f}% | DTE: {dte_str}\n"
                f"   Depth: buy {opp.buy_size:.0f} / sell {opp.sell_size:.0f} "
                f"[{depth_ok}]"
                f"{warning}"
            )

            # 更新冷却
            key = self._make_alert_key(opp)
            self._last_alert_time[key] = time.time()
            self._hourly_count += 1

        msg = "\n---\n".join(lines)
        await self._send_message(msg)
        logger.info(f"sent {len(sendable)} alerts to telegram")

    async def send_shutdown(self):
        """发送关闭消息"""
        if not self._enabled:
            return
        await self._send_message(
            "[Arb Monitor] System shutting down\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    async def send_paper_report(self, report: dict):
        """发送纸盘交易周报"""
        if not self._enabled:
            return

        msg = (
            "<b>Weekly Paper Trading Report</b>\n"
            f"Period: {report.get('period', 'N/A')}\n"
            f"---\n"
            f"Opportunities detected: {report.get('total_detected', 0)}\n"
            f"Paper trades: {report.get('total_trades', 0)}\n"
            f"Net P&L: ${report.get('net_pnl', 0):,.2f}\n"
            f"Avg APR: {report.get('avg_apr', 0):.1f}%\n"
            f"Best trade: ${report.get('best_trade', 0):,.2f}\n"
            f"Worst trade: ${report.get('worst_trade', 0):,.2f}\n"
            f"Win rate: {report.get('win_rate', 0):.0f}%"
        )
        await self._send_message(msg)
