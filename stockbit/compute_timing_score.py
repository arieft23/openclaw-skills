#!/usr/bin/env python3
"""
compute_timing_score.py — Entry timing score (v2)

Estimates whether NOW is a good entry point for a signal.
High score = fast-moving, timely signal
Low score  = slow accumulation, early/no urgency

Components:
  A. Recency       (0–30) — how recent is the insider/broker activity
  B. Volume        (0–30) — volume confirmation today vs baseline
  C. Momentum      (0–25) — price momentum acceleration
  D. Market Setup  (0–15) — Bollinger + RSI positioning

Usage:
  from compute_timing_score import compute_timing_score
  result = compute_timing_score(insider_snapshot, broker_snapshot, market_context)
"""


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


# ---------- A. RECENCY (0–30) ----------
def _recency_score(ins: dict, brk: dict) -> float:
    """
    How fresh is the signal? Combines insider recency and broker active days.
    """
    score = 0.0

    # Insider recency ratio (0→0, 1→20 pts)
    if ins:
        recency_ratio = ins.get("recency_ratio", 0) or 0
        score += _clamp(recency_ratio) * 20

    # Broker buy_days relative to days_back window (7 days)
    if brk:
        buy_days   = brk.get("buy_days", 0) or 0
        sell_days  = brk.get("sell_days", 0) or 0
        active_days = brk.get("active_days", 1) or 1
        brk_recency = buy_days / active_days
        score += _clamp(brk_recency) * 10

    return round(min(30, score), 2)


# ---------- B. VOLUME (0–30) ----------
def _volume_score(ctx: dict) -> float:
    """
    Volume confirmation: is today's volume unusually high?
    Also rewards expanding volume trend.
    """
    if not ctx:
        return 0.0

    vol = ctx.get("volume") or {}
    score = 0.0

    ratio_1d = vol.get("ratio_1d", 1.0) or 1.0
    ratio_7d = vol.get("ratio_7d", 1.0) or 1.0

    # Today's volume vs 60d avg
    # 1x = 0 pts, 2x = 15 pts, 3x+ = 20 pts (capped)
    if ratio_1d >= 3.0:
        score += 20
    elif ratio_1d >= 2.0:
        score += 15
    elif ratio_1d >= 1.5:
        score += 8
    elif ratio_1d >= 1.2:
        score += 3

    # Volume trend: expanding 7d avg vs 60d avg
    if ratio_7d >= 1.5:
        score += 10
    elif ratio_7d >= 1.2:
        score += 5
    elif ratio_7d < 0.8:
        score -= 5  # contracting volume = bad timing

    return round(min(30, max(0, score)), 2)


# ---------- C. MOMENTUM ACCELERATION (0–25) ----------
def _momentum_score(ctx: dict) -> float:
    """
    Measures price momentum and whether it's accelerating.
    Uses 5D vs 20D return to detect early vs late move.
    """
    if not ctx:
        return 0.0

    mom = ctx.get("momentum") or {}
    score = 0.0

    r5  = mom.get("return_5d",  0) or 0
    r20 = mom.get("return_20d", 0) or 0

    # Raw momentum direction
    if r5 > 0 and r20 > 0:
        score += 10   # both positive = confirmed uptrend
    elif r5 > 0 and r20 <= 0:
        score += 8    # 5D positive but 20D flat/negative = early reversal
    elif r5 <= 0 and r20 > 0:
        score += 3    # late move — 5D cooling off
    # both negative = 0

    # Acceleration: 5D return > 20D/4 means momentum is fresh
    if r5 > 0 and r20 > 0:
        acceleration = r5 - (r20 / 4)
        if acceleration > 2:
            score += 10   # strong acceleration
        elif acceleration > 0:
            score += 5    # mild acceleration
        elif acceleration < -3:
            score -= 5    # decelerating — late signal

    # MACD confirmation
    macd = ctx.get("macd") or {}
    hist = macd.get("histogram", 0) or 0
    if macd.get("trend") == "BULLISH":
        score += 5
        if hist > 0:
            score += min(5, hist * 10)  # stronger histogram = better timing
    elif macd.get("trend") == "BEARISH":
        score -= 5

    return round(min(25, max(0, score)), 2)


