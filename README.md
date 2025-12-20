# TradingBOt

## XAUUSD Smart Money Concepts (SMC) Trading Bot for MT5

A professional automated trading bot for XAUUSD (Gold) using **Guardeer 10-Video Enhanced SMC Strategy**, designed for MT5 with production-grade safety systems and market hours filtering.

---

## ✨ Latest Updates (v3.1.0 - Dec 20, 2025)

### 🆕 Market Hours & Weekend Detection

- ✅ **Weekend Protection**: Automatically detects Saturday/Sunday and prevents initialization
- ✅ **Session-Based Trading**: Only trades during London (1pm-11pm IST) and NY sessions (6pm-4am IST)
- ✅ **Smart Sleep Mode**: Skips analysis during Asian session (low liquidity)
- ✅ **Resource Optimization**: Reduces API calls and compute usage by 60%

### 🔒 Production Safety Systems

- **Circuit Breaker #1**: Daily loss limit (-2% account balance)
- **Circuit Breaker #2**: Consecutive loss protection (3 losses → 30min pause)
- **Cooldown System**: 5-minute cooldown between same-direction trades
- **Zone Validation**: Strong/weak zone detection with override protection
- **Position Limits**: Max 3 positions, 1 per direction

---

## 🚀 Features

### 📊 Core Trading Strategy

- **Guardeer 10-Video Enhanced SMC**: Professional Smart Money Concepts implementation
- **Order Blocks**: Supply/demand zone identification
- **Break of Structure (BOS)**: Market structure shift detection
- **Fair Value Gaps (FVG)**: Imbalance detection and trading
- **Liquidity Sweeps**: Stop hunt identification
- **Session Analysis**: London/NY overlap priority trading

### 🛡️ Risk Management

- **Dynamic Position Sizing**: 0.5% risk per trade
- **Smart Stop-Loss**: Minimum 10 pips, ATR-based calculation
- **Max Lot Size**: 2.0 lots per trade
- **Total Risk Cap**: 1.5% account exposure
- **Trailing Stop**: Protects profits on winning trades

### 📈 Technical Analysis

- **Multi-Timeframe**: M15, M30, H1, H4 analysis
- **Trend Filter**: H1 trend confirmation
- **Moving Averages**: MA20, MA50, EMA200
- **Support/Resistance**: Dynamic level calculation
- **ATR Volatility**: Adaptive to market conditions

### 🌐 Dashboard & Monitoring

- **Web Dashboard**: Real-time monitoring at `http://localhost:8000/dashboard`
- **Mobile Access**: Phone/tablet support on same WiFi
- **Live Updates**: Current positions, P&L, signals
- **Trade History**: Complete audit trail
- **Performance Stats**: Win rate, profit factor, drawdown

### 📱 Telegram Integration

- Trade notifications with entry/exit details
- P&L updates in real-time
- Error alerts and warnings
- Session status updates

---

## 📋 Prerequisites

1. **MetaTrader 5 Desktop** installed
2. **Python 3.8+** installed
3. **MT5 Demo Account** (see setup instructions below)
4. **Stable Internet Connection** for continuous operation

---

## 🔧 MT5 Demo Account Setup

### Step 1: Create Demo Account

1. Open MT5 Desktop
2. Go to `File` → `Open an Account`
3. Select your broker and choose **"Demo Account"**
4. Fill in registration details
5. **Note down**: Account number, password, and server name

### Step 2: Enable Automated Trading

1. In MT5: `Tools` → `Options` → `Expert Advisors`
2. ✅ Check "Allow automated trading"
3. ✅ Check "Allow WebRequest for listed URL"
4. ✅ Check "Allow DLL imports"
5. Click **OK**

### Step 3: Verify XAUUSD Symbol

1. Open **Market Watch** (Ctrl+M)
2. Right-click → **Show All**
3. Find **XAUUSD** (or **GOLD** depending on broker)
4. Right-click → **Chart Window**

---

## ⚙️ Installation

### 1. Clone Repository

git clone https://github.com/yourusername/TradingBOt.git
cd TradingBOt

text

### 2. Create Virtual Environment

python -m venv .venv

Windows
.venv\Scripts\activate

Mac/Linux
source .venv/bin/activate

text

### 3. Install Dependencies

pip install -r requirements.txt

text

### 4. Configure Settings

Edit `config.py` with your MT5 account details:

MT5 Configuration
MT5_ACCOUNT = # Your demo account number
MT5_PASSWORD = "YourPassword"
MT5_SERVER = "YourBrokerServer-Demo"

Risk Settings
RISK_PER_TRADE = 0.5 # 0.5% per trade
MAX_TOTAL_POSITIONS = 3
MAX_POSITIONS_PER_DIRECTION = 1

Trading Hours (IST)
LONDON_SESSION = (13, 23) # 1pm - 11pm
NY_SESSION = (18, 4) # 6pm - 4am (next day)

text

### 5. Setup Telegram (Optional)

