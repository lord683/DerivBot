import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import websocket

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "R_50", "R_100"]  # forex + volatility
TIMEFRAMES = {
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800
}

# ---------------- INDICATORS ----------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ---------------- TELEGRAM ----------------
def send_telegram(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, data=payload, timeout=10)
        except Exception as e:
            print("Telegram error:", e)

# ---------------- DERIV DATA ----------------
def get_deriv_candles(symbol, timeframe, count=100):
    url = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    ws = websocket.create_connection(url)
    
    auth = {"authorize": DERIV_API_KEY}
    ws.send(json.dumps(auth))
    ws.recv()  # response

    request = {
        "ticks_history": symbol,
        "count": count,
        "end": "latest",
        "style": "candles",
        "granularity": timeframe
    }
    ws.send(json.dumps(request))
    response = json.loads(ws.recv())
    ws.close()

    if "candles" in response.get("history", {}):
        df = pd.DataFrame(response["history"]["candles"])
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        return df
    return pd.DataFrame()

# ---------------- STRATEGY ----------------
def analyze(df, symbol, tf_name):
    if df.empty or len(df) < 30:
        return None

    closes = df["close"]
    ema_fast = ema(closes, 9).iloc[-1]
    ema_slow = ema(closes, 21).iloc[-1]
    rsi_val = rsi(closes, 14).iloc[-1]
    price = closes.iloc[-1]

    if ema_fast > ema_slow and rsi_val > 50:
        return f"📈 LONG {symbol} ({tf_name}) @ {price:.5f} | RSI {rsi_val:.2f}"
    elif ema_fast < ema_slow and rsi_val < 50:
        return f"📉 SHORT {symbol} ({tf_name}) @ {price:.5f} | RSI {rsi_val:.2f}"
    return None

# ---------------- MAIN ----------------
def run_bot():
    send_telegram("🤖 Deriv Bot started...")

    for symbol in SYMBOLS:
        for tf_name, tf_sec in TIMEFRAMES.items():
            try:
                df = get_deriv_candles(symbol, tf_sec)
                if not df.empty:
                    signal = analyze(df, symbol, tf_name)
                    if signal:
                        send_telegram(signal)
                time.sleep(1)
            except Exception as e:
                send_telegram(f"⚠️ Error {symbol} {tf_name}: {str(e)}")

    send_telegram(f"✅ Workflow completed at {datetime.now().strftime('%H:%M:%S')}")

if __name__ == "__main__":
    run_bot()
