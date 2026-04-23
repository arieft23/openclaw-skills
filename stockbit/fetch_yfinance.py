#!/usr/bin/env python3
"""
fetch_yfinance.py — IDX Top 200 market data fetcher
v2.6: added Bollinger Bands + ATR to market_context

Fetches 60-day OHLCV + computes indicators for:
  - TOP_200_IDX (hardcoded by market cap tier)
  - Any additional symbols from insider/broker latest files

Saves per-symbol to:
  data/yfinance/latest/SYMBOL.json
  data/yfinance/YYYY-MM-DD/SYMBOL.json
  data/yfinance/latest/_index.json

Run: python3 fetch_yfinance.py
"""

import json, os, sys, time, statistics, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yfinance as yf
    import numpy as np
except ImportError as e:
    print(f"❌ missing: {e}. Run: pip install yfinance numpy --break-system-packages", file=sys.stderr)
    sys.exit(1)

# ---------- CONFIG ----------
DAYS_HISTORY  = 60
DELAY_SEC     = 0.1   # per-thread delay (reduced from 0.25 — spread across workers)
IDX_SUFFIX    = ".JK"
CONCURRENCY   = 5     # parallel yfinance fetches — keep ≤ 8 to avoid rate limits

# Top 200 IDX by market cap tiers
TOP_200_IDX = [
    # Tier 1 — LQ45 / Blue chip
    "BBCA","BBRI","BMRI","TLKM","ASII","BREN","AMMN","ADRO","GOTO","BYAN",
    "CUAN","PTBA","UNTR","PGAS","ANTM","INCO","MDKA","BRPT","ICBP","UNVR",
    "KLBF","BBNI","BMTR","EXCL","SMGR","TBIG","TOWR","MNCN","BBTN","BJTM",
    "INDF","GGRM","HMSP","AALI","LSIP","JPFA","CPIN","MAIN","BFIN","ITMG",
    "HRUM","ESSA","DSSA","MBMA","NCKL","NICL","INCO","TINS","TKIM","INKP",
    # Tier 2 — BUMN + MSCI additions
    "JSMR","WSKT","PTPP","WIKA","ADHI","SSIA","NRCA","WTON","TOTL","ACST",
    "ISAT","FREN","LINK","CENT","BTEL","SUPR","TBLA","SGRO","SSMS","PALM",
    "SIMP","DSNG","TAPG","MGRO","ANJT","BWPT","GZCO","SMAR","WLSH","CSRA",
    "SMCB","INTP","SMGR","WSBP","AMRT","ACES","MAPI","RALS","HERO","MIDI",
    "LPPF","MTOR","AUTO","SMSM","GJTL","GDYR","BRAM","PBRX","RICY","SSTM",
    # Tier 3 — Midcap coverage
    "BBKP","BDMN","BNII","BNGA","MAYA","NISP","PNBN","SDRA","MCOR","BBYB",
    "BJBR","BJTM","BMAS","BNBA","BSWD","BCIC","BACA","AGRO","DNAR","NOBU",
    "MBSS","RAJA","BULL","TPMA","WINS","SHIP","SMDR","KEEN","ASSA","GIAA",
    "GMFI","CMPP","JAYA","DEAL","SAPX","TAXI","BLTZ","WEHA","HATM","IPCM",
    "SILO","HEAL","MIKA","SIDO","KLBF","MERK","PEHA","PYFA","TSPC","INAF",
    "KAEF","DVLA","SQBB","SCPI","CBPE","SRSN","YPAS","BRNA","IPOL","TRST",
    "AMFG","TOTO","ARNA","MARK","CLEO","CAMP","HOKI","GOOD","CEKA","AISA",
    "ULTJ","MYOR","SKLT","ROTI","BTEK","PANI","AIMS","KIOS","BCIP","KPIG",
    "LPKR","BSDE","CTRA","DILD","EMDE","JRPT","MDLN","MTLA","PWON","SMRA",
    "BEST","BIKA","GPRA","KIJA","NIRO","PLIN","RDTX","RODA","SMDM","COWL",
]

BASE_DIR     = Path(__file__).parent
TODAY        = datetime.now().strftime("%Y-%m-%d")
DATED_DIR    = BASE_DIR / "data" / "yfinance" / TODAY
LATEST_DIR   = BASE_DIR / "data" / "yfinance" / "latest"
INSIDER_FILE = BASE_DIR / "data" / "latest" / "insider.json"
BROKER_FILE  = BASE_DIR / "data" / "latest" / "broker.json"

# ---------- INDICATORS ----------
def sma(closes, n):
    return float(np.mean(closes[-n:])) if len(closes) >= n else None

def ema(closes, n):
    if len(closes) < n: return None
    k, e = 2/(n+1), float(closes[0])
    for p in closes[1:]: e = p*k + e*(1-k)
    return e

