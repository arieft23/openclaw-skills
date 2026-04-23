#!/usr/bin/env python3
"""
enrich_unified.py — Single-pass merger for insider + broker + yfinance
v2: insider_strength model, dynamic weighting, timing_score, risk penalties
    New final_score = 0.5 * flow_score + 0.3 * prediction_score + 0.2 * timing_score
    (prediction_score defaults to composite_score until model is trained)
"""

import json, sys
from datetime import datetime, timezone
from pathlib import Path

# v2: import new scoring modules (graceful fallback if not present)
try:
    from compute_insider_strength import compute_insider_strength
    from compute_timing_score import compute_timing_score
    HAS_SCORING_MODULES = True
except ImportError:
    HAS_SCORING_MODULES = False
    print("⚠️  compute_insider_strength / compute_timing_score not found — using legacy scoring", file=sys.stderr)

BASE_DIR      = Path(__file__).parent
INSIDER_FILE  = BASE_DIR / "data" / "latest" / "insider.json"
BROKER_FILE   = BASE_DIR / "data" / "latest" / "broker.json"
YFINANCE_DIR  = BASE_DIR / "data" / "yfinance" / "latest"
ENRICHED_DIR  = BASE_DIR / "data" / "unified_enriched"
LATEST_DIR    = BASE_DIR / "data" / "latest"
TODAY         = datetime.now().strftime("%Y-%m-%d")

# ---------- WEIGHTS ----------
INSIDER_W_DEFAULT = 0.45
BROKER_W_DEFAULT  = 0.55
INSIDER_W_ONLY    = 0.70
BROKER_W_ONLY_W   = 0.30
INSIDER_W_ONLY_B  = 0.30
BROKER_W_ONLY     = 0.70

COMPOSITE_W = 0.70
MARKET_W    = 0.30

# v2 final_score formula
FLOW_W    = 0.50
PREDICT_W = 0.30   # placeholder weight — uses composite until model trained
TIMING_W  = 0.20

SINGLE_PENALTY_AMBIGUOUS = 0.8
SINGLE_PENALTY_MIXED     = 0.6

CONVICTION_LEVELS = [("EXTREME", 75), ("HIGH", 50), ("MEDIUM", 30), ("LOW", 0)]
MKT_BULLISH = 60
MKT_BEARISH = 40
TECH_OPP_MIN_SCORE         = 65
TECH_OPP_MIN_QUALITY_SCORE = 2
MIN_BROKER_UNIQUE = 2
MIN_BROKER_DAYS   = 2
BROKER_STRONG_MIN_BUY_RATIO         = 0.65
BROKER_STRONG_MIN_CLUSTER_DAYS      = 2
BROKER_STRONG_MIN_UNIQUE            = 8
BROKER_STRONG_MIN_SCORE             = 55
BROKER_STRONG_EXTREME_MIN_COMPOSITE = 65
STALE_WARN_HOURS   = 24
STALE_REFUSE_HOURS = 48
BULLISH = {"STRONG_ACCUMULATION", "ACCUMULATION"}
BEARISH = {"DISTRIBUTION"}

# ---------- HELPERS ----------
def load_json(path, label):
    if not path.exists():
        print(f"❌ {label} not found: {path}", file=sys.stderr); sys.exit(1)
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
    if score <= 0: return 0
    return min(100, round(100 * (1 - 1 / (1 + score / inflection))))

def single_penalty(buy_ratio):
    if buy_ratio is None: return SINGLE_PENALTY_MIXED
    return SINGLE_PENALTY_AMBIGUOUS if buy_ratio in (0, 0.0, 1, 1.0) else SINGLE_PENALTY_MIXED

def hours_since(iso_str):
    if not iso_str: return float('inf')
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except: return float('inf')

def check_source_staleness(insider_meta, broker_meta):
    ins_h = hours_since(insider_meta.get("generated_at"))
    brk_h = hours_since(broker_meta.get("generated_at"))
    warns = []
    if ins_h > STALE_REFUSE_HOURS: warns.append(f"REFUSE: insider {ins_h:.0f}h old.")
    elif ins_h > STALE_WARN_HOURS:  warns.append(f"WARN: insider {ins_h:.0f}h old.")
    if brk_h > STALE_REFUSE_HOURS: warns.append(f"REFUSE: broker {brk_h:.0f}h old.")
    elif brk_h > STALE_WARN_HOURS:  warns.append(f"WARN: broker {brk_h:.0f}h old.")
    drift = abs(ins_h - brk_h)
    if drift > 12 and ins_h < STALE_REFUSE_HOURS and brk_h < STALE_REFUSE_HOURS:
        older = "insider" if ins_h > brk_h else "broker"
        warns.append(f"WARN: {older} data {drift:.0f}h older than counterpart.")
    return ins_h, brk_h, warns

