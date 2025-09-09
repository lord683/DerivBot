import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import websocket
import logging
import threading

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Only volatility indices
SYMBOLS = ["R_25", "R_50", "R_75", "R_100"]
TIMEFRAMES = {
    "5m": 300,
    "10m": 600,
    "15m": 900
}

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- TELEGRAM ----------------
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram tokens not configured")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        response = requests.post(url, data=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False

# ---------------- DERIV DATA ----------------
def get_deriv_candles(symbol, timeframe, count=50):
    """Fetch candles with WebSocket handling + auto-reconnect"""
    retries = 3
    while retries > 0:
        try:
            ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
            ws.send(json.dumps({"authorize": DERIV_API_KEY}))
            auth_resp = json.loads(ws.recv())
            if "error" in auth_resp:
                send_telegram(f"❌ Deriv auth error: {auth_resp['error']['message']}")
                ws.close()
                return pd.DataFrame()

            ws.send(json.dumps({
                "ticks_history": symbol,
                "count": count,
                "end": "latest",
                "style": "candles",
                "granularity": timeframe
            }))
            resp = json.loads(ws.recv())
            ws.close()

            if "history" in resp and "candles" in resp["history"]:
                df = pd.DataFrame(resp["history"]["candles"])
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna()
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error fetching {symbol} {timeframe}: {e}")
            retries -= 1
            time.sleep(5)
    send_telegram(f"⚠️ Failed to fetch data for {symbol} {timeframe} after retries")
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

def supply_demand_levels(df):
    recent_high = df['high'].rolling(20).max().iloc[-1]
    recent_low = df['low'].rolling(20).min().iloc[-1]
    return recent_high, recent_low

# ---------------- STRATEGY ----------------
def analyze(df, symbol, tf_name):
    if df.empty or len(df) < 20:
        return None
    try:
        closes = df['close']
        ema_fast = ema(closes, 9).iloc[-1]
        ema_slow = ema(closes, 21).iloc[-1]
        rsi_val = rsi(closes, 14).iloc[-1]
        price = closes.iloc[-1]
        high_zone, low_zone = supply_demand_levels(df)

        # Sniper LONG
        if ema_fast > ema_slow and 45 < rsi_val < 70 and price < low_zone:
            return f"""
🎯 *SNIPER LONG ENTRY* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {price:.5f}
*TP:* {price + (high_zone - low_zone)*0.5:.5f}
*SL:* {low_zone:.5f}
*RSI:* {rsi_val:.1f}
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""
        # Sniper SHORT
        if ema_fast < ema_slow and 30 < rsi_val < 55 and price > high_zone:
            return f"""
🎯 *SNIPER SHORT ENTRY* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {price:.5f}
*TP:* {price - (high_zone - low_zone)*0.5:.5f}
*SL:* {high_zone:.5f}
*RSI:* {rsi_val:.1f}
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""
    except Exception as e:
        logger.error(f"Analysis error for {symbol}: {e}")
    return None

# ---------------- THREAD WORKER ----------------
def symbol_worker(symbol):
    while True:
        for tf_name, tf_sec in TIMEFRAMES.items():
            df = get_deriv_candles(symbol, tf_sec, count=50)
            if not df.empty:
                signal = analyze(df, symbol, tf_name)
                if signal:
                    send_telegram(signal)
            time.sleep(1)  # prevent rate limit
        time.sleep(5)  # wait before next round

# ---------------- MAIN ----------------
def run_bot():
    send_telegram("🤖 Deriv Sniper Bot Connected! Monitoring 5m,10m,15m Volatility indices...")
    threads = []
    for symbol in SYMBOLS:
        t = threading.Thread(target=symbol_worker, args=(symbol,), daemon=True)
        t.start()
        threads.append(t)
    while True:
        time.sleep(60)  # keep main thread alive

if __name__ == "__main__":
    while True:
        try:
            run_bot()
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
            send_telegram(f"❌ Bot crashed: {e}")
            time.sleep(10)
