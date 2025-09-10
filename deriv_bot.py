# deriv_bot.py
"""
Deriv scalper/sniper bot
- Scans volatility indices (R_25, R_50, R_75, R_100)
- Timeframes: 1m, 3m, 5m, 10m, 15m
- Indicators: EMA(9/21), RSI(14), volatility
- Supply/Demand zones: recent highs/lows from 15m and 5m
- Requires multi-timeframe confirmation (>=2 TFs) before sending Telegram signal
- Telegram safe (MarkdownV2 escaped)
- Retries + backoff for Deriv websocket
"""
import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import websocket
import logging
from threading import Thread
import re

# ---------------- CONFIG ----------------
DERIV_API_KEY = os.getenv("DERIV_API_TOKEN") or os.getenv("DERIV_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["R_25", "R_50", "R_75", "R_100"]
TIMEFRAMES = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900
}
CANDLES_COUNT = 150  # keep enough history for indicators and zone detection
SUPPLY_LOOKBACK_15M = 50
SUPPLY_LOOKBACK_5M = 40
MIN_VOLATILITY_PCT = 0.15  # more lenient for scalping
ALERT_COOLDOWN_MINUTES = 30  # don't alert same symbol+direction too often

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("deriv_scalper")

# Notification flags / cooldowns
connected_message_sent = False
auth_error_notified = False
fetch_failure_notified = {}
last_alert_time = {}  # key: (symbol, direction) -> datetime

# ---------------- Telegram helpers ----------------
def escape_md2(text: str) -> str:
    # Escape characters for MarkdownV2
    esc = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(esc)), r'\\\1', text)

