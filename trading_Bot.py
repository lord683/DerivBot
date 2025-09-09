import asyncio
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging
from telegram import Bot
import yfinance as yf

# -------------------- CONFIGURATION --------------------
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Trading parameters
SYMBOLS = ["GC=F", "SI=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X"]  # Gold, Silver, Forex
TIMEFRAMES = {
    "5min": "5m",
    "10min": "10m", 
    "15min": "15m"
}

# Technical analysis parameters
EMA_FAST = 10
EMA_SLOW = 30
EMA_TREND = 50
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Risk management
RISK_REWARD_RATIO = 2.0
STOP_LOSS_PCT = 0.015  # 1.5%
TAKE_PROFIT_PCT = 0.03  # 3%

# -------------------- INITIALIZATION --------------------
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN and CHAT_ID else None
sent_signals = set()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------------------- SUPPORT/RESISTANCE ZONES --------------------
def calculate_support_resistance(df, lookback=20):
    """Calculate support and resistance levels using recent price action"""
    highs = df['High'].tail(lookback)
    lows = df['Low'].tail(lookback)
    
    # Simple support/resistance using recent highs and lows
    resistance = highs.max()
    support = lows.min()
    
    # Additional levels using pivot points
    pivot = (highs.max() + lows.min() + df['Close'].iloc[-1]) / 3
    r1 = (2 * pivot) - lows.min()
    s1 = (2 * pivot) - highs.max()
    
    return {
        'support': round(support, 4),
        'resistance': round(resistance, 4),
        'pivot': round(pivot, 4),
        'r1': round(r1, 4),
        's1': round(s1, 4)
    }

# -------------------- TECHNICAL INDICATORS --------------------
def calculate_ema(prices, period):
    """Calculate Exponential Moving Average"""
    return prices.ewm(span=period, adjust=False).mean()

def calculate_rsi(prices, period=14):
    """Calculate Relative Strength Index"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD indicator"""
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

# -------------------- TRADING SIGNAL GENERATION --------------------
def generate_trading_signal(df, symbol, timeframe):
    """Generate complete trading signal with entry/exit points"""
    if len(df) < 50:
        return None
    
    closes = df['Close']
    highs = df['High']
    lows = df['Low']
    
    # Calculate all indicators
    ema_fast = calculate_ema(closes, EMA_FAST).iloc[-1]
    ema_slow = calculate_ema(closes, EMA_SLOW).iloc[-1]
    ema_trend = calculate_ema(closes, EMA_TREND).iloc[-1]
    rsi = calculate_rsi(closes, RSI_PERIOD).iloc[-1]
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    macd_value = macd_line.iloc[-1]
    macd_signal_value = macd_signal.iloc[-1]
    
    # Support/Resistance
    sr_levels = calculate_support_resistance(df)
    
    current_price = closes.iloc[-1]
    previous_close = closes.iloc[-2]
    
    # Signal conditions for LONG
    long_conditions = [
        ema_fast > ema_slow,
        ema_slow > ema_trend,
        current_price > ema_trend,
        rsi > 50 and rsi < RSI_OVERBOUGHT,
        macd_value > macd_signal_value,
        current_price > previous_close,
        current_price > sr_levels['pivot']
    ]
    
    # Signal conditions for SHORT
    short_conditions = [
        ema_fast < ema_slow,
        ema_slow < ema_trend,
        current_price < ema_trend,
        rsi < 50 and rsi > RSI_OVERSOLD,
        macd_value < macd_signal_value,
        current_price < previous_close,
        current_price < sr_levels['pivot']
    ]
    
    long_score = sum(long_conditions)
    short_score = sum(short_conditions)
    
    if long_score >= 5 or short_score >= 5:
        signal_type = "LONG" if long_score >= short_score else "SHORT"
        
        # Calculate entry/exit points
        if signal_type == "LONG":
            entry_price = current_price
            stop_loss = entry_price * (1 - STOP_LOSS_PCT)
            take_profit = entry_price * (1 + TAKE_PROFIT_PCT)
            
            # Adjust SL to nearest support
            stop_loss = min(stop_loss, sr_levels['support'] * 0.998)
            
        else:  # SHORT
            entry_price = current_price
            stop_loss = entry_price * (1 + STOP_LOSS_PCT)
            take_profit = entry_price * (1 - TAKE_PROFIT_PCT)
            
            # Adjust SL to nearest resistance
            stop_loss = max(stop_loss, sr_levels['resistance'] * 1.002)
        
        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'signal': signal_type,
            'entry_price': round(entry_price, 4),
            'stop_loss': round(stop_loss, 4),
            'take_profit': round(take_profit, 4),
            'risk_reward': RISK_REWARD_RATIO,
            'confidence': max(long_score, short_score) / 7 * 100,
            'current_price': round(current_price, 4),
            'rsi': round(rsi, 2),
            'ema_fast': round(ema_fast, 4),
            'ema_slow': round(ema_slow, 4),
            'support': sr_levels['support'],
            'resistance': sr_levels['resistance'],
            'pivot': sr_levels['pivot'],
            'timestamp': datetime.now()
        }
    
    return None

