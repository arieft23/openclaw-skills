#!/usr/bin/env python3
"""
enrich_unified.py — Single-pass merger for insider + broker + yfinance

Reads:
  data/latest/insider.json
  data/latest/broker.json
  data/yfinance/latest/*.json  (top 200 + extras)

Writes:
  data/latest/unified_enriched.json   ← OpenClaw primary source
  data/unified_enriched/YYYY-MM-DD.json

Output has two sections:
  "signals"               — stocks with insider/broker activity, enriched with yfinance
  "technical_opportunities" — stocks with strong technicals but no insider/broker signal
"""

import json, sys
from datetime import datetime
from pathlib import Path

BASE_DIR      = Path(__file__).parent
INSIDER_FILE  = BASE_DIR / "data" / "latest" / "insider.json"
BROKER_FILE   = BASE_DIR / "data" / "latest" / "broker.json"
YFINANCE_DIR  = BASE_DIR / "data" / "yfinance" / "latest"
ENRICHED_DIR  = BASE_DIR / "data" / "unified_enriched"
LATEST_DIR    = BASE_DIR / "data" / "latest"
TODAY         = datetime.now().strftime("%Y-%m-%d")

# ---------- WEIGHTS ----------
INSIDER_W        = 0.45   # insider contribution to composite
BROKER_W         = 0.55   # broker contribution to composite
COMPOSITE_W      = 0.70   # composite in final_score
MARKET_W         = 0.30   # market in final_score

# Single-source penalty
SINGLE_PENALTY_AMBIGUOUS  = 0.8   # buy_ratio=0 or 1 (unambiguous, lighter)
SINGLE_PENALTY_MIXED      = 0.6   # buy_ratio between 0 and 1 (needs confirmation)

# Conviction thresholds (composite_score based)
CONVICTION_LEVELS = [
    ("EXTREME", 75), ("HIGH", 50), ("MEDIUM", 30), ("LOW", 0)
]

# Market score thresholds
MKT_BULLISH = 60
MKT_BEARISH = 40

# Technical opportunity: minimum market_score to surface as opportunity
TECH_OPP_MIN_SCORE = 65
# Minimum signal quality for technical opportunities
TECH_OPP_MIN_QUALITY_SCORE = 2

# Noise filter: broker-only signals need >= these values
MIN_BROKER_UNIQUE = 2
MIN_BROKER_DAYS   = 2

# Signal sets
BULLISH = {"STRONG_ACCUMULATION", "ACCUMULATION"}
BEARISH = {"DISTRIBUTION"}

# ---------- HELPERS ----------
def load_json(path, label):
    if not path.exists():
        print(f"❌ {label} not found: {path}", file=sys.stderr)
        print(f"   run the pipeline first", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text())

def load_yf(symbol):
    p = YFINANCE_DIR / f"{symbol}.json"
    if not p.exists(): return None
    try: return json.loads(p.read_text())
    except: return None

def conviction(score):
    for label, minimum in CONVICTION_LEVELS:
        if score >= minimum: return label
    return "LOW"

def normalize(score, inflection=50):
    """Soft cap normalization. Higher inflection = less compression at high scores."""
    if score <= 0: return 0
    return min(100, round(100 * (1 - 1 / (1 + score / inflection))))

def single_penalty(buy_ratio):
    if buy_ratio is None: return SINGLE_PENALTY_MIXED
    return SINGLE_PENALTY_AMBIGUOUS if buy_ratio in (0, 0.0, 1, 1.0) else SINGLE_PENALTY_MIXED

def process_broker_score(brk_raw, buy_ratio, cluster_days, unique_brokers, broker_only):
    # Tiered broker score for broker-only signals.
    # Dual-source: standard normalization.
    # STRONG  = raw>=70, buy_ratio>=0.70, cluster>=3, unique>=10 -> raw*0.85 (no compression)
    # MODERATE= raw>=40, buy_ratio>=0.50 -> normalize(75)*0.75
    # WEAK    = else -> normalize(50)*0.60
    if not broker_only:
        return normalize(brk_raw), "DUAL"
    is_strong = brk_raw >= 70 and buy_ratio >= 0.70 and cluster_days >= 3 and unique_brokers >= 10
    is_moderate = brk_raw >= 40 and buy_ratio >= 0.50
    if is_strong:
        return round(min(brk_raw, 100) * 0.85), "STRONG"
    elif is_moderate:
        return round(normalize(brk_raw, 75) * 0.75), "MODERATE"
    else:
        return round(normalize(brk_raw) * 0.60), "WEAK"

