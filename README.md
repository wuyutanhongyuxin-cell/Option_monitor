<p align="center">
  <h1 align="center">Crypto Options Arbitrage Monitor</h1>
  <p align="center">
    <strong>跨平台加密期权套利监控系统</strong>
  </p>
  <p align="center">
    7x24 小时自动扫描多交易所期权价差 &middot; 实时 Telegram 报警 &middot; 纸盘交易追踪
  </p>
  <p align="center">
    <a href="#快速开始">快速开始</a> &middot;
    <a href="#系统架构">架构说明</a> &middot;
    <a href="#套利原理">套利原理</a> &middot;
    <a href="#配置指南">配置指南</a> &middot;
    <a href="#模块详解">模块详解</a>
  </p>
</p>

---

## 这个项目做什么？

> 想象同一个苹果，A 超市卖 6 块，B 超市卖 8 块。你在 A 买入、在 B 卖出，赚 2 块差价。

加密期权套利完全一样：同一个 BTC Call 期权（相同行权价、相同到期日），在 Derive 交易所报价 \$6.40，在 Deribit 交易所报价 \$10.00。买入 Derive 的、卖出 Deribit 的，赚 \$3.60 差价。

**本系统做的事：7x24 小时自动扫描所有交易所的所有期权，找到这种价差机会，立刻通知你。**

### 核心能力

| 功能 | 说明 |
|------|------|
| **多交易所采集** | Deribit (WebSocket 实时推送) + Derive (REST 批量轮询) |
| **智能匹配** | 自动匹配相同标的/行权价/到期日的跨所期权对 |
| **费用精算** | 扣除 taker 手续费 + DEX Gas 费 + 滑点后计算净收益 |
| **年化收益** | 自动计算净 APR，按多维度过滤低质量机会 |
| **Telegram 报警** | 发现机会立即推送，含冷却防刷屏机制 |
| **纸盘交易** | 模拟记录每笔机会，跟踪盈亏，每周生成报告 |
| **自动容错** | 单交易所断线不影响全局，指数退避自动重连 |

> **Phase 1 只做监控和报警，不做自动执行。**

---

## 套利原理

### 什么是期权套利？

```
                    同一个期权合约
                 BTC Call, $90,000, 3月28日到期

    ┌─────────────────┐              ┌─────────────────┐
    │    Derive       │              │    Deribit       │
    │                 │              │                  │
    │   Ask: $6.40    │              │   Bid: $10.00    │
    │   (你可以买入)   │              │   (你可以卖出)    │
    └────────┬────────┘              └────────┬─────────┘
             │                                │
             │         在这里买入               │  在这里卖出
             │         花 $6.40               │  收 $10.00
             │                                │
             └────────────┐  ┌────────────────┘
                          │  │
                          ▼  ▼
                    毛利润: $3.60
                  - 手续费: ~$0.01
                  - Gas费:  ~$5.00
                  - 滑点:   ~$0.04
                  ─────────────────
                    净利润: ≈ -$1.45  (这个例子Gas费太高不划算)

                    如果是 10 张合约:
                    净利润: $36.00 - $5.04 ≈ $30.96  ✅
```

### 为什么会存在价差？

1. **市场分割** — CEX (中心化交易所) 和 DEX (去中心化交易所) 用户群不同
2. **流动性差异** — 大所做市商多、报价密；小所流动性差、报价偏离
3. **延迟差异** — 链上交易有区块确认延迟，链下即时成交
4. **资金门槛** — 不同交易所的入金/出金/KYC 要求形成壁垒

### 收益计算公式

```python
# 1. 毛价差
raw_spread = sell_bid - buy_ask

# 2. 费用
buy_fee   = buy_ask  × buy_exchange_taker_fee_rate    # 买入手续费
sell_fee  = sell_bid × sell_exchange_taker_fee_rate    # 卖出手续费
gas_cost  = 5.0 if "derive" in exchanges else 0       # DEX Gas 费
slippage  = (buy_ask + sell_bid) × 0.005 / 2          # 滑点估计 0.5%

# 3. 净价差
net_spread = raw_spread - (buy_fee + sell_fee + gas_cost + slippage)

# 4. 净年化收益率
net_apr = (net_spread / buy_ask) × (365 / dte_days) × 100%

# 5. 预估利润
profit = net_spread × min(buy_depth, sell_depth)
```

---

## 系统架构

### 整体数据流

