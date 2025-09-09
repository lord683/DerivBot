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
connection_sent = False  # Track if connection message was sent

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
            return True
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    return False

def send_connection_message():
    """Send connection success message"""
    global connection_sent
    if not connection_sent and bot and CHAT_ID:
        message = f"""
✅ **BOT CONNECTED SUCCESSFULLY!** ✅

🚀 **Trading Bot is Now Live**
📊 **Monitoring:** Gold, Silver, EUR/USD, GBP/USD, USD/JPY
⏰ **Timeframes:** 5min, 10min, 15min
⚡ **Volatility Filter:** Active
📈 **Strategy:** Multi-timeframe momentum

🕒 **Started:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🌐 **Platform:** GitHub Actions

**Status:** ✅ Online and analyzing markets...
"""
        if send_telegram(message):
            connection_sent = True
            logger.info("Connection message sent successfully")

# -------------------- MAIN BOT --------------------
def run_bot():
    logger.info("Starting volatility trading bot analysis...")
    
    # Send connection message first
    send_connection_message()
    
    # Then start analysis
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
                            if send_telegram(message):
                                sent_signals.add(signal_key)
                                logger.info(f"Signal sent for {symbol} ({tf_name})")
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing {symbol} ({tf_name}): {e}")
                time.sleep(2)

# -------------------- EXECUTION --------------------
if __name__ == "__main__":
    logger.info("🚀 Volatility Trading Bot Starting...")
    
    # Send connection message and start analysis
    try:
        run_bot()
        logger.info("✅ Analysis completed successfully")
        
        # Send completion message if no signals found
        if len(sent_signals) == 0:
            no_signal_msg = f"""
📊 **Analysis Complete**
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
✅ Scanned: {len(SYMBOLS)} assets × {len(TIMEFRAMES)} timeframes
🔍 Result: No high-probability signals found
⚡ Volatility too low or conditions not met

**Next scan in 5 minutes...**
"""
            send_telegram(no_signal_msg)
            
    except Exception as e:
        error_msg = f"""
❌ **BOT ERROR**
🕒 Time: {datetime.now().strftime('%H:%M:%S')}
⚠️ Error: {str(e)}
🔧 Status: Please check logs
"""
        send_telegram(error_msg)
        logger.error(f"Bot crashed: {e}")
