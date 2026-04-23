#!/usr/bin/env python3
"""
build_feature_dataset.py — Daily feature dataset builder

Runs after enrich_unified.py each day.
Appends one row per signal to data/features/dataset.csv with:
  - All model features (insider, broker, market, tag, scoring)
  - forward_return_5d  = None (filled later by fill_forward_returns.py)
  - forward_return_20d = None (filled later)
  - label_5d           = None (filled later: 1 if return > 0 else 0)
  - label_20d          = None (filled later)

Usage:
  python3 build_feature_dataset.py
  python3 build_feature_dataset.py --date 2026-04-22  (reprocess a specific date)
  python3 build_feature_dataset.py --dry-run          (print rows, don't write)
"""

import json, csv, sys, argparse
from datetime import datetime
from pathlib import Path

BASE_DIR     = Path(__file__).parent
UNIFIED_FILE = BASE_DIR / "data" / "latest" / "unified_enriched.json"
FEATURES_DIR = BASE_DIR / "data" / "features"
DATASET_FILE = FEATURES_DIR / "dataset.csv"

# ---------- FEATURE COLUMNS ----------
# Must stay stable — adding columns requires a migration
COLUMNS = [
    # Identity
    "date", "symbol", "final_signal", "conviction_level",

    # Insider features
    "insider_strength",
    "insider_buy_ratio",
    "insider_net_volume_norm",   # log10(|net_volume|) / 10
    "insider_key_person",        # 0/1
    "insider_multi_key_person",  # 0/1
    "insider_cluster_buy",       # 0/1
    "insider_recency_ratio",
    "insider_unique_actors",
    "insider_buy_days",
    "insider_active_days",
    "insider_weighted_actor",
    "insider_silent_buy_ratio",

    # Broker features
    "broker_buy_ratio",
    "broker_net_value_norm",     # log10(|net_value_idr|) / 15
    "broker_cluster_days",
    "broker_unique_brokers",
    "broker_foreign_net_norm",
    "broker_smart_money_net_norm",
    "broker_buy_days",
    "broker_active_days",
    "broker_consistency",

    # Market features
    "market_score",
    "rsi_14",
    "macd_bullish",              # 0/1
    "macd_histogram",
    "volume_spike",              # 0/1
    "volume_ratio_7d",
    "momentum_5d",
    "momentum_20d",
    "bollinger_pct_b",
    "atr_pct",
    "above_sma20",               # 0/1
    "above_sma50",               # 0/1
    "pct_from_high",
    "pct_from_low",

    # Scoring outputs
    "composite_score",
    "flow_score",
    "timing_score",
    "final_score",

    # Tag features (binary)
    "tag_aligned_bullish",
    "tag_cluster_buy",
    "tag_broad_buy",
    "tag_inflow",
    "tag_divergent",
    "tag_noisy",
    "tag_smart_money_dominant",
    "tag_foreign_dominant",
    "tag_multi_key_person",
    "tag_insider_cluster_buy",
    "tag_early_accumulation",

    # Forward returns (filled later)
    "forward_return_5d",
    "forward_return_20d",
    "label_5d",
    "label_20d",

    # Meta
    "data_generated_at",
]


def _safe(v, default=None):
    """Return v if not None/NaN, else default."""
    if v is None: return default
    try:
        f = float(v)
        return default if f != f else v  # NaN check
    except (TypeError, ValueError):
        return v


def _log_norm(value, scale=10):
    """log10 normalization for large monetary/volume values."""
    import math
    if value is None: return None
    try:
        v = abs(float(value))
        return round(math.log10(v + 1) / scale, 6) if v > 0 else 0.0
    except: return None


def _b(value) -> int:
    """Boolean to 0/1."""
    return 1 if value else 0


def _tag(tags: list, name: str) -> int:
    return 1 if name in tags else 0