def send_telegram(message: str) -> bool:
    """Send a safe Telegram message using MarkdownV2 escaping."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram secrets missing; skipping send.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": escape_md2(message),
            "parse_mode": "MarkdownV2"
        }
        r = requests.post(url, data=payload, timeout=12)
        if r.status_code == 200:
            logger.info("Telegram message sent")
            return True
        else:
            logger.error(f"Telegram API error: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")
        return False

# ---------------- Deriv fetch with retries ----------------
DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"

def fetch_candles(symbol: str, granularity: int, count: int = CANDLES_COUNT, attempts=3, timeout=15):
    """Fetch candles via Deriv websocket with retries/backoff."""
    global auth_error_notified
    backoff = [1, 2, 4]
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            ws = websocket.create_connection(DERIV_WS_URL, timeout=timeout)
            ws.send(json.dumps({"authorize": DERIV_API_KEY}))
            raw = ws.recv()
            auth = json.loads(raw)
            if "error" in auth:
                msg = auth["error"].get("message", str(auth["error"]))
                logger.error(f"Deriv auth error: {msg}")
                if not auth_error_notified:
                    send_telegram(f"❌ Deriv auth error: {msg}")
                    auth_error_notified = True
                ws.close()
                return pd.DataFrame()
            # request history
            req = {"ticks_history": symbol, "end": "latest", "count": count, "style": "candles", "granularity": granularity}
            ws.send(json.dumps(req))
            raw2 = ws.recv()
            ws.close()
            data = json.loads(raw2)
            if "error" in data:
                last_exc = Exception(data["error"].get("message", str(data["error"])))
                logger.warning(f"{symbol} {granularity} returned error: {last_exc}")
                time.sleep(backoff[min(attempt - 1, len(backoff)-1)])
                continue
            if "history" in data and "candles" in data["history"]:
                df = pd.DataFrame(data["history"]["candles"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna().reset_index(drop=True)
                return df
            last_exc = Exception("No candle data")
            time.sleep(backoff[min(attempt - 1, len(backoff)-1)])
        except Exception as e:
            last_exc = e
            logger.warning(f"Attempt {attempt} failed for {symbol} {granularity}: {e}")
            time.sleep(backoff[min(attempt - 1, len(backoff)-1)])
    # final notify once per key
    key = (symbol, granularity)
    if not fetch_failure_notified.get(key):
        fetch_failure_notified[key] = True
        send_telegram(f"⚠️ Failed to fetch {symbol} {granularity} after {attempts} attempts: {last_exc}")
    return pd.DataFrame()

# ---------------- Indicators & zones ----------------
def ema(series: pd.Series, period: int):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss.replace(0, np.nan))
    rs = rs.fillna(0)
    return 100 - (100 / (1 + rs))

def volatility_pct(series: pd.Series):
    return series.pct_change().std() * 100

def supply_demand_zones_from_df(df: pd.DataFrame, lookback: int):
    """Return simple zone as (high_zone, low_zone) using rolling extremes."""
    if df.empty:
        return None, None
    # Use rolling extremes over lookback
    if len(df) < lookback:
        high_zone = float(df["high"].max())
        low_zone = float(df["low"].min())
    else:
        high_zone = float(df["high"].rolling(lookback).max().iloc[-1])
        low_zone = float(df["low"].rolling(lookback).min().iloc[-1])
    return high_zone, low_zone

# ---------------- Signal logic ----------------
def single_tf_signal(df: pd.DataFrame, tf_name: str):
    """Return 'LONG', 'SHORT' or None for a single timeframe."""
    if df.empty or len(df) < 20:
        return None
    closes = df["close"]
    price = float(closes.iloc[-1])
    ema_fast = float(ema(closes, 9).iloc[-1])
    ema_slow = float(ema(closes, 21).iloc[-1])
    rsi_val = float(rsi(closes, 14).iloc[-1])
    vol = float(volatility_pct(closes))
    # Basic conditions
    if ema_fast > ema_slow and 40 < rsi_val < 70 and vol > MIN_VOLATILITY_PCT:
        # price near demand? We'll leave zone check to multi-tf confirmation
        return "LONG"
    if ema_fast < ema_slow and 30 < rsi_val < 60 and vol > MIN_VOLATILITY_PCT:
        return "SHORT"
    return None

def multi_tf_confirmation(symbol: str, candles_by_tf: dict):
    """
    Evaluate signals per timeframe and confirm only if >=2 TFs agree and zones align.
    returns dict with keys: direction, tf_list, price, tp, sl, note
    """
    tf_signals = {}
    for tf_name, df in candles_by_tf.items():
        s = single_tf_signal(df, tf_name)
        if s:
            tf_signals.setdefault(s, []).append(tf_name)

    # require at least two tf agreement
    for direction, tf_list in tf_signals.items():
        if len(tf_list) >= 2:
            # zone confirmation: use 15m & 5m zones
            df15 = candles_by_tf.get("15m")
            df5 = candles_by_tf.get("5m")
            h15, l15 = supply_demand_zones_from_df(df15, SUPPLY_LOOKBACK_15M) if df15 is not None else (None, None)
            h5, l5 = supply_demand_zones_from_df(df5, SUPPLY_LOOKBACK_5M) if df5 is not None else (None, None)

            # pick current price from highest resolution tf available (1m->...)
            price = None
            for tf in ["1m", "3m", "5m", "10m", "15m"]:
                df = candles_by_tf.get(tf)
                if df is not None and not df.empty:
                    price = float(df["close"].iloc[-1])
                    break

            # zone logic: for LONG require price <= demand zone (use min of l15,l5 if exist)
            demand_zone = None
            supply_zone = None
            if l15 is not None and l5 is not None:
                demand_zone = min(l15, l5)
                supply_zone = max(h15, h5) if h15 is not None and h5 is not None else (h15 or h5)
            elif l5 is not None:
                demand_zone = l5
                supply_zone = h5
            elif l15 is not None:
                demand_zone = l15
                supply_zone = h15

            # require zone alignment for scalp confidence:
            if direction == "LONG":
                if demand_zone is None:
                    note = "no-demand-zone"
                    # still allow if two TFs agree but mark lower confidence
                    sl = demand_zone if demand_zone else price - (price * 0.002)
                    tp = price + (price - sl) * 1.5
                    return {"direction": "LONG", "tfs": tf_list, "price": price, "tp": tp, "sl": sl, "note": note}
                else:
                    # price must be near demand: within 0.5% of demand_zone or below
                    if price <= demand_zone * 1.005:
                        sl = demand_zone
                        tp = price + (supply_zone - demand_zone) * 0.5 if supply_zone is not None else price + (price - sl) * 1.5
                        return {"direction": "LONG", "tfs": tf_list, "price": price, "tp": tp, "sl": sl, "note": "zone-confirmed"}
            else:  # SHORT
                if supply_zone is None:
                    note = "no-supply-zone"
                    sl = supply_zone if supply_zone else price + (price * 0.002)
                    tp = price - (sl - price) * 1.5
                    return {"direction": "SHORT", "tfs": tf_list, "price": price, "tp": tp, "sl": sl, "note": note}
                else:
                    if price >= supply_zone * 0.995:
                        sl = supply_zone
                        tp = price - (supply_zone - demand_zone) * 0.5 if demand_zone is not None else price - (sl - price) * 1.5
                        return {"direction": "SHORT", "tfs": tf_list, "price": price, "tp": tp, "sl": sl, "note": "zone-confirmed"}
    return None

# ---------------- Cooldown helper ----------------
def can_alert(symbol: str, direction: str):
    key = (symbol, direction)
    last = last_alert_time.get(key)
    if last is None:
        return True
    return datetime.utcnow() - last >= timedelta(minutes=ALERT_COOLDOWN_MINUTES)

def set_alert_time(symbol: str, direction: str):
    last_alert_time[(symbol, direction)] = datetime.utcnow()

# ---------------- Worker ----------------
def symbol_worker(symbol: str):
    """
    Per-symbol worker: fetch candles for all TFs, run multi-tf confirmation,
    and send alert if confirmed and cooldown allows.
    """
    logger.info(f"Starting worker for {symbol}")
    while True:
        try:
            # fetch candles for all timeframes (sequentially but fast)
            candles_by_tf = {}
            for tf_name, gran in TIMEFRAMES.items():
                df = fetch_candles(symbol, gran, count=CANDLES_COUNT)
                if df.empty:
                    # skip this cycle if major fetch failed
                    candles_by_tf[tf_name] = pd.DataFrame()
                else:
                    candles_by_tf[tf_name] = df
                time.sleep(0.4)  # small gap to avoid rate limit

            # run confirmation
            conf = multi_tf_confirmation(symbol, candles_by_tf)
            if conf:
                direction = conf["direction"]
                if can_alert(symbol, direction):
                    # Build message
                    tf_list = ", ".join(conf["tfs"])
                    price = conf["price"]
                    tp = conf["tp"]
                    sl = conf["sl"]
                    note = conf.get("note", "")
                    message = (
                        f"🎯 *{direction} SIGNAL* for {symbol}\n"
                        f"*Timeframes:* {tf_list}\n"
                        f"*Price:* {price:.5f}\n*TP:* {tp:.5f}\n*SL:* {sl:.5f}\n"
                        f"*Note:* {note}\n"
                        f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    )
                    sent = send_telegram(message)
                    if sent:
                        set_alert_time(symbol, direction)
                    else:
                        logger.warning("Telegram send failed")
                else:
                    logger.info(f"Skipping alert for {symbol} {direction} due cooldown")
            # sleep short before next full cycle
            time.sleep(5)
        except Exception as e:
            logger.exception(f"Worker error for {symbol}: {e}")
            # notify once
            send_telegram(f"❌ Worker error for {symbol}: {e}")
            time.sleep(10)

# ---------------- Main ----------------
def run_bot():
    global connected_message_sent
    if not DERIV_API_KEY:
        logger.error("DERIV_API_TOKEN not configured. Set DERIV_API_TOKEN secret.")
        send_telegram("❌ DERIV_API_TOKEN not configured. Please set secret.")
        return

    # notify once per run
    if not connected_message_sent:
        send_telegram("✅ *Deriv Scalper Bot Connected!* Monitoring 1m/3m/5m/10m/15m volatility indices.")
        connected_message_sent = True

    # start worker threads
    threads = []
    for sym in SYMBOLS:
        t = Thread(target=symbol_worker, args=(sym,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.2)

    # keep main alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt")
    except Exception as e:
        logger.exception(f"Main loop exception: {e}")
        send_telegram(f"❌ Bot crashed: {e}")

if __name__ == "__main__":
    run_bot()
