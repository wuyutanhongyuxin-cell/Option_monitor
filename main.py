"""
跨平台加密期权套利监控系统 - 主入口
Usage: python main.py
"""

import asyncio
import io
import os
import signal
import sys
import time
from datetime import datetime, timezone

# Windows UTF-8
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv

# 项目根目录
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from src.utils.logger import setup_logger
from src.collectors.deribit import DeribitCollector
from src.collectors.derive import DeriveCollector
from src.scanner.normalizer import OptionNormalizer
from src.scanner.matcher import CrossExchangeMatcher
from src.scanner.calculator import ArbitrageCalculator
from src.alerts.telegram import TelegramAlerter
from src.storage.database import Database


def load_config():
    """加载 YAML 配置"""
    with open(os.path.join(ROOT_DIR, "config", "exchanges.yaml"), "r", encoding="utf-8") as f:
        exchanges_cfg = yaml.safe_load(f)
    with open(os.path.join(ROOT_DIR, "config", "filters.yaml"), "r", encoding="utf-8") as f:
        filters_cfg = yaml.safe_load(f)
    return exchanges_cfg, filters_cfg


class ArbMonitor:
    """套利监控主控制器"""

    def __init__(self):
        load_dotenv(os.path.join(ROOT_DIR, ".env"))

        self.logger = setup_logger(os.getenv("LOG_LEVEL", "INFO"))
        self.exchanges_cfg, self.filters_cfg = load_config()

        self.collectors = {}
        self.normalizer = OptionNormalizer()
        self.matcher = CrossExchangeMatcher()
        self.calculator = ArbitrageCalculator(
            self.exchanges_cfg.get("exchanges", {}),
            self.filters_cfg.get("filters", {}),
        )
        self.db = Database()
        self.alerter = TelegramAlerter(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            config=self.filters_cfg.get("alerts", {}),
        )

        self._running = False
        self._collector_tasks = []
        self._scan_interval = self.filters_cfg.get("scan", {}).get(
            "interval_seconds", 10
        )
        self._last_status_time = 0
        self._total_opportunities_today = 0
        self._total_profit_today = 0.0
        self._last_paper_report = time.time()

    def _init_collectors(self):
        """初始化启用的交易所采集器"""
        exchanges = self.exchanges_cfg.get("exchanges", {})

        if exchanges.get("deribit", {}).get("enabled", False):
            cfg = exchanges["deribit"]
            cfg["use_testnet"] = os.getenv("USE_TESTNET", "true").lower() == "true"
            self.collectors["deribit"] = DeribitCollector(cfg)
            self.logger.info("collector initialized: deribit")

        if exchanges.get("derive", {}).get("enabled", False):
            cfg = exchanges["derive"]
            self.collectors["derive"] = DeriveCollector(cfg)
            self.logger.info("collector initialized: derive")

    async def start(self):
        """启动监控系统"""
        self.logger.info("=" * 50)
        self.logger.info("  Crypto Options Arbitrage Monitor")
        self.logger.info("=" * 50)

        # 初始化数据库
        await self.db.initialize()

        # 初始化 Telegram
        await self.alerter.initialize()

        # 初始化采集器
        self._init_collectors()

        if not self.collectors:
            self.logger.error("no collectors enabled, exiting")
            return

        self._running = True

        # 启动所有采集器（并发）
        for name, collector in self.collectors.items():
            task = asyncio.create_task(self._run_collector(name, collector))
            self._collector_tasks.append(task)

        # 等待采集器初始化
        await asyncio.sleep(5)

        # 启动扫描循环
        scan_task = asyncio.create_task(self._scan_loop())

        # 启动纸盘报告定时器
        report_task = asyncio.create_task(self._paper_report_loop())

        # 等待直到被中断
        try:
            await asyncio.gather(
                scan_task, report_task, *self._collector_tasks,
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _run_collector(self, name: str, collector):
        """运行单个采集器（带错误隔离）"""
        try:
            await collector.start()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"collector {name} crashed: {e}")

    async def _scan_loop(self):
        """主扫描循环"""
        self.logger.info(f"scan loop started (interval: {self._scan_interval}s)")

        while self._running:
            try:
                await self._do_scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"scan error: {e}")

            await asyncio.sleep(self._scan_interval)

    async def _do_scan(self):
        """执行一轮扫描"""
        # 1. 从所有采集器获取数据
        options_by_exchange = {}
        for name, collector in self.collectors.items():
            if collector.is_connected or collector.get_option_count() > 0:
                cache = await collector.get_all_options()
                if cache:
                    # 2. 归一化
                    normalized = self.normalizer.normalize(name, cache)
                    if normalized:
                        options_by_exchange[name] = normalized

        if len(options_by_exchange) < 2:
            # 需要至少 2 个交易所的数据才能匹配
            self._print_status(options_by_exchange, 0, [])
            return

        # 3. 跨所匹配
        raw_opportunities = self.matcher.match(options_by_exchange)

        # 4. 计算费用和 APR
        calculated = self.calculator.calculate(raw_opportunities)

        # 5. 应用过滤器
        filtered = self.calculator.apply_filters(calculated)

        # 6. 保存和报警
        for opp in filtered:
            await self.db.save_opportunity(opp)
            await self.db.save_paper_trade(opp)
            self._total_opportunities_today += 1
            self._total_profit_today += opp.estimated_profit_usd

        if filtered:
            await self.alerter.send_opportunities(filtered)

        # 7. 打印状态（每 60 秒）
        match_count = len(raw_opportunities)
        self._print_status(options_by_exchange, match_count, filtered)

    def _print_status(self, options_by_exchange, match_count, filtered):
        """控制台状态摘要"""
        now = time.time()
        if now - self._last_status_time < 60:
            return
        self._last_status_time = now

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "=" * 50,
            f"[{ts}] System Status",
            "-" * 50,
        ]

        for name, collector in self.collectors.items():
            status = "OK" if collector.is_connected else "DISCONNECTED"
            count = collector.get_option_count()
            norm_count = len(options_by_exchange.get(name, []))
            lines.append(f"  {name:12s}: {status:14s} | raw: {count:5d} | normalized: {norm_count:5d}")

        lines.extend([
            f"  Matched pairs: {match_count}",
            f"  This scan:     {len(filtered)} opportunities",
            f"  Today total:   {self._total_opportunities_today}",
            f"  Today profit:  ${self._total_profit_today:,.2f}",
            "=" * 50,
        ])

        print("\n".join(lines))

    async def _paper_report_loop(self):
        """每周发送纸盘报告"""
        while self._running:
            await asyncio.sleep(3600)  # 每小时检查一次
            now = time.time()
            # 每 7 天发一次
            if now - self._last_paper_report > 7 * 86400:
                try:
                    report = await self.db.get_paper_trade_report(days=7)
                    await self.alerter.send_paper_report(report)
                    self._last_paper_report = now
                except Exception as e:
                    self.logger.error(f"paper report error: {e}")

    async def stop(self):
        """优雅退出"""
        self.logger.info("shutting down...")
        self._running = False

        # 停止采集器
        for name, collector in self.collectors.items():
            try:
                await collector.stop()
            except Exception as e:
                self.logger.error(f"error stopping {name}: {e}")

        # 取消任务
        for task in self._collector_tasks:
            task.cancel()

        # 发送关闭消息
        await self.alerter.send_shutdown()

        # 每日清理
        try:
            await self.db.cleanup_old_data(30)
        except Exception:
            pass

        # 关闭数据库
        await self.db.close()

        self.logger.info("shutdown complete")


async def main():
    monitor = ArbMonitor()

    # 信号处理
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        stop_event.set()

    # Windows 不支持 loop.add_signal_handler，用线程安全方式
    if sys.platform == "win32":
        def win_handler(sig, frame):
            loop.call_soon_threadsafe(stop_event.set)
        signal.signal(signal.SIGINT, win_handler)
        signal.signal(signal.SIGTERM, win_handler)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

    # 启动监控
    monitor_task = asyncio.create_task(monitor.start())

    # 等待退出信号
    await stop_event.wait()
    await monitor.stop()
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExited.")