# ---------- DYNAMIC WEIGHTING (v2) ----------
def compute_dynamic_weights(insider_only, broker_only, insider_strength_score, brk):
    if insider_only: return 0.70, 0.30
    if broker_only:  return 0.30, 0.70
    brk_strength = normalize(brk.get("score", 0) or 0) if brk else 0
    if insider_strength_score >= 70: return 0.65, 0.35
    elif brk_strength >= 70:         return 0.35, 0.65
    else:                             return 0.50, 0.50

def process_broker_score(brk_raw, buy_ratio, cluster_days, unique_brokers, broker_only):
    if not broker_only: return normalize(brk_raw), "DUAL"
    is_strong = (brk_raw >= BROKER_STRONG_MIN_SCORE and buy_ratio >= BROKER_STRONG_MIN_BUY_RATIO
                 and cluster_days >= BROKER_STRONG_MIN_CLUSTER_DAYS and unique_brokers >= BROKER_STRONG_MIN_UNIQUE)
    is_moderate = brk_raw >= 40 and buy_ratio >= 0.50
    if is_strong:    return round(min(brk_raw,100)*0.85), "STRONG"
    elif is_moderate: return round(normalize(brk_raw,75)*0.75), "MODERATE"
    else:             return round(normalize(brk_raw)*0.60), "WEAK"

def is_broker_noisy(brk):
    if not brk: return False
    return ((brk.get("breadth") or {}).get("unique_brokers",0) < MIN_BROKER_UNIQUE
            or brk.get("active_days",0) < MIN_BROKER_DAYS)

def is_buy_side_tag_valid(brk):
    if not brk: return False
    return brk.get("net_value_idr",0) > 0 or brk.get("buy_ratio",0) >= 0.5

def bollinger(closes, n=20):
    import statistics
    if len(closes) < n: return None
    w = closes[-n:]; m = sum(w)/n; s = statistics.stdev(w)
    u, l = m+2*s, m-2*s
    return {"upper":round(u,2),"middle":round(m,2),"lower":round(l,2),
            "bandwidth":round(4*s/m*100,2) if m else None,
            "pct_b":round((closes[-1]-l)/(u-l)*100,2) if (u-l)>0 else None}

def atr(ohlcv, n=14):
    if len(ohlcv)<n+1: return None
    trs=[max(ohlcv[i]["high"]-ohlcv[i]["low"],abs(ohlcv[i]["high"]-ohlcv[i-1]["close"]),
             abs(ohlcv[i]["low"]-ohlcv[i-1]["close"])) for i in range(1,len(ohlcv))]
    if len(trs)<n: return None
    v=sum(trs[:n])/n
    for tr in trs[n:]: v=(v*(n-1)+tr)/n
    lc=ohlcv[-1]["close"]
    return {"atr":round(v,2),"atr_pct":round(v/lc*100,2) if lc else None}

BUY_SIDE_TAGS = {"BROAD_BUY","CLUSTER_BUY","CLUSTER_BUY_WEAK"}

def build_insider_map(d): return {s["symbol"]:s for s in d.get("signals",[])}
def build_broker_map(d):  return {s["symbol"]:s for s in d.get("signals",[])}
def load_yf_index():
    p = YFINANCE_DIR/"_index.json"
    if not p.exists(): return []
    try: return json.loads(p.read_text()).get("symbols",[])
    except: return []

