import yfinance as yf
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from telegram import Bot
from datetime import datetime
import time

# ---------------- CONFIG ----------------
SYMBOLS = ["XAUUSD=X", "XAGUSD=X", "XAUEUR=X"]
INTERVALS = ["1m", "5m", "15m"]
BOT_TOKEN = "8085883361:AAF6RnUUmet81QqBi76M4TGm0v3wNWUX414"
CHAT_ID = "7581536915"

bot = Bot(token=BOT_TOKEN)
sent_signals = set()  # Track signals already sent to avoid duplicates

# ---------------- FUNCTIONS ----------------
def fetch_prices(symbol, interval):
    """Fetch last day prices for symbol on given interval"""
    try:
        data = yf.download(symbol, interval=interval, period="1d")
        if data.empty:
            return None
        return data['Close']
    except Exception as e:
        print(f"Error fetching {symbol} {interval}: {e}")
        return None

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
    """Send message to Telegram"""
    bot.send_message(chat_id=CHAT_ID, text=message)
    print(f"📩 {message}")

def run_sniper():
    """Check all symbols and intervals, send signals if valid"""
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            prices = fetch_prices(symbol, interval)
            if prices is None or len(prices) < 20:
                print(f"{symbol} | {interval}: No data, skipping")
                continue
            signal = sniper_signal(prices)
            if signal:
                key = f"{symbol}_{interval}_{signal}"
                if key not in sent_signals:
                    message = f"⏱ {interval} | {symbol} | {signal} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    send_telegram(message)
                    sent_signals.add(key)
            else:
                print(f"{symbol} | {interval}: No valid signal")

# ---------------- MAIN ----------------
if __name__ == "__main__":
    send_telegram("✅ Gold & Silver Sniper Bot Connected! Monitoring XAU/USD, XAG/USD, XAU/EUR...")
    while True:
        run_sniper()
        time.sleep(60)  # Wait 1 minute before next cycle