def rsi(closes, n=14):
    if len(closes) < n+1: return None
    d = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g = [max(x,0) for x in d]; l = [abs(min(x,0)) for x in d]
    ag, al = np.mean(g[:n]), np.mean(l[:n])
    for i in range(n, len(g)):
        ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+l[i])/n
    return round(float(100 - 100/(1+ag/al)) if al else 100.0, 2)

def macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow+sig: return None
    mv = []
    for i in range(slow, len(closes)):
        ef = ema(closes[:i+1], fast); es = ema(closes[:i+1], slow)
        if ef and es: mv.append(ef-es)
    if len(mv) < sig: return None
    ml = mv[-1]; sl = ema(mv, sig)
    h  = round(ml-sl, 4) if sl else None
    return {"macd": round(ml,4), "signal": round(sl,4) if sl else None,
            "histogram": h, "trend": "BULLISH" if h and h>0 else "BEARISH" if h else None}

def bollinger(closes, n=20):
    """Bollinger Bands (20, 2). Returns upper/middle/lower/bandwidth/pct_b."""
    if len(closes) < n: return None
    window = closes[-n:]
    mean   = float(np.mean(window))
    std    = float(np.std(window, ddof=1))
    upper  = mean + 2 * std
    lower  = mean - 2 * std
    last   = closes[-1]
    bw     = round(4 * std / mean * 100, 2) if mean else None
    pct_b  = round((last - lower) / (upper - lower) * 100, 2) if (upper - lower) > 0 else None
    return {
        "upper":     round(upper, 2),
        "middle":    round(mean, 2),
        "lower":     round(lower, 2),
        "bandwidth": bw,
        "pct_b":     pct_b   # 0 = at lower band, 50 = at middle, 100 = at upper
    }