# ---------- MERGE ----------
def merge_symbol(symbol, ins, brk, yf_data):
    has_insider = ins is not None
    has_broker  = brk is not None
    has_yf      = yf_data is not None
    insider_only = has_insider and not has_broker
    broker_only  = has_broker  and not has_insider

    ins_signal  = ins["signal"] if ins else "NEUTRAL"
    brk_signal  = brk["signal"] if brk else "NEUTRAL"
    ins_bullish = ins_signal in BULLISH
    brk_bullish = brk_signal in BULLISH
    ins_bearish = ins_signal in BEARISH
    brk_bearish = brk_signal in BEARISH

    # v2: insider strength drives weighting
    if HAS_SCORING_MODULES and has_insider:
        isr = compute_insider_strength(ins)
    else:
        isr = {"insider_strength": normalize(ins["score"]) if ins else 0,
               "insider_label": "MODERATE_INSIDER", "components": {},
               "silent_buy_ratio": ins.get("buy_ratio",0.5) if ins else 0.5,
               "weighted_actor_score": 0.0}
    insider_strength_score = isr["insider_strength"]

    ins_w, brk_w = compute_dynamic_weights(insider_only, broker_only, insider_strength_score, brk)

    ins_raw   = normalize(ins["score"]) if ins else 0
    ins_score = (round(ins_raw * single_penalty(ins.get("buy_ratio") if ins else None))
                 if insider_only else ins_raw)
    brk_raw_score = brk["score"] if brk else 0
    brk_buy_ratio = brk.get("buy_ratio",0) if brk else 0
    brk_cluster   = (brk.get("cluster") or {}).get("cluster_days",0) if brk else 0
    brk_unique    = (brk.get("breadth") or {}).get("unique_brokers",0) if brk else 0
    brk_score, broker_tier = (process_broker_score(brk_raw_score,brk_buy_ratio,brk_cluster,brk_unique,broker_only)
                               if brk else (0,"NONE"))
    composite_score = round(ins_score*ins_w + brk_score*brk_w)

    # Final signal
    if ins_bullish and brk_bullish:
        final_signal = "EXTREME_CONVICTION" if composite_score>=65 else "HIGH_CONVICTION"
    elif ins_bearish and brk_bearish:   final_signal = "DISTRIBUTION"
    elif ins_bullish and brk_bearish:   final_signal = "NEUTRAL"
    elif ins_bearish and not brk_bullish: final_signal = "DISTRIBUTION"
    elif not ins_bearish and brk_bearish: final_signal = "DISTRIBUTION"
    elif ins_bullish or brk_bullish:    final_signal = "ACCUMULATION"
    else:                               final_signal = "NEUTRAL"

    # Tags
    tags = []
    if ins and ins_signal != "NEUTRAL": tags.append(f"insider:{ins_signal}")
    if ins:
        for t in (ins.get("tags") or []): tags.append(t)
    if brk:
        bsv = is_buy_side_tag_valid(brk)
        for t in (brk.get("tags") or []):
            if t in BUY_SIDE_TAGS and not bsv: continue
            tags.append(t)
    if ins_bullish and brk_bullish:  tags.append("ALIGNED_BULLISH")
    if ins_bearish and brk_bearish:  tags.append("ALIGNED_BEARISH")
    if ins_bullish and brk_bearish:  tags.append("DIVERGENT")
    if ins_bearish and brk_bullish:  tags.append("DIVERGENT")
    tags.append(isr["insider_label"])  # v2: STRONG_INSIDER / MODERATE_INSIDER / WEAK_INSIDER

    noisy = False
    if insider_only: tags.append("INSIDER_ONLY")
    if broker_only:
        tags.append("BROKER_ONLY")
        if broker_tier == "STRONG":
            tags.append("BROKER_STRONG")
            if composite_score >= BROKER_STRONG_EXTREME_MIN_COMPOSITE:
                if final_signal != "EXTREME_CONVICTION": final_signal = "EXTREME_CONVICTION"
            elif composite_score >= 40:
                if final_signal not in ("EXTREME_CONVICTION","HIGH_CONVICTION"): final_signal = "HIGH_CONVICTION"
        if is_broker_noisy(brk):
            tags.append("NOISY"); noisy = True
            if final_signal not in ("EXTREME_CONVICTION","HIGH_CONVICTION"): final_signal = "NEUTRAL"

    if insider_only and ins_bullish and ins_score >= 30: tags.append("EARLY_ACCUMULATION")

    # Market
    market_score = yf_data.get("market_score") if has_yf else None
    mkt_signal = ("BULLISH" if market_score is not None and market_score>=MKT_BULLISH else
                  "BEARISH" if market_score is not None and market_score<=MKT_BEARISH else
                  "NEUTRAL" if market_score is not None else "UNKNOWN")
    bullish_finals = {"EXTREME_CONVICTION","HIGH_CONVICTION","ACCUMULATION"}
    if mkt_signal == "UNKNOWN":                                       mkt_align = "UNKNOWN"
    elif final_signal in bullish_finals and mkt_signal=="BULLISH":    mkt_align = "CONFIRMING"
    elif final_signal in bullish_finals and mkt_signal=="BEARISH":    mkt_align = "CONTRADICTING"
    elif final_signal in BEARISH and mkt_signal=="BEARISH":           mkt_align = "CONFIRMING"
    elif final_signal in BEARISH and mkt_signal=="BULLISH":           mkt_align = "CONTRADICTING"
    else:                                                              mkt_align = "NEUTRAL"
    if final_signal=="DISTRIBUTION" and mkt_signal=="BULLISH": tags.append("REVERSAL_SETUP")

    # v2: timing score
    ctx = (yf_data.get("context") or {}) if has_yf else {}
    if HAS_SCORING_MODULES:
        tr = compute_timing_score(ins, brk, ctx, tags)
    else:
        tr = {"timing_score":50,"timing_label":"MODERATE","components":{},"penalty":0}
    timing_score = tr["timing_score"]

    # v2: flow_score (legacy composite + market)
    flow_score = (round(composite_score*COMPOSITE_W + market_score*MARKET_W)
                  if market_score is not None else composite_score)

    # v2: final_score formula — prediction_score is placeholder until model trained
    prediction_score = composite_score
    final_score = round(FLOW_W*flow_score + PREDICT_W*prediction_score + TIMING_W*timing_score)

    # Quality
    qs = 0; qf = []
    if ins_bullish: qs+=1; qf.append("insider_bullish")
    elif ins_bearish: qs+=1; qf.append("insider_bearish")
    if brk_bullish: qs+=1; qf.append("broker_bullish")
    elif brk_bearish: qs+=1; qf.append("broker_bearish")
    if mkt_align=="CONFIRMING":     qs+=1; qf.append("market_confirming")
    elif mkt_align=="CONTRADICTING": qs-=1; qf.append("market_contradicting")
    if ins and ins.get("key_person_activity"): qs+=1; qf.append("key_person_active")
    if ins and ins.get("multi_key_person"):    qs+=1; qf.append("multi_key_person")
    if "ALIGNED_BULLISH" in tags or "ALIGNED_BEARISH" in tags: qs+=1; qf.append("dual_source_aligned")
    if insider_strength_score >= 70: qs+=1; qf.append("strong_insider")
    if ctx:
        vd = ctx.get("volume") or {}
        if vd.get("spike_today"): qs+=1; qf.append("volume_spike")
        rv = ctx.get("rsi_14")
        if rv and rv<30: qf.append("rsi_oversold")
        if rv and rv>70: qf.append("rsi_overbought")
        md = ctx.get("macd") or {}
        if md.get("trend")=="BULLISH": qf.append("macd_bullish")
        elif md.get("trend")=="BEARISH": qf.append("macd_bearish")
    if "DIVERGENT" in tags: qs-=1; qf.append("signal_divergent")
    if noisy: qs-=1; qf.append("noisy_data")
    if insider_only or broker_only: qf.append("single_source")
    qs = max(0, qs)
    signal_quality = "STRONG" if qs>=3 else "MODERATE" if qs>=2 else "WEAK" if qs>=1 else "NOISE"

    # Market context
    market_context = None
    if has_yf:
        ohlcv  = yf_data.get("ohlcv") or []
        closes = [d["close"] for d in ohlcv]
        bb     = bollinger(closes) if len(closes)>=20 else None
        atr_v  = atr(ohlcv) if len(ohlcv)>=15 else None
        market_context = {
            "last_close":     ctx.get("last_close"),
            "sma20":          ctx.get("sma20"),       "sma50":       ctx.get("sma50"),
            "above_sma20":    ctx.get("above_sma20"), "above_sma50": ctx.get("above_sma50"),
            "rsi_14":         ctx.get("rsi_14"),
            "macd_trend":     (ctx.get("macd") or {}).get("trend"),
            "macd_histogram": (ctx.get("macd") or {}).get("histogram"),
            "volume_trend":   (ctx.get("volume") or {}).get("trend"),
            "volume_ratio_7d":(ctx.get("volume") or {}).get("ratio_7d"),
            "volume_spike":   (ctx.get("volume") or {}).get("spike_today"),
            "momentum":       ctx.get("momentum"),
            "pct_from_high":  ctx.get("pct_from_high"), "pct_from_low": ctx.get("pct_from_low"),
            "bollinger":      bb, "atr": atr_v,
        }

    return {
        "symbol": symbol, "final_signal": final_signal,
        "composite_score": composite_score, "insider_score": ins_score, "broker_score": brk_score,
        "insider_weight": ins_w, "broker_weight": brk_w,
        "insider_alignment": ins_bullish, "broker_alignment": brk_bullish,
        "net_flow_insider": ins.get("net_volume",0) if ins else 0,
        "net_flow_broker":  brk.get("net_value_idr",0) if brk else 0,
        "conviction_level": conviction(composite_score), "tags": tags,
        # v2 scoring fields
        "insider_strength":    insider_strength_score,
        "insider_label":       isr["insider_label"],
        "insider_components":  isr["components"],
        "timing_score":        timing_score,
        "timing_label":        tr["timing_label"],
        "timing_components":   tr["components"],
        "flow_score":          flow_score,
        "prediction_score":    prediction_score,
        "prediction_label":    "NEUTRAL",        # placeholder
        "prediction_confidence": "LOW",          # placeholder
        "final_score":         final_score,
        # Market
        "market_score": market_score, "market_signal": mkt_signal, "market_alignment": mkt_align,
        "signal_quality": signal_quality, "quality_factors": qf,
        "market_context": market_context, "broker_tier": broker_tier,
        # Snapshots
        "insider": {
            "signal": ins["signal"], "score": ins["score"],
            "key_person_activity": ins["key_person_activity"], "key_person_buys": ins["key_person_buys"],
            "foreign_accumulation": ins["foreign_accumulation"],
            "buy_volume": ins["buy_volume"], "sell_volume": ins["sell_volume"],
            "buy_ratio": ins["buy_ratio"], "active_days": ins["active_days"],
            "buy_days": ins["buy_days"], "sell_days": ins["sell_days"],
            "unique_actors": ins["unique_actors"],
            "multi_key_person": ins.get("multi_key_person", False),
            "insider_cluster_buy": ins.get("insider_cluster_buy", False),
            "recency_ratio": ins.get("recency_ratio"),
            "silent_buy_ratio": isr["silent_buy_ratio"],
            "weighted_actor_score": isr["weighted_actor_score"],
        } if ins else None,
        "broker": {
            "signal": brk["signal"], "score": brk["score"],
            "net_value_idr": brk["net_value_idr"], "buy_ratio": brk["buy_ratio"],
            "active_days": brk["active_days"], "buy_days": brk["buy_days"], "sell_days": brk["sell_days"],
            "consistency": brk.get("consistency"),
            "tags": brk["tags"], "foreign": brk["foreign"],
            "smart_money": brk["smart_money"], "breadth": brk["breadth"], "cluster": brk["cluster"],
        } if brk else None,
    }

