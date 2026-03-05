# Codex Code Review Prompt — Crypto Options Arbitrage Monitor

## Your Role

You are a **senior software engineer and security auditor** performing a comprehensive code review on a Python async crypto options arbitrage monitoring system. This system connects to live exchange APIs (Deribit WebSocket, Derive REST), processes real-time financial data, and sends Telegram alerts. Review it as if this code will run 24/7 in production handling real money decisions.

---

## Project Overview

**Repo**: https://github.com/wuyutanhongyuxin-cell/Option_monitor

A cross-exchange crypto options arbitrage monitor that:
1. Collects real-time option quotes from Deribit (WebSocket) and Derive/Lyra (REST polling)
2. Normalizes data from different exchanges into a unified format
3. Matches identical options across exchanges (same underlying/strike/expiry/type)
4. Calculates fee-adjusted spreads and annualized returns (APR)
5. Filters opportunities by configurable thresholds
6. Sends Telegram alerts and stores results in SQLite
7. Tracks paper trades for performance analysis

**Tech stack**: Python 3.9+, asyncio, aiohttp, aiosqlite, python-telegram-bot, PyYAML

---

## Files to Review (in order of priority)

### Critical Path (data accuracy = money)
1. `src/scanner/normalizer.py` — Price unit conversion (Deribit BTC→USD, Derive already USD), IV normalization, date parsing
2. `src/scanner/calculator.py` — Fee calculation, net spread, APR formula, slippage estimation
3. `src/scanner/matcher.py` — Cross-exchange matching logic, opportunity detection
4. `src/collectors/deribit.py` — WebSocket JSON-RPC, ticker processing, BTC/ETH price conversion
5. `src/collectors/derive.py` — REST batch polling, compressed ticker field mapping (b/B/a/A/I/M)

### Infrastructure
6. `src/collectors/base.py` — Abstract base class, reconnection logic, exponential backoff
7. `src/alerts/telegram.py` — Alert formatting, cooldown mechanism, rate limiting
8. `src/storage/database.py` — SQLite schema, async operations, data retention
9. `main.py` — Orchestration, signal handling, graceful shutdown, scan loop
10. `src/utils/logger.py` — Logging configuration

### Configuration
11. `config/exchanges.yaml` — Exchange fees, endpoints, rate limits
12. `config/filters.yaml` — Filtering thresholds
13. `.env.example` — Environment variable template

---

## Review Checklist — Please address EVERY item

### 1. Financial Logic Correctness (HIGHEST PRIORITY)

- [ ] **Price unit conversion**: Deribit prices are in BTC/ETH (multiply by underlying_price for USD). Is this conversion correct everywhere? Are there edge cases where `underlying_price` could be 0 or None?
- [ ] **Derive price handling**: Derive returns prices as strings in USD. Is `_to_float()` / `to_float()` correctly handling all edge cases (None, "0", negative, non-numeric strings)?
- [ ] **APR formula**: `(net_spread / buy_price) × (365 / dte_days) × 100`. Is this mathematically correct? What happens when `dte_days` is very small (< 0.01)? Division by zero risk?
- [ ] **Fee calculation**: Are taker fees applied correctly to both legs? Is the slippage estimate (0.5%) reasonable? Is the gas cost ($5) only applied once even when both exchanges are DEX?
- [ ] **Spread direction**: `sell_bid - buy_ask` — verify this is correct (sell at bid, buy at ask). Is the opportunity direction (which exchange to buy/sell) always right?
- [ ] **IV normalization**: Deribit returns IV as percentage (65.0), Derive as decimal (0.65). The code checks `if iv > 5: iv /= 100`. Is this heuristic reliable? Could a low IV (e.g., 3%) be incorrectly divided?
- [ ] **Date parsing**: Deribit uses `DDMMMYY` (28MAR26), Derive uses `YYYYMMDD` (20260320). Are all month abbreviations handled? Edge cases with dates?
- [ ] **DTE calculation**: Uses `datetime.strptime` with hardcoded 08:00 UTC expiry. Is this correct for both exchanges? What if expiry times differ?

### 2. Async / Concurrency Correctness

- [ ] **Race conditions**: `_options_cache` is a regular dict read from scan loop and written from collector tasks. Is this safe in asyncio (single-threaded but cooperative)?
- [ ] **Future lifecycle**: In `deribit.py`, `_pending_requests` stores Futures. Can a Future leak if WebSocket closes before response arrives? Are all Futures properly cleaned up on disconnect?
- [ ] **Task cancellation**: When `stop()` is called, are all running tasks (`_collector_tasks`, `scan_task`, `report_task`) properly cancelled? Could `asyncio.gather` swallow a CancelledError?
- [ ] **Message loop vs init**: Deribit starts `_message_loop()` as a task then sends init requests. If the message loop task completes (WebSocket closes) while `subscribe_options` is still running, what happens?
- [ ] **aiohttp session lifecycle**: Are `ClientSession` objects always closed, even on exceptions? Could there be unclosed session warnings?

