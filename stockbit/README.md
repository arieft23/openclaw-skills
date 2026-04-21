# OpenClaw Data Schemas

This document defines the data structures for Insider, Broker, and Unified datasets used in OpenClaw signal processing.

All datasets are generated from Stockbit market activity aggregation pipelines and stored locally for downstream analysis, backtesting, and agent reasoning.

---

# Insider Data Schema (Stockbit Major Holder)

Insider data represents major shareholder, director, commissioner, and controlling shareholder transactions.

Source: `~/stockbit/data/insider/YYYY-MM-DD.json`

---

## Root Object

```json
{
  "meta": {},
  "data": []
}
```

---

## meta

| Field              | Type   | Description                                     |
| ------------------ | ------ | ----------------------------------------------- |
| generated_at       | string | Timestamp when dataset was generated (ISO 8601) |
| date_start         | string | Start date of data range                        |
| date_end           | string | End date of data range                          |
| total_symbols_raw  | number | Total symbols before filtering                  |
| total_symbols_out  | number | Symbols after filtering                         |
| skipped_low_signal | number | Number of filtered-out weak signals             |

---

## data[] (Insider Signal Object)

| Field       | Type   | Description                                                 |
| ----------- | ------ | ----------------------------------------------------------- |
| symbol      | string | Stock ticker                                                |
| signal      | string | ACCUMULATION / DISTRIBUTION / STRONG_ACCUMULATION / NEUTRAL |
| score       | number | Strength score                                              |
| net_volume  | number | Buy minus sell volume                                       |
| buy_volume  | number | Total buy volume                                            |
| sell_volume | number | Total sell volume                                           |
| buy_ratio   | number | Buy ratio (0–1)                                             |
| active_days | number | Number of active days                                       |
| buy_days    | number | Days with net buying                                        |
| sell_days   | number | Days with net selling                                       |

---

## Insider Flags

| Field                | Type    | Description                                                    |
| -------------------- | ------- | -------------------------------------------------------------- |
| key_person_activity  | boolean | Insider includes director/commissioner/controlling shareholder |
| key_person_buys      | number  | Number of key person buy transactions                          |
| foreign_accumulation | boolean | Foreign insider accumulation detected                          |

---

## Insider Tags

* key_person
* strong_accum
* large_volume
* distribution

---

## Interpretation

* STRONG_ACCUMULATION = high conviction insider buying
* Key person activity increases signal weight
* Multi-day consistency improves reliability
* DISTRIBUTION indicates risk or profit-taking phase

---

# Broker Data Schema (Stockbit Broker Activity)

Broker data represents aggregated buy/sell activity from top securities brokers.

Source: `~/stockbit/data/broker/YYYY-MM-DD.json`

---

## Root Object

```json
{
  "meta": {},
  "data": []
}
```

---

## meta

| Field         | Type   | Description                          |
| ------------- | ------ | ------------------------------------ |
| generated_at  | string | Timestamp when dataset was generated |
| days_back     | number | Number of historical days processed  |
| brokers       | number | Number of brokers analyzed           |
| total_signals | number | Number of final stock signals        |

---

## data[] (Broker Signal Object)

| Field         | Type          | Description                                                 |
| ------------- | ------------- | ----------------------------------------------------------- |
| symbol        | string        | Stock ticker                                                |
| signal        | string        | ACCUMULATION / STRONG_ACCUMULATION / DISTRIBUTION / NEUTRAL |
| score         | number        | Broker activity strength score                              |
| net_value_idr | number        | Net buy/sell value                                          |
| buy_ratio     | number        | Buy ratio (0–1)                                             |
| active_days   | number        | Number of active trading days                               |
| buy_days      | number        | Days with net buying                                        |
| sell_days     | number        | Days with net selling                                       |
| tags          | array<string> | Signal modifiers                                            |

---

## Broker Tags

* INFLOW / OUTFLOW (foreign flow)
* ACCUMULATION / DISTRIBUTION (smart money)
* BROAD_BUY / BROAD_SELL
* CLUSTER_BUY / CLUSTER_BUY_WEAK

---

## Interpretation

* STRONG_ACCUMULATION = institutional buying alignment
* BROAD_BUY = multi-broker participation
* CLUSTER_BUY = repeated accumulation over time
* DISTRIBUTION = selling pressure / risk-off

---

# Unified Data Schema (Insider + Broker Fusion)

Unified data combines insider and broker signals into a single stock-level conviction model.

Source: `~/stockbit/data/unified/YYYY-MM-DD.json`

---

## Root Object

```json
{
  "meta": {},
  "data": []
}
```

---

## meta

| Field         | Type   | Description                         |
| ------------- | ------ | ----------------------------------- |
| date          | string | Dataset date (YYYY-MM-DD)           |
| insider_count | number | Number of insider records loaded    |
| broker_count  | number | Number of broker records loaded     |
| unified_count | number | Number of unified symbols generated |

---

## data[] (Unified Signal Object)

| Field           | Type   | Description                          |
| --------------- | ------ | ------------------------------------ |
| symbol          | string | Stock ticker                         |
| signal          | string | Final unified signal                 |
| score           | number | Combined conviction score            |
| alignment_score | number | Insider vs broker agreement strength |

---

## Insider Block

| Field      | Type    | Description            |
| ---------- | ------- | ---------------------- |
| signal     | string  | Insider signal         |
| score      | number  | Insider score          |
| net        | number  | Insider net volume     |
| key_person | boolean | Key person involvement |
| foreign    | boolean | Foreign accumulation   |

---

## Broker Block

| Field  | Type          | Description        |
| ------ | ------------- | ------------------ |
| signal | string        | Broker signal      |
| score  | number        | Broker score       |
| net    | number        | Broker net value   |
| tags   | array<string> | Broker signal tags |

---

## Unified Signals

| Signal                        | Meaning                                            |
| ----------------------------- | -------------------------------------------------- |
| STRONG_CONFLUENT_ACCUMULATION | Strong alignment between insider and broker buying |
| CONFLUENT_ACCUMULATION        | Moderate alignment of buying pressure              |
| INSIDER_DRIVEN                | Insider activity dominates signal                  |
| BROKER_DRIVEN                 | Broker activity dominates signal                   |
| RISK_DISTRIBUTION             | At least one side shows distribution pressure      |
| NEUTRAL                       | No strong directional conviction                   |

---

## Interpretation

* STRONG_CONFLUENT_ACCUMULATION = highest conviction setup
* Alignment score measures insider/broker agreement
* Distribution override always takes priority
* Used for backtesting and OpenClaw agent decision layer