def atr(ohlcv, n=14):
    """Average True Range (Wilder's, 14). Returns atr and atr_pct."""
    if len(ohlcv) < n + 1: return None
    trs = []
    for i in range(1, len(ohlcv)):
        h, l, pc = ohlcv[i]["high"], ohlcv[i]["low"], ohlcv[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n: return None
    atr_val = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr_val = (atr_val * (n - 1) + tr) / n
    last_close = ohlcv[-1]["close"]
    return {
        "atr":     round(atr_val, 2),
        "atr_pct": round(atr_val / last_close * 100, 2) if last_close else None
    }

def volume_analysis(vols, closes):
    if len(vols) < 5: return None
    avg60 = float(np.mean(vols))
    if avg60 == 0: return None
    avg7  = float(np.mean(vols[-7:])) if len(vols)>=7 else None
    last  = float(vols[-1])
    r7    = round(avg7/avg60, 2) if avg7 else None
    r1    = round(last/avg60, 2)
    trend = "EXPANDING" if r7 and r7>1.2 else "CONTRACTING" if r7 and r7<0.8 else "NORMAL"
    return {"avg_60d": round(avg60), "avg_7d": round(avg7) if avg7 else None,
            "last": round(last), "ratio_7d": r7, "ratio_1d": r1,
            "trend": trend, "spike_today": r1>2.0}

def momentum(closes):
    if len(closes) < 2: return None
    def pct(n): return round((closes[-1]-closes[-(n+1)])/closes[-(n+1)]*100,2) if len(closes)>=n+1 else None
    r5,r20,r60 = pct(5),pct(20),pct(min(59,len(closes)-1))
    dirs = [r for r in [r5,r20,r60] if r is not None]
    trend = "UPTREND" if all(r>0 for r in dirs) else "DOWNTREND" if all(r<0 for r in dirs) else "MIXED"
    return {"return_5d":r5,"return_20d":r20,"return_60d":r60,"trend":trend}

def market_context(ohlcv):
    if len(ohlcv) < 5: return None, None
    closes  = [d["close"]  for d in ohlcv]
    volumes = [d["volume"] for d in ohlcv]
    highs   = [d["high"]   for d in ohlcv]
    lows    = [d["low"]    for d in ohlcv]
    last    = closes[-1]

    s20,s50,e20 = sma(closes,20), sma(closes,50), ema(closes,20)
    r   = rsi(closes)
    m   = macd(closes)
    v   = volume_analysis(volumes, closes)
    mom = momentum(closes)
    bb  = bollinger(closes)
    atr_data = atr(ohlcv)
    h60,l60 = max(highs), min(lows)
    pfh = round((last-h60)/h60*100, 2)
    pfl = round((last-l60)/l60*100, 2)

    # Market score
    score = 50
    if r:
        if r<30: score+=15
        elif r<45: score+=8
        elif r>70: score-=15
        elif r>60: score-=5
    if m and m.get("trend"):
        score += 10 if m["trend"]=="BULLISH" else -10
    if mom:
        if mom["trend"]=="UPTREND": score+=10
        elif mom["trend"]=="DOWNTREND": score-=10
        if mom["return_5d"] and mom["return_5d"]>5: score+=5
        if mom["return_5d"] and mom["return_5d"]<-5: score-=5
    if v:
        if v["trend"]=="EXPANDING": score+=10
        if v["trend"]=="CONTRACTING": score-=5
        if v["spike_today"]: score+=5
    if s20: score += 5 if last>s20 else -5
    if s50: score += 5 if last>s50 else -5
    if pfl<5: score+=5
    if pfh>-5: score-=5
    # v2.6: Bollinger bonus — near lower band + market confirming = oversold setup
    if bb and bb.get("pct_b") is not None:
        if bb["pct_b"] < 10: score += 8   # very near lower band — potential bounce
        elif bb["pct_b"] > 90: score -= 5  # near upper band — extended

    ms = max(0, min(100, round(score)))
    ctx = {
        "last_close": round(last,2), "sma20": round(s20,2) if s20 else None,
        "sma50": round(s50,2) if s50 else None, "ema20": round(e20,2) if e20 else None,
        "above_sma20": last>s20 if s20 else None, "above_sma50": last>s50 if s50 else None,
        "rsi_14": r, "macd": m, "volume": v, "momentum": mom,
        "bollinger": bb, "atr": atr_data,
        "high_60d": round(h60,2), "low_60d": round(l60,2),
        "pct_from_high": pfh, "pct_from_low": pfl, "market_score": ms
    }
    return ctx, ms

# ---------- FETCH ----------
def fetch_symbol(symbol):
    end   = datetime.now()
    start = end - timedelta(days=90)
    try:
        hist = yf.Ticker(f"{symbol}{IDX_SUFFIX}").history(
            start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True)
        if hist.empty: return None
        rows = [{"date": d.strftime("%Y-%m-%d"),
                 "open": round(float(r["Open"]),2), "high": round(float(r["High"]),2),
                 "low":  round(float(r["Low"]),2),  "close": round(float(r["Close"]),2),
                 "volume": int(r["Volume"])}
                for d,r in hist.iterrows()]
        return rows[-60:] if len(rows)>60 else rows
    except Exception as e:
        print(f"  ⚠️  {symbol}: {e}", file=sys.stderr)
        return None

# ---------- SAVE ----------
def save_symbol(symbol, data):
    DATED_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    (DATED_DIR  / f"{symbol}.json").write_text(payload)
    (LATEST_DIR / f"{symbol}.json").write_text(payload)

# ---------- EXTRA SYMBOLS FROM INSIDER/BROKER ----------
def load_extra_symbols():
    extras = set()
    for f in [INSIDER_FILE, BROKER_FILE]:
        if f.exists():
            try:
                data = json.loads(f.read_text())
                for s in data.get("signals", []):
                    extras.add(s["symbol"])
            except Exception:
                pass
    return extras

# ---------- MAIN ----------
def main():
    start_time = time.time()
    extra = load_extra_symbols()
    all_symbols = list(dict.fromkeys(TOP_200_IDX + list(extra - set(TOP_200_IDX))))
    print(f"📋 {len(TOP_200_IDX)} top200 + {len(extra - set(TOP_200_IDX))} insider/broker extras = {len(all_symbols)} total", file=sys.stderr)

    results, failed = {}, []
    lock = threading.Lock()
    total = len(all_symbols)
    completed = 0

    def process(sym):
        nonlocal completed
        time.sleep(DELAY_SEC + (hash(sym) % 100) / 1000)  # per-thread jitter
        ohlcv = fetch_symbol(sym)
        if not ohlcv or len(ohlcv) < 5:
            return sym, None, None
        ctx, ms = market_context(ohlcv)
        if ctx is None:
            return sym, None, None
        payload = {
            "symbol": sym, "ticker": f"{sym}{IDX_SUFFIX}",
            "fetched_at": datetime.now().isoformat(),
            "trading_days": len(ohlcv),
            "market_score": ms, "context": ctx, "ohlcv": ohlcv
        }
        return sym, payload, ms

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {executor.submit(process, sym): sym for sym in all_symbols}
        for future in as_completed(futures):
            completed += 1
            sym, payload, ms = future.result()
            print(f"  [{completed}/{total}] {sym}", file=sys.stderr, end="\r")
            with lock:
                if payload is None:
                    failed.append(sym)
                else:
                    save_symbol(sym, payload)
                    results[sym] = ms

    runtime_seconds = round(time.time() - start_time)
    print(f"\n✅ fetched: {len(results)} | ❌ failed: {len(failed)} | ⏱ {runtime_seconds}s", file=sys.stderr)
    if failed:
        print(f"   failed symbols: {', '.join(failed)}", file=sys.stderr)

    index = {
        "generated_at": datetime.now().isoformat(), "date": TODAY,
        "total": len(results), "failed": len(failed),
        "runtime_seconds": runtime_seconds,
        "concurrency": CONCURRENCY,
        "symbols": list(results.keys()), "failed_symbols": failed,
        "market_scores": results
    }
    (LATEST_DIR / "_index.json").write_text(json.dumps(index, indent=2))
    print(f"💾 index → data/yfinance/latest/_index.json", file=sys.stderr)

if __name__ == "__main__":
    main()