def is_broker_noisy(brk):
    if not brk: return False
    return (brk.get("breadth", {}).get("unique_brokers", 0) < MIN_BROKER_UNIQUE or
            brk.get("active_days", 0) < MIN_BROKER_DAYS)

def is_buy_side_tag_valid(brk):
    if not brk: return False
    return brk.get("net_value_idr", 0) > 0 or brk.get("buy_ratio", 0) >= 0.5

BUY_SIDE_TAGS = {"BROAD_BUY", "CLUSTER_BUY", "CLUSTER_BUY_WEAK"}

# ---------- INSIDER SIGNALS ----------
def build_insider_map(insider_data):
    return {s["symbol"]: s for s in insider_data.get("signals", [])}

# ---------- BROKER SIGNALS ----------
def build_broker_map(broker_data):
    return {s["symbol"]: s for s in broker_data.get("signals", [])}

# ---------- YFINANCE INDEX ----------
def load_yf_index():
    p = YFINANCE_DIR / "_index.json"
    if not p.exists(): return []
    try: return json.loads(p.read_text()).get("symbols", [])
    except: return []

# ---------- MERGE SINGLE SYMBOL ----------
def merge_symbol(symbol, ins, brk, yf_data):
    """
    Merge insider + broker + yfinance for one symbol.
    Returns enriched unified record.
    """
    has_insider = ins is not None
    has_broker  = brk is not None
    has_yf      = yf_data is not None

    insider_only = has_insider and not has_broker
    broker_only  = has_broker  and not has_insider

    # --- Signal classification ---
    ins_signal = ins["signal"] if ins else "NEUTRAL"
    brk_signal = brk["signal"] if brk else "NEUTRAL"

    ins_bullish = ins_signal in BULLISH
    brk_bullish = brk_signal in BULLISH
    ins_bearish = ins_signal in BEARISH
    brk_bearish = brk_signal in BEARISH

    # --- Score normalization ---
    ins_raw = normalize(ins["score"]) if ins else 0
    ins_score = (round(ins_raw * single_penalty(ins.get("buy_ratio") if ins else None))
                 if insider_only else ins_raw)
    brk_raw_score = brk["score"] if brk else 0
    brk_buy_ratio = brk.get("buy_ratio", 0) if brk else 0
    brk_cluster   = (brk.get("cluster", {}) or {}).get("cluster_days", 0) if brk else 0
    brk_unique    = (brk.get("breadth", {}) or {}).get("unique_brokers", 0) if brk else 0
    brk_score, broker_tier = (process_broker_score(
        brk_raw_score, brk_buy_ratio, brk_cluster, brk_unique, broker_only)
        if brk else (0, "NONE"))

    composite_score = round(ins_score * INSIDER_W + brk_score * BROKER_W)

    # --- Final signal ---
    if ins_bullish and brk_bullish:
        final_signal = "EXTREME_CONVICTION" if composite_score >= 65 else "HIGH_CONVICTION"
    elif ins_bearish and brk_bearish:
        final_signal = "DISTRIBUTION"
    elif ins_bullish and brk_bearish:
        final_signal = "NEUTRAL"          # conflict
    elif ins_bearish and not brk_bullish:
        final_signal = "DISTRIBUTION"
    elif not ins_bearish and brk_bearish:
        final_signal = "DISTRIBUTION"
    elif ins_bullish or brk_bullish:
        final_signal = "ACCUMULATION"
    else:
        final_signal = "NEUTRAL"

    # --- Tags ---
    tags = []
    if ins and ins_signal != "NEUTRAL": tags.append(f"insider:{ins_signal}")

    if brk:
        buy_side_valid = is_buy_side_tag_valid(brk)
        for tag in (brk.get("tags") or []):
            if tag in BUY_SIDE_TAGS and not buy_side_valid: continue
            tags.append(tag)

    if ins_bullish and brk_bullish:   tags.append("ALIGNED_BULLISH")
    if ins_bearish and brk_bearish:   tags.append("ALIGNED_BEARISH")
    if ins_bullish and brk_bearish:   tags.append("DIVERGENT")
    if ins_bearish and brk_bullish:   tags.append("DIVERGENT")

    noisy = False
    if insider_only: tags.append("INSIDER_ONLY")
    if broker_only:
        tags.append("BROKER_ONLY")
        if broker_tier == "STRONG":
            tags.append("BROKER_STRONG")
            if composite_score >= 60 and final_signal not in ("EXTREME_CONVICTION",):
                final_signal = "EXTREME_CONVICTION"
            elif composite_score >= 40 and final_signal not in ("EXTREME_CONVICTION", "HIGH_CONVICTION"):
                final_signal = "HIGH_CONVICTION"
        if is_broker_noisy(brk):
            tags.append("NOISY")
            noisy = True
            if final_signal not in ("EXTREME_CONVICTION", "HIGH_CONVICTION"):
                final_signal = "NEUTRAL"

    # --- Yfinance enrichment ---
    # market_score can be None even when yf file exists (zero-volume / suspended stock)
    market_score = (yf_data.get("market_score") if has_yf else None)
    mkt_signal   = ("BULLISH" if market_score is not None and market_score >= MKT_BULLISH else
                    "BEARISH" if market_score is not None and market_score <= MKT_BEARISH else
                    "NEUTRAL" if market_score is not None else
                    "UNKNOWN")

    # Market alignment
    bullish_finals = {"EXTREME_CONVICTION", "HIGH_CONVICTION", "ACCUMULATION"}
    if mkt_signal == "UNKNOWN":
        mkt_align = "UNKNOWN"
    elif final_signal in bullish_finals and mkt_signal == "BULLISH":
        mkt_align = "CONFIRMING"
    elif final_signal in bullish_finals and mkt_signal == "BEARISH":
        mkt_align = "CONTRADICTING"
    elif final_signal in BEARISH and mkt_signal == "BEARISH":
        mkt_align = "CONFIRMING"
    elif final_signal in BEARISH and mkt_signal == "BULLISH":
        mkt_align = "CONTRADICTING"
    else:
        mkt_align = "NEUTRAL"

    # Final score blends composite + market
    final_score = (round(composite_score * COMPOSITE_W + market_score * MARKET_W)
                   if market_score is not None else composite_score)

    # --- Signal quality ---
    quality_score = 0
    quality_factors = []

    if ins_bullish: quality_score+=1; quality_factors.append("insider_bullish")
    elif ins_bearish: quality_score+=1; quality_factors.append("insider_bearish")
    if brk_bullish: quality_score+=1; quality_factors.append("broker_bullish")
    elif brk_bearish: quality_score+=1; quality_factors.append("broker_bearish")
    if mkt_align == "CONFIRMING":   quality_score+=1; quality_factors.append("market_confirming")
    elif mkt_align == "CONTRADICTING": quality_score-=1; quality_factors.append("market_contradicting")
    if ins and ins.get("key_person_activity"): quality_score+=1; quality_factors.append("key_person_active")
    if "ALIGNED_BULLISH" in tags or "ALIGNED_BEARISH" in tags:
        quality_score+=1; quality_factors.append("dual_source_aligned")
    if has_yf:
        ctx = yf_data.get("context", {})
        if ctx.get("volume", {}).get("spike_today"): quality_score+=1; quality_factors.append("volume_spike")
        rsi_val = ctx.get("rsi_14")
        if rsi_val and rsi_val < 30: quality_factors.append("rsi_oversold")
        if rsi_val and rsi_val > 70: quality_factors.append("rsi_overbought")
        macd_data = ctx.get("macd") or {}
        if macd_data.get("trend") == "BULLISH": quality_factors.append("macd_bullish")
        elif macd_data.get("trend") == "BEARISH": quality_factors.append("macd_bearish")
    if "DIVERGENT" in tags: quality_score-=1; quality_factors.append("signal_divergent")
    if noisy: quality_score-=1; quality_factors.append("noisy_data")
    if insider_only or broker_only: quality_factors.append("single_source")

    quality_score = max(0, quality_score)
    signal_quality = ("STRONG" if quality_score>=3 else
                      "MODERATE" if quality_score>=2 else
                      "WEAK" if quality_score>=1 else "NOISE")

    # Compact market context
    market_context = None
    if has_yf:
        ctx = yf_data.get("context", {})
        market_context = {
            "last_close":     ctx.get("last_close"),
            "sma20":          ctx.get("sma20"),
            "sma50":          ctx.get("sma50"),
            "above_sma20":    ctx.get("above_sma20"),
            "above_sma50":    ctx.get("above_sma50"),
            "rsi_14":         ctx.get("rsi_14"),
            "macd_trend":     (ctx.get("macd") or {}).get("trend"),
            "macd_histogram": (ctx.get("macd") or {}).get("histogram"),
            "volume_trend":   (ctx.get("volume") or {}).get("trend"),
            "volume_ratio_7d":(ctx.get("volume") or {}).get("ratio_7d"),
            "volume_spike":   (ctx.get("volume") or {}).get("spike_today"),
            "momentum":       ctx.get("momentum"),
            "pct_from_high":  ctx.get("pct_from_high"),
            "pct_from_low":   ctx.get("pct_from_low"),
        }

    return {
        # SKILL.md unified schema
        "symbol":            symbol,
        "final_signal":      final_signal,
        "composite_score":   composite_score,
        "insider_score":     ins_score,
        "broker_score":      brk_score,
        "insider_alignment": ins_bullish,
        "broker_alignment":  brk_bullish,
        "net_flow_insider":  ins.get("net_volume", 0)    if ins else 0,
        "net_flow_broker":   brk.get("net_value_idr", 0) if brk else 0,
        "conviction_level":  conviction(composite_score),
        "tags":              tags,
        # Enrichment
        "market_score":      market_score,
        "market_signal":     mkt_signal,
        "market_alignment":  mkt_align,
        "final_score":       final_score,
        "signal_quality":    signal_quality,
        "quality_factors":   quality_factors,
        "market_context":    market_context,
        "broker_tier":   broker_tier,
        # Source snapshots
        "insider": {
            "signal":               ins["signal"],
            "score":                ins["score"],
            "key_person_activity":  ins["key_person_activity"],
            "key_person_buys":      ins["key_person_buys"],
            "foreign_accumulation": ins["foreign_accumulation"],
            "buy_volume":           ins["buy_volume"],
            "sell_volume":          ins["sell_volume"],
            "buy_ratio":            ins["buy_ratio"],
            "active_days":          ins["active_days"],
            "buy_days":             ins["buy_days"],
            "sell_days":            ins["sell_days"],
            "unique_actors":        ins["unique_actors"]
        } if ins else None,
        "broker": {
            "signal":         brk["signal"],
            "score":          brk["score"],
            "net_value_idr":  brk["net_value_idr"],
            "buy_ratio":      brk["buy_ratio"],
            "active_days":    brk["active_days"],
            "buy_days":       brk["buy_days"],
            "sell_days":      brk["sell_days"],
            "tags":           brk["tags"],
            "foreign":        brk["foreign"],
            "smart_money":    brk["smart_money"],
            "breadth":        brk["breadth"],
            "cluster":        brk["cluster"]
        } if brk else None,
    }

