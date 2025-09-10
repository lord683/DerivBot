import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
import websocket
import logging
import re

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "R_25"
TIMEFRAMES = {"1m": 60, "5m": 300, "10m": 600, "15m": 900}
CANDLES_COUNT = 100
SUPPLY_DEMAND_LOOKBACK = 20
MIN_VOLATILITY_PCT = 0.3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deriv_bot")

connected_message_sent = False

# ---------------- TELEGRAM ----------------
def escape_md2(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(escape_chars)), r'\\\1', text)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram tokens not configured.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": escape_md2(message), "parse_mode": "MarkdownV2"}
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram message sent")
        else:
            logger.error(f"Telegram API error: {r.status_code} - {r.text}")
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")

# ---------------- DERIV CANDLES ----------------
def fetch_candles(symbol, granularity, count=CANDLES_COUNT):
    try:
        ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
        ws.send(json.dumps({"authorize": DERIV_API_KEY}))
        auth = json.loads(ws.recv())
        if "error" in auth:
            send_telegram(f"❌ Deriv auth error: {auth['error']['message']}")
            ws.close()
            return pd.DataFrame()
        
        req = {"ticks_history": symbol, "end":"latest", "count":count, "style":"candles", "granularity":granularity}
        ws.send(json.dumps(req))
        data = json.loads(ws.recv())
        ws.close()
        
        if "history" in data and "candles" in data["history"]:
            df = pd.DataFrame(data["history"]["candles"])
            for col in ['open','high','low','close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df.dropna().reset_index(drop=True)
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"Failed to fetch {symbol} {granularity}: {e}")
        return pd.DataFrame()

# ---------------- INDICATORS ----------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta>0,0)).rolling(period).mean()
    loss = (-delta.where(delta<0,0)).rolling(period).mean()
    rs = gain/loss
    return 100 - (100/(1+rs))

def supply_demand_zones(df, lookback=SUPPLY_DEMAND_LOOKBACK):
    if len(df)<lookback:
        return float(df['high'].max()), float(df['low'].min())
    high_zone = df['high'].rolling(lookback).max().iloc[-1]
    low_zone = df['low'].rolling(lookback).min().iloc[-1]
    return float(high_zone), float(low_zone)

# ---------------- STRATEGY ----------------
def analyze_signal(df, symbol, tf_name):
    if df.empty or len(df)<20:
        return None
    closes = df['close']
    price = float(closes.iloc[-1])
    ema_fast = float(ema(closes,9).iloc[-1])
    ema_slow = float(ema(closes,21).iloc[-1])
    rsi_val = float(rsi(closes,14).iloc[-1])
    high_zone, low_zone = supply_demand_zones(df)
    volatility = float(closes.pct_change().std()*100)

    # LONG
    if ema_fast>ema_slow and 45<rsi_val<70 and price<=low_zone and volatility>MIN_VOLATILITY_PCT:
        tp = price + (high_zone-low_zone)*0.5
        sl = low_zone
        return f"🎯 *SNIPER LONG ENTRY* 🎯\n*Pair:* {symbol}\n*TF:* {tf_name}\n*Entry:* {price:.5f}\n*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n*RSI:* {rsi_val:.1f}\n*Volatility:* {volatility:.2f}%\n*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"

    # SHORT
    if ema_fast<ema_slow and 30<rsi_val<55 and price>=high_zone and volatility>MIN_VOLATILITY_PCT:
        tp = price - (high_zone-low_zone)*0.5
        sl = high_zone
        return f"🎯 *SNIPER SHORT ENTRY* 🎯\n*Pair:* {symbol}\n*TF:* {tf_name}\n*Entry:* {price:.5f}\n*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n*RSI:* {rsi_val:.1f}\n*Volatility:* {volatility:.2f}%\n*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"

    return None

# ---------------- MAIN LOOP ----------------
def run_bot():
    global connected_message_sent
    if not DERIV_API_KEY:
        logger.error("DERIV_API_TOKEN not set!")
        return
    if not connected_message_sent:
        send_telegram("✅ *Deriv Sniper Bot Connected!* Monitoring R_25 on 1m/5m/10m/15m.")
        connected_message_sent = True

    while True:
        for tf_name, tf_sec in TIMEFRAMES.items():
            df = fetch_candles(SYMBOL, tf_sec)
            if df.empty:
                continue
            signal = analyze_signal(df, SYMBOL, tf_name)
            if signal:
                send_telegram(signal)
            time.sleep(1)  # small pause to avoid any accidental spamming
        time.sleep(5)  # wait before fetching next batch

if __name__ == "__main__":
    run_bot()
