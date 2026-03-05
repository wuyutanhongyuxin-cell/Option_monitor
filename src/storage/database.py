import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import aiosqlite

logger = logging.getLogger("arb.storage")


class Database:
    """异步 SQLite 存储"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "arb.db")
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """初始化数据库连接和表结构"""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TIMESTAMP,
                underlying TEXT,
                strike REAL,
                expiry TEXT,
                option_type TEXT,
                buy_exchange TEXT,
                sell_exchange TEXT,
                buy_price REAL,
                sell_price REAL,
                raw_spread REAL,
                net_spread REAL,
                net_apr REAL,
                dte_days REAL,
                buy_depth REAL,
                sell_depth REAL,
                estimated_profit REAL,
                status TEXT DEFAULT 'detected'
            )
        """)

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TIMESTAMP,
                underlying TEXT,
                strike REAL,
                expiry TEXT,
                option_type TEXT,
                buy_exchange TEXT,
                sell_exchange TEXT,
                buy_price REAL,
                sell_price REAL,
                quantity REAL,
                net_spread_at_entry REAL,
                settlement_price REAL,
                actual_pnl REAL,
                status TEXT DEFAULT 'open'
            )
        """)

        await self._db.commit()
        logger.info(f"database initialized: {self._db_path}")

    async def close(self):
        """关闭数据库"""
        if self._db:
            await self._db.close()
            self._db = None

    async def save_opportunity(self, opp):
        """保存一个套利机会"""
        await self._db.execute(
            """INSERT INTO opportunities
            (detected_at, underlying, strike, expiry, option_type,
             buy_exchange, sell_exchange, buy_price, sell_price,
             raw_spread, net_spread, net_apr, dte_days,
             buy_depth, sell_depth, estimated_profit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                opp.detected_at.isoformat(),
                opp.underlying,
                opp.strike,
                opp.expiry,
                opp.option_type,
                opp.buy_exchange,
                opp.sell_exchange,
                opp.buy_price_usd,
                opp.sell_price_usd,
                opp.raw_spread_usd,
                opp.net_spread_usd,
                opp.net_apr_percent,
                opp.dte_days,
                opp.buy_size,
                opp.sell_size,
                opp.estimated_profit_usd,
                "detected",
            ),
        )
        await self._db.commit()

    async def save_paper_trade(self, opp):
        """保存一笔纸盘交易"""
        await self._db.execute(
            """INSERT INTO paper_trades
            (detected_at, underlying, strike, expiry, option_type,
             buy_exchange, sell_exchange, buy_price, sell_price,
             quantity, net_spread_at_entry, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                opp.detected_at.isoformat(),
                opp.underlying,
                opp.strike,
                opp.expiry,
                opp.option_type,
                opp.buy_exchange,
                opp.sell_exchange,
                opp.buy_price_usd,
                opp.sell_price_usd,
                opp.max_tradable_size,
                opp.net_spread_usd,
                "open",
            ),
        )
        await self._db.commit()

    async def get_recent_opportunities(self, hours: int = 24) -> List[dict]:
        """获取最近 N 小时的机会"""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = await self._db.execute(
            "SELECT * FROM opportunities WHERE detected_at > ? ORDER BY detected_at DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    async def get_stats(self) -> dict:
        """返回今日统计"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cursor = await self._db.execute(
            "SELECT COUNT(*), COALESCE(AVG(net_apr), 0), COALESCE(SUM(estimated_profit), 0) "
            "FROM opportunities WHERE detected_at >= ?",
            (today,),
        )
        row = await cursor.fetchone()

        return {
            "total_today": row[0],
            "avg_apr": row[1],
            "total_profit": row[2],
        }

    async def get_paper_trade_report(self, days: int = 7) -> dict:
        """生成纸盘交易报告"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        cursor = await self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(net_spread_at_entry * quantity), 0), "
            "COALESCE(AVG(net_spread_at_entry / buy_price * 365 / "
            "MAX(julianday(expiry) - julianday(detected_at), 0.01) * 100), 0), "
            "COALESCE(MAX(net_spread_at_entry * quantity), 0), "
            "COALESCE(MIN(net_spread_at_entry * quantity), 0) "
            "FROM paper_trades WHERE detected_at >= ?",
            (cutoff,),
        )
        row = await cursor.fetchone()

        total_trades = row[0]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%d"
        )

        return {
            "period": f"{cutoff_date} ~ {now}",
            "total_trades": total_trades,
            "net_pnl": row[1],
            "avg_apr": row[2],
            "best_trade": row[3],
            "worst_trade": row[4],
            "win_rate": 100 if total_trades > 0 else 0,  # 简化
            "total_detected": total_trades,
        }

    async def cleanup_old_data(self, days: int = 30):
        """清理旧数据"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        await self._db.execute(
            "DELETE FROM opportunities WHERE detected_at < ?", (cutoff,)
        )
        await self._db.commit()
        logger.info(f"cleaned up data older than {days} days")
