# SKILL.md — Stockbit Market Intelligence Agent

## Skill Name
`stockbit-market-intelligence`

---

## Purpose

This skill enables OpenClaw agents to read, interpret, and continuously improve precomputed Stockbit market intelligence signals — combining insider trading activity and broker flow analysis into unified conviction scores for trading decisions.

The agent operates **strictly on local JSON files**. It never runs JavaScript, never calls APIs, and never modifies source scripts directly. Its only jobs are:

1. Read and interpret signal files
2. Generate trading analysis for the user
3. Propose threshold improvements based on observed signal quality
4. Write improvement proposals to a feedback file for human review

---

## File Structure

```
~/stockbit/data/
├── latest/
│   ├── insider.json             ← insider signals
│   ├── broker.json              ← broker flow signals
│   └── unified_enriched.json   ← PRIMARY SOURCE — agent reads this
├── insider/
│   └── YYYY-MM-DD.json
├── broker/
│   └── YYYY-MM-DD.json
├── yfinance/
│   ├── latest/
│   │   ├── _index.json          ← symbol list + market scores
│   │   └── SYMBOL.json          ← per-symbol OHLCV + indicators
│   └── YYYY-MM-DD/
│       └── SYMBOL.json
├── unified_enriched/
│   └── YYYY-MM-DD.json
└── feedback/
    └── YYYY-MM-DD.md            ← agent writes improvement proposals here
```

**Pipeline (run in order):**
```bash
node fetch_insider.js       # → data/latest/insider.json
node fetch_broker.js        # → data/latest/broker.json
python3 fetch_yfinance.py   # → data/yfinance/latest/*.json  (top 200 IDX)
python3 enrich_unified.py   # → data/latest/unified_enriched.json
```

Install dependencies:
```bash
pip install yfinance numpy --break-system-packages
```

Note: `fetch_unified.js` is no longer part of the pipeline. `enrich_unified.py` handles the full merge of insider + broker + yfinance in one pass.

**The agent must always load `latest/unified_enriched.json` first.** Insider, broker, and raw yfinance files are used only for deeper investigation.

---

## Data Contracts

### Unified Record Schema (Primary)

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Stock ticker |
| `final_signal` | string | EXTREME_CONVICTION / HIGH_CONVICTION / ACCUMULATION / DISTRIBUTION / NEUTRAL |
| `composite_score` | number | Combined insider + broker score (0–100) |
| `insider_score` | number | Insider contribution (0–100, penalized if single-source) |
| `broker_score` | number | Broker contribution (0–100, penalized if single-source) |
| `insider_alignment` | boolean | Insider is bullish |
| `broker_alignment` | boolean | Broker is bullish |
| `net_flow_insider` | number | Insider net volume (shares) |
| `net_flow_broker` | number | Broker net flow (IDR) |
| `conviction_level` | string | EXTREME / HIGH / MEDIUM / LOW |
| `tags` | array | Signal modifiers — see Tag Reference below |
| `insider` | object \| null | Insider snapshot (null if no insider data) |
| `broker` | object \| null | Broker snapshot (null if no broker data) |
| `market_score` | number \| null | yfinance market score 0–100 (null if no data) |
| `market_signal` | string | BULLISH / BEARISH / NEUTRAL / UNKNOWN |
| `market_alignment` | string | CONFIRMING / CONTRADICTING / NEUTRAL / UNKNOWN |
| `final_score` | number | composite_score × 0.7 + market_score × 0.3 |
| `signal_quality` | string | STRONG / MODERATE / WEAK / NOISE |
| `quality_factors` | array | List of contributing signals e.g. ["insider_bullish","market_confirming"] |
| `market_context` | object \| null | Compact indicator snapshot (RSI, MACD, volume, momentum, MAs) |

### Insider Snapshot Fields

| Field | Type | Description |
|---|---|---|
| `signal` | string | STRONG_ACCUMULATION / ACCUMULATION / DISTRIBUTION / NEUTRAL |
| `score` | number | Raw insider score |
| `key_person_activity` | boolean | PENGENDALI / KOMISARIS / DIREKTUR involved |
| `key_person_buys` | number | Count of key person buy transactions |
| `foreign_accumulation` | boolean | Foreign insider buying detected |
| `buy_volume` | number | Total shares bought |
| `sell_volume` | number | Total shares sold |
| `buy_ratio` | number | buy_volume / (buy_volume + sell_volume), 0–1 |
| `active_days` | number | Days with any activity |
| `buy_days` | number | Days with net buying |
| `sell_days` | number | Days with net selling |
| `unique_actors` | number | Number of distinct insiders active |

