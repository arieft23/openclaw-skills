#!/usr/bin/env python3
"""
Top Indonesian Stocks Command Handler
Usage: /top_idn_stock [number]
Example: /top_idn_stock 5
"""

import json
import os
import sys
from datetime import datetime

# Configuration - Updated to use unified_enriched.json
STOCKBIT_DATA_PATH = '/home/ubuntu/.openclaw/workspace/skills/stockbit/data/latest/unified_enriched.json'
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

# Company profiles (static)
COMPANY_PROFILES = {
    "TLKM": "Telkom Indonesia - Telecom giant, fiber & 5G provider",
    "BULL": "Buana Lintas Lautan - Oil & gas tanker shipping (2.4M DWT)",
    "KIJA": "Kija Holdings - Nickel mining & processing",
    "PACK": "Packaging Corp - Paper & packaging manufacturer",
    "BSDE": "Bumi Serpong Damai - Property developer (BSD City)",
    "BSML": "Bintang Samudera - Shipping & tanker services",
    "ARNA": "Arwana Citramulia - Ceramic tiles manufacturer",
    "UVCR": "Uvcar - EV & battery technology",
    "MEDS": "Mediwise Indonesia - Healthcare & pharmaceutical",
    "ROTI": "Nippon Indosari - Food & beverage (instant noodles)",
}

def load_stockbit_data():
    """Load Stockbit unified_enriched signals"""
    try:
        with open(STOCKBIT_DATA_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading Stockbit data: {e}")
        return None

def get_top_signals(data, limit=5):
    """Get top high conviction signals"""
    if not data or 'signals' not in data:
        return []
    
    # Filter for high conviction signals
    high_signals = [
        s for s in data['signals'] 
        if s['final_signal'] in ['EXTREME_CONVICTION', 'HIGH_CONVICTION']
    ]
    
    # Sort by composite score descending
    high_signals.sort(key=lambda x: x['composite_score'], reverse=True)
    
    return high_signals[:limit]

def get_company_profile(symbol):
    """Get company profile"""
    return COMPANY_PROFILES.get(symbol, "Company profile unavailable")

def generate_output(limit=5):
    """Generate the top N stocks output - requires live fetch"""
    
    # Load data
    data = load_stockbit_data()
    if not data:
        return "❌ Unable to load Stockbit data. Please run the pipeline first."
    
    # Get top signals
    top_signals = get_top_signals(data, limit)
    
    if not top_signals:
        return "❌ No high conviction signals found today."
    
    # Generate output header
    generated_at = data['meta']['generated_at'][:10]
    
    output = []
    output.append(f"🔥 Top {limit} Indonesian Stocks - {generated_at}")
    output.append("==================================================")
    output.append("")
    
    # Note: This is a placeholder. The actual command handler
    # should fetch prices via yfinance and news via websearch
    # and format the output with source attribution
    
    output.append("Note: Run via agent for live prices + news")
    output.append("==================================================")
    
    return "\n".join(output)

def main():
    """Main handler for bot command"""
    args = sys.argv[1:]
    
    if args:
        try:
            limit = int(args[0])
            limit = max(1, min(limit, MAX_LIMIT))
        except ValueError:
            limit = DEFAULT_LIMIT
    else:
        limit = DEFAULT_LIMIT
    
    print(generate_output(limit))

if __name__ == "__main__":
    main()