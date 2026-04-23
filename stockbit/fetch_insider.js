require('dotenv').config();
const axios = require('axios');
const fs    = require('fs');
const path  = require('path');

const TOKEN = process.env.STOCKBIT_TOKEN;
if (!TOKEN) { console.error("❌ STOCKBIT_TOKEN not found in .env"); process.exit(1); }

const BASE_URL = 'https://exodus.stockbit.com/insider/company/majorholder';

// ---------- CONFIG ----------
const MONTH_BACK             = 1;
const MIN_SCORE              = 50;
const LARGE_VOLUME_THRESHOLD = 10_000_000;
const REQUIRE_ANY            = ['key_person', 'large_volume', 'distribution'];
const REQUEST_TIMEOUT_MS     = 10_000;
const RETRY_COUNT            = 3;
const RETRY_DELAY_MS         = 500;

// ---------- HELPERS ----------
function formatDate(d) { return d.toISOString().slice(0, 10); }

function getDateRange() {
  const end   = new Date(); end.setDate(end.getDate() - 1);
  const start = new Date(end); start.setMonth(start.getMonth() - MONTH_BACK);
  return { date_start: formatDate(start), date_end: formatDate(end) };
}

function parseDate(str) {
  const months = { Jan:'01',Feb:'02',Mar:'03',Apr:'04',May:'05',Jun:'06',
                   Jul:'07',Aug:'08',Sep:'09',Oct:'10',Nov:'11',Dec:'12' };
  const [day, mon, year] = str.split(' ');
  return `20${year}-${months[mon]}-${day.padStart(2,'0')}`;
}

function toNumber(str) {
  if (!str) return null;
  const n = parseFloat(str.replace(/,/g, ''));
  return isNaN(n) ? null : n;
}

// Recency weight: transactions in the last 14 days get 1.0, older decay toward 0.5
function recencyWeight(dateStr) {
  const txDate  = new Date(dateStr);
  const now     = new Date();
  const daysAgo = Math.max(0, (now - txDate) / (1000 * 60 * 60 * 24));
  return daysAgo <= 14 ? 1.0 : Math.max(0.5, 1.0 - (daysAgo - 14) / 60);
}

// ---------- RETRY WRAPPER ----------
async function withRetry(fn, retries = RETRY_COUNT, delayMs = RETRY_DELAY_MS) {
  try {
    return await fn();
  } catch (e) {
    if (retries === 0) throw e;
    await new Promise(r => setTimeout(r, delayMs));
    return withRetry(fn, retries - 1, delayMs * 1.5);
  }
}

// ---------- SAVE ----------
function saveToFile(data) {
  const today = formatDate(new Date());

  const archiveDir = path.join(__dirname, 'data', 'insider');
  fs.mkdirSync(archiveDir, { recursive: true });
  fs.writeFileSync(path.join(archiveDir, `${today}.json`), JSON.stringify(data, null, 2));

  const latestDir = path.join(__dirname, 'data', 'latest');
  fs.mkdirSync(latestDir, { recursive: true });
  fs.writeFileSync(path.join(latestDir, 'insider.json'), JSON.stringify(data, null, 2));

  console.error(`💾 insider → data/insider/${today}.json + data/latest/insider.json`);
}

