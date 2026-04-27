import pandas as pd
from tradingbot.strategy.smc.signal_engine import SignalEngine


# =========================
# LOAD DATA
# =========================
def load_data(path):
    df = pd.read_csv(path, sep="\t")

    print("RAW COLUMNS:", df.columns.tolist())  # DEBUG

    # Clean column names properly
    df.columns = (
        df.columns
        .str.strip()
        .str.replace("<", "", regex=False)
        .str.replace(">", "", regex=False)
        .str.lower()
    )

    print("CLEANED COLUMNS:", df.columns.tolist())  # DEBUG

    # Now safe to access
    df["date"] = df["date"].astype(str).str.strip()
    df["time"] = df["time"].astype(str).str.strip()

    # Create datetime
    df["datetime"] = pd.to_datetime(
        df["date"] + " " + df["time"],
        format="%Y.%m.%d %H:%M:%S",
        errors="coerce"
        
    )
    print("Invalid datetime rows:", df["datetime"].isna().sum())

    df = df.dropna(subset=["datetime"])
    df["datetime"] = df["datetime"].dt.tz_localize("UTC")
    df.set_index("datetime", inplace=True)

    print("Total rows after clean:", len(df))

    return df


# =========================
# CREATE MULTI-TIMEFRAME
# =========================
def create_timeframes(df):
    df_m5 = df.copy()

    df_m15 = df.resample("15min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    }).dropna()

    df_h4 = df.resample("4h").agg({   # fixed warning
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    }).dropna()

    df_d1 = df.resample("1d").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    }).dropna()

    return df_d1, df_h4, df_m15, df_m5


# =========================
# BACKTEST ENGINE
# =========================
def run_backtest(df):
    engine = SignalEngine()
    trades = []

    df_d1, df_h4, df_m15, df_m5 = create_timeframes(df)

    max_len = min(len(df_m5), 500)  # limit for speed

    # TEMP: force HTF pass (for debugging)
    engine._step_htf_bias = lambda d1, h4: {
        "passed": True,
        "direction": "BULLISH",
        "reason": "FORCED"
    }

    for i in range(100, max_len):
        print(f"\nProcessing candle: {i}")

        try:
            # 🔥 CRITICAL FIX — TIME-ALIGNED SLICING
            current_time = df_m5.index[i]

            d1_slice = df_d1[df_d1.index <= current_time].copy()
            h4_slice = df_h4[df_h4.index <= current_time].copy()
            m15_slice = df_m15[df_m15.index <= current_time].copy()
            m5_slice = df_m5.iloc[max(0, i-100):i].copy()

            # CRITICAL — Preserve datetime as 'time' column
            for df_ in [m5_slice, m15_slice, h4_slice, d1_slice]:
                if not isinstance(df_.index, pd.DatetimeIndex):
                    continue
                df_["time"] = df_.index
            print("M5 slice len:", len(m5_slice))
            print("M15 slice len:", len(m15_slice))
            result = engine.evaluate(
                d1_slice,
                h4_slice,
                m15_slice,
                m5_slice
            )

            print("Action:", result.action)
            print("Reason:", result.reason)

            # Deep debug
            if hasattr(result, "__dict__"):
                print("Full Result:", result.__dict__)

            if result.action == "ENTER":
                print("🔥 TRADE FOUND")

                trades.append({
                    "index": i,
                    "direction": result.direction,
                    "entry": result.entry_price,
                    "sl": result.stop_loss,
                    "tp": result.take_profit
                })

        except Exception as e:
            print(f"Error at candle {i}: {e}")
            continue

    return trades


# =========================
# RESULT ANALYSIS
# =========================
def analyze_results(trades):
    total = len(trades)

    print("\n📊 BACKTEST RESULT")

    if total == 0:
        print("Total Trades: 0")
        print("Wins: 0")
        print("Losses: 0")
        print("Win Rate: 0.00%")
        return

    wins = 0
    losses = 0

    for t in trades:
        if t["tp"] > t["entry"]:
            wins += 1
        else:
            losses += 1

    win_rate = (wins / total) * 100

    print(f"Total Trades: {total}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {win_rate:.2f}%")


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    path = "data.csv"

    df = load_data(path)
    trades = run_backtest(df)
    analyze_results(trades)