### Broker Snapshot Fields

| Field | Type | Description |
|---|---|---|
| `signal` | string | STRONG_ACCUMULATION / ACCUMULATION / DISTRIBUTION / NEUTRAL |
| `score` | number | Raw broker score |
| `net_value_idr` | number | Net buy minus sell in IDR |
| `buy_ratio` | number | buy_value / (buy_value + sell_value), 0–1 |
| `active_days` | number | Days with activity |
| `buy_days` | number | Calendar days with net buying |
| `sell_days` | number | Calendar days with net selling |
| `tags` | array | Raw broker-level tags (may differ from unified tags) |
| `foreign` | object | Foreign broker sub-signal |
| `smart_money` | object | Weighted institutional sub-signal |
| `breadth` | object | Broker count buy vs sell |
| `cluster` | object | Multi-broker same-day activity |

---

## Tag Reference

Tags are the agent's primary signal modifiers. Always read tags before making a recommendation.

### Source Tags
| Tag | Meaning |
|---|---|
| `INSIDER_ONLY` | No broker data — signal unconfirmed |
| `BROKER_ONLY` | No insider data — signal unconfirmed |
| `ALIGNED_BULLISH` | Both insider and broker are bullish — strong conviction |
| `ALIGNED_BEARISH` | Both insider and broker are bearish |
| `DIVERGENT` | Insider and broker disagree — treat as NEUTRAL |
| `NOISY` | Signal from single broker or single day — unreliable |

### Flow Tags (only present when net flow confirms direction)
| Tag | Meaning |
|---|---|
| `INFLOW` | Foreign broker net buying |
| `OUTFLOW` | Foreign broker net selling |
| `ACCUMULATION` | Smart money (high-weight brokers) net buying |
| `DISTRIBUTION` | Smart money net selling |
| `BROAD_BUY` | 3+ brokers net buying AND net flow positive |
| `BROAD_SELL` | 3+ brokers net selling |
| `CLUSTER_BUY` | 2+ brokers buying same stock same day, 2+ days |
| `CLUSTER_BUY_WEAK` | 2+ brokers buying same stock same day, 1 day only |

### Insider Tags
| Tag | Meaning |
|---|---|
| `insider:STRONG_ACCUMULATION` | Key person insider buying |
| `insider:ACCUMULATION` | Net insider buying |
| `insider:DISTRIBUTION` | Net insider selling |

---

## Signal Definitions

### Final Signals

| Signal | Condition | Agent Action |
|---|---|---|
| `EXTREME_CONVICTION` | Both insider + broker bullish, composite ≥ 65 | Strong buy candidate — flag immediately |
| `HIGH_CONVICTION` | Both bullish, composite < 65 | Buy candidate — include in watchlist |
| `ACCUMULATION` | One side bullish, other neutral | Watch — needs confirmation |
| `DISTRIBUTION` | Either side bearish (and no conflict) | Risk flag — include in avoid list |
| `NEUTRAL` | Conflict or no clear direction | Skip unless user asks specifically |

### Conviction Levels

| Level | Score Range | Meaning |
|---|---|---|
| `EXTREME` | ≥ 75 | Act with high confidence |
| `HIGH` | 50–74 | Act with moderate confidence |
| `MEDIUM` | 30–49 | Watch, not act |
| `LOW` | 0–29 | Informational only |

---

## Decision Logic

The agent must follow this priority order when evaluating a symbol:

1. **Check `tags` first** — `DIVERGENT` or `NOISY` overrides any positive signal
2. **Check `final_signal`** — primary classification
3. **Check `conviction_level`** — scales confidence
4. **Check `insider.key_person_activity`** — upgrades any bullish signal
5. **Check `broker.foreign.signal`** — INFLOW + bullish = strong confirmation
6. **Check source coverage** — `INSIDER_ONLY` or `BROKER_ONLY` = lower confidence

### Bullish Condition
- `final_signal` = EXTREME_CONVICTION or HIGH_CONVICTION
- AND `ALIGNED_BULLISH` in tags
- AND no `DIVERGENT` or `NOISY` tags

### Bearish Condition
- `final_signal` = DISTRIBUTION
- AND `conviction_level` ≥ MEDIUM
- AND no `DIVERGENT` tag

