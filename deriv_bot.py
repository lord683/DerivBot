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
import re

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_TOKEN") or os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["R_25", "R_50", "R_75", "R_100"]

# ⏱️ Scalping + Sniper Timeframes
TIMEFRAMES = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900
}

CANDLES_COUNT = 100
SUPPLY_DEMAND_LOOKBACK = 20
MIN_VOLATILITY_PCT = 0.3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deriv_bot")

connected_message_sent = False
auth_error_notified = False
fetch_failure_notified = {}

# ---------------- TELEGRAM ----------------
def escape_md2(text):
    """Escape Telegram MarkdownV2 special characters"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(escape_chars)), r'\\\1', text)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram tokens not configured, skipping send.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": escape_md2(message), "parse_mode": "MarkdownV2"}
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram message sent")
            return True
        else:
            logger.error(f"Telegram API error: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")
        return False

# ---------------- DERIV ----------------
def fetch_candles_with_retries(symbol, granularity, count=CANDLES_COUNT, max_attempts=3):
    global auth_error_notified
    backoff_seconds = [1, 2, 4]
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
            ws.send(json.dumps({"authorize": DERIV_API_KEY}))
            auth_raw = ws.recv()
            auth = json.loads(auth_raw)
            if "error" in auth:
                err_msg = auth["error"].get("message", str(auth["error"]))
                logger.error(f"Deriv auth error: {err_msg}")
                if not auth_error_notified:
                    send_telegram(f"❌ Deriv auth error: {err_msg}")
                    auth_error_notified = True
                ws.close()
                return pd.DataFrame()
            req = {"ticks_history": symbol, "end": "latest", "count": count, "style": "candles", "granularity": granularity}
            ws.send(json.dumps(req))
            raw = ws.recv()
            ws.close()
            data = json.loads(raw)
            if "error" in data:
                last_exc = Exception(data["error"].get("message", str(data["error"])))
                logger.warning(f"Deriv returned error for {symbol} {granularity}: {last_exc}")
                time.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds)-1)])
                continue
            if "history" in data and "candles" in data["history"]:
                df = pd.DataFrame(data["history"]["candles"])
                for col in ['open','high','low','close']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                return df.dropna().reset_index(drop=True)
            last_exc = Exception("No candle data in response")
            time.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds)-1)])
        except Exception as e:
            last_exc = e
            logger.warning(f"Attempt {attempt} failed for {symbol} {granularity}: {e}")
            time.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds)-1)])
    key = (symbol, granularity)
    if not fetch_failure_notified.get(key):
        fetch_failure_notified[key] = True
        send_telegram(f"⚠️ Failed to fetch {symbol} {granularity} after {max_attempts} attempts: {last_exc}")
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
        high_zone = df['high'].max()
        low_zone = df['low'].min()
    else:
        high_zone = df['high'].rolling(lookback).max().iloc[-1]
        low_zone = df['low'].rolling(lookback).min().iloc[-1]
    return float(high_zone), float(low_zone)

# ---------------- STRATEGY ----------------
def analyze_sniper(df, symbol, tf_name):
    if df.empty or len(df)<20: return None
    try:
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
            return (f"🎯 *SNIPER LONG ENTRY* 🎯\n*Pair:* {symbol}\n*TF:* {tf_name}\n*Entry:* {price:.5f}\n"
                    f"*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n*RSI:* {rsi_val:.1f}\n*Volatility:* {volatility:.2f}%\n"
                    f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        # SHORT
        if ema_fast<ema_slow and 30<rsi_val<55 and price>=high_zone and volatility>MIN_VOLATILITY_PCT:
            tp = price - (high_zone-low_zone)*0.5
            sl = high_zone
            return (f"🎯 *SNIPER SHORT ENTRY* 🎯\n*Pair:* {symbol}\n*TF:* {tf_name}\n*Entry:* {price:.5f}\n"
                    f"*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n*RSI:* {rsi_val:.1f}\n*Volatility:* {volatility:.2f}%\n"
                    f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    except Exception as e:
        logger.error(f"Strategy error for {symbol} {tf_name}: {e}")
    return None

# ---------------- WORKER ----------------
def symbol_worker(symbol):
    logger.info(f"Worker started for {symbol}")
    while True:
        try:
            for tf_name, tf_sec in TIMEFRAMES.items():
                df = fetch_candles_with_retries(symbol, tf_sec, count=CANDLES_COUNT)
                if df.empty: continue
                signal = analyze_sniper(df, symbol, tf_name)
                if signal:
                    send_telegram(signal)
                time.sleep(1)
        except Exception as e:
            logger.exception(f"Unhandled error in symbol_worker {symbol}: {e}")
            send_telegram(f"❌ Worker crash for {symbol}: {e}")
            time.sleep(5)

# ---------------- MAIN ----------------
def run_bot():
    global connected_message_sent
    if not DERIV_API_KEY:
        logger.error("DERIV_API_TOKEN not set!")
        send_telegram("❌ DERIV_API_TOKEN not configured.")
        return
    if not connected_message_sent:
        send_telegram("✅ *Deriv Sniper Bot Connected!* Monitoring 1m/3m/5m/10m/15m volatility indices.")
        connected_message_sent = True
    # spawn workers
    threads = []
    for sym in SYMBOLS:
        t = Thread(target=symbol_worker, args=(sym,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

if __name__ == "__main__":
    run_bot()
