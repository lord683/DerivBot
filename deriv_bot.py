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
DERIV_API_KEY = os.getenv("DERIV_API_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
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
        ws = websocket.create_connection(
            "wss://ws.derivws.com/websockets/v3?app_id=1089",
            timeout=15
        )
        ws.send(json.dumps({"authorize": DERIV_API_KEY}))
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
                for col in ['open', 'high', 'low', 'close']:
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
    except Exception as e:
        error_msg = f"Unexpected error fetching {symbol}: {str(e)}"
        logger.error(error_msg)
        send_telegram(f"❌ {error_msg}")
    return pd.DataFrame()

# ---------------- INDICATORS ----------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ---------------- STRATEGY ----------------
def analyze(df, symbol, tf_name):
    if df.empty or len(df) < 20:
        return None
    try:
        closes = df["close"]
        ema_fast = ema(closes, 9).iloc[-1]
        ema_slow = ema(closes, 21).iloc[-1]
        rsi_val = rsi(closes, 14).iloc[-1]
        price = closes.iloc[-1]
        volatility = closes.pct_change().std() * 100
        # Long signal
        if (ema_fast > ema_slow and 45 < rsi_val < 70 and volatility > 0.3):
            return f"🎯 *LONG SIGNAL*\nPair: {symbol}\nTimeframe: {tf_name}\nEntry: {price:.5f}\nRSI: {rsi_val:.1f}\nVolatility: {volatility:.2f}%\nTime: {datetime.now().strftime('%H:%M:%S')}"
        # Short signal
        elif (ema_fast < ema_slow and 30 < rsi_val < 55 and volatility > 0.3):
            return f"🎯 *SHORT SIGNAL*\nPair: {symbol}\nTimeframe: {tf_name}\nEntry: {price:.5f}\nRSI: {rsi_val:.1f}\nVolatility: {volatility:.2f}%\nTime: {datetime.now().strftime('%H:%M:%S')}"
    except Exception as e:
        logger.error(f"Analysis error for {symbol}: {e}")
    return None

# ---------------- MAIN BOT ----------------
def run_bot():
    logger.info("Starting Deriv Trading Bot...")
    if not send_telegram("🤖 *DERIV BOT CONNECTED SUCCESSFULLY!* 🤖\nBot is online and analyzing..."):
        logger.error("Failed to send Telegram initial message. Check tokens.")
    if not DERIV_API_KEY:
        error_msg = "❌ DERIV_API_KEY not found!"
        logger.error(error_msg)
        send_telegram(error_msg)
        return

    signals_found = 0
    for symbol in SYMBOLS:
        for tf_name, tf_sec in TIMEFRAMES.items():
            df = get_deriv_candles(symbol, tf_sec, count=50)
            if not df.empty:
                signal = analyze(df, symbol, tf_name)
                if signal:
                    send_telegram(signal)
                    signals_found += 1
            time.sleep(1)
    # Send final summary
    send_telegram(f"✅ Analysis complete! {signals_found} signals found.\nPairs analyzed: {len(SYMBOLS)}\nTimeframes scanned: {len(TIMEFRAMES)}\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ---------------- EXECUTION ----------------
if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped manually")
        send_telegram("🛑 Bot manually stopped by user")
    except Exception as e:
        error_msg = f"❌ CRITICAL ERROR: {str(e)}"
        logger.error(error_msg)
        send_telegram(error_msg)
