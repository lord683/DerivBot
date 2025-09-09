# deriv_bot.py
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
# Read secrets (accepts either name variants for safety)
DERIV_API_KEY = os.getenv("DERIV_API_TOKEN") or os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Only volatility indices (adjust list if needed)
SYMBOLS = ["R_25", "R_50", "R_75", "R_100"]

# Timeframes we scan: 5m, 10m, 15m
TIMEFRAMES = {
    "5m": 300,
    "10m": 600,
    "15m": 900
}

# Tune these
CANDLES_COUNT = 100
SUPPLY_DEMAND_LOOKBACK = 20
MIN_VOLATILITY_PCT = 0.3  # filter flat markets

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deriv_bot")

# Global notification flags to prevent spam
connected_message_sent = False
auth_error_notified = False
fetch_failure_notified = {}  # keyed by (symbol, timeframe)

# ---------------- TELEGRAM ----------------
def send_telegram(message):
    """
    Send a Telegram message. If tokens are missing, only logs locally.
    Returns True if message was sent (200), False otherwise.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram tokens not configured, skipping send.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
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

# ---------------- DERIV FETCH with retries ----------------
def fetch_candles_with_retries(symbol, granularity, count=CANDLES_COUNT, max_attempts=3):
    """
    Fetch candles via Deriv websocket. Retries with backoff. Notifies Telegram once on repeated failure.
    Returns DataFrame or empty DataFrame on failure.
    """
    global auth_error_notified
    backoff_seconds = [1, 2, 4]
    last_exc = None

    for attempt in range(1, max_attempts + 1):
        try:
            ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
            # Authorize
            ws.send(json.dumps({"authorize": DERIV_API_KEY}))
            auth_raw = ws.recv()
            auth = json.loads(auth_raw)

            if "error" in auth:
                # One-time notify for auth failure
                err_msg = auth["error"].get("message", str(auth["error"]))
                logger.error(f"Deriv auth error: {err_msg}")
                if not auth_error_notified:
                    send_telegram(f"❌ Deriv auth error: {err_msg}")
                    auth_error_notified = True
                ws.close()
                return pd.DataFrame()

            # Request candle history
            req = {
                "ticks_history": symbol,
                "end": "latest",
                "count": count,
                "style": "candles",
                "granularity": granularity
            }
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
                for col in ['open', 'high', 'low', 'close']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna().reset_index(drop=True)
                return df

            last_exc = Exception("No candle data in response")
            time.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds)-1)])

        except Exception as e:
            last_exc = e
            logger.warning(f"Attempt {attempt} failed for {symbol} {granularity}: {e}")
            time.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds)-1)])

    # after retries -> notify once per (symbol,granularity)
    key = (symbol, granularity)
    if not fetch_failure_notified.get(key):
        fetch_failure_notified[key] = True
        send_telegram(f"⚠️ Failed to fetch {symbol} {granularity} after {max_attempts} attempts: {last_exc}")
    return pd.DataFrame()

# ---------------- INDICATORS & ZONES ----------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def supply_demand_zones(df, lookback=SUPPLY_DEMAND_LOOKBACK):
    """
    Simple supply/demand zones: recent high and low over lookback candles.
    Returns (high_zone, low_zone).
    """
    if len(df) < lookback:
        high_zone = df['high'].max()
        low_zone = df['low'].min()
    else:
        high_zone = df['high'].rolling(lookback).max().iloc[-1]
        low_zone = df['low'].rolling(lookback).min().iloc[-1]
    return float(high_zone), float(low_zone)

# ---------------- STRATEGY ----------------
def analyze_sniper(df, symbol, tf_name):
    """
    Use EMA(9/21), RSI(14), supply/demand zones, volatility to produce sniper entries.
    Returns formatted message string or None.
    """
    if df.empty or len(df) < 20:
        return None
    try:
        closes = df['close']
        price = float(closes.iloc[-1])
        ema_fast = float(ema(closes, 9).iloc[-1])
        ema_slow = float(ema(closes, 21).iloc[-1])
        rsi_val = float(rsi(closes, 14).iloc[-1])
        high_zone, low_zone = supply_demand_zones(df)
        volatility = float(closes.pct_change().std() * 100)

        # SNIPER LONG: trend up, momentum OK, price touching demand (low_zone) and market not flat
        if ema_fast > ema_slow and 45 < rsi_val < 70 and price <= low_zone and volatility > MIN_VOLATILITY_PCT:
            tp = price + (high_zone - low_zone) * 0.5
            sl = low_zone
            msg = (
                f"🎯 *SNIPER LONG ENTRY* 🎯\n"
                f"*Pair:* {symbol}\n*TF:* {tf_name}\n*Entry:* {price:.5f}\n"
                f"*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n"
                f"*RSI:* {rsi_val:.1f}\n*Volatility:* {volatility:.2f}%\n"
                f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            return msg

        # SNIPER SHORT: trend down, momentum OK, price touching supply (high_zone) and market not flat
        if ema_fast < ema_slow and 30 < rsi_val < 55 and price >= high_zone and volatility > MIN_VOLATILITY_PCT:
            tp = price - (high_zone - low_zone) * 0.5
            sl = high_zone
            msg = (
                f"🎯 *SNIPER SHORT ENTRY* 🎯\n"
                f"*Pair:* {symbol}\n*TF:* {tf_name}\n*Entry:* {price:.5f}\n"
                f"*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n"
                f"*RSI:* {rsi_val:.1f}\n*Volatility:* {volatility:.2f}%\n"
                f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            return msg

    except Exception as e:
        logger.error(f"Strategy error for {symbol} {tf_name}: {e}")
    return None

# ---------------- WORKER ----------------
def symbol_worker(symbol):
    """
    Worker loop for a single symbol scanning all timeframes sequentially.
    This function runs forever (or until process is killed).
    """
    logger.info(f"Worker started for {symbol}")
    while True:
        try:
            for tf_name, tf_seconds in TIMEFRAMES.items():
                df = fetch_candles_with_retries(symbol, tf_seconds, count=CANDLES_COUNT)
                if df.empty:
                    # fetch function already notified (once) on repeated failures
                    continue
                signal = analyze_sniper(df, symbol, tf_name)
                if signal:
                    send_telegram(signal)
                    # small cooldown to avoid duplicate push
                    time.sleep(1)
                time.sleep(0.5)  # small pause to avoid rate limit
        except Exception as e:
            logger.exception(f"Unhandled error in symbol_worker {symbol}: {e}")
            # One-time notify of this critical exception
            send_telegram(f"❌ Worker crash for {symbol}: {e}")
            time.sleep(5)

# ---------------- MAIN ----------------
def run_bot():
    global connected_message_sent

    # Basic validation
    if not DERIV_API_KEY:
        logger.error("DERIV_API_TOKEN is not set. Put your Deriv API token into repository secrets as 'DERIV_API_TOKEN'. Exiting.")
        send_telegram("❌ DERIV_API_TOKEN not configured. Please set the secret and re-run.")  # safe: will skip if telegram not set
        return

    # Connected notification: only once per run
    if not connected_message_sent:
        send_telegram("✅ *Deriv Sniper Bot Connected!* Monitoring volatility indices on 5m/10m/15m (one connected message per run).")
        connected_message_sent = True

    # Start a worker thread per symbol
    threads = []
    for s in SYMBOLS:
        t = Thread(target=symbol_worker, args=(s,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.2)  # stagger start

    # Keep main thread alive; workers run forever
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, exiting.")
    except Exception as e:
        logger.exception(f"Main loop crashed: {e}")
        send_telegram(f"❌ Deriv bot main loop crashed: {e}")

if __name__ == "__main__":
    run_bot()