# ---------- TECH OPPORTUNITIES ----------
def build_tech_opportunities(insider_map, broker_map, yf_symbols):
    opps = []
    for symbol in yf_symbols:
        if symbol in insider_map or symbol in broker_map: continue
        yf_data = load_yf(symbol)
        if not yf_data: continue
        ms  = yf_data.get("market_score", 0)
        ctx = yf_data.get("context") or {}
        if not ctx or ms < TECH_OPP_MIN_SCORE: continue

        factors = []
        rsi_val = ctx.get("rsi_14"); macd_d = ctx.get("macd") or {}
        vol_d = ctx.get("volume") or {}; mom_d = ctx.get("momentum") or {}
        if rsi_val and rsi_val<35: factors.append("rsi_oversold")
        if macd_d.get("trend")=="BULLISH": factors.append("macd_bullish")
        if vol_d.get("trend")=="EXPANDING": factors.append("volume_expanding")
        if vol_d.get("spike_today"): factors.append("volume_spike")
        if ctx.get("above_sma20"): factors.append("above_sma20")
        if ctx.get("above_sma50"): factors.append("above_sma50")
        if mom_d.get("trend")=="UPTREND": factors.append("momentum_uptrend")
        pfl = ctx.get("pct_from_low")
        if pfl is not None and pfl<5: factors.append("near_60d_low")

        combo = (("rsi_oversold" in factors and "macd_bullish" in factors) or
                 ("volume_spike" in factors and "above_sma20" in factors))
        if not combo and len(factors) < TECH_OPP_MIN_QUALITY_SCORE: continue

        tr = compute_timing_score(None, None, ctx, factors) if HAS_SCORING_MODULES else {"timing_score":50,"timing_label":"MODERATE"}
        ohlcv = yf_data.get("ohlcv") or []
        closes = [d["close"] for d in ohlcv]

        opps.append({
            "symbol": symbol, "market_score": ms,
            "timing_score": tr["timing_score"], "timing_label": tr["timing_label"],
            "signal_quality": "MODERATE" if len(factors)>=3 else "WEAK",
            "factors": factors, "note": "Technical setup only — no insider/broker signal",
            "context": {
                "last_close": ctx.get("last_close"), "rsi_14": rsi_val,
                "macd_trend": macd_d.get("trend"), "volume_trend": vol_d.get("trend"),
                "volume_spike": vol_d.get("spike_today"),
                "above_sma20": ctx.get("above_sma20"), "above_sma50": ctx.get("above_sma50"),
                "momentum": mom_d, "pct_from_high": ctx.get("pct_from_high"), "pct_from_low": pfl,
                "bollinger": bollinger(closes) if len(closes)>=20 else None,
                "atr": atr(yf_data.get("ohlcv") or []) if len(yf_data.get("ohlcv") or [])>=15 else None,
            }
        })
    return sorted(opps, key=lambda x: -(x["market_score"]*0.7 + x["timing_score"]*0.3))

