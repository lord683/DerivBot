import yfinance as yf
import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from telegram import Bot
from datetime import datetime
import time

# ---------------- CONFIG ----------------
SYMBOLS = ["XAUUSD=X", "XAGUSD=X", "XAUEUR=X", "XAGEUR=X"]
TIMEFRAMES = {"1min": "1m", "5min": "5m", "10min": "10m"}  # label: yfinance interval
BOT_TOKEN = "8085883361:AAF6RnUUmet81QqBi76M4TGm0v3wNWUX414"
CHAT_ID = "7581536915"
bot = Bot(token=BOT_TOKEN)
sent_signals = set()  # avoid duplicates per session

# ---------------- FUNCTIONS ----------------
def fetch_ohlc(symbol, interval, period="1d"):
    try:
        df = yf.download(symbol, interval=interval, period=period)
        return df
    except Exception as e:
        print(f"❌ Error fetching {symbol}: {e}")
        return pd.DataFrame()

def calculate_indicators(df):
    close = df['Close']
    ema_fast = EMAIndicator(close, window=10).ema_indicator()
    ema_slow = EMAIndicator(close, window=30).ema_indicator()
    rsi = RSIIndicator(close, window=14).rsi()
    macd = MACD(close)
    macd_val = macd.macd()
    macd_signal = macd.macd_signal()
    return ema_fast, ema_slow, rsi, macd_val, macd_signal

def sniper_signal(ema_fast, ema_slow, rsi, macd_val, macd_signal, close):
    last_price = close.iloc[-1]
    signal = None
    tp = None
    sl = None

    # Sniper logic
    if ema_fast.iloc[-1] > ema_slow.iloc[-1] and rsi.iloc[-1] < 70 and macd_val.iloc[-1] > macd_signal.iloc[-1]:
        signal = "BUY"
        tp = round(last_price * 1.002, 2)  # example 0.2% TP
        sl = round(last_price * 0.998, 2)  # example 0.2% SL
    elif ema_fast.iloc[-1] < ema_slow.iloc[-1] and rsi.iloc[-1] > 30 and macd_val.iloc[-1] < macd_signal.iloc[-1]:
        signal = "SELL"
        tp = round(last_price * 0.998, 2)
        sl = round(last_price * 1.002, 2)
    
    return signal, tp, sl

def send_telegram(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
        print(f"📩 {message}")
    except Exception as e:
        print(f"❌ Telegram error: {e}")

def run_sniper():
    for symbol in SYMBOLS:
        signals = []
        prices_dict = {}
        for label, interval in TIMEFRAMES.items():
            df = fetch_ohlc(symbol, interval)
            if df.empty or len(df) < 20:
                continue
            ema_fast, ema_slow, rsi, macd_val, macd_signal = calculate_indicators(df)
            signal, tp, sl = sniper_signal(ema_fast, ema_slow, rsi, macd_val, macd_signal, df['Close'])
            signals.append(signal)
            prices_dict[label] = (signal, tp, sl)
        
        # Only send if at least 2 timeframes agree
        signal_counts = pd.Series(signals).value_counts()
        if not signal_counts.empty:
            top_signal = signal_counts.idxmax()
            if signal_counts[top_signal] >= 2 and top_signal is not None:
                key = f"{symbol}_{top_signal}"
                if key not in sent_signals:
                    # Choose TP/SL from the longest timeframe for safety
                    final_signal, final_tp, final_sl = prices_dict["10min"]
                    msg = (
                        f"⏱ Sniper Alert | {symbol.replace('=X','')} | {final_signal}\n"
                        f"Price: {df['Close'].iloc[-1]:.2f}\n"
                        f"TP: {final_tp} | SL: {final_sl}\n"
                        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    send_telegram(msg)
                    sent_signals.add(key)
                    # Save to log
                    with open("signals_log.txt", "a") as f:
                        f.write(msg + "\n")

# ---------------- MAIN LOOP ----------------
if __name__ == "__main__":
    send_telegram("✅ Sniper Bot Connected! Monitoring Gold & Silver...")
    start_time = time.time()
    RUN_HOURS = 6

    while (time.time() - start_time) < RUN_HOURS * 3600:
        run_sniper()
        print("Waiting 1 minute before next check...")
        time.sleep(60)
    
    send_telegram("⏹ Sniper Bot Finished 6 hours run.")
