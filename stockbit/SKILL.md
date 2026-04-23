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
node refresh_token.js
node fetch_insider.js       # → data/latest/insider.json
node fetch_broker.js        # → data/latest/broker.json
python3 fetch_yfinance.py   # → data/yfinance/latest/*.json  (top 200 IDX)
python3 enrich_unified.py   # → data/latest/unified_enriched.json
```

Install dependencies:
```bash
pip install yfinance numpy --break-system-packages
```

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
| `insider_weight` | number | Dynamic weight applied to insider (0.30–0.70) |
| `broker_weight` | number | Dynamic weight applied to broker (0.30–0.70) |
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
| `market_context` | object \| null | Compact indicator snapshot (RSI, MACD, volume, momentum, MAs, Bollinger, ATR) |
| `broker_tier` | string | DUAL / STRONG / MODERATE / WEAK / NONE |

### Meta Block (Staleness — always check first)

| Field | Type | Description |
|---|---|---|
| `generated_at` | string | When unified file was created |
| `insider_generated` | string | When insider.json was created |
| `broker_generated` | string | When broker.json was created |
| `insider_age_hours` | number | Hours since insider data was fetched |
| `broker_age_hours` | number | Hours since broker data was fetched |
| `staleness_warnings` | array | Any WARN or REFUSE messages from cross-source staleness check |

**Staleness rules:**
- `staleness_warnings` contains "REFUSE" → do NOT make trading recommendations; tell user to refresh
- `staleness_warnings` contains "WARN" → proceed but note data may be stale
- `insider_age_hours` and `broker_age_hours` differ by > 12h → report cross-source drift to user

### Insider Snapshot Fields

| Field | Type | Description |
|---|---|---|
| `signal` | string | STRONG_ACCUMULATION / ACCUMULATION / DISTRIBUTION / NEUTRAL |
| `score` | number | Raw insider score (log10-based in v2.6) |
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
| `multi_key_person` | boolean | 2+ distinct key persons (PENGENDALI/KOMISARIS/DIREKTUR) active |
| `insider_cluster_buy` | boolean | Key person buying on 3+ separate days |
| `recency_ratio` | number | Fraction of activity in last 14 days (0–1) — higher = fresher signal |

### Broker Snapshot Fields

| Field | Type | Description |
|---|---|---|
| `signal` | string | STRONG_ACCUMULATION / ACCUMULATION / DISTRIBUTION / NEUTRAL |
| `score` | number | Raw broker score (log10-based volume in v2.6) |
| `net_value_idr` | number | Net buy minus sell in IDR |
| `buy_ratio` | number | buy_value / (buy_value + sell_value), 0–1 |
| `active_days` | number | Days with activity |
| `buy_days` | number | Calendar days with net buying |
| `sell_days` | number | Calendar days with net selling |
| `consistency` | number | buy_days / (buy_days + sell_days) — directional steadiness |
| `tags` | array | Raw broker-level tags (may differ from unified tags) |
| `foreign` | object | Foreign broker sub-signal |
| `smart_money` | object | Weighted institutional sub-signal |
| `breadth` | object | Broker count buy vs sell |
| `cluster` | object | Multi-broker same-day activity |

### Market Context Fields (v2.6 additions)

| Field | Type | Description |
|---|---|---|
| `bollinger` | object \| null | `upper`, `middle`, `lower`, `bandwidth`, `pct_b` (0=at lower band, 100=at upper) |
| `atr` | object \| null | `atr` (absolute), `atr_pct` (% of price) — volatility measure |

---

## Tag Reference

Tags are the agent's primary signal modifiers. Always read tags before making a recommendation.

### Source Tags
| Tag | Meaning |
|---|---|
| `INSIDER_ONLY` | No broker data — signal unconfirmed |
| `BROKER_ONLY` | No insider data — signal unconfirmed |
| `BROKER_STRONG` | Broker-only signal passed all STRONG tier guardrails (buy_ratio≥0.65, cluster≥2, unique≥8, score≥55) |
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
| `SMART_MONEY_DOMINANT` | Smart money (weight≥1.3) accounts for >60% of buy flow |
| `FOREIGN_DOMINANT` | Foreign brokers account for >50% of buy flow |

### Insider Tags
| Tag | Meaning |
|---|---|
| `insider:STRONG_ACCUMULATION` | Key person insider buying |
| `insider:ACCUMULATION` | Net insider buying |
| `insider:DISTRIBUTION` | Net insider selling |
| `MULTI_KEY_PERSON` | 2+ distinct PENGENDALI/KOMISARIS/DIREKTUR buying simultaneously |
| `INSIDER_CLUSTER_BUY` | Key person buying on 3+ separate days — sustained, not one-off |

### Signal Pattern Tags
| Tag | Meaning |
|---|---|
| `EARLY_ACCUMULATION` | Insider-only bullish with moderate conviction — no broker confirmation yet; watch for confirmation |
| `REVERSAL_SETUP` | DISTRIBUTION signal but market score is BULLISH — possible bottom or institutional repositioning; do not short blindly |

---

## Dynamic Weighting (v2.6)

Composite score weights are no longer fixed. They adapt based on source availability:

| Source State | Insider Weight | Broker Weight | Rationale |
|---|---|---|---|
| Both sources present | 0.45 | 0.55 | Broker has slightly more real-time signal |
| Insider only | 0.70 | 0.30 | Trust what we have; reduce noise from absent source |
| Broker only | 0.30 | 0.70 | Trust what we have; reduce noise from absent source |

The applied weights are stored in `insider_weight` and `broker_weight` fields on each record.

---

## BROKER_STRONG Upgrade Guardrails (v2.6)

A broker-only signal can be upgraded to HIGH_CONVICTION or EXTREME_CONVICTION only when **all** of the following pass:

| Guardrail | Threshold |
|---|---|
| Raw broker score | ≥ 55 |
| Buy ratio | ≥ 0.65 |
| Cluster days (2+ brokers same day) | ≥ 2 |
| Unique brokers | ≥ 8 |
| Composite score (for EXTREME upgrade) | ≥ 65 |

If composite < 65 but all other guardrails pass → upgrade to HIGH_CONVICTION only.
If composite < 40 → no upgrade regardless of guardrails.

The agent should note `broker_tier: STRONG` in output and explain the guardrail status when flagging BROKER_STRONG signals.

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

1. **Check meta staleness** — if `staleness_warnings` has REFUSE, stop. Report stale data.
2. **Check `tags` first** — `DIVERGENT` or `NOISY` overrides any positive signal
3. **Check `final_signal`** — primary classification
4. **Check `conviction_level`** — scales confidence
5. **Check `insider.key_person_activity`** — upgrades any bullish signal
6. **Check `MULTI_KEY_PERSON` tag** — further upgrades confidence
7. **Check `broker.foreign.signal`** — INFLOW + bullish = strong confirmation
8. **Check `SMART_MONEY_DOMINANT` or `FOREIGN_DOMINANT`** — institutional conviction
9. **Check source coverage** — `INSIDER_ONLY` or `BROKER_ONLY` = lower confidence
10. **Check `EARLY_ACCUMULATION`** — note as early watch, wait for broker confirmation
11. **Check `REVERSAL_SETUP`** — do not act on DISTRIBUTION; flag as monitor

### Bullish Condition
- `final_signal` = EXTREME_CONVICTION or HIGH_CONVICTION
- AND `ALIGNED_BULLISH` in tags
- AND no `DIVERGENT` or `NOISY` tags

### Bearish Condition
- `final_signal` = DISTRIBUTION
- AND `conviction_level` ≥ MEDIUM
- AND no `DIVERGENT` tag
- AND no `REVERSAL_SETUP` tag (if present, demote to monitor)

### Conflict / Skip Condition
- `DIVERGENT` tag present → report as "mixed signal, monitor"
- `NOISY` tag present → discard signal, note data quality issue
- `INSIDER_ONLY` + `conviction_level` = LOW → monitor only
- `BROKER_ONLY` + no `BROKER_STRONG` tag → weak signal
- `EARLY_ACCUMULATION` → watch only, do not act

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
1. Check `staleness_warnings` — if any contain "REFUSE", stop and tell user to refresh. Do not produce trading recommendations.
2. Check `insider_age_hours` vs `broker_age_hours` — if drift > 12h, note in output.
3. Check `generated_at`. If unified file itself is older than 24h, warn the user before proceeding.
4. Report summary from `meta.by_signal` and `meta.by_conviction`.

### Step 3 — Rank and filter signals
Apply this filter to `signals` array:

**Include:**
- `final_signal` IN [EXTREME_CONVICTION, HIGH_CONVICTION] → top opportunities
- `final_signal` = ACCUMULATION AND `conviction_level` ≥ MEDIUM → watchlist
- `final_signal` = DISTRIBUTION AND `conviction_level` ≥ MEDIUM → risk list
- `EARLY_ACCUMULATION` tag → early watch section

**Exclude:**
- `NOISY` tag present
- `DIVERGENT` tag present (report separately as "conflicted")
- `conviction_level` = LOW AND single source only

### Step 4 — Generate output
Produce these sections:

**A. Top Opportunities** (EXTREME + HIGH conviction, bullish)
For each: symbol, final_signal, composite_score, conviction_level, key tags, insider summary (include multi_key_person / insider_cluster_buy if present), broker summary, recommended action.

**B. Risk / Distribution List** (DISTRIBUTION, conviction ≥ MEDIUM)
For each: symbol, net flows, why it's bearish, key tags. Note REVERSAL_SETUP if present.

**C. Early Watch** (EARLY_ACCUMULATION tag)
For each: symbol, insider signal, score, note that broker confirmation is pending.

**D. Conflicted Signals** (DIVERGENT tag)
For each: what insider says vs what broker says, why they disagree.

**E. Technical Opportunities**
From `technical_opportunities` array. Include Bollinger pct_b and ATR where available.
Label clearly: "No insider/broker confirmation — technical setup only."

### Step 5 — Quality Assessment
Write feedback to `~/stockbit/data/feedback/YYYY-MM-DD.md`.

---

## Continuous Improvement Protocol

The agent assesses signal quality on every run and proposes threshold adjustments.

### When to Propose Changes

| Pattern | Proposed Fix |
|---|---|
| Many `INSIDER_ONLY` signals with `buy_ratio: 0` or `1` rated LOW conviction | Lower `MEDIUM` threshold or reduce single-source penalty for unambiguous signals |
| `BROKER_ONLY` DISTRIBUTION with `unique_brokers: 1` classified as real signal | Raise `MIN_BROKER_UNIQUE` filter |
| `BROAD_BUY` or `CLUSTER_BUY` tags present on stocks with `net_value_idr < 0` | Tighten buy_ratio cutoff |
| Many EXTREME/HIGH signals with `INSIDER_ONLY` and no confirmation | Raise minimum for EXTREME/HIGH to require both sources |
| Distribution signals where insider sells but volume is tiny (< 50M shares) | Add minimum net volume filter |
| `BROKER_STRONG` upgrades on stocks that subsequently don't follow through | Review guardrail thresholds |
| Many `EARLY_ACCUMULATION` signals that never get broker confirmation | Increase insider score floor for EARLY_ACCUMULATION tag |
| `REVERSAL_SETUP` clusters in same sector | Note sector rotation pattern; do not change thresholds |
| `recency_ratio` < 0.3 on HIGH/EXTREME signals | Flag as stale insider interest; lower confidence |
| Conflicted signals cluster around same sector | Note sector divergence; do not change thresholds |

### Feedback File Format

The agent writes proposals to:
```
~/stockbit/data/feedback/YYYY-MM-DD.md
```

```markdown
## Signal Quality Report — YYYY-MM-DD

