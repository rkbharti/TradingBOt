# Enhanced SMC Trading Bot - Implementation Guide

## 🎯 What's New in This Version

This enhanced version implements **professional Smart Money Concepts (SMC)** with significant improvements over the basic version:

### New Features

#### 1. Fair Value Gaps (FVG) Detection
- Identifies imbalances in price action
- Bullish FVG: When current low > previous high (2 candles ago)
- Bearish FVG: When current high < previous low (2 candles ago)
- Used as entry zones for high-probability trades

#### 2. Break of Structure (BOS)
- Tracks market structure shifts
- Identifies when price breaks recent swing highs/lows
- Determines overall market bias (BULLISH/BEARISH/NEUTRAL)

#### 3. Liquidity Sweep Detection
- Detects false breakouts at swing points
- Identifies "stop hunts" by institutions
- High sweep: Wick above recent high, close below
- Low sweep: Wick below recent low, close above

#### 4. Premium/Discount Zones (Fibonacci)
- Calculates 50-period high/low range
- Fibonacci levels: 0.382, 0.5, 0.618
- **Buy in discount zones** (below 50%)
- **Sell in premium zones** (above 61.8%)

#### 5. ATR-Based Dynamic Stops
- Stop loss: 1.5-2.0x ATR (adapts to volatility)
- Take profit: 3.0-4.0x ATR (better risk/reward)
- Trailing stops after 2x ATR profit

#### 6. Session Filters
- **London Session**: 1:30 PM - 10:00 PM IST (High activity)
- **New York Session**: 6:00 PM - 2:30 AM IST (High activity)
- **Asian Session**: Avoided (Low volume for Gold)

#### 7. Enhanced Risk Management
- Kelly Criterion for optimal position sizing
- Maximum 3 concurrent positions
- Maximum 5% total account risk
- Per-trade risk: 1-2% (configurable)

#### 8. Backtesting Framework
- Test strategy on historical data
- Calculate win rate, profit factor, Sharpe ratio
- Track equity curve and drawdowns
- Export results to JSON

## 📦 Installation

### Prerequisites
1. **Python 3.8+** installed
2. **MetaTrader 5** desktop application
3. **MT5 Demo Account** (see setup below)

### Setup Steps

```bash
# 1. Clone the repository
git clone https://github.com/rkbharti/TradingBOt.git
cd TradingBOt

# 2. Switch to enhanced branch
git checkout enhanced-smc-algorithm

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your MT5 account
# Edit config.json with your demo account details
```

### MT5 Demo Account Setup

1. **Open MT5 Desktop** → `File` → `Open an Account`
2. Select broker (e.g., Exness, XM, IC Markets)
3. Choose **"Demo Account"**
4. Fill registration details
5. Note: **Account Number**, **Password**, **Server**
6. Go to `Tools` → `Options` → `Expert Advisors`
7. Enable:
   - ✅ Allow automated trading
   - ✅ Allow WebRequest

### Configure config.json

```json
{
    "login": "YOUR_DEMO_ACCOUNT_NUMBER",
    "password": "YOUR_MT5_PASSWORD",
    "server": "YOUR_DEMO_SERVER",
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "lot_size": 0.01,
    "max_spread": 2.0,
    "risk_per_trade": 1.0
}
```

## 🚀 Usage

### Run Live Bot

```bash
python main.py
```

### Run Backtesting

Create `run_backtest.py`:

```python
from utils.mt5_connection import MT5Connection
from strategy.smc_strategy import SMCStrategy
from strategy.backtester import Backtester

# Initialize
mt5 = MT5Connection("config.json")
mt5.initialize_mt5()

# Fetch historical data (last 3 months)
historical_data = mt5.get_historical_data(bars=10000)

# Run backtest
strategy = SMCStrategy()
backtester = Backtester(initial_balance=10000, risk_per_trade=1.0)
metrics = backtester.run_backtest(historical_data, strategy)

# Display results
backtester.print_results(metrics)
backtester.save_results("backtest_results.json")

mt5.shutdown()
```

Run:
```bash
python run_backtest.py
```

## 📊 Understanding the Output

### Live Trading Output

