#!/usr/bin/env python3
"""
evaluate_performance.py — Offline signal performance evaluator

Reads data/features/dataset.csv (after forward returns have been filled in),
produces performance analysis across:
  A. Signal type win rates
  B. Tag interaction analysis
  C. Timing curve (Day 1 / 3 / 5 / 10 proxy via 5D and 20D)
  D. Insider strength vs outcome
  E. Drift detection (rolling 30-day win rate)

Usage:
  python3 evaluate_performance.py
  python3 evaluate_performance.py --horizon 5      (5d or 20d)
  python3 evaluate_performance.py --min-rows 20    (skip groups with < N rows)
  python3 evaluate_performance.py --output report.md

Forward returns must be filled in dataset.csv first:
  forward_return_5d  = actual % return 5 trading days after signal date
  forward_return_20d = actual % return 20 trading days after signal date
  label_5d           = 1 if forward_return_5d > 0 else 0
  label_20d          = 1 if forward_return_20d > 0 else 0

To fill returns, run: python3 fill_forward_returns.py (separate script, see below)
"""

import csv, sys, json, argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime

BASE_DIR     = Path(__file__).parent
DATASET_FILE = BASE_DIR / "data" / "features" / "dataset.csv"


# ---------- HELPERS ----------
def _f(v):
    try: return float(v)
    except: return None

def _pct(numerator, denominator):
    if not denominator: return None
    return round(numerator / denominator * 100, 1)


def load_dataset(path: Path, horizon: int = 5) -> list:
    """Load rows that have forward returns filled in."""
    label_col  = f"label_{horizon}d"
    return_col = f"forward_return_{horizon}d"
    rows = []
    if not path.exists():
        print(f"❌ dataset not found: {path}", file=sys.stderr)
        print("   Run: python3 build_feature_dataset.py", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        for row in csv.DictReader(f):
            label  = _f(row.get(label_col))
            ret    = _f(row.get(return_col))
            if label is None or ret is None:
                continue  # skip rows not yet filled
            row["_label"] = int(label)
            row["_return"] = ret
            rows.append(row)
    return rows


def win_rate(rows) -> dict:
    """Compute win rate and avg return for a group of rows."""
    if not rows:
        return {"n": 0, "win_rate": None, "avg_return": None, "median_return": None}
    wins    = sum(1 for r in rows if r["_label"] == 1)
    returns = sorted([r["_return"] for r in rows])
    n = len(rows)
    med = returns[n//2] if returns else None
    return {
        "n":           n,
        "win_rate":    _pct(wins, n),
        "avg_return":  round(sum(r["_return"] for r in rows) / n, 2),
        "median_return": round(med, 2) if med is not None else None,
    }


# ---------- A. SIGNAL TYPE ANALYSIS ----------
def signal_analysis(rows, min_rows=5) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("final_signal","?")].append(r)
    result = {}
    for sig, grp in sorted(groups.items()):
        if len(grp) >= min_rows:
            result[sig] = win_rate(grp)
    return result


# ---------- B. TAG INTERACTION ANALYSIS ----------
def tag_analysis(rows, min_rows=5) -> dict:
    """Analyze performance of specific tag combinations."""
    combos = {
        "ALIGNED_BULLISH":                   lambda r: r.get("tag_aligned_bullish")=="1",
        "ALIGNED_BULLISH + CLUSTER_BUY":     lambda r: r.get("tag_aligned_bullish")=="1" and r.get("tag_cluster_buy")=="1",
        "ALIGNED_BULLISH + INFLOW":          lambda r: r.get("tag_aligned_bullish")=="1" and r.get("tag_inflow")=="1",
        "CLUSTER_BUY":                        lambda r: r.get("tag_cluster_buy")=="1",
        "BROAD_BUY":                          lambda r: r.get("tag_broad_buy")=="1",
        "DIVERGENT":                          lambda r: r.get("tag_divergent")=="1",
        "NOISY":                              lambda r: r.get("tag_noisy")=="1",
        "SMART_MONEY_DOMINANT":               lambda r: r.get("tag_smart_money_dominant")=="1",
        "FOREIGN_DOMINANT":                   lambda r: r.get("tag_foreign_dominant")=="1",
        "MULTI_KEY_PERSON":                   lambda r: r.get("tag_multi_key_person")=="1",
        "INSIDER_CLUSTER_BUY":                lambda r: r.get("tag_insider_cluster_buy")=="1",
        "EARLY_ACCUMULATION":                 lambda r: r.get("tag_early_accumulation")=="1",
    }
    result = {}
    for name, fn in combos.items():
        grp = [r for r in rows if fn(r)]
        if len(grp) >= min_rows:
            result[name] = win_rate(grp)
    return result


# ---------- C. INSIDER STRENGTH BUCKETS ----------
def insider_strength_analysis(rows, min_rows=5) -> dict:
    buckets = {"STRONG (70-100)": [], "MODERATE (40-69)": [], "WEAK (0-39)": []}
    for r in rows:
        s = _f(r.get("insider_strength"))
        if s is None: continue
        if s >= 70:   buckets["STRONG (70-100)"].append(r)
        elif s >= 40: buckets["MODERATE (40-69)"].append(r)
        else:         buckets["WEAK (0-39)"].append(r)
    return {k: win_rate(v) for k, v in buckets.items() if len(v) >= min_rows}


# ---------- D. TIMING SCORE BUCKETS ----------
def timing_analysis(rows, min_rows=5) -> dict:
    buckets = {"FAST (65+)": [], "MODERATE (35-64)": [], "SLOW (0-34)": []}
    for r in rows:
        t = _f(r.get("timing_score"))
        if t is None: continue
        if t >= 65:   buckets["FAST (65+)"].append(r)
        elif t >= 35: buckets["MODERATE (35-64)"].append(r)
        else:         buckets["SLOW (0-34)"].append(r)
    return {k: win_rate(v) for k, v in buckets.items() if len(v) >= min_rows}


# ---------- E. CONVICTION LEVEL ----------
def conviction_analysis(rows, min_rows=5) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("conviction_level","?")].append(r)
    return {k: win_rate(v) for k, v in groups.items() if len(v) >= min_rows}


