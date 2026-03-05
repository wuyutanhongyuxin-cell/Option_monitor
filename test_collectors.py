"""
测试脚本：验证 Deribit 和 Derive 采集器。
连接后打印期权数量和示例数据，运行 30 秒后退出。
"""

import asyncio
import logging
import sys
import os
import io

# Windows 控制台 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.collectors.deribit import DeribitCollector
from src.collectors.derive import DeriveCollector
from src.utils.logger import setup_logger

logger = setup_logger("DEBUG")


async def test_deribit():
    """测试 Deribit 采集器"""
    config = {
        "use_testnet": True,
        "testnet_ws_url": "wss://test.deribit.com/ws/api/v2",
        "supported_assets": ["BTC", "ETH"],
    }

    collector = DeribitCollector(config)
    collector._should_run = True

    print("\n" + "=" * 60)
    print("[TEST] Deribit (testnet)")
    print("=" * 60)

    try:
        await asyncio.wait_for(collector.connect(), timeout=25)
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        print(f"Deribit error: {e}")
    finally:
        options = await collector.get_all_options()
        print(f"\n[Deribit] options count: {len(options)}")

        count = 0
        for name, data in list(options.items())[:3]:
            count += 1
            print(f"\n  #{count}: {name}")
            print(f"    Bid (USD): ${data.get('bid_usd', 'N/A')}")
            print(f"    Ask (USD): ${data.get('ask_usd', 'N/A')}")
            print(f"    Mark IV:   {data.get('mark_iv', 'N/A')}%")
            print(f"    Spot:      ${data.get('underlying_price', 'N/A')}")
            bid_raw = data.get("best_bid_price", "N/A")
            ask_raw = data.get("best_ask_price", "N/A")
            print(f"    Raw Bid:   {bid_raw} | Raw Ask: {ask_raw}")

        await collector.disconnect()
        return len(options)


async def test_derive():
    """测试 Derive 采集器"""
    config = {
        "base_url": "https://api.lyra.finance",
        "supported_assets": ["BTC", "ETH"],
    }

    collector = DeriveCollector(config)
    collector._should_run = True
    collector._poll_interval = 30  # 只轮询一次

    print("\n" + "=" * 60)
    print("[TEST] Derive")
    print("=" * 60)

    try:
        await asyncio.wait_for(collector.connect(), timeout=25)
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        print(f"Derive error: {e}")
    finally:
        options = await collector.get_all_options()
        print(f"\n[Derive] options count: {len(options)}")

        count = 0
        for name, data in list(options.items())[:3]:
            count += 1
            print(f"\n  #{count}: {name}")
            print(f"    Bid (USD): ${data.get('bid_usd', 'N/A')}")
            print(f"    Ask (USD): ${data.get('ask_usd', 'N/A')}")
            print(f"    Mark IV:   {data.get('mark_iv', 'N/A')}")
            print(f"    Spot:      ${data.get('underlying_price', 'N/A')}")

        await collector.disconnect()
        return len(options)


async def main():
    print("=" * 60)
    print("  Crypto Options Arb - Collector Test")
    print("=" * 60)

    results = await asyncio.gather(
        test_deribit(),
        test_derive(),
        return_exceptions=True,
    )

    print("\n" + "=" * 60)
    print("  Results")
    print("=" * 60)

    for name, result in zip(["Deribit", "Derive"], results):
        if isinstance(result, Exception):
            print(f"  {name}: FAIL - {result}")
        else:
            status = "OK" if result > 0 else "WARN (0 options)"
            print(f"  {name}: {status} - {result} options")

    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