# -------------------- DATA FETCHING --------------------
def fetch_ohlc_data(symbol, interval="5m", period="1d"):
    """Fetch OHLC data from Yahoo Finance"""
    try:
        data = yf.download(symbol, interval=interval, period=period, progress=False)
        if not data.empty:
            return data
    except Exception as e:
        logger.error(f"Error fetching {symbol} ({interval}): {e}")
    return pd.DataFrame()

# -------------------- NOTIFICATION SYSTEM --------------------
async def send_trading_signal(signal):
    """Send detailed trading signal to Telegram"""
    if not bot or not CHAT_ID:
        return
    
    symbol = signal['symbol']
    timeframe = signal['timeframe']
    
    # Create unique signal key to avoid duplicates
    signal_key = f"{symbol}_{timeframe}_{signal['signal']}_{datetime.now().strftime('%Y%m%d%H')}"
    
    if signal_key in sent_signals:
        return
    
    # Prepare signal message
    if signal['signal'] == "LONG":
        message = f"""
🎯 **LONG ENTRY SIGNAL** 🎯

**Asset:** {symbol}
**Timeframe:** {timeframe}
**Confidence:** {signal['confidence']:.1f}%

📊 **TRADING LEVELS:**
• 🟢 ENTRY: {signal['entry_price']}
• 🔴 STOP LOSS: {signal['stop_loss']}
• 🟢 TAKE PROFIT: {signal['take_profit']}
• 📈 Risk/Reward: 1:{signal['risk_reward']}

📈 **TECHNICALS:**
• RSI: {signal['rsi']}
• Price: {signal['current_price']}
• EMA Fast: {signal['ema_fast']}
• EMA Slow: {signal['ema_slow']}

🏰 **KEY LEVELS:**
• Support: {signal['support']}
• Resistance: {signal['resistance']}
• Pivot: {signal['pivot']}

⏰ **TIME:** {signal['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}

**Action:** BUY at market price
"""
    else:
        message = f"""
🎯 **SHORT ENTRY SIGNAL** 🎯

**Asset:** {symbol}
**Timeframe:** {timeframe}
**Confidence:** {signal['confidence']:.1f}%

📊 **TRADING LEVELS:**
• 🔴 ENTRY: {signal['entry_price']}
• 🟢 STOP LOSS: {signal['stop_loss']}
• 🔴 TAKE PROFIT: {signal['take_profit']}
• 📈 Risk/Reward: 1:{signal['risk_reward']}

📈 **TECHNICALS:**
• RSI: {signal['rsi']}
• Price: {signal['current_price']}
• EMA Fast: {signal['ema_fast']}
• EMA Slow: {signal['ema_slow']}

🏰 **KEY LEVELS:**
• Support: {signal['support']}
• Resistance: {signal['resistance']}
• Pivot: {signal['pivot']}

⏰ **TIME:** {signal['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}

**Action:** SELL at market price
"""
    
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
        sent_signals.add(signal_key)
        logger.info(f"✅ Signal sent for {symbol} ({timeframe}) - {signal['signal']}")
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message: {e}")

async def send_telegram_message(message):
    """Send simple message to Telegram"""
    if bot and CHAT_ID:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message)
        except Exception as e:
            logger.error(f"Telegram message failed: {e}")

# -------------------- MAIN TRADING BOT --------------------
async def trading_bot():
    """Main trading bot function"""
    
    await send_telegram_message("🚀 Professional Trading Bot Started!")
    await send_telegram_message("📊 Monitoring: Gold, Silver, EUR/USD, GBP/USD, USD/JPY")
    await send_telegram_message("⏰ Timeframes: 5min, 10min, 15min")
    
    analysis_interval = 300  # Check every 5 minutes
    
    while True:
        try:
            logger.info("Starting analysis cycle...")
            
            for symbol in SYMBOLS:
                for tf_name, tf_interval in TIMEFRAMES.items():
                    try:
                        # Fetch data
                        ohlc_data = fetch_ohlc_data(symbol, tf_interval, "2d")
                        
                        if not ohlc_data.empty and len(ohlc_data) > 20:
                            # Generate signal
                            signal = generate_trading_signal(ohlc_data, symbol, tf_name)
                            
                            if signal:
                                logger.info(f"Signal found for {symbol} ({tf_name}): {signal['signal']}")
                                await send_trading_signal(signal)
                            
                        await asyncio.sleep(2)  # Brief pause between requests
                        
                    except Exception as e:
                        logger.error(f"Error analyzing {symbol} ({tf_name}): {e}")
                        await asyncio.sleep(5)
            
            logger.info(f"Cycle completed. Waiting {analysis_interval} seconds...")
            await asyncio.sleep(analysis_interval)
            
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            await asyncio.sleep(60)

# -------------------- EXECUTION --------------------
if __name__ == "__main__":
    print("Starting Professional Trading Bot...")
    print("Monitoring:", SYMBOLS)
    print("Timeframes:", list(TIMEFRAMES.keys()))
    print("Press Ctrl+C to stop")
    
    try:
        asyncio.run(trading_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
