#!/usr/bin/env python3
"""
compute_insider_strength.py — Structured insider strength model (v2)

Replaces flat insider score with a 5-component model:
  A. Volume Strength   (0–30)
  B. Actor Quality     (0–25)
  C. Consistency       (0–20)
  D. Recency           (0–15)
  E. Structure         (0–10)

Max total: 100

Usage:
  from compute_insider_strength import compute_insider_strength
  result = compute_insider_strength(insider_snapshot)

Input: insider snapshot dict (from unified_enriched.json or insider.json signals)
Output: dict with insider_strength (0–100), label, component breakdown, derived metrics
"""

import math


# ---------- COMPONENT WEIGHTS ----------
MAX_VOLUME_STRENGTH  = 30
MAX_ACTOR_QUALITY    = 25
MAX_CONSISTENCY      = 20
MAX_RECENCY          = 15
MAX_STRUCTURE        = 10

# ---------- LABELS ----------
STRONG_THRESHOLD   = 70
MODERATE_THRESHOLD = 40


def _clamp(value, lo=0, hi=1):
    return max(lo, min(hi, value))


# ---------- A. VOLUME STRENGTH (0–30) ----------
def _volume_strength(ins: dict) -> float:
    """
    Measures how decisive the volume signal is.
    - Normalized net volume (log10 scale, capped at 20 pts)
    - Buy ratio conviction (capped at 10 pts)
    """
    net_vol   = abs(ins.get("net_volume", 0) or 0)
    buy_ratio = ins.get("buy_ratio", 0.5) or 0.5
    buy_vol   = ins.get("buy_volume", 0) or 0
    sell_vol  = ins.get("sell_volume", 0) or 0
    total_vol = buy_vol + sell_vol

    # Log10 normalization: 1M shares ≈ 7 pts, 10M ≈ 13 pts, 100M ≈ 20 pts
    vol_score = min(20, math.log10(net_vol + 1) * 3.0) if net_vol > 0 else 0

    # Buy ratio conviction: 0.5 = neutral, 1.0 = max conviction
    # Scaled so 0.5→0, 0.7→5, 1.0→10
    ratio_score = max(0, (buy_ratio - 0.5) / 0.5) * 10

    return round(vol_score + ratio_score, 2)


# ---------- B. ACTOR QUALITY (0–25) ----------
def _actor_quality(ins: dict) -> float:
    """
    Measures who is trading, not just how much.
    - key_person_activity: +10
    - multi_key_person:    +10
    - unique_actors scale: +5 (log-scaled, caps at 5)
    """
    score = 0.0

    if ins.get("key_person_activity"):
        score += 10
        # Additional bonus per key person buy transaction (capped at 5)
        kp_buys = min(5, ins.get("key_person_buys", 0) or 0)
        score += kp_buys * 0.5  # up to +2.5 bonus

    if ins.get("multi_key_person"):
        score += 10

    unique_actors = ins.get("unique_actors", 1) or 1
    actor_scale = min(5, math.log10(unique_actors + 1) * 4)
    score += actor_scale

    return round(min(MAX_ACTOR_QUALITY, score), 2)


# ---------- C. CONSISTENCY (0–20) ----------
def _consistency(ins: dict) -> float:
    """
    Measures how steady the buying pattern is over time.
    - buy_days / active_days ratio
    - cluster bonus for repeated multi-day buying
    """
    active_days = ins.get("active_days", 0) or 0
    buy_days    = ins.get("buy_days", 0) or 0

    if active_days == 0:
        return 0.0

    # Directional consistency: what fraction of active days were net buying
    consistency_ratio = buy_days / active_days
    consistency_score = consistency_ratio * 15  # up to 15 pts

    # Cluster bonus: insider_cluster_buy = buying on 3+ separate days by key person
    cluster_bonus = 5 if ins.get("insider_cluster_buy") else 0

    return round(min(MAX_CONSISTENCY, consistency_score + cluster_bonus), 2)


# ---------- D. RECENCY (0–15) ----------
def _recency(ins: dict) -> float:
    """
    Measures how fresh the signal is.
    - recency_ratio: fraction of activity in last 14 days
    - Recent signals get full weight; stale signals get penalized
    """
    recency_ratio = ins.get("recency_ratio", 0) or 0
    # Linear: 0 → 0 pts, 1.0 → 15 pts
    return round(_clamp(recency_ratio) * MAX_RECENCY, 2)