# ---------- MAIN ----------
def main():
    print("🔀 loading sources...", file=sys.stderr)
    insider_data = load_json(INSIDER_FILE, "insider")
    broker_data  = load_json(BROKER_FILE,  "broker")
    yf_symbols   = load_yf_index()

    ins_hours, brk_hours, stale_warnings = check_source_staleness(
        insider_data.get("meta",{}), broker_data.get("meta",{}))
    if stale_warnings:
        for w in stale_warnings: print(f"⚠️  {w}", file=sys.stderr)
        if any("REFUSE" in w for w in stale_warnings):
            print("❌ Aborting: stale data.", file=sys.stderr); sys.exit(1)

    insider_map = build_insider_map(insider_data)
    broker_map  = build_broker_map(broker_data)
    mode = "v2" if HAS_SCORING_MODULES else "v1_legacy"
    print(f"📥 insider:{len(insider_map)} ({ins_hours:.0f}h) | broker:{len(broker_map)} ({brk_hours:.0f}h) | yf:{len(yf_symbols)} | mode:{mode}", file=sys.stderr)

    ib_symbols = set(insider_map) | set(broker_map)
    signals = []
    for symbol in sorted(ib_symbols):
        signals.append(merge_symbol(symbol, insider_map.get(symbol), broker_map.get(symbol), load_yf(symbol)))

    sig_order = {"EXTREME_CONVICTION":0,"HIGH_CONVICTION":1,"ACCUMULATION":2,"NEUTRAL":3,"DISTRIBUTION":4}
    signals.sort(key=lambda r: (sig_order.get(r["final_signal"],9), -r["final_score"]))

    tech_opps = build_tech_opportunities(insider_map, broker_map, yf_symbols)

    by_s={}; by_c={}; by_q={}; by_a={}; by_t={}
    for r in signals:
        by_s[r["final_signal"]]    = by_s.get(r["final_signal"],0)+1
        by_c[r["conviction_level"]]= by_c.get(r["conviction_level"],0)+1
        by_q[r["signal_quality"]]  = by_q.get(r["signal_quality"],0)+1
        by_a[r["market_alignment"]]= by_a.get(r["market_alignment"],0)+1
        by_t[r["timing_label"]]    = by_t.get(r["timing_label"],0)+1

    output = {
        "meta": {
            "generated_at": datetime.now().isoformat(), "scoring_version": mode,
            "insider_generated": insider_data.get("meta",{}).get("generated_at"),
            "broker_generated":  broker_data.get("meta",{}).get("generated_at"),
            "insider_age_hours": round(ins_hours,1), "broker_age_hours": round(brk_hours,1),
            "staleness_warnings": stale_warnings,
            "total_signals": len(signals), "total_tech_opportunities": len(tech_opps),
            "insider_only":   sum(1 for r in signals if r["insider"] and not r["broker"]),
            "broker_only":    sum(1 for r in signals if not r["insider"] and r["broker"]),
            "both_sources":   sum(1 for r in signals if r["insider"] and r["broker"]),
            "yfinance_coverage": sum(1 for r in signals if r["market_score"] is not None),
            "by_signal": by_s, "by_conviction": by_c, "by_quality": by_q,
            "market_alignment": by_a, "by_timing": by_t,
        },
        "signals": signals,
        "technical_opportunities": tech_opps,
    }

    ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(output, indent=2)
    (ENRICHED_DIR/f"{TODAY}.json").write_text(payload)
    (LATEST_DIR/"unified_enriched.json").write_text(payload)
    print(f"💾 → data/unified_enriched/{TODAY}.json", file=sys.stderr)
    print(f"✅ {len(signals)} signals + {len(tech_opps)} tech opps", file=sys.stderr)

if __name__ == "__main__":
    main()