def extract_row(record: dict, date: str) -> dict:
    """Extract a flat feature row from a unified_enriched signal record."""
    ins  = record.get("insider") or {}
    brk  = record.get("broker") or {}
    ctx  = record.get("market_context") or {}
    tags = record.get("tags") or []

    mom  = ctx.get("momentum") or {}
    vol  = {}
    bb   = ctx.get("bollinger") or {}
    atr  = ctx.get("atr") or {}
    macd = ctx.get("macd") or {}

    # volume info might be nested differently depending on version
    # try market_context volume sub-object, fall back to top-level flags
    if "volume_ratio_7d" in ctx:
        vol_ratio_7d  = ctx.get("volume_ratio_7d")
        vol_spike     = ctx.get("volume_spike")
    else:
        vol_ratio_7d  = None
        vol_spike     = None

    return {
        "date":    date,
        "symbol":  record.get("symbol"),
        "final_signal":   record.get("final_signal"),
        "conviction_level": record.get("conviction_level"),

        # Insider
        "insider_strength":          _safe(record.get("insider_strength"), 0),
        "insider_buy_ratio":         _safe(ins.get("buy_ratio"), 0.5),
        "insider_net_volume_norm":   _log_norm(record.get("net_flow_insider")),
        "insider_key_person":        _b(ins.get("key_person_activity")),
        "insider_multi_key_person":  _b(ins.get("multi_key_person")),
        "insider_cluster_buy":       _b(ins.get("insider_cluster_buy")),
        "insider_recency_ratio":     _safe(ins.get("recency_ratio"), 0),
        "insider_unique_actors":     _safe(ins.get("unique_actors"), 0),
        "insider_buy_days":          _safe(ins.get("buy_days"), 0),
        "insider_active_days":       _safe(ins.get("active_days"), 0),
        "insider_weighted_actor":    _safe(ins.get("weighted_actor_score"), 0),
        "insider_silent_buy_ratio":  _safe(ins.get("silent_buy_ratio"), 0.5),

        # Broker
        "broker_buy_ratio":           _safe(brk.get("buy_ratio"), 0.5),
        "broker_net_value_norm":      _log_norm(record.get("net_flow_broker"), scale=15),
        "broker_cluster_days":        _safe((brk.get("cluster") or {}).get("cluster_days"), 0),
        "broker_unique_brokers":      _safe((brk.get("breadth") or {}).get("unique_brokers"), 0),
        "broker_foreign_net_norm":    _log_norm((brk.get("foreign") or {}).get("net_idr"), scale=15),
        "broker_smart_money_net_norm":_log_norm((brk.get("smart_money") or {}).get("net_idr"), scale=15),
        "broker_buy_days":            _safe(brk.get("buy_days"), 0),
        "broker_active_days":         _safe(brk.get("active_days"), 0),
        "broker_consistency":         _safe(brk.get("consistency"), 0),

        # Market
        "market_score":     _safe(record.get("market_score")),
        "rsi_14":           _safe(ctx.get("rsi_14")),
        "macd_bullish":     _b(macd.get("trend") == "BULLISH"),
        "macd_histogram":   _safe(macd.get("histogram")),
        "volume_spike":     _b(vol_spike),
        "volume_ratio_7d":  _safe(vol_ratio_7d),
        "momentum_5d":      _safe(mom.get("return_5d")),
        "momentum_20d":     _safe(mom.get("return_20d")),
        "bollinger_pct_b":  _safe(bb.get("pct_b")),
        "atr_pct":          _safe(atr.get("atr_pct")),
        "above_sma20":      _b(ctx.get("above_sma20")),
        "above_sma50":      _b(ctx.get("above_sma50")),
        "pct_from_high":    _safe(ctx.get("pct_from_high")),
        "pct_from_low":     _safe(ctx.get("pct_from_low")),

        # Scoring
        "composite_score": _safe(record.get("composite_score"), 0),
        "flow_score":      _safe(record.get("flow_score"), 0),
        "timing_score":    _safe(record.get("timing_score"), 50),
        "final_score":     _safe(record.get("final_score"), 0),

        # Tags
        "tag_aligned_bullish":     _tag(tags, "ALIGNED_BULLISH"),
        "tag_cluster_buy":         _tag(tags, "CLUSTER_BUY"),
        "tag_broad_buy":           _tag(tags, "BROAD_BUY"),
        "tag_inflow":              _tag(tags, "INFLOW"),
        "tag_divergent":           _tag(tags, "DIVERGENT"),
        "tag_noisy":               _tag(tags, "NOISY"),
        "tag_smart_money_dominant":_tag(tags, "SMART_MONEY_DOMINANT"),
        "tag_foreign_dominant":    _tag(tags, "FOREIGN_DOMINANT"),
        "tag_multi_key_person":    _tag(tags, "MULTI_KEY_PERSON"),
        "tag_insider_cluster_buy": _tag(tags, "INSIDER_CLUSTER_BUY"),
        "tag_early_accumulation":  _tag(tags, "EARLY_ACCUMULATION"),

        # Forward returns — filled later
        "forward_return_5d":  None,
        "forward_return_20d": None,
        "label_5d":           None,
        "label_20d":          None,

        # Meta
        "data_generated_at": record.get("_data_generated_at", ""),
    }


def load_existing_keys(csv_path: Path) -> set:
    """Return set of (date, symbol) already in dataset to avoid duplicates."""
    if not csv_path.exists(): return set()
    keys = set()
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add((row.get("date",""), row.get("symbol","")))
    return keys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Override date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print rows, don't write")
    parser.add_argument("--unified", default=str(UNIFIED_FILE), help="Path to unified_enriched.json")
    args = parser.parse_args()

    unified_path = Path(args.unified)
    if not unified_path.exists():
        print(f"❌ unified file not found: {unified_path}", file=sys.stderr)
        print("   Run: python3 enrich_unified.py", file=sys.stderr)
        sys.exit(1)

    data = json.loads(unified_path.read_text())
    meta = data.get("meta", {})
    date = args.date or meta.get("generated_at", "")[:10] or datetime.now().strftime("%Y-%m-%d")
    signals = data.get("signals", [])

    print(f"📅 date={date} | signals={len(signals)}", file=sys.stderr)

    if args.dry_run:
        for s in signals[:3]:
            s["_data_generated_at"] = meta.get("generated_at","")
            row = extract_row(s, date)
            print(json.dumps({k: row[k] for k in list(row)[:15]}, indent=2))
        print(f"... ({len(signals)} total rows, dry run)")
        return

    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    existing_keys = load_existing_keys(DATASET_FILE)
    write_header  = not DATASET_FILE.exists()

    new_rows = 0
    skipped  = 0

    with open(DATASET_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()

        for s in signals:
            key = (date, s.get("symbol",""))
            if key in existing_keys:
                skipped += 1
                continue
            s["_data_generated_at"] = meta.get("generated_at","")
            row = extract_row(s, date)
            # Ensure all columns present
            for col in COLUMNS:
                if col not in row: row[col] = None
            writer.writerow({k: row[k] for k in COLUMNS})
            new_rows += 1

    print(f"✅ wrote {new_rows} rows | skipped {skipped} duplicates → {DATASET_FILE}", file=sys.stderr)
    print(f"   Total rows in dataset: {len(existing_keys) + new_rows}", file=sys.stderr)


if __name__ == "__main__":
    main()