# ---------- E. STRUCTURE (0–10) ----------
def _structure(ins: dict) -> float:
    """
    Captures structural signal patterns.
    - insider_cluster_buy: sustained buying across days (+5)
    - foreign_accumulation: foreign insider participating (+3)
    - Signal type bonus: STRONG_ACCUMULATION vs ACCUMULATION vs DISTRIBUTION
    """
    score = 0.0

    if ins.get("insider_cluster_buy"):
        score += 5

    if ins.get("foreign_accumulation"):
        score += 3

    signal = ins.get("signal", "NEUTRAL")
    if signal == "STRONG_ACCUMULATION":
        score += 2
    elif signal == "ACCUMULATION":
        score += 1

    return round(min(MAX_STRUCTURE, score), 2)


# ---------- SILENT BUY RATIO ----------
def _silent_buy_ratio(ins: dict) -> float:
    """
    Fraction of total volume that was buying.
    Same as buy_ratio but named explicitly for clarity in downstream features.
    """
    buy_vol  = ins.get("buy_volume", 0) or 0
    sell_vol = ins.get("sell_volume", 0) or 0
    total    = buy_vol + sell_vol
    return round(buy_vol / total, 4) if total > 0 else 0.5


# ---------- WEIGHTED ACTOR SCORE ----------
def _weighted_actor_score(ins: dict) -> float:
    """
    Composite actor quality index (0–1).
    Key persons weighted 3x vs regular actors.
    Used as a feature in prediction model.
    """
    key_person_buys = ins.get("key_person_buys", 0) or 0
    unique_actors   = ins.get("unique_actors", 0) or 0

    if unique_actors == 0:
        return 0.0

    # Estimate non-key-person actors
    non_key = max(0, unique_actors - min(key_person_buys, unique_actors))
    weighted = (key_person_buys * 3 + non_key * 1)
    max_weighted = unique_actors * 3  # if all were key persons

    return round(weighted / max_weighted, 4) if max_weighted > 0 else 0.0


# ---------- MAIN FUNCTION ----------
def compute_insider_strength(ins: dict) -> dict:
    """
    Compute structured insider strength from an insider snapshot.

    Args:
        ins: insider snapshot dict (from unified_enriched insider field,
             or directly from insider.json signals array)

    Returns:
        dict with:
          insider_strength         (0–100)
          insider_label            STRONG_INSIDER | MODERATE_INSIDER | WEAK_INSIDER
          components               breakdown dict
          silent_buy_ratio         (0–1)
          weighted_actor_score     (0–1)
    """
    if ins is None:
        return {
            "insider_strength":     0,
            "insider_label":        "WEAK_INSIDER",
            "components":           {"volume": 0, "actor": 0, "consistency": 0, "recency": 0, "structure": 0},
            "silent_buy_ratio":     0.5,
            "weighted_actor_score": 0.0,
        }

    vol   = _volume_strength(ins)
    actor = _actor_quality(ins)
    cons  = _consistency(ins)
    rec   = _recency(ins)
    struc = _structure(ins)

    total = round(vol + actor + cons + rec + struc, 2)
    total = min(100, total)

    label = (
        "STRONG_INSIDER"   if total >= STRONG_THRESHOLD   else
        "MODERATE_INSIDER" if total >= MODERATE_THRESHOLD else
        "WEAK_INSIDER"
    )

    return {
        "insider_strength":     total,
        "insider_label":        label,
        "components": {
            "volume":      vol,
            "actor":       actor,
            "consistency": cons,
            "recency":     rec,
            "structure":   struc,
        },
        "silent_buy_ratio":     _silent_buy_ratio(ins),
        "weighted_actor_score": _weighted_actor_score(ins),
    }


# ---------- CLI (for testing) ----------
if __name__ == "__main__":
    import json, sys
    if len(sys.argv) > 1:
        data = json.loads(open(sys.argv[1]).read())
        signals = data.get("signals", [data])
        for s in signals[:5]:
            result = compute_insider_strength(s)
            print(f"{s.get('symbol','?'):8s}  strength={result['insider_strength']:5.1f}  {result['insider_label']}")
            print(f"          components: {result['components']}")
    else:
        # Quick smoke test
        test = {
            "signal": "STRONG_ACCUMULATION",
            "score": 85,
            "key_person_activity": True,
            "key_person_buys": 3,
            "multi_key_person": True,
            "foreign_accumulation": False,
            "buy_volume": 8_000_000,
            "sell_volume": 2_000_000,
            "buy_ratio": 0.80,
            "active_days": 5,
            "buy_days": 4,
            "sell_days": 1,
            "unique_actors": 3,
            "insider_cluster_buy": True,
            "recency_ratio": 0.8,
        }
        result = compute_insider_strength(test)
        print(json.dumps(result, indent=2))
