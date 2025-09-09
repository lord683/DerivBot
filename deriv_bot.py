import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import websocket
import logging

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "R_50", "R_100"]
TIMEFRAMES = {
    "5m": 300,
    "10m": 600, 
    "15m": 900
}

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- TELEGRAM ----------------
def send_telegram(message):
    """Send message to Telegram with robust error handling"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram tokens not configured")
        return False
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID, 
            "text": message, 
            "parse_mode": "Markdown"
        }
        response = requests.post(url, data=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        else:
            logger.error(f"Telegram API error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False

# ---------------- DERIV DATA ----------------
def get_deriv_candles(symbol, timeframe, count=50):
    """Fetch candle data from Deriv with proper error handling"""
    try:
        logger.info(f"Fetching {symbol} {timeframe} data...")
        
        # Create WebSocket connection with timeout
        ws = websocket.create_connection(
            "wss://ws.derivws.com/websockets/v3?app_id=1089",
            timeout=15
        )
        
        # Authorize
        auth_payload = {"authorize": DERIV_API_KEY}
        ws.send(json.dumps(auth_payload))
        auth_response = ws.recv()
        
        auth_data = json.loads(auth_response)
        if "error" in auth_data:
            error_msg = f"Deriv auth error: {auth_data['error']['message']}"
            logger.error(error_msg)
            send_telegram(f"❌ {error_msg}")
            ws.close()
            return pd.DataFrame()
        
        # Request candle data
        request_payload = {
            "ticks_history": symbol,
            "count": count,
            "end": "latest",
            "style": "candles",
            "granularity": timeframe
        }
        
        ws.send(json.dumps(request_payload))
        
        # Get response with timeout
        response = ws.recv()
        ws.close()
        
        response_data = json.loads(response)
        
        if "error" in response_data:
            error_msg = f"Deriv data error: {response_data['error']['message']}"
            logger.error(error_msg)
            return pd.DataFrame()
            
        if "history" in response_data and "candles" in response_data["history"]:
            candles = response_data["history"]["candles"]
            if candles:
                df = pd.DataFrame(candles)
                # Convert to numeric
                numeric_cols = ['open', 'high', 'low', 'close']
                for col in numeric_cols:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna()
                logger.info(f"Successfully fetched {len(df)} candles for {symbol}")
                return df
        
        logger.warning(f"No candle data received for {symbol}")
        return pd.DataFrame()
        
    except websocket.WebSocketTimeoutException:
        error_msg = f"WebSocket timeout for {symbol}"
        logger.error(error_msg)
        send_telegram(f"⏰ {error_msg}")
    except websocket.WebSocketConnectionClosedException:
        error_msg = f"WebSocket connection closed for {symbol}"
        logger.error(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error fetching {symbol}: {str(e)}"
        logger.error(error_msg)
        send_telegram(f"❌ {error_msg}")
    
    return pd.DataFrame()

# ---------------- INDICATORS ----------------
def ema(series, period):
    """Calculate Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    """Calculate Relative Strength Index"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ---------------- STRATEGY ----------------
def analyze(df, symbol, tf_name):
    """Analyze market data for trading signals"""
    if df.empty or len(df) < 20:
        return None

    try:
        closes = df["close"]
        ema_fast = ema(closes, 9).iloc[-1]
        ema_slow = ema(closes, 21).iloc[-1]
        rsi_val = rsi(closes, 14).iloc[-1]
        price = closes.iloc[-1]
        
        # Calculate volatility (standard deviation of returns)
        volatility = closes.pct_change().std() * 100
        
        # Long signal conditions
        if (ema_fast > ema_slow and 
            rsi_val > 45 and rsi_val < 70 and
            volatility > 0.3):  # Minimum volatility filter
            return f"""
🎯 *LONG SIGNAL* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {price:.5f}
*RSI:* {rsi_val:.1f}
*Volatility:* {volatility:.2f}%
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""
        
        # Short signal conditions  
        elif (ema_fast < ema_slow and 
              rsi_val < 55 and rsi_val > 30 and
              volatility > 0.3):  # Minimum volatility filter
            return f"""
🎯 *SHORT SIGNAL* 🎯  
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {price:.5f}
*RSI:* {rsi_val:.1f}
*Volatility:* {volatility:.2f}%
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""
            
    except Exception as e:
        logger.error(f"Analysis error for {symbol}: {e}")
        
    return None

# ---------------- MAIN ----------------
def run_bot():
    """Main bot function"""
    logger.info("Starting Deriv Trading Bot...")
    
    # Test Telegram connection first
    test_message = """
🤖 *DERIV BOT CONNECTED SUCCESSFULLY!* 🤖

✅ *Telegram:* Connected
✅ *Status:* Online and analyzing
📊 *Pairs:* EURUSD, GBPUSD, USDJPY, Volatility Indices
⏰ *Timeframes:* 5m, 10m, 15m
🕒 *Started:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

*Beginning market analysis...*
"""
    
    if not send_telegram(test_message):
        logger.error("Failed to send initial Telegram message. Check tokens.")
        return
    
    # Check if Deriv API key is available
    if not DERIV_API_KEY:
        error_msg = "❌ DERIV_API_KEY not found! Please check GitHub Secrets."
        logger.error(error_msg)
        send_telegram(error_msg)
        return
    
    signals_found = 0
    
    # Analyze each symbol and timeframe
    for symbol in SYMBOLS:
        for tf_name, tf_sec in TIMEFRAMES.items():
            try:
                logger.info(f"Analyzing {symbol} on {tf_name} timeframe...")
                
                # Get market data
                df = get_deriv_candles(symbol, tf_sec, count=50)
                
                if not df.empty:
                    # Generate signal
                    signal = analyze(df, symbol, tf_name)
                    if signal:
                        if send_telegram(signal):
                            signals_found += 1
                            logger.info(f"Signal sent for {symbol} ({tf_name})")
                        time.sleep(2)  # Avoid rate limiting
                else:
                    logger.warning(f"No data received for {symbol} {tf_name}")
                    
                time.sleep(1)  # Brief pause between requests
                
            except Exception as e:
                error_msg = f"Error processing {symbol} {tf_name}: {str(e)}"
                logger.error(error_msg)
                send_telegram(f"⚠️ {error_msg}")
                time.sleep(5)
    
    # Send completion report
    completion_message = f"""
📊 *ANALYSIS COMPLETE* 📊

*Signals Found:* {signals_found}
*Pairs Analyzed:* {len(SYMBOLS)}
*Timeframes Scanned:* {len(TIMEFRAMES)}
*Completed At:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

*Next analysis in 5 minutes...*
"""
    
    send_telegram(completion_message)
    logger.info(f"Analysis completed. {signals_found} signals found.")

# ---------------- EXECUTION ----------------
if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        send_telegram("🛑 Bot manually stopped by user")
    except Exception as e:
        error_msg = f"❌ CRITICAL ERROR: Bot crashed - {str(e)}"
        logger.error(error_msg)
        send_telegram(error_msg)
