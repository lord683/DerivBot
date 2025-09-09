            'import os
import json
import time
import pandas as pd
import websocket
from datetime import datetime
from telegram import Bot

# 🔑 Secrets
DERIV_API_KEY = os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID else None

# Markets
SYMBOLS = ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "R_100", "R_50"]

# Timeframes (granularity in seconds)
TIMEFRAMES = {
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
}

# ---------------- Functions ----------------
def send_telegram(message: str):
    if bot:
        try:
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        except Exception as e:
            print(f"Telegram error: {e}")

def get_candles(symbol, count=100, timeframe=60):
    """Fetch candles from Deriv API"""
    ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089")
    req = {
        "ticks_history": symbol,
        "count": count,
        "granularity": timeframe,
        "style": "candles",
        "end": "latest",
        "subscribe": 0,
        "api_token": DERIV_API_KEY
    }
    ws.send(json.dumps(req))
    res = ws.recv()
    ws.close()

    try:
        candles = json.loads(res)["candles"]
        df = pd.DataFrame(candles)
        df['open_time'] = pd.to_datetime(df['epoch'], unit='s')
        return df
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()

def calculate_ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

def generate_signal(df, symbol, tf_name):
    if len(df) < 30:
        return None

    closes = df['close']
    ema_fast = calculate_ema(closes, 9).iloc[-1]
    ema_slow = calculate_ema(closes, 21).iloc[-1]
    current_price = closes.iloc[-1]

    if ema_fast > ema_slow:
        return f"📈 LONG {symbol} ({tf_name})\nEntry: {current_price:.5f} ✅ EMA9 > EMA21"
    elif ema_fast < ema_slow:
        return f"📉 SHORT {symbol} ({tf_name})\nEntry: {current_price:.5f} ✅ EMA9 < EMA21"

    return None

# ---------------- Main ----------------
if __name__ == "__main__":
    send_telegram(f"✅ Deriv Bot Connected!\n🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n📊 Timeframes: {', '.join(TIMEFRAMES.keys())}")

    for sym in SYMBOLS:
        for tf_name, tf_seconds in TIMEFRAMES.items():
            df = get_candles(sym, count=100, timeframe=tf_seconds)
            if not df.empty:
                signal = generate_signal(df, sym, tf_name)
                if signal:
                    send_telegram(signal)
            time.sleep(2)

    send_telegram(f"📊 Scan complete ✅\n🕒 {datetime.now().strftime('%H:%M:%S')}")