```
┌────────────────────────────────────────────────────────────────────┐
│                        main.py  (ArbMonitor)                       │
│         主控制器：编排所有组件，驱动 10 秒一次的扫描循环              │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
   ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
   │   Deribit    │    │    Derive    │    │   (未来扩展)     │
   │  Collector   │    │  Collector   │    │  OKX / Binance  │
   │             │    │              │    │   / Gate.io     │
   │  WebSocket   │    │  REST 轮询   │    │                 │
   │  JSON-RPC    │    │  批量获取     │    │                 │
   └──────┬──────┘    └──────┬───────┘    └─────────────────┘
          │                  │
          │   Dict[instrument_name, ticker_data]
          │                  │
          └────────┬─────────┘
                   ▼
          ┌─────────────────┐
          │   Normalizer    │  统一格式：NormalizedOption
          │                 │  价格 → USD, IV → 小数, 日期 → YYYY-MM-DD
          └────────┬────────┘
                   ▼
       ┌────────────────────┐
       │ CrossExchangeMatcher│  按 (标的+行权价+到期日+类型) 分组
       │                    │  检测 sell_bid > buy_ask 的机会
       └────────┬───────────┘
                ▼
      ┌──────────────────────┐
      │ ArbitrageCalculator  │  扣除手续费 / Gas / 滑点
      │                      │  计算净 APR，应用过滤器
      └────────┬─────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
  ┌──────────┐   ┌───────────┐
  │ Database │   │ Telegram  │
  │ (SQLite) │   │  Alerter  │
  │          │   │           │
  │ 保存机会  │   │ 实时推送   │
  │ 纸盘交易  │   │ 周报汇总   │
  └──────────┘   └───────────┘
```

### 项目目录结构

```
crypto-options-arb/
│
├── main.py                      # 🚀 主入口 — 启动监控系统
│
├── config/
│   ├── exchanges.yaml           # 交易所配置（端点、费率、资产）
│   └── filters.yaml             # 过滤参数（APR 阈值、扫描间隔）
│
├── src/
│   ├── collectors/              # 📡 数据采集层
│   │   ├── base.py              #    抽象基类（自动重连 + 心跳）
│   │   ├── deribit.py           #    Deribit WebSocket 采集器
│   │   └── derive.py            #    Derive REST 批量采集器
│   │
│   ├── scanner/                 # 🔍 分析引擎
│   │   ├── normalizer.py        #    多交易所数据归一化
│   │   ├── matcher.py           #    跨所期权匹配
│   │   └── calculator.py        #    价差 & APR 计算 + 过滤
│   │
│   ├── alerts/                  # 📢 报警通知
│   │   └── telegram.py          #    Telegram Bot 推送
│   │
│   ├── storage/                 # 💾 数据存储
│   │   └── database.py          #    SQLite 异步存储
│   │
│   └── utils/                   # 🔧 工具
│       └── logger.py            #    日志（控制台 + 按天轮转文件）
│
├── data/                        # 运行时生成 — SQLite 数据库
├── logs/                        # 运行时生成 — 日志文件
│
├── .env.example                 # 环境变量模板
├── requirements.txt             # Python 依赖
├── test_collectors.py           # 采集器测试脚本
└── README.md                    # 本文件
```

---

## 快速开始

### 前置条件

- Python 3.9+
- 网络可访问 Deribit 和 Derive API（可能需要代理）

### 1. 克隆仓库

```bash
git clone https://github.com/wuyutanhongyuxin-cell/Option_monitor.git
cd Option_monitor
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# Deribit 测试网 API Key（免费注册 test.deribit.com 获取）
DERIBIT_API_KEY=your_key_here
DERIBIT_API_SECRET=your_secret_here

# Telegram Bot（在 @BotFather 创建 bot 获取 token）
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 开发阶段用测试网
USE_TESTNET=true
LOG_LEVEL=INFO
```