### Conflict / Skip Condition
- `DIVERGENT` tag present → report as "mixed signal, monitor"
- `NOISY` tag present → discard signal, note data quality issue
- `INSIDER_ONLY` + `conviction_level` = LOW → monitor only
- `BROKER_ONLY` + no `ALIGNED_*` tag → weak signal

---

## Agent Usage Flow

The agent follows this exact sequence on every run:

### Step 1 — Load unified data
```
Read: ~/stockbit/data/latest/unified_enriched.json
```
Never run any JS script. Never fetch from APIs. If the file doesn't exist, tell the user:
"Run: `node fetch_insider.js && node fetch_broker.js && python3 fetch_yfinance.py && python3 enrich_unified.py`"

### Step 2 — Read meta block
Check `meta.generated_at`. If older than 24 hours, warn the user that data may be stale before proceeding.

Report summary from `meta.by_signal` and `meta.by_conviction`.

### Step 3 — Rank and filter signals
Apply this filter to `signals` array:

**Include:**
- `final_signal` IN [EXTREME_CONVICTION, HIGH_CONVICTION] → top opportunities
- `final_signal` = ACCUMULATION AND `conviction_level` ≥ MEDIUM → watchlist
- `final_signal` = DISTRIBUTION AND `conviction_level` ≥ MEDIUM → risk list

**Exclude:**
- `NOISY` tag present
- `DIVERGENT` tag present (report separately as "conflicted")
- `conviction_level` = LOW AND single source only

### Step 4 — Generate output
Produce three sections:

**A. Top Opportunities** (EXTREME + HIGH conviction, bullish)
For each: symbol, final_signal, composite_score, conviction_level, key tags, insider summary, broker summary, recommended action.

**B. Risk / Distribution List** (DISTRIBUTION, conviction ≥ MEDIUM)
For each: symbol, net flows, why it's bearish, key tags.

**C. Conflicted Signals** (DIVERGENT tag)
For each: what insider says vs what broker says, why they disagree.

### Step 5 — Quality Assessment
After generating output, the agent evaluates signal quality and writes a feedback proposal.

---

## Continuous Improvement Protocol

This is the most important section. The agent must assess signal quality on every run and propose threshold adjustments when patterns indicate the current config is miscalibrated.

### When to Propose Changes

The agent proposes a threshold change when it observes any of these patterns:

| Pattern | Proposed Fix |
|---|---|
| Many `INSIDER_ONLY` signals with `buy_ratio: 0` or `1` rated LOW conviction | Lower `MEDIUM` threshold or reduce single-source penalty for unambiguous signals |
| `BROKER_ONLY` DISTRIBUTION with `unique_brokers: 1` classified as real signal | Raise `MIN_BROKER_UNIQUE` filter |
| `BROAD_BUY` or `CLUSTER_BUY` tags present on stocks with `net_value_idr < 0` | Tighten `isBuySideTagValid` threshold (raise buy_ratio cutoff from 0.5 to 0.6) |
| Many EXTREME/HIGH signals with `INSIDER_ONLY` and no confirmation | Raise minimum for EXTREME/HIGH to require both sources |
| Distribution signals where insider sells but volume is tiny (< 50M shares) | Add minimum net volume filter for distribution to be actionable |
| Conflicted signals cluster around same sector | Note sector divergence pattern, do not change thresholds |

### Feedback File Format

The agent writes proposals to:
```
~/stockbit/data/feedback/YYYY-MM-DD.md
```

Each proposal follows this format:

```markdown
## Signal Quality Report — YYYY-MM-DD

### Data Summary
- Total signals: N
- EXTREME/HIGH: N
- DISTRIBUTION: N
- NOISY/skipped: N
- Single-source signals: N

### Observed Patterns

#### Pattern 1: [description]
- Affected symbols: WINR, EMTK, HILL
- Issue: INSIDER_ONLY DISTRIBUTION signals with buy_ratio=0 rated LOW conviction
- Current threshold: MEDIUM starts at 30
- Observation: These are unambiguous (buy_ratio=0) but score 24-26 — just below MEDIUM
- Proposed change: Lower MEDIUM threshold to 22 for unambiguous single-source signals
- OR: Apply lighter penalty (0.85 instead of 0.8) for buy_ratio=0 cases
- Confidence in proposal: HIGH — pattern is consistent across 3+ symbols

#### Pattern 2: [description]
...

### Threshold Change Proposals

| Parameter | Current Value | Proposed Value | Reason |
|---|---|---|---|
| MEDIUM conviction min | 30 | 22 (for unambiguous) | INSIDER_ONLY buy_ratio=0 signals score 24-26 |
| SINGLE_SOURCE_PENALTY | 0.6 | 0.75 for buy_ratio=0\|1 | Unambiguous signals don't need heavy penalty |

### No-Change Decisions

| Pattern | Reason not to change |
|---|---|
| BUMI BROAD_BUY stripped | Correct — 94 buy brokers but net -329B IDR. Filter working as intended. |

### Notes for Human Review
[Any observations that don't fit neatly into threshold changes]
```

