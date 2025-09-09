import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import websocket
import logging
from threading import Thread

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["R_50", "R_75", "R_100", "R_25", "R_25S"]
TIMEFRAMES = {
    "5m": 300,
    "10m": 600,
    "15m": 900
}

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global flag for connected message
connected_message_sent = False

# ---------------- TELEGRAM ----------------
def send_telegram(message):
    """Send message to Telegram safely"""
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
    """Fetch candles from Deriv WebSocket with reconnection"""
    try:
        ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
        ws.send(json.dumps({"authorize": DERIV_API_KEY}))
        auth_resp = json.loads(ws.recv())

        if "error" in auth_resp:
            logger.error(f"Deriv auth error: {auth_resp['error']['message']}")
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
            return df.dropna()
        return pd.DataFrame()

    except Exception as e:
        logger.error(f"Error fetching {symbol} {timeframe}: {e}")
        send_telegram(f"⚠️ Error fetching {symbol} {timeframe}: {e}")
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
    """Supply/Demand zones based on recent 20 candles"""
    recent_high = df['high'].rolling(20).max().iloc[-1]
    recent_low = df['low'].rolling(20).min().iloc[-1]
    return recent_high, recent_low

# ---------------- STRATEGY ----------------
def analyze(df, symbol, tf_name):
    """Sniper entries using EMA, RSI, Supply/Demand, Volatility"""
    if df.empty or len(df) < 20:
        return None

    try:
        closes = df['close']
        price = closes.iloc[-1]
        ema_fast = ema(closes, 9).iloc[-1]
        ema_slow = ema(closes, 21).iloc[-1]
        rsi_val = rsi(closes, 14).iloc[-1]
        high_zone, low_zone = supply_demand_levels(df)
        volatility = closes.pct_change().std() * 100

        # Long sniper entry
        if ema_fast > ema_slow and 45 < rsi_val < 70 and price < low_zone and volatility > 0.3:
            tp = price + (high_zone - low_zone) * 0.5
            sl = low_zone
            return f"""
🎯 *SNIPER LONG ENTRY* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {price:.5f}
*TP:* {tp:.5f}
*SL:* {sl:.5f}
*RSI:* {rsi_val:.1f}
*Volatility:* {volatility:.2f}%
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""

        # Short sniper entry
        if ema_fast < ema_slow and 30 < rsi_val < 55 and price > high_zone and volatility > 0.3:
            tp = price - (high_zone - low_zone) * 0.5
            sl = high_zone
            return f"""
🎯 *SNIPER SHORT ENTRY* 🎯
*Pair:* {symbol}
*Timeframe:* {tf_name}
*Entry:* {price:.5f}
*TP:* {tp:.5f}
*SL:* {sl:.5f}
*RSI:* {rsi_val:.1f}
*Volatility:* {volatility:.2f}%
*Time:* {datetime.now().strftime('%H:%M:%S')}
"""

    except Exception as e:
        logger.error(f"Analysis error for {symbol} {tf_name}: {e}")
    return None

# ---------------- PARALLEL ANALYSIS ----------------
def analyze_symbol(symbol):
    """Run analysis on all timeframes for a symbol"""
    for tf_name, tf_sec in TIMEFRAMES.items():
        df = get_deriv_candles(symbol, tf_sec, count=50)
        if not df.empty:
            signal = analyze(df, symbol, tf_name)
            if signal:
                send_telegram(signal)
        time.sleep(1)

# ---------------- MAIN BOT ----------------
def run_bot():
    global connected_message_sent

    # Send connected message once
    if not connected_message_sent:
        send_telegram("🤖 Deriv Sniper Bot Connected! Monitoring 5m,10m,15m volatility indices...")
        connected_message_sent = True

    threads = []
    for symbol in SYMBOLS:
        t = Thread(target=analyze_symbol, args=(symbol,))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

if __name__ == "__main__":
    while True:
        try:
            run_bot()
            logger.info("Waiting 5 minutes before next scan...")
            time.sleep(300)  # 5 minutes
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
            send_telegram(f"❌ Bot crashed: {e}")
            time.sleep(60)
