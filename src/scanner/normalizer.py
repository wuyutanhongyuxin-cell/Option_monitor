import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.collectors.deribit import parse_deribit_instrument
from src.collectors.derive import parse_derive_instrument

logger = logging.getLogger("arb.scanner.normalizer")

# 交易所 instrument name 解析器映射
PARSERS = {
    "deribit": parse_deribit_instrument,
    "derive": parse_derive_instrument,
}


@dataclass
class NormalizedOption:
    exchange: str           # "deribit" / "derive" 等
    underlying: str         # "BTC" / "ETH"
    strike: float           # 行权价（USD）
    expiry: str             # 到期日 "YYYY-MM-DD"
    option_type: str        # "call" / "put"
    bid_usd: float          # 最优买价（USD）
    ask_usd: float          # 最优卖价（USD）
    bid_size: float         # 买方深度（合约数）
    ask_size: float         # 卖方深度（合约数）
    mark_price_usd: float   # 标记价格（USD）
    iv: float               # 隐含波动率（小数，如 0.65 = 65%）
    underlying_price: float # 标的现价（USD）
    dte_days: float         # 距到期天数（含小数）
    raw_instrument: str     # 原始合约名
    timestamp: datetime     # 报价时间


class OptionNormalizer:
    """将各交易所原始数据归一化为统一格式"""

    def normalize(
        self, exchange: str, options_cache: Dict[str, dict]
    ) -> List[NormalizedOption]:
        """归一化一个交易所的所有期权数据"""
        parser = PARSERS.get(exchange)
        if parser is None:
            logger.warning(f"no parser for exchange: {exchange}")
            return []

        results = []
        now = datetime.now(timezone.utc)

        for instrument_name, data in options_cache.items():
            try:
                parsed = parser(instrument_name)
                if parsed is None:
                    continue

                # 提取并验证价格
                bid_usd = data.get("bid_usd")
                ask_usd = data.get("ask_usd")
                mark_usd = data.get("mark_usd")
                underlying_price = data.get("underlying_price", 0)

                # 过滤无效数据
                if bid_usd is None and ask_usd is None:
                    continue
                if bid_usd is not None and ask_usd is not None:
                    if bid_usd > ask_usd:
                        continue  # 数据异常

                # 计算距到期天数
                expiry_str = parsed["expiry"]
                try:
                    # Deribit 和 Derive 期权均在 08:00 UTC 到期结算
                    expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d").replace(
                        hour=8, tzinfo=timezone.utc
                    )
                    dte = (expiry_dt - now).total_seconds() / 86400.0
                except ValueError:
                    continue

                if dte < 0:
                    continue  # 已过期

                # 提取 IV
                iv = data.get("mark_iv")
                if iv is not None:
                    # Deribit 始终返回百分比格式 (如 65.0 表示 65%)，无论大小一律除以 100
                    if exchange == "deribit":
                        iv = iv / 100.0
                    # Derive 已经是小数格式 (如 0.65)，无需转换

                opt = NormalizedOption(
                    exchange=exchange,
                    underlying=parsed["underlying"],
                    strike=parsed["strike"],
                    expiry=expiry_str,
                    option_type=parsed["option_type"],
                    bid_usd=bid_usd or 0.0,
                    ask_usd=ask_usd or 0.0,
                    bid_size=data.get("best_bid_amount") or 0.0,
                    ask_size=data.get("best_ask_amount") or 0.0,
                    mark_price_usd=mark_usd or 0.0,
                    iv=iv or 0.0,
                    underlying_price=underlying_price or 0.0,
                    dte_days=dte,
                    raw_instrument=instrument_name,
                    timestamp=now,
                )
                results.append(opt)

            except Exception as e:
                logger.debug(f"normalize error {instrument_name}: {e}")
                continue

        return results
