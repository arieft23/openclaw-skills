# SKILL.md — Stockbit Enhanced Market Intelligence Agent

## Skill Name
`stockbit-enhanced`

---

## Purpose

This skill enhances the Stockbit Market Intelligence Agent by integrating real-time stock prices from yfinance and news sentiment from websearch. It combines precomputed Stockbit signals with live market data and news context to provide comprehensive trading analysis.

**Core Principle:** This skill is a read-only overlay. It NEVER stores JSON files locally. All data is fetched on-demand:
- Stockbit signals → read from `stockbit/data/latest/unified.json` (read-only)
- Real-time prices → fetched via yfinance (no caching)
- News → fetched via websearch (no caching)

---

## File Structure

```
~/stockbit-enhanced/
├── SKILL.md              ← This file
├── top_idn_stock.py      ← Main command handler
└── analyze.py            ← Analysis engine
```

---

## Data Sources (Read-Only)

### 1. Stockbit Signals
```
Path: ~/stockbit/data/latest/unified.json
Access: READ ONLY
Action: Load unified signals, filter by conviction
```

### 2. Real-time Prices (yfinance)
```
API: yfinance Python package
Action: Fetch current price, change%, volume
No caching - always fresh data
```

### 3. News (websearch)
```
Tool: ollama_web_search
Action: Search recent news for target symbols
No caching - always fresh data
```

---

## Integration Flow

```
User Request
    ↓
Step 1: Read Stockbit unified.json (filter top signals)
    ↓
Step 2: Fetch real-time prices via yfinance
    ↓
Step 3: Search news via websearch
    ↓
Step 4: Generate integrated analysis
    ↓
Output: Human-readable recommendation
```

---

## Telegram Bot Command: /top-idn-stock

```
/top-idn-stock           → Show top 5 stocks (default)
/top-idn-stock 5         → Show top 5 stocks
/top-idn-stock 7         → Show top 7 stocks
/top-idn-stock 10        → Show top 10 stocks (max)
```

**Implementation:**
- File: `top_idn_stock.py`
- Integrates: Stockbit signals + yfinance prices + websearch news
- Max limit: 10 stocks
- Company profiles: Included for each stock

**Example Output:**
```
🔥 Top 5 Indonesian Stocks - 2026-04-21
==================================================

🚀 #1 TLKM
   Signal: EXTREME_CONVICTION (EXTREME)
   Score: 78/100
   💰 Price: Rp 3,030 (-2.27%) Vol: 19.5M
   🏢 Telkom Indonesia - Telecom giant, fiber & 5G provider
   📰 TLKM cancels EGMS, consolidating PLN Icon+ fiber
   🎯 STRONG BUY ⭐

🚀 #2 BULL
   Signal: EXTREME_CONVICTION (EXTREME)
   Score: 76/100
   💰 Price: Rp 520 (+6.06%) Vol: 560.8M
   🏢 Buana Lintas Lautan - Oil & gas tanker shipping
   📰 Strong broker cluster, 7-day insider buying
   🎯 BUY ⭐

🚀 #3 KIJA
   Signal: EXTREME_CONVICTION (EXTREME)
   Score: 75/100
   💰 Price: Rp 190 (+0.53%) Vol: 32.6M
   🏢 Kija Holdings - Nickel mining & processing
   📰 Foreign accumulation, strong broker 90% buy ratio
   🎯 BUY ⭐

📈 #4 PACK
   Signal: EXTREME_CONVICTION (HIGH)
   Score: 74/100
   💰 Price: Rp 290 (+2.84%) Vol: 194.2M
   🏢 Packaging Corp - Paper & packaging manufacturer
   📰 Massive insider volume, smart money buying
   🎯 BUY ⭐

📈 #5 ARNA
   Signal: HIGH_CONVICTION (HIGH)
   Score: 57/100
   💰 Price: Rp 505 (0.00%) Vol: 1.6M
   🏢 Arwana Citramulia - Ceramic tiles manufacturer
   📰 DIVIDEND JUMBO! Rp330B cash dividend
   🎯 BUY ⭐ (dividend play)

==================================================
💡 Use /top-idn-stock [N] for more stocks
```

### Full Analysis (Python Script)
```
Run: python ~/stockbit-enhanced/analyze.py
Output: Top 5 enhanced signals with prices + news
```

### Single Symbol Analysis
```
User asks: "Analyze TLKM"
Action: 
  1. Read TLKM from unified.json
  2. Fetch yfinance price
  3. Search websearch news
  4. Output integrated recommendation
```

### Filtered Analysis
```
User asks: "Show only EXTREME_CONVICTION stocks"
Action:
  1. Filter unified.json by EXTREME_CONVICTION
  2. Fetch prices for filtered list
  3. Search news for top 3
  4. Output results
```

---

## Signal Convergence Rules

| Stockbit Signal | Price Action | News Sentiment | → Recommendation |
|---|---|---|---|
| EXTREME_CONVICTION | Uptrend (+>2%) | Positive | 🚀 STRONG BUY |
| HIGH_CONVICTION | Uptrend | Positive | 📈 BUY |
| HIGH_CONVICTION | Flat/Mixed | Neutral | 👀 WATCH |
| ACCUMULATION | Any | Any | 👀 WATCH |
| DISTRIBUTION | Downtrend | Negative | ⚠️ AVOID |
| DIVERGENT | Any | Any | ⚠️ MONITOR |

---

## Constraints

- **NEVER write to Stockbit data files** — read-only access
- **NEVER cache JSON locally** — always fetch fresh
- **Use yfinance for prices** — no alternative APIs
- **Use websearch for news** — no premium news APIs
- **Respect rate limits** — space out calls
- **Handle missing data gracefully** — partial analysis OK

---

## Installation

```bash
pip install yfinance
```

No other dependencies required. OpenClaw provides websearch tool.

---

## Version History

| Version | Change |
|---------|--------|
| v1.0 | Initial release - read-only overlay design |
| v1.1 | Removed all local JSON caching |
| v1.2 | Simplified file structure (single script) |
| v1.3 | Added company profiles to output |
| v1.4 | Added real-time yfinance prices |