> **Deribit 测试网注册**: 访问 [test.deribit.com](https://test.deribit.com)，注册账户后在 API 管理页面创建 Key，无需 KYC。
>
> **Telegram Bot 创建**: 在 Telegram 搜索 `@BotFather`，发送 `/newbot`，按提示操作即可获得 token。获取 chat_id 可以搜索 `@userinfobot` 并发送任意消息。

### 4. 启动系统

```bash
python main.py
```

你会看到类似输出：

```
[2026-03-05 16:28:04] INFO  Crypto Options Arbitrage Monitor
[2026-03-05 16:28:07] INFO  [deribit] WebSocket connected
[2026-03-05 16:28:07] INFO  [derive] BTC: 704 active options, 11 expiries
[2026-03-05 16:28:15] INFO  [deribit] BTC: 528/1050 options subscribed
[2026-03-05 16:28:19] INFO  matched 1208 option keys, found 10 raw opportunities
[2026-03-05 16:28:19] INFO  filter: 0/10 opportunities passed
```

### 5. 停止系统

按 `Ctrl+C`，系统会优雅退出（断开连接、关闭数据库、发送 Telegram 关闭通知）。

---

## 配置指南

### 交易所配置 (`config/exchanges.yaml`)

```yaml
exchanges:
  deribit:
    enabled: true                    # 是否启用
    type: cex                        # 交易所类型
    use_testnet: true                # 使用测试网
    supported_assets: ["BTC", "ETH"] # 支持的资产
    fees:
      taker: 0.0003                  # Taker 费率 0.03%
      maker: 0.0003                  # Maker 费率 0.03%
      settlement: 0.00015            # 结算费 0.015%
    rate_limit_per_second: 20        # API 速率限制

  derive:
    enabled: true
    type: dex
    base_url: "https://api.lyra.finance"  # 稳定域名
    fees:
      taker: 0.0005                  # 0.05%
      maker: 0.0002                  # 0.02%
    gas_cost_estimate_usd: 5.0       # L2 Gas 费估计

  # 以下交易所已预配置，Phase 1 未启用
  okx:     { enabled: false, ... }
  binance: { enabled: false, ... }
  gate:    { enabled: false, ... }
```

### 过滤器配置 (`config/filters.yaml`)

```yaml
filters:
  min_net_apr_percent: 50        # 最低净年化 50%（低于此不报警）
  min_absolute_spread_usd: 0.05  # 最低净价差 $0.05
  min_depth_contracts: 3         # 最低报价深度 3 张合约
  min_dte_hours: 24              # 最短距到期 24 小时
  max_dte_days: 90               # 最长距到期 90 天

scan:
  interval_seconds: 10            # 扫描间隔

alerts:
  cooldown_seconds: 300           # 同一机会 5 分钟内不重复报警
  max_alerts_per_hour: 20         # 每小时最多 20 条报警
```

---

## 模块详解

### 1. 数据采集层 (`src/collectors/`)

#### 抽象基类 `BaseCollector`

所有采集器的基础，提供：

| 功能 | 说明 |
|------|------|
| `_options_cache` | 内存字典缓存，`instrument_name → ticker_data` |
| `start()` | 主循环，含自动重连（指数退避，5s → 10s → 20s → ... → 60s 上限） |
| `stop()` | 优雅停止 |
| `get_all_options()` | 返回当前缓存快照 |

```
重连策略:
尝试1: 5秒后重连
尝试2: 10秒后重连
尝试3: 20秒后重连
...
尝试N: min(5 × 2^(N-1), 60) 秒后重连
最多10次，超过则放弃
```

#### Deribit 采集器 (`deribit.py`)

**连接方式**: WebSocket JSON-RPC 2.0

```
连接流程:
1. 建立 WebSocket 连接
2. 启用心跳 (public/set_heartbeat, 30s)
3. 获取活跃期权列表 (public/get_instruments)
4. 过滤行权价在现货 ±50% 范围内的合约
5. 分批订阅 ticker (每批最多 400 个频道)
6. 进入消息循环，处理推送数据
```

**价格单位**: Deribit 期权价格以 BTC/ETH 计价，需要乘以 `underlying_price` 转换为 USD。

```
例: best_bid_price = 0.0035 BTC, underlying_price = $87,000
    bid_usd = 0.0035 × 87,000 = $304.50
```

**合约名格式**: `{ASSET}-{DDMMMYY}-{STRIKE}-{C/P}`

```
BTC-28MAR26-90000-C
│    │        │     └─ C=Call, P=Put
│    │        └─ 行权价 $90,000
│    └─ 到期日: 2026年3月28日
└─ 标的资产: BTC
```

#### Derive 采集器 (`derive.py`)

**连接方式**: REST 批量轮询 (api.lyra.finance)

```
轮询流程:
1. 获取合约列表 (POST /public/get_instruments)
2. 提取所有到期日
3. 每 10 秒按到期日批量获取 ticker (POST /public/get_tickers)
4. 解析压缩格式的 ticker 数据
```

**价格单位**: Derive 价格直接以 USD (USDC) 计价，无需转换。

**合约名格式**: `{ASSET}-{YYYYMMDD}-{STRIKE}-{C/P}`

```
ETH-20260320-2100-C
│    │         │    └─ Call
│    │         └─ 行权价 $2,100
│    └─ 到期日: 2026年3月20日 (注意: YYYYMMDD 格式)
└─ 标的资产: ETH
```

**压缩 Ticker 字段映射**:

| 压缩字段 | 含义 | 示例 |
|---------|------|------|
| `b` | best_bid_price | `"1384"` |
| `B` | best_bid_amount | `"0.00019"` |
| `a` | best_ask_price | `"1405"` |
| `A` | best_ask_amount | `"1"` |
| `I` | index_price (现货) | `"71816"` |
| `M` | mark_price | `"1384"` |
| `t` | timestamp | `1772699187558` |
| `option_pricing.i` | implied_volatility | `"0.58435"` |
| `option_pricing.d` | delta | `"-0.21856"` |

---

### 2. 分析引擎 (`src/scanner/`)

#### 归一化器 (`normalizer.py`)

将不同交易所的原始数据转换为统一的 `NormalizedOption` 格式：

```python
@dataclass
class NormalizedOption:
    exchange: str           # "deribit" / "derive"
    underlying: str         # "BTC" / "ETH"
    strike: float           # 行权价 (USD)
    expiry: str             # "YYYY-MM-DD"
    option_type: str        # "call" / "put"
    bid_usd: float          # 最优买价 (USD)
    ask_usd: float          # 最优卖价 (USD)
    bid_size: float         # 买方深度 (合约数)
    ask_size: float         # 卖方深度 (合约数)
    mark_price_usd: float   # 标记价格 (USD)
    iv: float               # 隐含波动率 (小数, 0.65=65%)
    underlying_price: float # 标的现价
    dte_days: float         # 距到期天数 (含小数)
    raw_instrument: str     # 原始合约名 (调试用)
    timestamp: datetime     # 报价时间
```

**归一化规则**:

```
Deribit:  价格 × underlying_price → USD
          IV 65.0 → 0.65 (除以100)
          日期 28MAR26 → 2026-03-28

Derive:   价格已是 USD，直接用
          IV 0.65 → 0.65 (不变)
          日期 20260320 → 2026-03-20

无效数据过滤:
  - bid = 0 或 null → 跳过
  - ask = 0 或 null → 跳过
  - bid > ask → 数据异常, 跳过
  - dte < 0 → 已过期, 跳过
```

#### 匹配器 (`matcher.py`)

```
匹配键 = "{underlying}_{strike}_{expiry}_{option_type}"
例: "BTC_90000_2026-03-28_call"

步骤:
1. 所有归一化期权按匹配键分组
2. 对每个有 2+ 交易所的分组:
   - 遍历所有交易所对 (i, j)
   - 如果 exchange_i.bid > exchange_j.ask:
     → 在 j 买入 (ask), 在 i 卖出 (bid) = 套利!
3. 输出 ArbitrageOpportunity 列表
```

#### 计算器 (`calculator.py`)

逐项扣除所有成本后得到净收益：

```
┌────────────────────────────┐
│ 毛价差 (raw_spread)       │
│ = sell_bid - buy_ask       │
├────────────────────────────┤
│ - 买入手续费               │
│ - 卖出手续费               │
│ - DEX Gas 费 ($5)         │
│ - 滑点估计 (0.5%)         │
├────────────────────────────┤
│ = 净价差 (net_spread)      │
├────────────────────────────┤
│ 净 APR                    │
│ = (净价差/买价)×(365/DTE)  │
│ × 100%                    │
└────────────────────────────┘
```

**过滤器链** — 必须全部通过：

```
net_spread  ≥ $0.05     → 价差太小不值得操作
net_apr     ≥ 50%       → 收益率太低
depth       ≥ 3 张      → 流动性不足
dte         ≥ 24 小时    → 临近到期风险太高
dte         ≤ 90 天     → 远期合约流动性差
```

---

### 3. Telegram 报警 (`src/alerts/telegram.py`)

#### 报警消息格式

```
Arb Opportunities
2026-03-05 14:22 UTC

1) ETH CALL | $2,100 | exp 2026-03-20
   Buy  @ Derive: $6.40
   Sell @ Deribit: $10.00
   Gross: $3.60 | Net: $3.28
   Profit: $32.80 (10 contracts)
   APR: 187.8% | DTE: 15.0d
   Depth: buy 10 / sell 8 [OK]
---
2) BTC PUT | $55,000 | exp 2026-03-06
   Buy  @ Deribit: $188.00
   Sell @ Derive: $309.00
   Gross: $121.00 | Net: $118.50
   Profit: $118.50 (1 contracts)
   APR: 2298.0% | DTE: 1.0d
   Depth: buy 5 / sell 3 [OK]
   [!] DTE<3d HIGH RISK
```

#### 防刷屏机制

```
同一机会冷却: 5 分钟内不重复报警
每小时上限:   最多 20 条
每条最多:     5 个机会
```

#### 每周纸盘报告

```
Weekly Paper Trading Report
Period: 2026-02-26 ~ 2026-03-05
---
Opportunities detected: 23
Paper trades: 15
Net P&L: $456.78
Avg APR: 89.0%
Best trade: $121.00
Worst trade: $-12.50
Win rate: 80%
```

---

### 4. 数据存储 (`src/storage/database.py`)

使用 SQLite (WAL 模式) 异步存储，自动清理 30 天前数据。

#### 数据表

**opportunities** — 检测到的套利机会

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| detected_at | TIMESTAMP | 检测时间 |
| underlying | TEXT | BTC / ETH |
| strike | REAL | 行权价 |
| expiry | TEXT | 到期日 |
| option_type | TEXT | call / put |
| buy_exchange | TEXT | 买入交易所 |
| sell_exchange | TEXT | 卖出交易所 |
| buy_price | REAL | 买入价 (USD) |
| sell_price | REAL | 卖出价 (USD) |
| raw_spread | REAL | 毛价差 |
| net_spread | REAL | 净价差 |
| net_apr | REAL | 净年化 % |
| dte_days | REAL | 距到期天数 |
| estimated_profit | REAL | 预估利润 |
| status | TEXT | detected / executed / expired |

**paper_trades** — 纸盘交易记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| detected_at | TIMESTAMP | 建仓时间 |
| quantity | REAL | 合约数量 |
| net_spread_at_entry | REAL | 入场时净价差 |
| settlement_price | REAL | 结算价 (到期后填入) |
| actual_pnl | REAL | 实际盈亏 |
| status | TEXT | open / settled / expired |

---

### 5. 日志系统 (`src/utils/logger.py`)

```
输出目标: 控制台 + 文件 (logs/arb.log)
轮转策略: 每天午夜，保留 7 天
格式:     [2026-03-05 16:28:04] INFO    arb.module - message

日志层级树:
arb                         ← 根日志
├── arb.collector           ← 采集器通用
│   ├── arb.collector.deribit
│   └── arb.collector.derive
├── arb.scanner
│   ├── arb.scanner.normalizer
│   ├── arb.scanner.matcher
│   └── arb.scanner.calculator
├── arb.alerts.telegram
└── arb.storage
```

---

## 支持的交易所

| 交易所 | 类型 | 状态 | 采集方式 | 资产 |
|--------|------|------|----------|------|
| **Deribit** | CEX | 已启用 | WebSocket 实时推送 | BTC, ETH |
| **Derive** (Lyra V2) | DEX | 已启用 | REST 批量轮询 | BTC, ETH |
| OKX | CEX | 已配置 | 待开发 | BTC, ETH, SOL |
| Binance | CEX | 已配置 | 待开发 | BTC, ETH |
| Gate.io | CEX | 已配置 | 待开发 | BTC |

### 添加新交易所

1. 在 `src/collectors/` 下创建新采集器，继承 `BaseCollector`
2. 实现 `connect()`, `disconnect()`, `subscribe_options()` 方法
3. 在 `normalizer.py` 的 `PARSERS` 中注册解析函数
4. 在 `config/exchanges.yaml` 中添加配置，设置 `enabled: true`
5. 在 `main.py` 的 `_init_collectors()` 中初始化

---

## 运行效果

### 系统状态摘要（每 60 秒输出一次）

```
==================================================
[2026-03-05 16:30:00] System Status
--------------------------------------------------
  deribit     : OK             | raw:   804 | normalized:   463
  derive      : OK             | raw:  1294 | normalized:  1187
  Matched pairs: 1345
  This scan:     3 opportunities
  Today total:   15
  Today profit:  $234.50
==================================================
```

### 实测数据（Deribit 测试网 + Derive 主网）

```
Deribit: 1050 BTC + 880 ETH 活跃期权，过滤后订阅 804 个
Derive:  704 BTC + 590 ETH 活跃期权 (11 个到期日)
匹配:    1345 个跨所期权对
机会:    每轮 3-10 个原始机会 (过滤后视市场情况)
```

---

## 技术细节

### 异步架构

全程使用 `asyncio`，无多线程：

```python
# 并发启动所有采集器
for name, collector in self.collectors.items():
    task = asyncio.create_task(self._run_collector(name, collector))

# 扫描循环与采集器并行运行
scan_task = asyncio.create_task(self._scan_loop())
await asyncio.gather(scan_task, *collector_tasks)
```

### 错误隔离

每个交易所采集器在独立的 `asyncio.Task` 中运行，单个交易所异常不影响其他：

```python
async def _run_collector(self, name, collector):
    try:
        await collector.start()  # 含自动重连
    except Exception as e:
        self.logger.error(f"collector {name} crashed: {e}")
        # 不会影响其他采集器或扫描循环
```

### 性能优化

| 策略 | 说明 |
|------|------|
| 过滤订阅 | Deribit 只订阅行权价在现货 ±50% 范围内的合约 (528/1050) |
| 批量获取 | Derive 使用 `get_tickers` 按到期日批量获取，而非逐个请求 |
| 内存缓存 | 所有最新报价在字典中缓存，O(1) 查找 |
| WAL 模式 | SQLite 使用 Write-Ahead Logging，读写不阻塞 |
| 速率控制 | Derive 请求间隔 250ms (4 TPS)，避免触发限流 |

### Windows 兼容性

- 控制台输出强制 UTF-8 编码，避免 GBK 乱码
- YAML 配置文件使用 `encoding="utf-8"` 读取
- 信号处理使用 `signal.signal()` 而非 `loop.add_signal_handler()`

---

## 后续扩展方向

Phase 1 完成后可逐步扩展：

- [ ] 增加 OKX、Bybit、Gate.io 采集器
- [ ] 增加半自动执行（Telegram inline button 触发下单）
- [ ] 增加波动率曲面对比（不仅比价格，还比 IV）
- [ ] 增加历史回测模块（用 tardis.dev 数据）
- [ ] 增加 Greeks 计算（用 py_vollib）
- [ ] 增加 Web 仪表盘（FastAPI + 前端）
- [ ] 增加资金费率套利模块（期货 vs 现货）

---

## 常见问题

**Q: 为什么 Derive 用 `api.lyra.finance` 而不是 `api.derive.xyz`？**

A: 实测发现 `api.derive.xyz` 的 SSL 握手在某些网络环境下会失败（特别是通过代理时），而 `api.lyra.finance`（Derive 的旧域名）连接更稳定。两者 API 完全一致。

**Q: 为什么测试网没有找到通过过滤器的机会？**

A: Deribit 测试网的期权报价是模拟数据，与 Derive 主网的真实报价存在较大偏差但流动性（depth）很低。切换到 Deribit 主网 (`use_testnet: false`) 后会有更真实的匹配结果。

**Q: 如何降低过滤阈值看更多机会？**

A: 编辑 `config/filters.yaml`，降低 `min_net_apr_percent` 和 `min_depth_contracts`。设为 `LOG_LEVEL=DEBUG` 可以看到每个被过滤机会的具体原因。

**Q: 需要代理吗？**

A: 如果你在中国大陆，访问 Deribit 和 Derive 需要代理。设置环境变量 `https_proxy=http://127.0.0.1:PORT` 即可。aiohttp 已配置 `trust_env=True` 自动读取代理。

---

## 依赖说明

| 包 | 版本 | 用途 |
|----|------|------|
| `aiohttp` | >=3.9.0 | WebSocket + REST 异步客户端 |
| `aiosqlite` | >=0.19.0 | SQLite 异步封装 |
| `python-telegram-bot` | >=20.0 | Telegram Bot API |
| `pyyaml` | >=6.0 | YAML 配置解析 |
| `python-dotenv` | >=1.0.0 | .env 文件加载 |
| `ccxt` | >=4.0.0 | 交易所抽象层 (预留扩展) |
| `pandas` | >=2.0.0 | 数据分析 (预留扩展) |
| `numpy` | >=1.24.0 | 数值计算 (预留扩展) |

---

## License

MIT
