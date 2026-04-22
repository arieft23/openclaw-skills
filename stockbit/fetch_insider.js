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

// ---------- SAVE ----------
function saveToFile(data) {
  const today = formatDate(new Date());

  // dated archive
  const archiveDir = path.join(__dirname, 'data', 'insider');
  fs.mkdirSync(archiveDir, { recursive: true });
  fs.writeFileSync(path.join(archiveDir, `${today}.json`), JSON.stringify(data, null, 2));

  // latest symlink
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

  // aggregate splits
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
        actors: new Set()
      };
    }

    const s = map[d.symbol];
    s.actors.add(d.actor);
    s.active_days.add(d.date);

    if (d.action === 'BUY') {
      s.buy_volume  += d.volume;
      s.net_volume  += d.volume;
      s.buy_count++;
      s.buy_days_set.add(d.date);
      if (d.is_key_person) s.key_person_buys++;
    } else {
      s.sell_volume += d.volume;
      s.net_volume  -= d.volume;
      s.sell_count++;
      s.sell_days_set.add(d.date);
    }

    if (d.is_key_person) s.key_person_activity = true;
    if (d.is_foreign && d.action === 'BUY') s.foreign_accumulation = true;
  }

  return Object.values(map).map(s => {
    let score = Math.abs(s.net_volume) / 1_000_000;
    if (s.key_person_activity) score += 20;
    if (s.key_person_buys > 0) score += s.key_person_buys * 5;
    if (s.foreign_accumulation) score += 10;
    score += s.actors.size * 5;
    score += s.active_days.size * 3;

    let signal = "NEUTRAL";
    if (s.net_volume > 0 && s.buy_count > s.sell_count) signal = "ACCUMULATION";
    if (s.net_volume > 0 && s.key_person_buys > 0)      signal = "STRONG_ACCUMULATION";
    if (s.net_volume < 0 && s.sell_count > s.buy_count) signal = "DISTRIBUTION";

    const total_tx = s.buy_volume + s.sell_volume;

    // SKILL.md schema-compliant field names
    return {
      symbol:               s.symbol,
      signal,
      score:                Math.round(score),
      net_volume:           s.net_volume,
      buy_volume:           s.buy_volume,
      sell_volume:          s.sell_volume,
      buy_ratio:            total_tx > 0 ? +(s.buy_volume / total_tx).toFixed(2) : 0,
      active_days:          s.active_days.size,
      buy_days:             s.buy_days_set.size,
      sell_days:            s.sell_days_set.size,
      key_person_activity:  s.key_person_activity,   // SKILL.md name
      key_person_buys:      s.key_person_buys,        // SKILL.md name
      foreign_accumulation: s.foreign_accumulation,   // SKILL.md name
      unique_actors:        s.actors.size
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
      const res = await axios.get(BASE_URL, {
        params: { page, limit: 50, date_start, date_end },
        headers: { authorization: `Bearer ${TOKEN}` }
      });

      const data = res.data?.data;
      if (!data?.movement?.length) break;

      all.push(...transformData(data.movement));
      if (!data.is_more || page >= 20) break;

      page++;
      await new Promise(r => setTimeout(r, 300));

    } catch (e) {
      console.error("❌ insider fetch error:", e.message);
      break;
    }
  }

  return all;
}

// ---------- MAIN ----------
(async () => {
  const raw     = await fetchAll();
  const signals = buildSignals(raw);
  const filtered = signals.filter(isImportant);

  const output = {
    meta: {
      generated_at:    new Date().toISOString(),
      date_range_months: MONTH_BACK,
      total_symbols:   signals.length,
      filtered:        filtered.length,
      min_score:       MIN_SCORE
    },
    signals: filtered
  };

  saveToFile(output);
})();