# ---------- TECHNICAL OPPORTUNITIES ----------
def build_tech_opportunities(insider_map, broker_map, yf_symbols):
    """
    Surface stocks with strong technicals but no insider/broker signal.
    Only from yfinance universe.
    """
    opps = []
    for symbol in yf_symbols:
        # Skip if already has insider or broker signal
        if symbol in insider_map or symbol in broker_map:
            continue

        yf_data = load_yf(symbol)
        if not yf_data: continue

        ms  = yf_data.get("market_score", 0)
        ctx = yf_data.get("context", {})
        if ms < TECH_OPP_MIN_SCORE: continue

        # Count how many bullish factors present
        factors = []
        rsi_val = ctx.get("rsi_14")
        macd_d  = ctx.get("macd") or {}
        vol_d   = ctx.get("volume") or {}
        mom_d   = ctx.get("momentum") or {}

        if rsi_val and rsi_val < 35: factors.append("rsi_oversold")
        if macd_d.get("trend") == "BULLISH": factors.append("macd_bullish")
        if vol_d.get("trend") == "EXPANDING": factors.append("volume_expanding")
        if vol_d.get("spike_today"): factors.append("volume_spike")
        if ctx.get("above_sma20"): factors.append("above_sma20")
        if ctx.get("above_sma50"): factors.append("above_sma50")
        if mom_d.get("trend") == "UPTREND": factors.append("momentum_uptrend")
        pfl = ctx.get("pct_from_low")
        if pfl is not None and pfl < 5: factors.append("near_60d_low")

        if len(factors) < TECH_OPP_MIN_QUALITY_SCORE: continue

        opps.append({
            "symbol":         symbol,
            "market_score":   ms,
            "signal_quality": "MODERATE" if len(factors)>=3 else "WEAK",
            "factors":        factors,
            "note":           "Technical setup only — no insider/broker signal",
            "context": {
                "last_close":     ctx.get("last_close"),
                "rsi_14":         rsi_val,
                "macd_trend":     macd_d.get("trend"),
                "volume_trend":   vol_d.get("trend"),
                "volume_spike":   vol_d.get("spike_today"),
                "above_sma20":    ctx.get("above_sma20"),
                "above_sma50":    ctx.get("above_sma50"),
                "momentum":       mom_d,
                "pct_from_high":  ctx.get("pct_from_high"),
                "pct_from_low":   pfl,
            }
        })

    return sorted(opps, key=lambda x: -x["market_score"])

