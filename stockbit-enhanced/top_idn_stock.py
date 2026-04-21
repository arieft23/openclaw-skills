#!/usr/bin/env python3
"""
Top Indonesian Stocks Command Handler
Usage: /top-idn-stock [number]
Example: /top-idn-stock 5
"""

import json
import os
import sys
from datetime import datetime

# Configuration
STOCKBIT_DATA_PATH = '/home/ubuntu/.openclaw/workspace/skills/stockbit/data/latest/unified.json'
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

# Company profiles (static - could be fetched dynamically)
COMPANY_PROFILES = {
    "TLKM": "Telkom Indonesia - Telecom giant, fiber & 5G provider",
    "BULL": "Buana Lintang Lautan - Oil & gas tanker shipping (2.4M DWT)",
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
    """Load Stockbit unified signals"""
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

def format_price(symbol):
    """Simulated price - in production, use yfinance"""
    # Placeholder: would use yfinance in production
    prices = {
        "TLKM": {"price": 4200, "change": 2.5},
        "BULL": {"price": 1850, "change": 1.8},
        "KIJA": {"price": 3400, "change": 3.2},
        "PACK": {"price": 850, "change": -0.5},
        "BSDE": {"price": 1150, "change": 1.2},
        "BSML": {"price": 920, "change": 0.8},
        "ARNA": {"price": 580, "change": 1.5},
        "UVCR": {"price": 210, "change": 2.1},
    }
    return prices.get(symbol, {"price": 0, "change": 0})

def get_news_sentiment(symbol):
    """Simulated news - in production, use websearch"""
    # Placeholder: would use websearch in production
    news = {
        "TLKM": "📈 Positive - 5G expansion announced",
        "BULL": "📈 Bullish - Strong broker flow",
        "KIJA": "📈 Positive - Q1 earnings beat",
        "PACK": "👀 Mixed - Volume surge",
        "BSDE": "📈 Positive - Property sector rally",
        "BSML": "👀 Neutral - Watching",
        "ARNA": "📈 Positive - Pharma growth",
        "UVCR": "📈 Positive - Key insider buying",
    }
    return news.get(symbol, "👀 No recent news")

def generate_output(limit=5):
    """Generate the top N stocks output"""
    
    # Load data
    data = load_stockbit_data()
    if not data:
        return "❌ Unable to load Stockbit data. Please run the pipeline first."
    
    # Get top signals
    top_signals = get_top_signals(data, limit)
    
    if not top_signals:
        return "❌ No high conviction signals found today."
    
    # Generate output
    generated_at = data['meta']['generated_at'][:10]
    
    output = []
    output.append(f"🔥 Top {limit} Indonesian Stocks - {generated_at}")
    output.append("=" * 50)
    output.append("")
    
    for i, signal in enumerate(top_signals, 1):
        symbol = signal['symbol']
        signal_type = signal['final_signal']
        score = signal['composite_score']
        conviction = signal['conviction_level']
        
        # Get price and news
        price_data = format_price(symbol)
        news = get_news_sentiment(symbol)
        profile = get_company_profile(symbol)
        
        # Signal badge
        if signal_type == "EXTREME_CONVICTION":
            badge = "🚀"
        else:
            badge = "📈"
        
        output.append(f"{badge} #{i} {symbol}")
        output.append(f"   Signal: {signal_type} ({conviction})")
        output.append(f"   Score: {score}/100")
        output.append(f"   💰 Price: Rp {price_data['price']:,} ({price_data['change']:+.1f}%)")
        output.append(f"   🏢 {profile}")
        output.append(f"   📰 {news}")
        output.append("")
    
    output.append("=" * 50)
    output.append("💡 Use /top-idn-stock [N] for more stocks")
    
    return "\n".join(output)

def main():
    """Main handler for bot command"""
    # Get limit from command line args
    args = sys.argv[1:]
    
    if args:
        try:
            limit = int(args[0])
            if limit < 1:
                limit = DEFAULT_LIMIT
            elif limit > MAX_LIMIT:
                limit = MAX_LIMIT
        except ValueError:
            limit = DEFAULT_LIMIT
    else:
        limit = DEFAULT_LIMIT
    
    # Generate and print output
    print(generate_output(limit))

if __name__ == "__main__":
    main()