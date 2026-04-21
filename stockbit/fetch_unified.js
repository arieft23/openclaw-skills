require('dotenv').config();
const fs   = require('fs');
const path = require('path');

// ---------- PATHS ----------
const DATA_DIR    = path.join(__dirname, 'data');
const INSIDER_F   = path.join(DATA_DIR, 'latest', 'insider.json');
const BROKER_F    = path.join(DATA_DIR, 'latest', 'broker.json');
const UNIFIED_DIR = path.join(DATA_DIR, 'unified');
const LATEST_DIR  = path.join(DATA_DIR, 'latest');

// ---------- CONFIG ----------
const INSIDER_WEIGHT = 0.45;
const BROKER_WEIGHT  = 0.55;

// Single-source penalty: if only one data source present,
// reduce conviction since signal is unconfirmed
const SINGLE_SOURCE_PENALTY = 0.6;

// Conviction thresholds
// MEDIUM starts at 30 (not 25) to avoid cliff-splitting near-identical single-source signals
const CONVICTION = [
  { level: 'EXTREME', min: 75 },
  { level: 'HIGH',    min: 50 },
  { level: 'MEDIUM',  min: 30 },
  { level: 'LOW',     min: 0  }
];

// Broker-only noise filter: single broker + single day = block trade / noise, not signal
const MIN_BROKER_UNIQUE   = 2;  // at least 2 unique brokers
const MIN_BROKER_DAYS     = 2;  // at least 2 active days

const BULLISH = new Set(['STRONG_ACCUMULATION', 'ACCUMULATION']);
const BEARISH = new Set(['DISTRIBUTION']);

// ---------- HELPERS ----------
function formatDate(d) { return d.toISOString().slice(0, 10); }

function convictionLevel(score) {
  return CONVICTION.find(c => score >= c.min)?.level ?? 'LOW';
}

// Normalize score: soft cap using sigmoid-like curve so 200 > 100 still matters
// but extreme values don't dominate. Maps any positive score to 0–100.
function normalizeScore(score) {
  if (score <= 0) return 0;
  // log normalization: score of 100 → ~82, score of 200 → ~96, score of 50 → ~70
  return Math.min(100, Math.round(100 * (1 - 1 / (1 + score / 50))));
}

// Returns true if broker signal is too thin to trust (single broker, single day)
function isBrokerSignalNoisy(brk) {
  if (!brk) return false;
  return brk.breadth?.unique_brokers < MIN_BROKER_UNIQUE ||
         brk.active_days < MIN_BROKER_DAYS;
}