### Data Summary
- Total signals: N
- EXTREME/HIGH: N
- DISTRIBUTION: N
- NOISY/skipped: N
- Single-source signals: N
- Insider age: Xh | Broker age: Yh | Drift: Zh

### Observed Patterns
...

### Threshold Change Proposals
| Parameter | Current Value | Proposed Value | Reason |
|---|---|---|---|

### No-Change Decisions
| Pattern | Reason not to change |
|---|---|

### Notes for Human Review
```

---

## Output Requirements

### 1. Data Freshness
```
📅 Unified generated: YYYY-MM-DD HH:mm
📅 Insider: Xh old | Broker: Yh old
⚠️  [staleness warnings if any]
📊 Total: N symbols | N EXTREME/HIGH | N DISTRIBUTION | N NEUTRAL
```

### 2. Top Opportunities
Ranked by final_score descending. For each:
- Symbol + signal badge + conviction level
- Composite score breakdown (insider / broker) + weights used
- Key tags in plain English — highlight MULTI_KEY_PERSON, INSIDER_CLUSTER_BUY, SMART_MONEY_DOMINANT, FOREIGN_DOMINANT
- Bollinger pct_b if available (< 20 = near lower band = potential entry)
- ATR % if available (context for stop placement)
- 1-2 sentence interpretation
- Recommended stance: Watch / Buy candidate / Strong candidate

### 3. Distribution / Risk Flags
For each bearish signal with conviction ≥ MEDIUM:
- Symbol + why bearish
- Net flow direction
- Note REVERSAL_SETUP if present: "Bearish flow but market turning — monitor before shorting"

### 4. Early Watch (EARLY_ACCUMULATION)
- Symbol + insider signal + recency_ratio
- Note: "Waiting for broker confirmation"

### 5. Conflicted Signals
Only if DIVERGENT tags present.

### 6. Technical Opportunities
Include Bollinger and ATR context.

### 7. Data Quality Notes
- Count of NOISY signals discarded
- Count of single-source signals
- Cross-source staleness drift if > 12h
- Any anomalies observed

---

## Constraints

- **Never run any JS script** — agent reads files only
- **Never call any external API** — all data is precomputed
- **Never modify fetch_insider.js, fetch_broker.js, or enrich_unified.py** directly
- **Only write to** `~/stockbit/data/feedback/YYYY-MM-DD.md`
- If unified.json is missing → tell user to run the pipeline
- If `staleness_warnings` contains REFUSE → refuse trading recommendations, prompt refresh
- If data is older than 48 hours → refuse recommendations regardless of staleness_warnings content

---

## Example Interpretations

### EXTREME_CONVICTION + ALIGNED_BULLISH + MULTI_KEY_PERSON + INSIDER_CLUSTER_BUY
"Multiple key insiders (PENGENDALI + KOMISARIS) have been buying on 3+ separate days AND broker flow confirms. Highest quality signal. Sustained, not a one-off. Both money flows aligned."

### ACCUMULATION + EARLY_ACCUMULATION + INSIDER_ONLY + buy_ratio: 1.0
"Insider is buying with full conviction (100% buy ratio) but no broker confirmation yet. Early signal — tagged EARLY_ACCUMULATION. Watch for broker flow to confirm before acting."

### DISTRIBUTION + ALIGNED_BEARISH + REVERSAL_SETUP
"Insider and broker both selling, but market score is BULLISH (RSI oversold, MACD turning). Possible institutional bottom-fishing. Do NOT short — flag as monitor. Wait for flow resolution."

### DISTRIBUTION + NOISY
"Single broker, single day sell. Likely a block trade. Not a directional signal — discard."

### NEUTRAL + DIVERGENT
"Insider buying but broker selling. Classic conflict. Do not take directional position. Monitor for resolution."

### HIGH_CONVICTION + BROKER_ONLY + BROKER_STRONG + SMART_MONEY_DOMINANT
"No insider data, but broker signal passed all STRONG guardrails AND smart money accounts for >60% of flow. Meaningful institutional signal — treat as moderate confidence. Lower confidence than dual-source."

### ACCUMULATION + FOREIGN_DOMINANT + INFLOW
"Foreign brokers dominating buy flow (>50%). Strong foreign institutional interest — historically precedes sustained moves in IDX mid-caps."

---

## Version History

| Version | Change |
|---|---|
| v1.0 | Initial schema — insider + broker + unified |
| v2.0 | Added tag validation (BROAD_BUY requires net positive flow) |
| v2.1 | Added CLUSTER_BUY validity check |
| v2.2 | Added single-source penalty with ambiguity factor |
| v2.3 | Added NOISY filter (min 2 brokers, min 2 days) |
| v2.4 | Raised MEDIUM conviction threshold from 25 to 30 |
| v2.5 | Added continuous improvement protocol and feedback file |
| v3.0 | Replaced fetch_unified.js with enrich_unified.py — single-pass merger |
| v3.0 | Added fetch_yfinance.py — top 200 IDX + insider/broker extras |
| v3.0 | Added market_score, final_score, market_alignment, signal_quality fields |
| v3.0 | Added technical_opportunities section (pure yfinance setups) |
| v3.1 | fetch_broker.js: async p-limit concurrency (5 parallel), retry+timeout, noise filter (buy_days < 2 AND cluster = 0), consistency score, SMART_MONEY_DOMINANT + FOREIGN_DOMINANT tags |
| v3.1 | fetch_insider.js: log10 scoring (reduces volume bias), recency weighting (14-day window), MULTI_KEY_PERSON + INSIDER_CLUSTER_BUY tags |
| v3.1 | enrich_unified.py: dynamic weighting (insider_only=0.70/0.30, broker_only=0.30/0.70), tightened BROKER_STRONG guardrails (buy_ratio≥0.65, cluster≥2, unique≥8, score≥55, composite≥65 for EXTREME), cross-source staleness check with WARN/REFUSE, EARLY_ACCUMULATION + REVERSAL_SETUP tags |
| v3.1 | fetch_yfinance.py: added Bollinger Bands (pct_b, bandwidth) and ATR to market_context |
| v3.1 | SKILL.md: updated decision logic, tag reference, output format, example interpretations |