```
🎯 XAUUSD Price: $2645.30 (Spread: $0.50)

📈 Market Structure: BULLISH
🎯 Zone: DISCOUNT
⏰ Session: LONDON ✅

📊 Technical Levels:
   EMA200: $2620.15
   MA20: $2635.40 | MA50: $2625.80
   Support: $2630.00 | Resistance: $2660.00
   ATR: $8.50

💡 SMC Indicators:
   FVG Bullish: ✅
   FVG Bearish: ❌
   Last BOS: BULLISH

🔔 Signal: BUY
   Reason: Bullish SMC: Bullish FVG, Discount zone, MA alignment [LONDON session]

💼 Trade Execution Details:
   Direction: BUY
   Entry: $2645.30
   Stop Loss: $2632.55 (12.75 pips)
   Take Profit: $2670.80 (25.50 pips)
   Lot Size: 0.02
   Risk: $25.50 (1.00%)
   Potential Reward: $51.00
   R:R Ratio: 1:2.0
```

### Backtest Output

```
🔬 Starting Backtest...
============================================================

📊 BACKTEST RESULTS
============================================================
Total Trades: 45
Win Rate: 62.22%
Winning Trades: 28 | Losing Trades: 17
Average Win: $85.30 | Average Loss: -$42.15
Profit Factor: 2.02
============================================================
Initial Balance: $10,000.00
Final Balance: $11,450.00
Total Return: 14.50%
Max Drawdown: -8.30%
Sharpe Ratio: 1.85
============================================================
Best Trade: $245.00
Worst Trade: -$95.00
============================================================
```

## 🎛️ Configuration Options

### config.json Parameters

| Parameter | Description | Recommended |
|-----------|-------------|-------------|
| `risk_per_trade` | % of account to risk per trade | 1.0 - 2.0 |
| `timeframe` | Chart timeframe | M15 or M5 |
| `max_spread` | Maximum spread to allow trades | 2.0 |
| `lot_size` | Fixed lot size (overridden by calculator) | 0.01 |

### Strategy Tuning

In `smc_strategy.py`, adjust:

```python
# Line 245: Minimum score for signals
min_score = 4.0  # Higher = fewer but stronger signals

# Line 173: ATR multipliers
sl_multiplier = 1.5  # Stop loss distance
tp_multiplier = 3.0  # Take profit distance
```

## 📈 Performance Tips

### For Better Results

1. **Trade only London/NY sessions** (already implemented)
2. **Increase minimum score** from 4.0 to 5.0 for higher quality signals
3. **Use M15 timeframe** instead of M5 for more reliable signals
4. **Enable trailing stops** (modify `stoploss_calc.py`)
5. **Add news filter** to avoid high-impact events

### Risk Management

- Start with **1% risk per trade**
- Never exceed **2% per trade**
- Keep **max 3 positions** open
- **Total portfolio risk < 5%**

## 🐛 Troubleshooting

### Common Issues

**"Failed to initialize MT5"**
- Ensure MT5 desktop is running
- Check account credentials in config.json
- Verify server name is correct

**"Outside trading session"**
- Normal during Asian session
- Bot only trades London/NY sessions
- Adjust session times in `smc_strategy.py` if needed

**"Risk limits exceeded"**
- Too many open positions (max 3)
- Total risk > 5%
- Close some positions or wait

**"Insufficient data"**
- Need minimum 200 candles
- Wait a few minutes after starting
- Check MT5 connection

## 📚 Learning Resources

### Understanding SMC

- **Fair Value Gaps**: Imbalances where price moved too fast
- **Order Blocks**: Zones where institutions placed large orders
- **Liquidity Sweeps**: False breakouts to trigger stops
- **BOS**: Confirms trend direction changes

### Key Concepts

1. **Buy in discount zones** (below 50% Fibonacci)
2. **Sell in premium zones** (above 61.8% Fibonacci)
3. **Follow Break of Structure** for trend direction
4. **Use FVG as entry zones** when price returns
5. **Respect session times** (avoid Asian session)

## 🔄 Next Steps

1. **Run backtest** to validate strategy
2. **Paper trade** for 2-4 weeks
3. **Track performance** metrics
4. **Adjust parameters** based on results
5. **Consider live trading** only after consistent profits

## ⚠️ Important Notes

- This is for **DEMO/PAPER TRADING ONLY**
- Always test thoroughly before live trading
- Past performance ≠ future results
- Risk only what you can afford to lose
- Consider consulting a financial advisor

## 🤝 Contributing

Improvements welcome! Focus areas:
- Multi-timeframe analysis
- Machine learning integration
- News sentiment API
- Mobile notifications
- Performance dashboard

## 📞 Support

For issues or questions:
1. Check troubleshooting section
2. Review code comments
3. Open GitHub issue
4. Test in demo account first

---

**Happy Trading! 📈💰**
