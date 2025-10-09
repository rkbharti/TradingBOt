# TradingBOt
## XAUUSD Trading Bot for MT5

A professional trading bot for XAUUSD (Gold) using Smart Money Concepts (SMC) strategy, designed for MT5 demo account trading.

## 🚀 Features

- **SMC Strategy Implementation**: Order blocks, liquidity levels, market structure
- **Risk Management**: Dynamic position sizing and stop-loss calculation
- **Technical Analysis**: Moving averages, support/resistance levels
- **Demo Trading**: Paper trading only - no real money involved
- **Live Monitoring**: Real-time price data from MT5
- **Comprehensive Logging**: Detailed trade analysis and signals

## 📋 Prerequisites

1. **MetaTrader 5 Desktop** installed
2. **Python 3.8+** installed
3. **MT5 Demo Account** (see setup instructions below)

## 🔧 MT5 Demo Account Setup

### Step 1: Create Demo Account
1. Open MT5 Desktop
2. Go to `File` → `Open an Account`
3. Select your broker and choose "Demo Account"
4. Fill in registration details
5. Note down your account number, password, and server name

### Step 2: Enable Automated Trading
1. In MT5: `Tools` → `Options` → `Expert Advisors`
2. Check "Allow automated trading"
3. Check "Allow WebRequest for listed URL"
4. Click OK

## ⚙️ Installation

1. **Clone/Download this project**
   ```bash
   mkdir TradingBot
   cd TradingBot