// ---------- TRANSFORM ----------
function transformData(movement) {
  const dedupMap = {};

  for (const item of movement) {
    const action = item.action_type.replace('ACTION_TYPE_', '');
    if (!['BUY', 'SELL'].includes(action)) continue;

    const date   = parseDate(item.date);
    const actor  = item.name;
    const symbol = item.symbol;
    const volume = Math.abs(parseInt((item.changes?.value || "0").replace(/,/g, ''))) || 0;
    const rawPrice = item.price_formatted;
    const price  = (rawPrice && rawPrice !== "0") ? toNumber(rawPrice) : null;
    const badges = item.badges || [];

    const is_key_person = badges.some(b =>
      b === 'SHAREHOLDER_BADGE_PENGENDALI' ||
      b === 'SHAREHOLDER_BADGE_KOMISARIS'  ||
      b === 'SHAREHOLDER_BADGE_DIREKTUR'
    );

    const fingerprint = `${item.previous?.value || ''}->${item.current?.value || ''}`;
    const dedupKey    = `${symbol}-${actor}-${date}-${action}-${fingerprint}`;
    if (dedupMap[dedupKey]) continue;

    dedupMap[dedupKey] = { symbol, date, actor, action, volume, price,
      is_foreign: item.nationality === 'NATIONALITY_TYPE_FOREIGN',
      is_key_person, badges };
  }

  // Aggregate splits
  const aggMap = {};
  for (const d of Object.values(dedupMap)) {
    const key = `${d.symbol}-${d.actor}-${d.date}-${d.action}`;
    if (!aggMap[key]) aggMap[key] = { ...d, volume: 0, _prices: [], _lots: 0 };
    aggMap[key].volume += d.volume;
    aggMap[key]._lots++;
    if (d.price !== null) aggMap[key]._prices.push({ price: d.price, volume: d.volume });
    if (d.is_key_person) aggMap[key].is_key_person = true;
    if (d.is_foreign)    aggMap[key].is_foreign    = true;
  }

  return Object.values(aggMap).map(d => {
    if (d._prices.length > 0) {
      const totalVol = d._prices.reduce((s, p) => s + p.volume, 0);
      d.price = totalVol > 0
        ? d._prices.reduce((s, p) => s + p.price * p.volume, 0) / totalVol
        : d._prices[0].price;
    }
    delete d._prices; delete d._lots;
    return d;
  });
}

// ---------- SIGNAL ----------
function buildSignals(data) {
  const map = {};

  for (const d of data) {
    if (!map[d.symbol]) {
      map[d.symbol] = {
        symbol: d.symbol,
        net_volume: 0, buy_volume: 0, sell_volume: 0,
        buy_count: 0,  sell_count: 0, key_person_buys: 0,
        key_person_activity: false, foreign_accumulation: false,
        active_days: new Set(), buy_days_set: new Set(), sell_days_set: new Set(),
        actors: new Set(),
        // v2.6: track recent days count for recency weighting
        recent_days: new Set(),
        weighted_buy_volume: 0, weighted_sell_volume: 0,
        multi_key_person: false, key_person_count: new Set()
      };
    }

    const s = map[d.symbol];
    s.actors.add(d.actor);
    s.active_days.add(d.date);

    const rw = recencyWeight(d.date);
    const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 14);
    if (new Date(d.date) >= cutoff) s.recent_days.add(d.date);

    if (d.action === 'BUY') {
      s.buy_volume  += d.volume;
      s.net_volume  += d.volume;
      s.buy_count++;
      s.buy_days_set.add(d.date);
      s.weighted_buy_volume += d.volume * rw;
      if (d.is_key_person) {
        s.key_person_buys++;
        s.key_person_count.add(d.actor);
      }
    } else {
      s.sell_volume += d.volume;
      s.net_volume  -= d.volume;
      s.sell_count++;
      s.sell_days_set.add(d.date);
      s.weighted_sell_volume += d.volume * rw;
    }

    if (d.is_key_person) s.key_person_activity = true;
    if (d.is_foreign && d.action === 'BUY') s.foreign_accumulation = true;
  }

  return Object.values(map).map(s => {
    // v2.6: log10 scoring — reduces raw volume bias
    const net_weighted = s.weighted_buy_volume - s.weighted_sell_volume;
    let score = Math.log10(Math.abs(net_weighted) + 1) * 10;

    // Recency boost: recent_days / active_days ratio
    const recency_ratio = s.active_days.size > 0
      ? s.recent_days.size / s.active_days.size : 0;
    score += recency_ratio * 10;

    if (s.key_person_activity) score += 20;
    if (s.key_person_buys > 0) score += s.key_person_buys * 5;
    if (s.foreign_accumulation) score += 10;
    score += s.actors.size * 5;
    score += s.active_days.size * 3;

    // v2.6: multi-key-person bonus
    const multi_key_person = s.key_person_count.size >= 2;
    if (multi_key_person) score += 15;

    // v2.6: insider cluster tag (buying on 3+ separate days by key persons)
    const insider_cluster_buy = s.key_person_buys >= 2 && s.buy_days_set.size >= 3;

    let signal = "NEUTRAL";
    if (s.net_volume > 0 && s.buy_count > s.sell_count) signal = "ACCUMULATION";
    if (s.net_volume > 0 && s.key_person_buys > 0)      signal = "STRONG_ACCUMULATION";
    if (s.net_volume < 0 && s.sell_count > s.buy_count) signal = "DISTRIBUTION";

    const total_tx = s.buy_volume + s.sell_volume;
    const tags     = [];
    if (multi_key_person)    tags.push("MULTI_KEY_PERSON");
    if (insider_cluster_buy) tags.push("INSIDER_CLUSTER_BUY");

    return {
      symbol:               s.symbol,
      signal,
      score:                Math.round(score),
      tags,
      net_volume:           s.net_volume,
      buy_volume:           s.buy_volume,
      sell_volume:          s.sell_volume,
      buy_ratio:            total_tx > 0 ? +(s.buy_volume / total_tx).toFixed(2) : 0,
      active_days:          s.active_days.size,
      buy_days:             s.buy_days_set.size,
      sell_days:            s.sell_days_set.size,
      recent_days:          s.recent_days.size,
      recency_ratio:        +recency_ratio.toFixed(2),
      key_person_activity:  s.key_person_activity,
      key_person_buys:      s.key_person_buys,
      foreign_accumulation: s.foreign_accumulation,
      unique_actors:        s.actors.size,
      multi_key_person,
      insider_cluster_buy
    };
  }).sort((a, b) => b.score - a.score);
}