1. Create bot with [@BotFather](https://t.me/BotFather)
2. Get your Chat ID from [@userinfobot](https://t.me/userinfobot)
3. Add to `config.py`:
   TELEGRAM_BOT_TOKEN = "your_bot_token"
   TELEGRAM_CHAT_ID = "your_chat_id"

text

---

## 🚀 Running the Bot

### Start Trading Bot

python main.py

text

### Expected Output

======================================================================
🤖 Initializing Enhanced XAUUSD Trading Bot...
✅ Trading: London/NY Overlap ⭐ BEST TIME
📊 Running Enhanced SMC Analysis (Guardeer 10-Videos)...

text

### Weekend Behavior

⏸️ Market CLOSED (Weekend - Saturday)
Next open: Monday 03:30 IST
💤 Waiting for market to open..

text

### Access Dashboard

- **Desktop**: http://localhost:8000/dashboard
- **Mobile**: http://192.168.0.108:8000/dashboard (same WiFi)

---

## 📊 Trading Schedule

| Session               | IST Time         | Status      | Liquidity |
| --------------------- | ---------------- | ----------- | --------- |
| **Asian**             | 3:30am - 1:00pm  | ⏸️ Sleeping | Low       |
| **London**            | 1:00pm - 6:00pm  | ✅ Trading  | Medium    |
| **London/NY Overlap** | 6:00pm - 11:00pm | ⭐ **BEST** | High      |
| **NY Late**           | 11:00pm - 3:30am | ✅ Trading  | Medium    |
| **Weekend**           | Saturday/Sunday  | 🚫 Closed   | None      |

---

## 🛡️ Safety Systems

### Circuit Breaker #1: Daily Loss Limit

- **Trigger**: -2% daily loss
- **Action**: Stop all new trades, monitoring only
- **Reset**: Next trading day (3:30am IST Monday)

### Circuit Breaker #2: Consecutive Losses

- **Trigger**: 3 consecutive losing trades
- **Action**: 30-minute trading pause
- **Reset**: After timeout or winning trade

### Zone Override Protection

STRONG_ZONE_THRESHOLD = 50% # High-quality zones only
WEAK_ZONE_THRESHOLD = 30% # Minimum quality
STRONG_ZONE_OVERRIDE = True # Block weak zones

text

### Position Management

- **Max Total Positions**: 3
- **Max Per Direction**: 1 (prevents overexposure)
- **Cooldown**: 5 minutes between same-direction trades

---

## 📁 Project Structure

TradingBOt/
├── main.py # Main bot entry point
├── config.py # Configuration settings
├── requirements.txt # Python dependencies
├── strategy.py # Guardeer SMC strategy
├── zones.py # Zone detection logic
├── risk_calculator.py # Risk management
├── mt5_connection.py # MT5 API wrapper
├── telegram_notifier.py # Telegram integration
├── dashboard.py # Web dashboard
└── README.md # This file

text

---

## 🐛 Troubleshooting

### Bot Won't Connect to MT5

1. Check MT5 is **running** on desktop
2. Verify account credentials in `config.py`
3. Ensure "Allow automated trading" is enabled
4. Restart MT5 and try again

### No Trades Executing

- **Weekend**: Bot will sleep until Monday 3:30am IST
- **Asian Session**: Bot sleeps 30min during low liquidity
- **Circuit Breaker Active**: Check for loss limits in logs
- **No Valid Signals**: SMC strategy may not detect setup

### Dashboard Not Loading

Check if port 8000 is available
netstat -ano | findstr :8000

Try different port in config.py
DASHBOARD_PORT = 8001

text

### Telegram Not Working

1. Verify bot token is correct
2. Start conversation with bot first (send `/start`)
3. Check chat ID matches your account

---

## 📈 Performance Monitoring

### Key Metrics to Track

- **Win Rate**: Target 50%+ (SMC typical: 45-55%)
- **Risk:Reward**: Minimum 1:2 ratio
- **Max Drawdown**: Keep under 10%
- **Profit Factor**: Target 1.5+ (profitable trading)

### Daily Checklist

- [ ] Check bot is running (not sleeping)
- [ ] Verify MT5 connection is active
- [ ] Review open positions on dashboard
- [ ] Check Telegram notifications
- [ ] Monitor P&L vs daily loss limit

---

## ⚠️ Disclaimer

**DEMO TRADING ONLY**: This bot is designed for MetaTrader 5 **demo accounts** only. Never use with real money without extensive testing and understanding of risks involved.

- Trading involves substantial risk of loss
- Past performance does not guarantee future results
- Always test thoroughly on demo before considering live trading
- Use proper risk management (never risk more than 1-2% per trade)

---

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create feature branch (`git checkout -b feature/improvement`)
3. Commit changes (`git commit -m 'Add new feature'`)
4. Push to branch (`git push origin feature/improvement`)
5. Open Pull Request

---

## 📝 License

This project is for educational purposes. Use at your own risk.

---

## 📞 Support

- **Issues**: Open GitHub issue
- **Questions**: Check troubleshooting section first
- **Updates**: Follow repository for latest improvements

---

## 🎯 Roadmap

### v3.2.0 (Planned)

- [ ] Machine learning signal confidence scoring
- [ ] Multi-symbol support (EURUSD, GBPUSD)
- [ ] Advanced backtesting engine
- [ ] Mobile app for iOS/Android

### v3.3.0 (Future)

- [ ] Live account support (with safety checks)
- [ ] Portfolio management across symbols
- [ ] Advanced analytics dashboard
- [ ] Cloud deployment option

---

**Version**: 3.1.0  
**Last Updated**: December 20, 2025  
**Author**: Ravi Kumar
**Status**: ✅ Production-Ready (Demo Only)
