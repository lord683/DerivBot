import yfinance as yf
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from telegram import Bot
from datetime import datetime
import time

# ---------------- CONFIG ----------------
SYMBOLS = ["GC=F", "SI=F"]  # Gold & Silver futures
TIMEFRAMES = {"1min": "1m", "5min": "5m", "15min": "15m"}  # GitHub workflow friendly
BOT_TOKEN = "8085883361:AAF6RnUUmet81QqBi76M4TGm0v3wNWUX414"
CHAT_ID = "7581536915"

bot = Bot(token=BOT_TOKEN)
sent_signals = set()  # Avoid duplicates per session

# ---------------- FUNCTIONS ----------------
def fetch_prices(symbol, interval="1m"):
    """Fetch OHLC historical data for given interval"""
    try:
        data = yf.download(symbol, interval=interval, period="1d", progress=False)
        return data['Close']
    except Exception as e:
        print(f"Error fetching {symbol} ({interval}): {e}")
        return pd.Series()

def calculate_indicators(prices):
    """Compute EMA, RSI, MACD"""
    ema_fast = EMAIndicator(prices, window=10).ema_indicator()[-1]
    ema_slow = EMAIndicator(prices, window=30).ema_indicator()[-1]
    rsi = RSIIndicator(prices, window=14).rsi()[-1]
    macd_val = MACD(prices).macd()[-1]
    macd_signal = MACD(prices).macd_signal()[-1]
    return ema_fast, ema_slow, rsi, macd_val, macd_signal

def sniper_signal(prices):
    """High-confidence signal logic"""
    ema_fast, ema_slow, rsi, macd_val, macd_signal = calculate_indicators(prices)
    if ema_fast > ema_slow and rsi < 70 and macd_val > macd_signal:
        return "BUY"
    elif ema_fast < ema_slow and rsi > 30 and macd_val < macd_signal:
        return "SELL"
    return None

def send_telegram(message):
    """Send signal to Telegram synchronously"""
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
        print(f"📩 {message}")
    except Exception as e:
        print(f"Telegram error: {e}")

def run_sniper():
    """Main sniper loop for all symbols & timeframes"""
    for label, interval in TIMEFRAMES.items():
        for symbol in SYMBOLS:
            prices = fetch_prices(symbol, interval)
            if len(prices) < 20:
                continue
            signal = sniper_signal(prices)
            if signal:
                key = f"{symbol}_{signal}_{label}"
                if key not in sent_signals:
                    message = f"⏱ {label} | {symbol} | {signal}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    send_telegram(message)
                    sent_signals.add(key)
            else:
                print(f"{label} | {symbol}: No valid signal")

# ---------------- MAIN LOOP ----------------
if __name__ == "__main__":
    send_telegram("✅ Sniper Bot Connected! Monitoring Gold & Silver...")
    while True:
        run_sniper()
        time.sleep(60)  # 1 minute between cycles
