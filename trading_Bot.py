import pandas as pd
import numpy as np
from datetime import datetime
import time
from telegram import Bot
import yfinance as yf
import os
import logging

# -------------------- CONFIGURATION --------------------
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOLS = ["GC=F", "SI=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X"]
TIMEFRAMES = {
    "5min": "5m",
    "10min": "10m", 
    "15min": "15m"
}

# -------------------- INITIALIZATION --------------------
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN and CHAT_ID else None
sent_signals = set()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------------------- TECHNICAL INDICATORS --------------------
def calculate_ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line, signal_line

# -------------------- TRADING SIGNAL --------------------
def generate_signal(df, symbol, timeframe):
    if len(df) < 30:
        return None
    
    closes = df['Close']
    highs = df['High']
    lows = df['Low']
    
    # Calculate indicators
    ema_fast = calculate_ema(closes, 10).iloc[-1]
    ema_slow = calculate_ema(closes, 30).iloc[-1]
    rsi = calculate_rsi(closes, 14).iloc[-1]
    macd_line, macd_signal = calculate_macd(closes)
    macd_val = macd_line.iloc[-1]
    macd_sig = macd_signal.iloc[-1]
    
    # Support/Resistance
    support = lows.tail(20).min()
    resistance = highs.tail(20).max()
    pivot = (highs.tail(20).max() + lows.tail(20).min() + closes.iloc[-1]) / 3
    
    current_price = closes.iloc[-1]
    
    # Volatility check
    volatility = closes.pct_change().std() * 100
    if volatility < 0.5:  # Skip low volatility markets
        return None
    
    # Long conditions
    long_conditions = [
        ema_fast > ema_slow,
        rsi > 45 and rsi < 75,
        macd_val > macd_sig,
        current_price > pivot,
        current_price > support * 1.005,
        volatility > 0.8  # Good volatility
    ]
    
    # Short conditions
    short_conditions = [
        ema_fast < ema_slow,
        rsi < 55 and rsi > 25,
        macd_val < macd_sig,
        current_price < pivot,
        current_price < resistance * 0.995,
        volatility > 0.8  # Good volatility
    ]
    
    long_score = sum(long_conditions)
    short_score = sum(short_conditions)
    
    if long_score >= 4:
        return {
            'symbol': symbol, 'timeframe': timeframe, 'signal': "LONG",
            'entry': round(current_price, 4),
            'sl': round(current_price * 0.985, 4),
            'tp': round(current_price * 1.03, 4),
            'rsi': round(rsi, 2),
            'volatility': round(volatility, 2),
            'support': round(support, 4),
            'resistance': round(resistance, 4)
        }
    elif short_score >= 4:
        return {
            'symbol': symbol, 'timeframe': timeframe, 'signal': "SHORT",
            'entry': round(current_price, 4),
            'sl': round(current_price * 1.015, 4),
            'tp': round(current_price * 0.97, 4),
            'rsi': round(rsi, 2),
            'volatility': round(volatility, 2),
            'support': round(support, 4),
            'resistance': round(resistance, 4)
        }
    
    return None

# -------------------- DATA FETCHING --------------------
def fetch_data(symbol, interval):
    try:
        data = yf.download(symbol, interval=interval, period="2d", progress=False)
        return data
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()

# -------------------- TELEGRAM NOTIFICATIONS --------------------
def send_telegram(message):
    if bot and CHAT_ID:
        try:
            bot.send_message(chat_id=CHAT_ID, text=message)
            logger.info(f"Message sent: {message}")
        except Exception as e:
            logger.error(f"Telegram error: {e}")

# -------------------- MAIN BOT --------------------
def run_bot():
    logger.info("Starting volatility trading bot analysis...")
    
    for symbol in SYMBOLS:
        for tf_name, tf_interval in TIMEFRAMES.items():
            try:
                data = fetch_data(symbol, tf_interval)
                if not data.empty and len(data) > 20:
                    signal = generate_signal(data, symbol, tf_name)
                    if signal:
                        signal_key = f"{symbol}_{tf_name}_{signal['signal']}"
                        if signal_key not in sent_signals:
                            if signal['signal'] == "LONG":
                                message = f"""
🎯 LONG SIGNAL {symbol} ({tf_name})
📍 Entry: {signal['entry']}
🛑 SL: {signal['sl']}
🎯 TP: {signal['tp']}
📊 RSI: {signal['rsi']}
⚡ Volatility: {signal['volatility']}%
🏰 Support: {signal['support']}
🏰 Resistance: {signal['resistance']}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
"""
                            else:
                                message = f"""
🎯 SHORT SIGNAL {symbol} ({tf_name})
📍 Entry: {signal['entry']}
🛑 SL: {signal['sl']}
🎯 TP: {signal['tp']}
📊 RSI: {signal['rsi']}
⚡ Volatility: {signal['volatility']}%
🏰 Support: {signal['support']}
🏰 Resistance: {signal['resistance']}
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
"""
                            send_telegram(message)
                            sent_signals.add(signal_key)
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing {symbol} ({tf_name}): {e}")
                time.sleep(2)

# -------------------- EXECUTION --------------------
if __name__ == "__main__":
    # Send startup message
    if bot and CHAT_ID:
        try:
            bot.send_message(chat_id=CHAT_ID, text="🚀 Volatility Trading Bot Started! Monitoring Gold, Silver, Forex...")
        except:
            pass
    
    logger.info("Volatility trading bot started")
    run_bot()
    logger.info("Analysis completed")
