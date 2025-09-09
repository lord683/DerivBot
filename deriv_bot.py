import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

# ---------------- CONFIG ----------------
DERIV_APP_ID = 1089  # Deriv demo app ID
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAUUSD"]  # Using simpler symbols
TIMEFRAMES = {
    "5m": "5",
    "15m": "15",
    "1h": "60"
}

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- TELEGRAM ----------------
def send_telegram(message):
    """Send message to Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram tokens missing")
        return False
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID, 
            "text": message, 
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False

# ---------------- MARKET DATA (REST API) ----------------
def get_market_data(symbol, timeframe, count=50):
    """Get market data using REST API instead of WebSocket"""
    try:
        # Use free financial API as fallback (Twelvedata, Alpha Vantage, etc.)
        # For now, let's use Yahoo Finance as backup
        yf_symbol = {
            "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X", 
            "USDJPY": "USDJPY=X",
            "GBPJPY": "GBPJPY=X",
            "XAUUSD": "GC=F"
        }.get(symbol, f"{symbol}=X")
        
        # Download data
        interval = f"{timeframe}m"
        data = yf.download(yf_symbol, period="5d", interval=interval, progress=False)
        
        if not data.empty:
            return data.tail(count)
            
    except Exception as e:
        logger.error(f"Market data error for {symbol}: {e}")
    
    return pd.DataFrame()

# ---------------- INDICATORS ----------------
def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ---------------- STRATEGY ----------------
def analyze_market(df, symbol, tf_name):
    """Analyze market for trading opportunities"""
    if df.empty or len(df) < 20:
        return None

    try:
        closes = df['Close']
        highs = df['High']
        lows = df['Low']
        
        # Calculate indicators
        ema_fast = calculate_ema(closes, 9).iloc[-1]
        ema_slow = calculate_ema(closes, 21).iloc[-1]
        rsi_val = calculate_rsi(closes, 14).iloc[-1]
        current_price = closes.iloc[-1]
        
        # Calculate volatility
        volatility = closes.pct_change().std() * 100
        
        # Support/Resistance
        support = lows.tail(20).min()
        resistance = highs.tail(20).max()
        
        # Trading signals
        long_conditions = [
            ema_fast > ema_slow,
            45 < rsi_val < 70,
            current_price > support,
            volatility > 0.5,
            current_price > closes.iloc[-2]  # Upward momentum
        ]
        
        short_conditions = [
            ema_fast < ema_slow,
            30 < rsi_val < 55,
            current_price < resistance,
            volatility > 0.5,
            current_price < closes.iloc[-2]  # Downward momentum
        ]
        
        if sum(long_conditions) >= 4:
            return f"""
🎯 *LONG SIGNAL* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {current_price:.5f}
*SL:* {support:.5f}
*TP:* {resistance:.5f}
*RSI:* {rsi_val:.1f}
*Volatility:* {volatility:.2f}%
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""
        
        elif sum(short_conditions) >= 4:
            return f"""
🎯 *SHORT SIGNAL* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {current_price:.5f}
*SL:* {resistance:.5f}
*TP:* {support:.5f}
*RSI:* {rsi_val:.1f}
*Volatility:* {volatility:.2f}%
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""
            
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        
    return None

# ---------------- MAIN BOT ----------------
def run_bot():
    """Main trading bot function"""
    logger.info("Starting Trading Bot...")
    
    # Send connection message
    connection_msg = f"""
🤖 *TRADING BOT CONNECTED* 🤖

✅ *Status:* Online and Monitoring
📊 *Pairs:* {', '.join(SYMBOLS)}
⏰ *Timeframes:* {', '.join(TIMEFRAMES.keys())}
🕒 *Start Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🌐 *Data Source:* Yahoo Finance API

*Beginning market analysis...*
"""
    
    if not send_telegram(connection_msg):
        logger.error("Failed to send connection message")
        return
    
    signals_found = 0
    
    # Analyze each market
    for symbol in SYMBOLS:
        for tf_name, tf_value in TIMEFRAMES.items():
            try:
                logger.info(f"Analyzing {symbol} on {tf_name} timeframe...")
                
                # Get market data
                df = get_market_data(symbol, tf_value)
                
                if not df.empty and len(df) > 15:
                    signal = analyze_market(df, symbol, tf_name)
                    if signal:
                        if send_telegram(signal):
                            signals_found += 1
                            logger.info(f"Signal found for {symbol}")
                        time.sleep(2)  # Avoid rate limiting
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                time.sleep(3)
    
    # Send completion report
    report_msg = f"""
📊 *ANALYSIS COMPLETE* 📊

*Signals Found:* {signals_found}
*Markets Analyzed:* {len(SYMBOLS) * len(TIMEFRAMES)}
*Completion Time:* {datetime.now().strftime('%H:%M:%S')}

*Next analysis in 5 minutes...*
"""
    
    send_telegram(report_msg)
    logger.info(f"Analysis completed. Found {signals_found} signals.")

# ---------------- EXECUTION ----------------
if __name__ == "__main__":
    # Test Telegram immediately
    test_msg = "🔔 Bot starting up... Testing Telegram connection"
    if send_telegram(test_msg):
        logger.info("Telegram test successful")
    else:
        logger.error("Telegram test failed - check tokens")
    
    # Run main bot
    try:
        run_bot()
    except Exception as e:
        error_msg = f"❌ Bot crashed: {str(e)}"
        logger.error(error_msg)
        send_telegram(error_msg)
