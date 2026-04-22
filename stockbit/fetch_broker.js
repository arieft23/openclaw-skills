require('dotenv').config();
const axios = require('axios');
const fs    = require('fs');
const path  = require('path');

const TOKEN = process.env.STOCKBIT_TOKEN;
if (!TOKEN) { console.error("❌ STOCKBIT_TOKEN not found in .env"); process.exit(1); }

const BASE_TOP      = 'https://exodus.stockbit.com/order-trade/broker/top';
const BASE_ACTIVITY = 'https://exodus.stockbit.com/order-trade/broker/activity';

const headers = {
  'accept': 'application/json',
  'authorization': `Bearer ${TOKEN}`,
  'user-agent': 'Mozilla/5.0'
};

// ---------- CONFIG ----------
const DAYS_BACK             = 7;
const TOP_N                 = 50;
const MIN_TRANSACTION_VALUE = 50_000_000;
const MIN_DAILY_TOTAL       = 200_000_000;
const MIN_NET_ABS           = 200_000_000;
const MIN_SCORE             = 5;

// Tier 1 = large foreign/prime, Tier 2 = mid institutional, Tier 3 = local semi-inst
// Unlisted brokers = DEFAULT_WEIGHT (low tier)
const BROKER_WEIGHT = {
  "AK": 1.8, "BK": 1.8, "KZ": 1.8,
  "YU": 1.6, "RX": 1.6, "TP": 1.6,
  "CC": 1.3, "SQ": 1.3, "PD": 1.3, "NI": 1.3, "ZP": 1.3, "DB": 1.3, "ML": 1.3,
  "XC": 1.0, "XL": 1.0, "OD": 1.0, "GR": 1.0
};
const DEFAULT_WEIGHT = 0.6;

const FOREIGN_BROKERS = new Set([
  "AK","BK","KZ","YU","RX","TP","ZP","DB","ML","CS","MS","JP","UB","CI","BV","DX"
]);

// ---------- DATE ----------
function formatDate(d) { return d.toISOString().slice(0, 10); }

function getDates() {
  const dates = [], today = new Date();
  for (let i = 1; i <= DAYS_BACK; i++) {
    const d = new Date(today); d.setDate(d.getDate() - i);
    dates.push(formatDate(d));
  }
  return dates;
}

// ---------- SAVE ----------
function saveToFile(data) {
  const today = formatDate(new Date());

  const archiveDir = path.join(__dirname, 'data', 'broker');
  fs.mkdirSync(archiveDir, { recursive: true });
  fs.writeFileSync(path.join(archiveDir, `${today}.json`), JSON.stringify(data, null, 2));

  const latestDir = path.join(__dirname, 'data', 'latest');
  fs.mkdirSync(latestDir, { recursive: true });
  fs.writeFileSync(path.join(latestDir, 'broker.json'), JSON.stringify(data, null, 2));

  console.error(`💾 broker → data/broker/${today}.json + data/latest/broker.json`);
}

// ---------- FETCH ----------
async function getTopBrokers() {
  const res = await axios.get(BASE_TOP, {
    params: { sort: 'TB_SORT_BY_TOTAL_VALUE' }, headers
  });
  return res.data?.data?.list?.slice(0, TOP_N) || [];
}

async function getBrokerActivity(code, date) {
  try {
    const res = await axios.get(BASE_ACTIVITY, {
      params: { broker_code: code, page: 1, limit: 50, from: date, to: date },
      headers
    });

    const data = res.data?.data?.broker_activity_transaction;
    if (!data || (!data.brokers_buy?.length && !data.brokers_sell?.length)) return null;

    const total =
      (data.brokers_buy  || []).reduce((a, b) => a + (b.value || 0), 0) +
      (data.brokers_sell || []).reduce((a, b) => a + (b.value || 0), 0);
    if (total < MIN_DAILY_TOTAL) return null;

    return data;
  } catch (err) {
    console.error(`❌ ${code} ${date} →`, err.response?.status || err.message);
    return null;
  }
}

