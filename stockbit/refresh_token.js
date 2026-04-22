#!/usr/bin/env node
/**
 * refresh_token.js — Stockbit token lifecycle manager
 *
 * Strategy:
 *   1. If access token is still valid → skip (nothing to do)
 *   2. If access token expired but refresh token valid → use refresh endpoint
 *   3. If both expired → re-login with username + password
 *
 * Updates .env in-place with new tokens + expiry timestamps.
 *
 * Usage:
 *   node refresh_token.js           # auto-detect what's needed
 *   node refresh_token.js --force   # force re-login even if token valid
 *
 * Add to pipeline:
 *   node refresh_token.js && node fetch_insider.js && ...
 */

require('dotenv').config();
const axios = require('axios');
const fs    = require('fs');
const path  = require('path');

const ENV_FILE = path.join(__dirname, '.env');

// ---------- CONFIG ----------
// Refresh token endpoint (discovered from login response)
const LOGIN_URL   = 'https://exodus.stockbit.com/login/v6/username';
const REFRESH_URL = 'https://exodus.stockbit.com/login/refresh'; // adjust if different

const PLAYER_ID   = process.env.STOCKBIT_PLAYER_ID || '99a772b7-c359-4bce-80b1-89ca0c7e3608';

const EMAIL       = process.env.STOCKBIT_EMAIL;
const PASSWORD    = process.env.STOCKBIT_PASSWORD;

// How many minutes before expiry to treat token as "expired" (safety buffer)
const BUFFER_MINUTES = 30;

const headers = {
  'User-Agent':   'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
  'Content-Type': 'application/json'
};

// ---------- TOKEN HELPERS ----------
function isExpired(isoTimestamp) {
  if (!isoTimestamp) return true;
  const expiry  = new Date(isoTimestamp).getTime();
  const now     = Date.now();
  const buffer  = BUFFER_MINUTES * 60 * 1000;
  return now >= expiry - buffer;
}

function readEnv() {
  return fs.existsSync(ENV_FILE) ? fs.readFileSync(ENV_FILE, 'utf8') : '';
}

function updateEnv(updates) {
  let env = readEnv();

  for (const [key, value] of Object.entries(updates)) {
    const line = `${key}=${value}`;
    if (env.match(new RegExp(`^${key}=`, 'm'))) {
      env = env.replace(new RegExp(`^${key}=.*`, 'm'), line);
    } else {
      env += `\n${line}`;
    }
  }

  fs.writeFileSync(ENV_FILE, env.trim() + '\n');
}

// ---------- LOGIN (password) ----------
async function loginWithPassword() {
  if (!EMAIL || !PASSWORD) {
    console.error('❌ STOCKBIT_EMAIL and STOCKBIT_PASSWORD required in .env for full re-login');
    console.error('   Add them to .env:\n   STOCKBIT_EMAIL=your@email.com\n   STOCKBIT_PASSWORD=yourpassword');
    process.exit(1);
  }

  console.error('🔑 logging in with password...');

  const res = await axios.post(LOGIN_URL, {
    user:      EMAIL,
    password:  PASSWORD,
    player_id: PLAYER_ID
  }, { headers });

  const td = res.data?.data?.login?.token_data;
  if (!td?.access?.token) {
    console.error('❌ unexpected login response:', JSON.stringify(res.data).slice(0, 300));
    process.exit(1);
  }

  return {
    access_token:          td.access.token,
    access_token_expiry:   td.access.expired_at,
    refresh_token:         td.refresh.token,
    refresh_token_expiry:  td.refresh.expired_at
  };
}

// ---------- REFRESH (use refresh token) ----------
async function refreshWithToken(refreshToken) {
  console.error('🔄 refreshing access token using refresh token...');

  try {
    const res = await axios.post(REFRESH_URL, {}, {
      headers: {
        ...headers,
        authorization: `Bearer ${refreshToken}`
      }
    });

    const td = res.data?.access?.token;
    if (!td) {
      // Refresh endpoint may return different structure — try fallback
      const token = res.data?.data?.token || res.data?.token;
      if (token) {
        return {
          access_token:         token,
          access_token_expiry:  res.data?.data?.expired_at || null,
          refresh_token:        refreshToken,        // keep existing refresh token
          refresh_token_expiry: process.env.STOCKBIT_REFRESH_TOKEN_EXPIRY || null
        };
      }
      throw new Error('unexpected refresh response: ' + JSON.stringify(res.data).slice(0, 200));
    }

    return {
      access_token:          td.access.token,
      access_token_expiry:   td.access.expired_at,
      refresh_token:         td.refresh?.token         || refreshToken,
      refresh_token_expiry:  td.refresh?.expired_at    || process.env.STOCKBIT_REFRESH_TOKEN_EXPIRY
    };

  } catch (err) {
    if (err.response?.status === 401 || err.response?.status === 403) {
      console.error('⚠️  refresh token rejected — falling back to password login');
      return null; // signal caller to use password
    }
    throw err;
  }
}

// ---------- MAIN ----------
async function main() {
  const forceRefresh = process.argv.includes('--force');

  const currentToken        = process.env.STOCKBIT_TOKEN;
  const currentTokenExpiry  = process.env.STOCKBIT_ACCESS_TOKEN_EXPIRY;
  const refreshToken        = process.env.STOCKBIT_REFRESH_TOKEN;
  const refreshTokenExpiry  = process.env.STOCKBIT_REFRESH_TOKEN_EXPIRY;

  // --- Check if access token is still valid ---
  if (!forceRefresh && currentToken && !isExpired(currentTokenExpiry)) {
    const exp = new Date(currentTokenExpiry);
    console.error(`✅ access token still valid until ${exp.toISOString()} — skipping refresh`);
    process.exit(0);
  }

  let result = null;

  // --- Try refresh token first ---
  if (refreshToken && !isExpired(refreshTokenExpiry)) {
    result = await refreshWithToken(refreshToken);
  }

  // --- Fall back to password login ---
  if (!result) {
    result = await loginWithPassword();
  }

  // --- Update .env ---
  updateEnv({
    STOCKBIT_TOKEN:               result.access_token,
    STOCKBIT_ACCESS_TOKEN_EXPIRY: result.access_token_expiry  || '',
    STOCKBIT_REFRESH_TOKEN:       result.refresh_token         || '',
    STOCKBIT_REFRESH_TOKEN_EXPIRY:result.refresh_token_expiry  || ''
  });

  const expiry = result.access_token_expiry
    ? new Date(result.access_token_expiry).toISOString()
    : 'unknown';

  console.error(`✅ token updated — expires ${expiry}`);
  console.error(`   access:  ${result.access_token.slice(0, 30)}...`);
  if (result.refresh_token) {
    console.error(`   refresh: ${result.refresh_token.slice(0, 30)}...`);
  }
}

main().catch(err => {
  console.error('❌ refresh_token.js error:', err.response?.status, err.message);
  process.exit(1);
});