# ---------- MAIN ----------
def main():
    print("🔀 loading sources...", file=sys.stderr)

    insider_data = load_json(INSIDER_FILE, "insider")
    broker_data  = load_json(BROKER_FILE,  "broker")
    yf_symbols   = load_yf_index()

    insider_map = build_insider_map(insider_data)
    broker_map  = build_broker_map(broker_data)

    print(f"📥 insider: {len(insider_map)} | broker: {len(broker_map)} | yfinance: {len(yf_symbols)}", file=sys.stderr)

    # All symbols that have insider or broker data
    ib_symbols = set(insider_map) | set(broker_map)

    # --- Build main signals ---
    signals = []
    for symbol in sorted(ib_symbols):
        ins     = insider_map.get(symbol)
        brk     = broker_map.get(symbol)
        yf_data = load_yf(symbol)
        record  = merge_symbol(symbol, ins, brk, yf_data)
        signals.append(record)

    # Sort: EXTREME → HIGH → ACCUMULATION → NEUTRAL → DISTRIBUTION, then by final_score
    sig_order = {"EXTREME_CONVICTION":0,"HIGH_CONVICTION":1,"ACCUMULATION":2,"NEUTRAL":3,"DISTRIBUTION":4}
    signals.sort(key=lambda r: (sig_order.get(r["final_signal"],9), -r["final_score"]))

    # --- Build technical opportunities ---
    tech_opps = build_tech_opportunities(insider_map, broker_map, yf_symbols)

    # --- Stats ---
    by_signal = {}
    by_conv   = {}
    by_qual   = {}
    by_align  = {}
    for r in signals:
        by_signal[r["final_signal"]]   = by_signal.get(r["final_signal"], 0) + 1
        by_conv[r["conviction_level"]] = by_conv.get(r["conviction_level"], 0) + 1
        by_qual[r["signal_quality"]]   = by_qual.get(r["signal_quality"], 0) + 1
        by_align[r["market_alignment"]]= by_align.get(r["market_alignment"], 0) + 1

    output = {
        "meta": {
            "generated_at":            datetime.now().isoformat(),
            "insider_generated":       insider_data.get("meta", {}).get("generated_at"),
            "broker_generated":        broker_data.get("meta", {}).get("generated_at"),
            "total_signals":           len(signals),
            "total_tech_opportunities":len(tech_opps),
            "insider_only":            sum(1 for r in signals if r["insider"] and not r["broker"]),
            "broker_only":             sum(1 for r in signals if not r["insider"] and r["broker"]),
            "both_sources":            sum(1 for r in signals if r["insider"] and r["broker"]),
            "yfinance_coverage":       sum(1 for r in signals if r["market_score"] is not None),
            "by_signal":               by_signal,
            "by_conviction":           by_conv,
            "by_quality":              by_qual,
            "market_alignment":        by_align,
        },
        # Primary section: insider/broker signals enriched with yfinance
        "signals": signals,
        # Secondary section: pure technical setups (no insider/broker activity)
        "technical_opportunities": tech_opps
    }

    ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(output, indent=2)
    (ENRICHED_DIR / f"{TODAY}.json").write_text(payload)
    (LATEST_DIR   / "unified_enriched.json").write_text(payload)

    print(f"💾 → data/unified_enriched/{TODAY}.json", file=sys.stderr)
    print(f"💾 → data/latest/unified_enriched.json", file=sys.stderr)
    print(f"✅ {len(signals)} signals + {len(tech_opps)} tech opportunities", file=sys.stderr)

if __name__ == "__main__":
    main()
