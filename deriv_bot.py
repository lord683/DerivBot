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
TIMEFRAMES = {"1m": 60, "5m": 300, "10m": 600, "15m": 900}
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
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(escape_chars)), r'\\\1', text)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
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
def fetch_candles(symbol, granularity, count=CANDLES_COUNT, max_attempts=3):
    global auth_error_notified
    backoff = [1,2,4]
    last_exc = None
    key = (symbol, granularity)
    for attempt in range(1, max_attempts+1):
        try:
            ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
            ws.send(json.dumps({"authorize": DERIV_API_KEY}))
            auth = json.loads(ws.recv())
            if "error" in auth:
                if not auth_error_notified:
                    send_telegram(f"❌ Deriv auth error: {auth['error']['message']}")
                    auth_error_notified = True
                ws.close()
                return pd.DataFrame()
            ws.send(json.dumps({"ticks_history":symbol,"end":"latest","count":count,"style":"candles","granularity":granularity}))
            data = json.loads(ws.recv())
            ws.close()
            if "history" in data and "candles" in data["history"]:
                df = pd.DataFrame(data["history"]["candles"])
                for col in ['open','high','low','close']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                return df.dropna().reset_index(drop=True)
            last_exc = Exception("No candle data")
        except Exception as e:
            last_exc = e
            logger.warning(f"{symbol} {granularity} attempt {attempt} failed: {e}")
        time.sleep(backoff[min(attempt-1,len(backoff)-1)])
    if not fetch_failure_notified.get(key) and granularity>=300:
        fetch_failure_notified[key] = True
        send_telegram(f"⚠️ Failed to fetch {symbol} {granularity}: {last_exc}")
    return pd.DataFrame()

# ---------------- INDICATORS ----------------
def ema(series, period): return series.ewm(span=period, adjust=False).mean()
def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta>0,0)).rolling(period).mean()
    loss = (-delta.where(delta<0,0)).rolling(period).mean()
    rs = gain/loss
    return 100 - (100/(1+rs))
def supply_demand_zones(df, lookback=SUPPLY_DEMAND_LOOKBACK):
    high = df['high'].rolling(lookback).max() if len(df)>=lookback else df['high'].max()
    low = df['low'].rolling(lookback).min() if len(df)>=lookback else df['low'].min()
    return float(high.iloc[-1]) if hasattr(high,'iloc') else float(high), float(low.iloc[-1]) if hasattr(low,'iloc') else float(low)

# ---------------- STRATEGY ----------------
def analyze_sniper(df, symbol, tf):
    if df.empty or len(df)<20: return None
    closes = df['close']
    price = float(closes.iloc[-1])
    ema_f = float(ema(closes,9).iloc[-1])
    ema_s = float(ema(closes,21).iloc[-1])
    r = float(rsi(closes,14).iloc[-1])
    high_zone, low_zone = supply_demand_zones(df)
    vol = float(closes.pct_change().std()*100)
    if ema_f>ema_s and 45<r<70 and price<=low_zone and vol>MIN_VOLATILITY_PCT:
        tp = price + (high_zone-low_zone)*0.5
        sl = low_zone
        return f"🎯 *SNIPER LONG* 🎯\n*Pair:* {symbol}\n*TF:* {tf}\n*Entry:* {price:.5f}\n*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n*RSI:* {r:.1f}\n*Vol:* {vol:.2f}%\n*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    if ema_f<ema_s and 30<r<55 and price>=high_zone and vol>MIN_VOLATILITY_PCT:
        tp = price - (high_zone-low_zone)*0.5
        sl = high_zone
        return f"🎯 *SNIPER SHORT* 🎯\n*Pair:* {symbol}\n*TF:* {tf}\n*Entry:* {price:.5f}\n*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n*RSI:* {r:.1f}\n*Vol:* {vol:.2f}%\n*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    return None

# ---------------- WORKER ----------------
def symbol_worker(symbol):
    logger.info(f"Worker started for {symbol}")
    while True:
        for tf, sec in TIMEFRAMES.items():
            df = fetch_candles(symbol, sec)
            if df.empty: continue
            msg = analyze_sniper(df, symbol, tf)
            if msg: send_telegram(msg)
            time.sleep(1)

# ---------------- MAIN ----------------
def run_bot():
    global connected_message_sent
    if not DERIV_API_KEY:
        logger.error("DERIV_API_TOKEN not set")
        send_telegram("❌ DERIV_API_TOKEN not configured.")
        return
    if not connected_message_sent:
        send_telegram("✅ *Deriv Sniper Bot Connected!* Monitoring R_25/R_50/R_75/R_100 on 1m/5m/10m/15m")
        connected_message_sent = True
    threads = []
    for s in SYMBOLS:
        t = Thread(target=symbol_worker,args=(s,),daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.2)
    while True:
        time.sleep(60)

if __name__=="__main__":
    run_bot()