// ---------- TRANSFORM ----------
function transform(brokerCode, raw, date) {
  const map        = {};
  const weight     = BROKER_WEIGHT[brokerCode] ?? DEFAULT_WEIGHT;
  const is_foreign = FOREIGN_BROKERS.has(brokerCode);

  const process = (list, side) => {
    for (const item of list) {
      if ((item.value || 0) < MIN_TRANSACTION_VALUE) continue;
      const key = `${item.stock_code}__${brokerCode}__${date}`;
      if (!map[key]) map[key] = { stock: item.stock_code, date, broker: brokerCode,
                                   weight, is_foreign, buy_value: 0, sell_value: 0 };
      if (side === 'buy') map[key].buy_value  += item.value;
      else                map[key].sell_value += item.value;
    }
  };

  process(raw.brokers_buy  || [], 'buy');
  process(raw.brokers_sell || [], 'sell');
  return Object.values(map);
}

// ---------- SIGNAL ENGINE ----------
function buildSignals(allRecords) {
  const byStock = {};

  for (const r of allRecords) {
    if (!byStock[r.stock]) {
      byStock[r.stock] = {
        stock: r.stock, records: [], dates: new Set(), brokers: new Set(),
        foreign_buy: 0, foreign_sell: 0,
        smart_buy: 0,   smart_sell: 0,
        total_buy: 0,   total_sell: 0,
        date_net: {}
      };
    }
    const s = byStock[r.stock];
    s.records.push(r);
    s.dates.add(r.date);
    s.brokers.add(r.broker);
    s.total_buy  += r.buy_value;
    s.total_sell += r.sell_value;
    s.date_net[r.date] = (s.date_net[r.date] || 0) + (r.buy_value - r.sell_value);
    if (r.is_foreign) { s.foreign_buy += r.buy_value; s.foreign_sell += r.sell_value; }
    if (r.weight >= 1.3) {
      s.smart_buy  += r.buy_value  * r.weight;
      s.smart_sell += r.sell_value * r.weight;
    }
  }

  const results = [];

  for (const s of Object.values(byStock)) {
    const net_value = s.total_buy - s.total_sell;
    if (Math.abs(net_value) < MIN_NET_ABS) continue;

    // calendar-accurate buy/sell days
    let buy_days = 0, sell_days = 0;
    for (const net of Object.values(s.date_net)) {
      if (net > 0) buy_days++;
      if (net < 0) sell_days++;
    }

    // --- 4 sub-signals ---
    const foreign_net    = s.foreign_buy - s.foreign_sell;
    const foreign_signal = foreign_net > 0 ? "INFLOW" : foreign_net < 0 ? "OUTFLOW" : null;

    const smart_net    = s.smart_buy - s.smart_sell;
    const smart_signal = smart_net > 50_000_000  ? "ACCUMULATION"
      : smart_net < -50_000_000 ? "DISTRIBUTION" : null;

    const broker_buy_count  = s.records.filter(r => r.buy_value > r.sell_value).length;
    const broker_sell_count = s.records.filter(r => r.sell_value > r.buy_value).length;
    const breadth_signal    = broker_buy_count >= 3 && broker_buy_count > broker_sell_count
      ? "BROAD_BUY"
      : broker_sell_count >= 3 && broker_sell_count > broker_buy_count
        ? "BROAD_SELL" : null;

    const day_broker_buy = {};
    for (const r of s.records) {
      if (r.buy_value > r.sell_value)
        day_broker_buy[r.date] = (day_broker_buy[r.date] || 0) + 1;
    }
    const cluster_days   = Object.values(day_broker_buy).filter(c => c >= 2).length;
    const cluster_signal = cluster_days >= 2 ? "CLUSTER_BUY"
      : cluster_days === 1 ? "CLUSTER_BUY_WEAK" : null;

    // --- combined score ---
    let score = 0;
    const absNet = Math.abs(net_value);
    if      (absNet >= 100_000_000_000) score += 50;
    else if (absNet >=  10_000_000_000) score += 35;
    else if (absNet >=   1_000_000_000) score += 20;
    else if (absNet >=     200_000_000) score += 8;

    score += s.dates.size   * 4;
    score += buy_days        * 5;
    score -= sell_days       * 5;
    if (foreign_signal === "INFLOW")           score += 15;
    if (foreign_signal === "OUTFLOW")          score -= 10;
    if (smart_signal   === "ACCUMULATION")     score += 20;
    if (smart_signal   === "DISTRIBUTION")     score -= 15;
    if (breadth_signal === "BROAD_BUY")        score += 10;
    if (breadth_signal === "BROAD_SELL")       score -= 8;
    if (cluster_signal === "CLUSTER_BUY")      score += 10;
    if (cluster_signal === "CLUSTER_BUY_WEAK") score += 5;

    if (score < MIN_SCORE) continue;

    // --- overall signal ---
    let signal = "NEUTRAL";
    if (net_value > 0 && buy_days >= sell_days) signal = "ACCUMULATION";
    if (net_value > 0 && buy_days >= sell_days &&
        (smart_signal === "ACCUMULATION" || foreign_signal === "INFLOW")) signal = "STRONG_ACCUMULATION";
    if (net_value < 0 && sell_days >= buy_days) signal = "DISTRIBUTION";

    const total_tx = s.total_buy + s.total_sell;
    const tags     = [foreign_signal, smart_signal, breadth_signal, cluster_signal].filter(Boolean);

    // SKILL.md schema-compliant fields
    results.push({
      symbol:         s.stock,
      signal,
      score:          Math.round(score),
      tags,
      net_value_idr:  Math.round(net_value),    // SKILL.md name
      buy_value_idr:  Math.round(s.total_buy),
      sell_value_idr: Math.round(s.total_sell),
      buy_ratio:      total_tx > 0 ? +(s.total_buy / total_tx).toFixed(2) : 0,
      active_days:    s.dates.size,
      buy_days,
      sell_days,
      // sub-signal detail (for unified merger + debugging)
      foreign: {
        signal:   foreign_signal,
        net_idr:  Math.round(foreign_net),
        buy_idr:  Math.round(s.foreign_buy),
        sell_idr: Math.round(s.foreign_sell)
      },
      smart_money: {
        signal:   smart_signal,
        net_idr:  Math.round(smart_net)
      },
      breadth: {
        signal:            breadth_signal,
        broker_buy_count,
        broker_sell_count,
        unique_brokers:    s.brokers.size
      },
      cluster: {
        signal:       cluster_signal,
        cluster_days
      }
    });
  }

  return results.sort((a, b) => b.score - a.score);
}

// ---------- MAIN ----------
async function main() {
  console.error("🚀 fetching top brokers...");
  const brokers = await getTopBrokers();
  const dates   = getDates();
  console.error(`📋 ${brokers.length} brokers × ${dates.length} days = ${brokers.length * dates.length} requests`);

  const allRecords = [];

  for (const broker of brokers) {
    console.error(`🏦 ${broker.code}`);
    for (const date of dates) {
      const raw = await getBrokerActivity(broker.code, date);
      if (!raw) continue;
      allRecords.push(...transform(broker.code, raw, date));
      await new Promise(r => setTimeout(r, 120));
    }
  }

  console.error(`📊 ${allRecords.length} records → building signals...`);
  const signals = buildSignals(allRecords);
  console.error(`✅ ${signals.length} signals`);

  const output = {
    meta: {
      generated_at:   new Date().toISOString(),
      days_back:      DAYS_BACK,
      top_n_brokers:  brokers.length,
      total_signals:  signals.length
    },
    signals   // SKILL.md uses 'signals' not 'data'
  };

  saveToFile(output);
}

main();
