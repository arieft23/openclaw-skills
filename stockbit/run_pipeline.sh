#!/bin/bash

# Exit immediately if any command fails
set -e

WORKDIR=~/stockbit
LOG_PREFIX="[PIPELINE]"

echo "$LOG_PREFIX ===== $(date -u '+%Y-%m-%d %H:%M:%S UTC') START ====="

cd "$WORKDIR" || {
  echo "$LOG_PREFIX ERROR: Cannot cd to $WORKDIR"
  exit 1
}

echo "$LOG_PREFIX Running fetch_insider.js"
node fetch_insider.js

echo "$LOG_PREFIX Running fetch_broker.js"
node fetch_broker.js

echo "$LOG_PREFIX Running fetch_unified.js"
node fetch_unified.js

echo "$LOG_PREFIX ===== $(date -u '+%Y-%m-%d %H:%M:%S UTC') DONE ====="
echo ""