The agent must write this file every run, even if no changes are proposed. A "no issues found" report is still valuable.

---

## Output Requirements

The agent produces human-readable output with these sections, in this order:

### 1. Data Freshness
```
📅 Data generated: YYYY-MM-DD HH:mm
⚠️  [warning if > 24h old]
📊 Total: N symbols | N EXTREME/HIGH | N DISTRIBUTION | N NEUTRAL
```

### 2. Top Opportunities
Ranked by composite_score descending. For each:
- Symbol + signal badge + conviction level
- Composite score breakdown (insider / broker)
- Key tags in plain English
- 1-2 sentence interpretation
- Recommended stance: Watch / Buy candidate / Strong candidate

### 3. Distribution / Risk Flags
For each bearish signal with conviction ≥ MEDIUM:
- Symbol + why bearish
- Net flow direction
- Recommended stance: Avoid / Reduce / Monitor

### 4. Conflicted Signals
Only if DIVERGENT tags present:
- Symbol + what insider says + what broker says
- Why the conflict may exist (insider buying but market selling = stealth accumulation?)

### 5. Technical Opportunities (separate section)
From `technical_opportunities` array — stocks with strong yfinance setups but no insider/broker signal yet.
For each: symbol, market_score, factors list, brief note.
Label clearly: "No insider/broker confirmation — technical setup only."

### 6. Data Quality Notes
- Count of NOISY signals discarded
- Count of single-source signals (lower reliability)
- Any anomalies observed

---

## Constraints

- **Never run any JS script** — the agent reads files only
- **Never call any external API** — all data is precomputed
- **Never modify fetch_insider.js, fetch_broker.js, or fetch_unified.js** directly
- **Only write to** `~/stockbit/data/feedback/YYYY-MM-DD.md`
- Unified dataset is the source of truth — insider and broker files are for reference only
- If unified.json is missing, respond: "Unified data not found. Please run the pipeline: `node fetch_insider.js && node fetch_broker.js && node fetch_unified.js`"
- If data is older than 48 hours, refuse to make trading recommendations and tell the user to refresh

---

## Example Interpretations

### EXTREME_CONVICTION + ALIGNED_BULLISH + key_person_activity
"Key insider (PENGENDALI/KOMISARIS) is buying AND broker flow confirms accumulation. Highest quality signal. Both money flows aligned."

### ACCUMULATION + INSIDER_ONLY + buy_ratio: 1.0
"Insider is buying with full conviction (100% buy ratio) but no broker confirmation yet. Early signal — watch for broker flow to confirm before acting."

### DISTRIBUTION + ALIGNED_BEARISH + INFLOW
"Insider selling AND broker net selling, but foreign flow is coming in. Possible institutional repositioning. Conflicted — monitor rather than act."

### DISTRIBUTION + NOISY
"Single broker, single day sell. Likely a block trade or portfolio rebalancing. Not a directional signal — discard."

### NEUTRAL + DIVERGENT
"Insider buying but broker selling. Classic conflict — either insider is early, or broker is hedging. Do not take directional position. Monitor for resolution."

---

## Version History

| Version | Change |
|---|---|
| v1.0 | Initial schema — insider + broker + unified |
| v2.0 | Added tag validation (BROAD_BUY requires net positive flow) |
| v2.1 | Added CLUSTER_BUY validity check (same rule as BROAD_BUY) |
| v2.2 | Added single-source penalty with ambiguity factor |
| v2.3 | Added NOISY filter (min 2 brokers, min 2 days for broker-only signals) |
| v2.4 | Raised MEDIUM conviction threshold from 25 to 30 |
| v2.5 | Added continuous improvement protocol and feedback file |
| v3.0 | Replaced fetch_unified.js with enrich_unified.py — single-pass merger |
| v3.0 | Added fetch_yfinance.py — top 200 IDX + insider/broker extras |
| v3.0 | Added market_score, final_score, market_alignment, signal_quality fields |
| v3.0 | Added technical_opportunities section (pure yfinance setups) |
