# SKILL.md — Stockbit Enhanced Market Intelligence Agent

## Skill Name
`stockbit-enhanced`

---

## Purpose

This skill enhances the Stockbit Market Intelligence Agent by integrating real-time stock prices from yfinance and news sentiment from websearch. It combines precomputed Stockbit signals with live market data and news context to provide comprehensive trading analysis.

**Core Principle:** This skill is a read-only overlay. It NEVER stores JSON files locally. All data is fetched on-demand from the Stockbit skill data directory.

---

## File Structure

```
~/stockbit-enhanced/
├── SKILL.md              ← This file
└── top_idn_stock.py      ← Main command handler
```

---

## Data Sources (Read-Only)

### 1. Stockbit Signals
```
Path: ~/stockbit/data/latest/unified_enriched.json
Access: READ ONLY
Action: Load unified signals, filter by conviction
```

### 2. Historical Price Data
```
Path: ~/stockbit/data/yfinance/latest/SYMBOL.json
Access: READ ONLY
Action: Load historical OHLCV, technical indicators
Contains: 60-day price history, RSI, MACD, volume trends
```

### 3. Historical Insider Data
```
Path: ~/stockbit/data/insider/YYYY-MM-DD.json
Access: READ ONLY
Action: Track insider activity over time
Contains: buy/sell volume, unique actors, key person activity
```

### 4. Historical Broker Data
```
Path: ~/stockbit/data/broker/YYYY-MM-DD.json
Access: READ ONLY
Action: Track broker flow over time
Contains: net flow, buy ratio, cluster days, breadth
```

### 5. News (websearch)
```
Tool: ollama_web_search
Action: Search recent news for target symbols
```

---

## Telegram Bot Command: /top_idn_stock

```
/top_idn_stock           → Show top 5 stocks (default)
/top_idn_stock 5         → Show top 5 stocks
/top_idn_stock 10        → Show top 10 stocks (max)
```

---

## Usage: Check Individual Stock

### Syntax
```
check <SYMBOL>           → Full analysis with all history
history <SYMBOL>         → Historical price data only
```

### Example: Check TLKM
```
User: check TLKM
Action:
  1. Read unified_enriched.json for TLKM signal
  2. Read yfinance/latest/TLKM.json for price history
  3. Read insider/YYYY-MM-DD.json for insider history
  4. Read broker/YYYY-MM-DD.json for broker history
  5. Fetch live price via yfinance
  6. Search news via websearch
  7. Output comprehensive analysis
```

### Example Output: TLKM
```
📊 TLKM - Comprehensive Analysis
==================================================

🏢 Telkom Indonesia - Telecom giant, fiber & 5G

💰 Price: Rp 3,000 (-0.33%) [live yfinance]
📊 Signal: EXTREME_CONVICTION (score: 80/100)
🎯 Conviction: EXTREME

📈 Price History (from yfinance):
   60D Range: Rp 2,830 - Rp 3,990
   Current: Rp 3,010 (-24.6% from high)
   RSI(14): 38.7 (oversold)
   MACD: BULLISH
   SMA20: Rp 3,125 (price below)
   Volume: CONTRACTING

💵 Insider History (from insider/):
   Latest: 5.6B shares bought (6 days, 100% buy ratio)
   Key Person: None
   Unique Actors: 1
   Foreign: Accumulating

💵 Broker History (from broker/):
   Latest: Rp 16.9B net flow (5 days)
   Buy Ratio: 70% (30 buy / 14 sell)
   Cluster: 5 days
   Unique Brokers: 20
   Breadth: BROAD_BUY

📰 News:
   Q1 Earnings Apr 22 | MarketBeat | Apr 2026
   Buyback Program + ARPU Growth | Seeking Alpha
   BP BUMN controls 0.52% | IDNFinancials

🎯 Recommendation: STRONG BUY
   - Dual source (insider + broker) aligned
   - Price oversold, potential bounce
   - Wait for price > Rp 3,125 (SMA20)
```

---

## Historical Data Analysis

### Price History (yfinance)
```
Source: ~/stockbit/data/yfinance/latest/SYMBOL.json
Fields: date, open, high, low, close, volume
Range: 60 trading days
```

### Insider History
```
Source: ~/stockbit/data/insider/YYYY-MM-DD.json
Fields: symbol, buy_volume, sell_volume, buy_ratio, active_days, unique_actors
Use: Track insider conviction over time
```

### Broker History
```
Source: ~/stockbit/data/broker/YYYY-MM-DD.json
Fields: symbol, net_value_idr, buy_ratio, cluster_days, broker_buy_count
Use: Track broker flow trends over time
```

---

## Version History

| Version | Change |
|---------|--------|
| v1.0 | Initial release |
| v1.5 | Updated to unified_enriched.json |
| v1.6 | Added historical data analysis from yfinance |
| v1.7 | Added check/history commands |
| v1.8 | Added insider & broker historical tracking |