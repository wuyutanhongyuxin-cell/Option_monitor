import logging
from typing import Dict, List

from .matcher import ArbitrageOpportunity

logger = logging.getLogger("arb.scanner.calculator")


class ArbitrageCalculator:
    """计算套利机会的净价差、费用和年化收益"""

    def __init__(self, exchange_configs: dict, filter_config: dict):
        self._exchange_configs = exchange_configs
        self._filters = filter_config

    def _get_fee(self, exchange: str, side: str = "taker") -> float:
        """获取交易所费率"""
        cfg = self._exchange_configs.get(exchange, {})
        fees = cfg.get("fees", {})
        return fees.get(side, 0.0003)

    def _get_gas_cost(self, exchange: str) -> float:
        """获取 DEX 的 gas 费用估计"""
        cfg = self._exchange_configs.get(exchange, {})
        return cfg.get("gas_cost_estimate_usd", 0.0)

    def calculate(
        self, opportunities: List[ArbitrageOpportunity]
    ) -> List[ArbitrageOpportunity]:
        """计算每个机会的净价差和净 APR"""
        for opp in opportunities:
            # 1. 费用计算
            buy_fee = opp.buy_price_usd * self._get_fee(opp.buy_exchange)
            sell_fee = opp.sell_price_usd * self._get_fee(opp.sell_exchange)

            # Gas 费（分别检查买卖双方是否为 DEX）
            dex_exchanges = ("derive",)
            gas_cost = 0.0
            if opp.buy_exchange in dex_exchanges:
                gas_cost += self._get_gas_cost(opp.buy_exchange)
            if opp.sell_exchange in dex_exchanges:
                gas_cost += self._get_gas_cost(opp.sell_exchange)

            # 滑点估计（从配置读取，默认 0.5%）
            slippage_pct = self._filters.get("slippage_percent", 0.5) / 100.0
            slippage = (opp.buy_price_usd + opp.sell_price_usd) * slippage_pct / 2

            total_cost = buy_fee + sell_fee + gas_cost + slippage

            # 2. 净价差
            opp.net_spread_usd = opp.raw_spread_usd - total_cost

            # 3. 净年化收益率
            if opp.dte_days > 0 and opp.buy_price_usd > 0:
                opp.net_apr_percent = (
                    (opp.net_spread_usd / opp.buy_price_usd)
                    * (365 / opp.dte_days)
                    * 100
                )
            else:
                opp.net_apr_percent = 0.0

            # 4. 预估利润
            opp.estimated_profit_usd = opp.net_spread_usd * opp.max_tradable_size

        return opportunities

    def apply_filters(
        self, opportunities: List[ArbitrageOpportunity]
    ) -> List[ArbitrageOpportunity]:
        """应用过滤器，返回符合条件的机会"""
        filters = self._filters
        min_apr = filters.get("min_net_apr_percent", 50)
        min_spread = filters.get("min_absolute_spread_usd", 0.05)
        min_depth = filters.get("min_depth_contracts", 3)
        min_dte_hours = filters.get("min_dte_hours", 24)
        max_dte_days = filters.get("max_dte_days", 90)

        filtered = []
        for opp in opportunities:
            if opp.net_spread_usd < min_spread:
                logger.debug(
                    f"filtered: {opp.underlying} {opp.strike} {opp.expiry} "
                    f"spread=${opp.net_spread_usd:.4f} < min ${min_spread}"
                )
                continue
            if opp.net_apr_percent < min_apr:
                logger.debug(
                    f"filtered: {opp.underlying} {opp.strike} {opp.expiry} "
                    f"APR={opp.net_apr_percent:.1f}% < min {min_apr}%"
                )
                continue
            if opp.max_tradable_size < min_depth:
                logger.debug(
                    f"filtered: {opp.underlying} {opp.strike} {opp.expiry} "
                    f"depth={opp.max_tradable_size} < min {min_depth}"
                )
                continue
            if opp.dte_days < min_dte_hours / 24.0:
                continue
            if opp.dte_days > max_dte_days:
                continue

            filtered.append(opp)

        logger.info(
            f"filter: {len(filtered)}/{len(opportunities)} opportunities passed"
        )
        return filtered