### 3. Error Handling & Resilience

- [ ] **Network timeouts**: Are all network calls (WebSocket connect, REST POST, Telegram send) protected by timeouts?
- [ ] **Reconnection logic**: Exponential backoff goes 5→10→20→40→60→60... up to 10 attempts. After 10 failures, the collector stops permanently. Is this the right behavior for a 24/7 system?
- [ ] **Partial data**: What if Deribit returns some tickers with `best_bid_price: null`? What if `underlying_price` is 0 in a ticker? Will this cause division by zero?
- [ ] **Telegram failures**: If Telegram bot token is invalid or network is down, does the system continue scanning? (It should)
- [ ] **Database errors**: If SQLite write fails (disk full, locked), does the scan loop crash?
- [ ] **Derive API changes**: The compressed ticker format (`b`, `B`, `a`, `A`) is undocumented. If Derive changes field names, how gracefully does the system degrade?

### 4. Security

- [ ] **Credential handling**: Are API keys/secrets only loaded from `.env` and never logged or printed?
- [ ] **SQL injection**: Are all database queries parameterized? (Should be, using `?` placeholders)
- [ ] **WebSocket message validation**: Is there any validation of incoming WebSocket data? Could a malicious/malformed message crash the parser?
- [ ] **.gitignore**: Does it properly exclude `.env`, `data/`, `logs/`? Are there any credentials in committed files?
- [ ] **SSL verification**: `trust_env=True` is used for proxy support. Are there any `ssl=False` calls that skip certificate verification?

### 5. Code Quality

- [ ] **Type safety**: Are there places where `None` could propagate and cause `TypeError` later? (e.g., `data.get('best_bid_price')` returning None being passed to arithmetic)
- [ ] **Data class consistency**: `NormalizedOption` uses `float` for `bid_usd` but the normalizer sets it to `0.0` when None. Does the matcher correctly handle `bid_usd == 0`?
- [ ] **Duplicate opportunities**: Could the same opportunity be detected multiple times across consecutive scans? Is the cooldown mechanism in Telegram sufficient, or should dedup happen earlier?
- [ ] **Memory management**: With 1000+ options cached per exchange, is memory usage bounded? Are old/expired options ever evicted from `_options_cache`?
- [ ] **Logging**: Are log messages at appropriate levels? Any sensitive data in DEBUG logs?

### 6. Architecture & Design

- [ ] **Separation of concerns**: Is the boundary between collector/normalizer/matcher/calculator clean? Any circular dependencies?
- [ ] **Configuration**: Are all magic numbers configurable? (e.g., 0.5% slippage, $5 gas, ±50% strike filter, 400 batch size)
- [ ] **Extensibility**: How easy is it to add a new exchange (e.g., OKX)? What needs to change?
- [ ] **Testing**: There's `test_collectors.py` but no unit tests. What would you recommend for critical path testing?

### 7. Performance

- [ ] **Derive polling efficiency**: 1294 options across 22 expiry-date groups, each a separate HTTP request. Is this efficient enough for 10-second intervals? Could requests pile up?
- [ ] **Deribit subscription count**: 800+ WebSocket subscriptions on one connection. Is this within Deribit's limits?
- [ ] **Scan loop timing**: If a scan takes longer than `interval_seconds` (10s), do scans pile up or skip?
- [ ] **Database writes**: Every filtered opportunity triggers a `commit()`. Should this be batched?

---

## Expected Output Format

Please structure your review as:

### Summary
A 3-5 sentence overview of code quality and most critical findings.

### Critical Issues (must fix before production)
Numbered list with file path, line numbers, description, and suggested fix.

### Important Issues (should fix)
Numbered list with same format.

### Minor Issues / Suggestions
Numbered list.

### Positive Observations
What's done well.

### Recommended Action Items
Prioritized list of changes to make.

---

## Additional Context

- This runs on Windows 11 with Python 3.9 (Anaconda)
- Network access requires HTTP proxy (127.0.0.1:10808)
- Deribit testnet (test.deribit.com) is used for development; production uses www.deribit.com
- Derive uses `api.lyra.finance` domain (not `api.derive.xyz`) for SSL stability
- Phase 1 is monitoring only — no trade execution
- The system has been tested running for 80+ seconds with successful data collection and matching