# ---------- D. MARKET SETUP (0–15) ----------
def _market_setup_score(ctx: dict) -> float:
    """
    Rewards entry when price is at a favorable position on Bollinger Bands and RSI.
    Best timing = oversold RSI + price near lower Bollinger band.
    Worst timing = overbought RSI + price near upper band.
    """
    if not ctx:
        return 0.0

    score = 7.5  # neutral baseline

    rsi = ctx.get("rsi_14")
    bb  = ctx.get("bollinger") or {}
    pct_b = bb.get("pct_b")

    # RSI positioning
    if rsi is not None:
        if rsi < 30:
            score += 5    # oversold — great entry
        elif rsi < 45:
            score += 2
        elif rsi > 70:
            score -= 5    # overbought — bad timing
        elif rsi > 60:
            score -= 2

    # Bollinger %B positioning
    # pct_b: 0=at lower band, 50=middle, 100=at upper band
    if pct_b is not None:
        if pct_b < 20:
            score += 3    # near lower band — good entry
        elif pct_b < 40:
            score += 1
        elif pct_b > 80:
            score -= 3    # near upper band — extended
        elif pct_b > 60:
            score -= 1

    return round(min(15, max(0, score)), 2)


# ---------- RISK PENALTIES ----------
def _risk_penalties(ctx: dict, tags: list) -> float:
    """
    Applies penalties for adverse conditions.
    Returns a negative adjustment (0 to -20).
    """
    penalty = 0.0

    rsi  = (ctx.get("rsi_14") or 0) if ctx else 0
    macd = (ctx.get("macd") or {}) if ctx else {}
    atr  = (ctx.get("atr") or {}) if ctx else {}
    vol  = (ctx.get("volume") or {}) if ctx else {}

    # Divergence risk: RSI overbought + MACD bearish
    if rsi > 70 and macd.get("trend") == "BEARISH":
        penalty -= 8

    # NOISY tag
    if "NOISY" in tags:
        penalty -= 5

    # DIVERGENT tag
    if "DIVERGENT" in tags:
        penalty -= 5

    # High ATR without volume/broker confirmation
    atr_pct = atr.get("atr_pct", 0) or 0
    spike   = vol.get("spike_today", False)
    if atr_pct > 20:
        if not spike:
            penalty -= 6   # high volatility without volume = dangerous entry
        # if spike present, allow (per spec)

    return round(max(-20, penalty), 2)


# ---------- MAIN FUNCTION ----------
def compute_timing_score(ins: dict, brk: dict, ctx: dict, tags: list = None) -> dict:
    """
    Compute entry timing score.

    Args:
        ins:  insider snapshot (or None)
        brk:  broker snapshot (or None)
        ctx:  market_context dict from yfinance (or None)
        tags: list of signal tags (for penalty checks)

    Returns:
        dict with:
          timing_score       (0–100)
          timing_label       FAST | MODERATE | SLOW
          components         breakdown
          penalty            applied risk penalty
    """
    tags = tags or []

    rec  = _recency_score(ins, brk)
    vol  = _volume_score(ctx)
    mom  = _momentum_score(ctx)
    mkt  = _market_setup_score(ctx)
    pen  = _risk_penalties(ctx, tags)

    raw   = rec + vol + mom + mkt
    total = round(max(0, min(100, raw + pen)), 2)

    label = (
        "FAST"     if total >= 65 else
        "MODERATE" if total >= 35 else
        "SLOW"
    )

    return {
        "timing_score": total,
        "timing_label": label,
        "components": {
            "recency":      rec,
            "volume":       vol,
            "momentum":     mom,
            "market_setup": mkt,
        },
        "penalty": pen,
    }


# ---------- CLI ----------
if __name__ == "__main__":
    import json, sys

    # Smoke test
    ctx_test = {
        "rsi_14": 38,
        "macd": {"trend": "BULLISH", "histogram": 0.15},
        "volume": {"ratio_1d": 2.5, "ratio_7d": 1.4, "spike_today": True, "trend": "EXPANDING"},
        "momentum": {"return_5d": 3.2, "return_20d": 6.1},
        "bollinger": {"pct_b": 25},
        "atr": {"atr_pct": 8},
    }
    ins_test = {"recency_ratio": 0.85, "buy_days": 4, "active_days": 5}
    brk_test = {"buy_days": 5, "sell_days": 1, "active_days": 6}
    tags_test = ["ALIGNED_BULLISH", "CLUSTER_BUY"]

    result = compute_timing_score(ins_test, brk_test, ctx_test, tags_test)
    print(json.dumps(result, indent=2))
