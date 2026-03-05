import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List

from .normalizer import NormalizedOption

logger = logging.getLogger("arb.scanner.matcher")


@dataclass
class ArbitrageOpportunity:
    underlying: str
    strike: float
    expiry: str
    option_type: str
    buy_exchange: str       # 在哪里买入（用较低的 ask）
    sell_exchange: str      # 在哪里卖出（用较高的 bid）
    buy_price_usd: float    # 买入价（ask）
    sell_price_usd: float   # 卖出价（bid）
    buy_size: float         # 买方可用深度
    sell_size: float        # 卖方可用深度
    raw_spread_usd: float   # 毛价差
    net_spread_usd: float   # 净价差（扣费后）
    net_apr_percent: float  # 净年化收益率
    dte_days: float
    max_tradable_size: float  # min(buy_size, sell_size)
    estimated_profit_usd: float  # net_spread * max_tradable_size
    detected_at: datetime


class CrossExchangeMatcher:
    """跨交易所期权匹配器"""

    def match(
        self, options_by_exchange: Dict[str, List[NormalizedOption]]
    ) -> List[ArbitrageOpportunity]:
        """
        匹配来自不同交易所的相同期权，检测套利机会。
        返回原始 ArbitrageOpportunity 列表（未应用过滤器，未计算费用）。
        """
        # 1. 按匹配键分组
        groups: Dict[str, Dict[str, NormalizedOption]] = defaultdict(dict)

        for exchange, options in options_by_exchange.items():
            for opt in options:
                key = f"{opt.underlying}_{opt.strike}_{opt.expiry}_{opt.option_type}"
                # 对每个交易所只保留一个（最新的）
                groups[key][exchange] = opt

        # 2. 遍历所有匹配键，找跨所套利
        opportunities = []
        now = datetime.now(timezone.utc)

        for key, exchange_opts in groups.items():
            if len(exchange_opts) < 2:
                continue

            exchanges = list(exchange_opts.keys())

            # 遍历所有交易所对
            for i in range(len(exchanges)):
                for j in range(len(exchanges)):
                    if i == j:
                        continue

                    sell_ex = exchanges[i]  # 卖出方
                    buy_ex = exchanges[j]   # 买入方

                    sell_opt = exchange_opts[sell_ex]
                    buy_opt = exchange_opts[buy_ex]

                    # 检查: sell_bid > buy_ask 才有套利空间
                    if sell_opt.bid_usd <= 0 or buy_opt.ask_usd <= 0:
                        continue

                    raw_spread = sell_opt.bid_usd - buy_opt.ask_usd
                    if raw_spread <= 0:
                        continue

                    # 可交易数量
                    max_size = min(sell_opt.bid_size, buy_opt.ask_size)
                    if max_size <= 0:
                        continue

                    dte = min(sell_opt.dte_days, buy_opt.dte_days)

                    # 简单年化（费用在 calculator 中扣除）
                    if dte > 0 and buy_opt.ask_usd > 0:
                        raw_apr = (raw_spread / buy_opt.ask_usd) * (365 / dte) * 100
                    else:
                        raw_apr = 0.0

                    opp = ArbitrageOpportunity(
                        underlying=buy_opt.underlying,
                        strike=buy_opt.strike,
                        expiry=buy_opt.expiry,
                        option_type=buy_opt.option_type,
                        buy_exchange=buy_ex,
                        sell_exchange=sell_ex,
                        buy_price_usd=buy_opt.ask_usd,
                        sell_price_usd=sell_opt.bid_usd,
                        buy_size=buy_opt.ask_size,
                        sell_size=sell_opt.bid_size,
                        raw_spread_usd=raw_spread,
                        net_spread_usd=raw_spread,  # calculator 会更新
                        net_apr_percent=raw_apr,     # calculator 会更新
                        dte_days=dte,
                        max_tradable_size=max_size,
                        estimated_profit_usd=raw_spread * max_size,
                        detected_at=now,
                    )
                    opportunities.append(opp)

        logger.info(
            f"matched {len(groups)} option keys across exchanges, "
            f"found {len(opportunities)} raw opportunities"
        )
        return opportunities