function loadJSON(filePath, label) {
  if (!fs.existsSync(filePath)) {
    console.error(`❌ missing ${label}: ${filePath}`);
    console.error(`   run fetch_${label}.js first`);
    process.exit(1);
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

// ---------- SAVE ----------
function saveToFile(data) {
  const today = formatDate(new Date());
  fs.mkdirSync(UNIFIED_DIR, { recursive: true });
  fs.mkdirSync(LATEST_DIR,  { recursive: true });
  fs.writeFileSync(path.join(UNIFIED_DIR, `${today}.json`), JSON.stringify(data, null, 2));
  fs.writeFileSync(path.join(LATEST_DIR,  'unified.json'),  JSON.stringify(data, null, 2));
  console.error(`💾 unified → data/unified/${today}.json + data/latest/unified.json`);
}

// ---------- TAG VALIDITY CHECKS ----------
// Buy-side tags (BROAD_BUY, CLUSTER_BUY, CLUSTER_BUY_WEAK) are misleading when
// net flow is negative AND buy_ratio < 0.5 — many small buyers overwhelmed by
// fewer large sellers. Valid only when: net_value_idr > 0 OR buy_ratio >= 0.5
const BUY_SIDE_TAGS = new Set(['BROAD_BUY', 'CLUSTER_BUY', 'CLUSTER_BUY_WEAK']);

function isBuySideTagValid(brk) {
  if (!brk) return false;
  return brk.net_value_idr > 0 || brk.buy_ratio >= 0.5;
}

// ---------- MERGE ----------
function merge(insiderSignals, brokerSignals) {
  const brokerMap  = {};
  for (const b of brokerSignals) brokerMap[b.symbol]  = b;
  const insiderMap = {};
  for (const i of insiderSignals) insiderMap[i.symbol] = i;

  const allSymbols = new Set([
    ...insiderSignals.map(s => s.symbol),
    ...brokerSignals.map(s => s.symbol)
  ]);

  const results = [];

  for (const symbol of allSymbols) {
    const ins = insiderMap[symbol] || null;
    const brk = brokerMap[symbol]  || null;

    const hasBoth    = ins !== null && brk !== null;
    const insiderOnly = ins !== null && brk === null;
    const brokerOnly  = ins === null && brk !== null;

    // --- Signal classification (must come before score calc) ---
    const ins_signal = ins?.signal ?? 'NEUTRAL';
    const brk_signal = brk?.signal ?? 'NEUTRAL';

    // Normalize scores with soft cap (preserves signal strength differences)
    const ins_score_raw = ins ? normalizeScore(ins.score) : 0;
    const brk_score_raw = brk ? normalizeScore(brk.score) : 0;

    // Single-source penalty — lighter for unambiguous signals (buy_ratio 0 or 1),
    // heavier for mixed signals that need confirmation.
    function ambiguityFactor(ratio) {
      const isUnambiguous = ratio === 0 || ratio === 1;
      return isUnambiguous ? 0.8 : SINGLE_SOURCE_PENALTY;
    }
    const ins_score = insiderOnly
      ? Math.round(ins_score_raw * ambiguityFactor(ins?.buy_ratio ?? 0.5))
      : ins_score_raw;
    const brk_score = brokerOnly
      ? Math.round(brk_score_raw * ambiguityFactor(brk?.buy_ratio ?? 0.5))
      : brk_score_raw;

    const composite_score = Math.round(
      ins_score * INSIDER_WEIGHT +
      brk_score * BROKER_WEIGHT
    );

    const insider_bullish = BULLISH.has(ins_signal);
    const broker_bullish  = BULLISH.has(brk_signal);
    const insider_bearish = BEARISH.has(ins_signal);
    const broker_bearish  = BEARISH.has(brk_signal);

    // Alignment flags per SKILL.md schema
    const insider_alignment = insider_bullish;
    const broker_alignment  = broker_bullish;

    // --- Final signal per SKILL.md decision logic (fixed) ---
    let final_signal;

    if (insider_bullish && broker_bullish) {
      // Both bullish → strength determines level
      final_signal = composite_score >= 65 ? 'EXTREME_CONVICTION' : 'HIGH_CONVICTION';

    } else if (insider_bearish && broker_bearish) {
      // Both bearish → confirmed distribution
      final_signal = 'DISTRIBUTION';

    } else if (insider_bullish && broker_bearish) {
      // FIX: conflict → NEUTRAL not DISTRIBUTION (SKILL.md: conflict = NEUTRAL/LOW)
      final_signal = 'NEUTRAL';

    } else if (insider_bearish && !broker_bullish) {
      // Insider selling, broker neutral or also selling
      final_signal = 'DISTRIBUTION';

    } else if (!insider_bearish && broker_bearish) {
      // Broker selling, insider neutral — watch signal
      final_signal = 'DISTRIBUTION';

    } else if (insider_bullish && !broker_bullish) {
      // Insider buying, broker not confirming — early / unconfirmed
      final_signal = 'ACCUMULATION';

    } else if (!insider_bullish && broker_bullish) {
      // Broker buying, no insider signal — watch
      final_signal = 'ACCUMULATION';

    } else {
      final_signal = 'NEUTRAL';
    }

    // --- Tags: only add insider tag if insider data actually exists ---
    const tags = [];

    if (ins && ins_signal !== 'NEUTRAL') {
      tags.push(`insider:${ins_signal}`);
    }

    // Only add broker buy-side tags when net flow confirms the direction
    if (brk) {
      const buySideValid = isBuySideTagValid(brk);
      for (const tag of (brk.tags ?? [])) {
        // Drop BROAD_BUY / CLUSTER_BUY / CLUSTER_BUY_WEAK when net flow is net negative
        if (BUY_SIDE_TAGS.has(tag) && !buySideValid) continue;
        tags.push(tag);
      }
    }

    // Alignment badge
    if (insider_bullish && broker_bullish)   tags.push('ALIGNED_BULLISH');
    if (insider_bearish && broker_bearish)   tags.push('ALIGNED_BEARISH');
    if (insider_bullish && broker_bearish)   tags.push('DIVERGENT');
    if (insider_bearish && broker_bullish)   tags.push('DIVERGENT');

    // Single-source warning
    if (insiderOnly) tags.push('INSIDER_ONLY');
    if (brokerOnly) {
      tags.push('BROKER_ONLY');
      // Downgrade noisy broker-only signals (single broker or single day)
      if (isBrokerSignalNoisy(brk)) {
        tags.push('NOISY');
        // Override to NEUTRAL — not enough evidence for directional call
        if (final_signal !== 'EXTREME_CONVICTION' && final_signal !== 'HIGH_CONVICTION') {
          final_signal = 'NEUTRAL';  // eslint-disable-line no-param-reassign
        }
      }
    }

    results.push({
      // SKILL.md Unified Record Schema
      symbol,
      final_signal,
      composite_score,
      insider_score:     ins_score,
      broker_score:      brk_score,
      insider_alignment,
      broker_alignment,
      net_flow_insider:  ins?.net_volume    ?? 0,
      net_flow_broker:   brk?.net_value_idr ?? 0,
      conviction_level:  convictionLevel(composite_score),
      tags,

      // Source snapshots for agent explanation
      insider: ins ? {
        signal:               ins.signal,
        score:                ins.score,
        key_person_activity:  ins.key_person_activity,
        key_person_buys:      ins.key_person_buys,
        foreign_accumulation: ins.foreign_accumulation,
        buy_volume:           ins.buy_volume,
        sell_volume:          ins.sell_volume,
        buy_ratio:            ins.buy_ratio,
        active_days:          ins.active_days,
        buy_days:             ins.buy_days,
        sell_days:            ins.sell_days,
        unique_actors:        ins.unique_actors
      } : null,

      broker: brk ? {
        signal:         brk.signal,
        score:          brk.score,
        net_value_idr:  brk.net_value_idr,
        buy_ratio:      brk.buy_ratio,
        active_days:    brk.active_days,
        buy_days:       brk.buy_days,
        sell_days:      brk.sell_days,
        tags:           brk.tags,
        foreign:        brk.foreign,
        smart_money:    brk.smart_money,
        breadth:        brk.breadth,
        cluster:        brk.cluster
      } : null
    });
  }

  // Sort: EXTREME → HIGH → ACCUMULATION → NEUTRAL → DISTRIBUTION
  const ORDER = {
    EXTREME_CONVICTION: 0, HIGH_CONVICTION: 1,
    ACCUMULATION: 2, NEUTRAL: 3, DISTRIBUTION: 4
  };

  return results.sort((a, b) =>
    (ORDER[a.final_signal] ?? 9) - (ORDER[b.final_signal] ?? 9) ||
    b.composite_score - a.composite_score
  );
}

// ---------- MAIN ----------
(async () => {
  console.error('🔀 loading insider + broker...');

  const insiderData = loadJSON(INSIDER_F, 'insider');
  const brokerData  = loadJSON(BROKER_F,  'broker');

  const insiderSignals = insiderData.signals ?? [];
  const brokerSignals  = brokerData.signals  ?? [];

  console.error(`📥 insider: ${insiderSignals.length} signals`);
  console.error(`📥 broker:  ${brokerSignals.length} signals`);

  const unified = merge(insiderSignals, brokerSignals);

  const bySignal     = {};
  const byConviction = {};
  for (const u of unified) {
    bySignal[u.final_signal]         = (bySignal[u.final_signal]         || 0) + 1;
    byConviction[u.conviction_level] = (byConviction[u.conviction_level] || 0) + 1;
  }

  const output = {
    meta: {
      generated_at:      new Date().toISOString(),
      insider_generated: insiderData.meta?.generated_at ?? null,
      broker_generated:  brokerData.meta?.generated_at  ?? null,
      total_symbols:     unified.length,
      insider_only:      unified.filter(u => u.insider && !u.broker).length,
      broker_only:       unified.filter(u => !u.insider && u.broker).length,
      both_sources:      unified.filter(u => u.insider  && u.broker).length,
      by_signal:         bySignal,
      by_conviction:     byConviction
    },
    signals: unified
  };

  saveToFile(output);
  console.log(JSON.stringify(output, null, 2));
})();