// ---------- FILTER ----------
function isImportant(s) {
  if (s.score < MIN_SCORE) return false;
  const reasons = [];
  if (s.key_person_activity)                          reasons.push('key_person');
  if (Math.abs(s.net_volume) >= LARGE_VOLUME_THRESHOLD) reasons.push('large_volume');
  if (s.signal === 'DISTRIBUTION')                    reasons.push('distribution');
  return reasons.some(r => REQUIRE_ANY.includes(r));
}

// ---------- FETCH ----------
async function fetchAll() {
  let page = 1, all = [];
  const { date_start, date_end } = getDateRange();
  console.error(`📅 insider ${date_start} → ${date_end}`);

  while (true) {
    try {
      const res = await withRetry(() => axios.get(BASE_URL, {
        params: { page, limit: 50, date_start, date_end },
        headers: { authorization: `Bearer ${TOKEN}` },
        timeout: REQUEST_TIMEOUT_MS
      }));

      const data = res.data?.data;
      if (!data?.movement?.length) break;

      all.push(...transformData(data.movement));
      if (!data.is_more || page >= 20) break;

      page++;
      await new Promise(r => setTimeout(r, 300));

    } catch (e) {
      console.error(JSON.stringify({ stage: "insider_fetch", page, error: e.message }));
      break;
    }
  }

  return all;
}

// ---------- MAIN ----------
(async () => {
  const startTime = Date.now();
  const raw     = await fetchAll();
  const signals = buildSignals(raw);
  const filtered = signals.filter(isImportant);

  const runtime_seconds = Math.round((Date.now() - startTime) / 1000);
  console.error(JSON.stringify({ stage: "insider_done", runtime_seconds, total: signals.length, filtered: filtered.length }));

  const output = {
    meta: {
      generated_at:      new Date().toISOString(),
      date_range_months: MONTH_BACK,
      total_symbols:     signals.length,
      filtered:          filtered.length,
      min_score:         MIN_SCORE,
      runtime_seconds
    },
    signals: filtered
  };

  saveToFile(output);
})();