# ---------- F. DRIFT DETECTION (rolling 30-day win rate) ----------
def drift_analysis(rows) -> dict:
    """
    Bucket rows into 30-day windows by date.
    Alert if win rate drops more than 15 ppts from the rolling baseline.
    """
    by_date = defaultdict(list)
    for r in rows:
        d = r.get("date","")[:7]  # YYYY-MM (monthly buckets)
        by_date[d].append(r)

    monthly = {}
    baseline_wr = None
    alerts = []

    for month in sorted(by_date.keys()):
        grp = by_date[month]
        if len(grp) < 5: continue
        wr = win_rate(grp)
        monthly[month] = wr
        if baseline_wr is None:
            baseline_wr = wr["win_rate"]
        elif wr["win_rate"] is not None and baseline_wr is not None:
            drop = baseline_wr - wr["win_rate"]
            if drop > 15:
                alerts.append(f"{month}: win rate dropped {drop:.1f}ppts from baseline ({baseline_wr}% → {wr['win_rate']}%)")
            # Rolling update baseline
            baseline_wr = baseline_wr * 0.7 + wr["win_rate"] * 0.3

    return {"monthly": monthly, "alerts": alerts}


# ---------- FORMAT OUTPUT ----------
def fmt_section(title: str, data: dict) -> str:
    if not data:
        return f"\n## {title}\nInsufficient data.\n"
    lines = [f"\n## {title}"]
    lines.append(f"{'Group':<35} {'N':>5} {'Win%':>7} {'AvgRet':>8} {'MedRet':>8}")
    lines.append("-" * 65)
    for group, stats in data.items():
        n   = stats.get("n", 0)
        wr  = f"{stats['win_rate']:.1f}%" if stats.get("win_rate") is not None else "  N/A"
        ar  = f"{stats['avg_return']:+.2f}%" if stats.get("avg_return") is not None else "  N/A"
        mr  = f"{stats['median_return']:+.2f}%" if stats.get("median_return") is not None else "  N/A"
        lines.append(f"{group:<35} {n:>5} {wr:>7} {ar:>8} {mr:>8}")
    return "\n".join(lines)


# ---------- MAIN ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon",  type=int, default=5,  choices=[5, 20])
    parser.add_argument("--min-rows", type=int, default=10)
    parser.add_argument("--output",   default=None, help="Write report to file")
    parser.add_argument("--json",     action="store_true", help="Output raw JSON instead of markdown")
    args = parser.parse_args()

    rows = load_dataset(DATASET_FILE, horizon=args.horizon)
    if not rows:
        print(f"❌ No rows with filled {args.horizon}d forward returns found in {DATASET_FILE}", file=sys.stderr)
        print("   Forward returns need to be filled in. See fill_forward_returns.py.", file=sys.stderr)
        sys.exit(0)  # not a hard error — just no data yet

    print(f"📊 {len(rows)} rows with {args.horizon}d labels loaded", file=sys.stderr)

    results = {
        "generated_at":    datetime.now().isoformat(),
        "horizon_days":    args.horizon,
        "total_rows":      len(rows),
        "overall":         win_rate(rows),
        "by_signal":       signal_analysis(rows, args.min_rows),
        "by_tag":          tag_analysis(rows, args.min_rows),
        "by_insider_strength": insider_strength_analysis(rows, args.min_rows),
        "by_timing":       timing_analysis(rows, args.min_rows),
        "by_conviction":   conviction_analysis(rows, args.min_rows),
        "drift":           drift_analysis(rows),
    }

    if args.json:
        out = json.dumps(results, indent=2)
    else:
        lines = [
            f"# Signal Performance Report — {args.horizon}D Horizon",
            f"Generated: {results['generated_at']}",
            f"Total evaluated rows: {results['total_rows']}",
            f"Overall win rate: {results['overall']['win_rate']}% | Avg return: {results['overall']['avg_return']}%",
        ]
        lines.append(fmt_section("By Signal Type",       results["by_signal"]))
        lines.append(fmt_section("By Tag Combination",   results["by_tag"]))
        lines.append(fmt_section("By Insider Strength",  results["by_insider_strength"]))
        lines.append(fmt_section("By Timing Label",      results["by_timing"]))
        lines.append(fmt_section("By Conviction Level",  results["by_conviction"]))

        # Drift
        drift = results["drift"]
        lines.append("\n## Drift Detection (Monthly Win Rate)")
        for month, stats in drift["monthly"].items():
            lines.append(f"  {month}  win={stats['win_rate']}%  n={stats['n']}  avg={stats['avg_return']}%")
        if drift["alerts"]:
            lines.append("\n⚠️  DRIFT ALERTS:")
            for a in drift["alerts"]: lines.append(f"  {a}")
        else:
            lines.append("  No drift alerts.")

        lines.append(
            "\n---\n"
            "## Notes\n"
            "- Forward returns must be filled before running this report.\n"
            "- Minimum group size for display: " + str(args.min_rows) + " rows.\n"
            "- Win rate = % of signals where forward return > 0.\n"
            "- This report does NOT train a model — it evaluates existing signals.\n"
            "- Run regularly as dataset grows. Use drift alerts to trigger threshold review.\n"
        )
        out = "\n".join(lines)

    if args.output:
        Path(args.output).write_text(out)
        print(f"✅ Report written to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
