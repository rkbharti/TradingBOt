import pandas as pd
import csv
from pathlib import Path
from bisect import bisect_left
from tradingbot.strategy.smc.signal_engine import SignalEngine
from backtest_logger import BacktestLogger
import uuid


INITIAL_CAPITAL = 1000000.0
RISK_PER_TRADE = 0.01


def load_data(path):
    df = pd.read_csv(path, sep=None, engine="python")

    df.columns = (
        df.columns
        .str.strip()
        .str.replace("<", "", regex=False)
        .str.replace(">", "", regex=False)
        .str.lower()
    )

    df["datetime"] = pd.to_datetime(
        df["date"] + " " + df["time"],
        format="%Y.%m.%d %H:%M:%S",
        errors="coerce",
    )

    df = df.dropna(subset=["datetime"])
    df.set_index("datetime", inplace=True)
    df = df[["open", "high", "low", "close"]]

    return df.sort_index()


def create_timeframes(df):
    df_m5 = df.copy()

    df_m15 = (
        df_m5.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    df_h4 = (
        df_m5.resample("4h", offset="2h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    df_d1 = (
        df_m5.resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    return df_d1, df_h4, df_m15, df_m5


def run_backtest(df):
    engine = SignalEngine()
    capital = INITIAL_CAPITAL
    active_trade = None
    last_entry_price = None

    peak_capital = INITIAL_CAPITAL
    max_dd = 0.0

    logger = BacktestLogger(reset=False)
    run_id = str(uuid.uuid4())[:8]
    logger.start_run(run_id)

    df_d1, df_h4, df_m15, df_m5 = create_timeframes(df)

    m15_index = list(df_m15.index)
    h4_index = list(df_h4.index)
    d1_index = list(df_d1.index)

    print(f"\n🚀 START BACKTEST | RUN_ID: {run_id} | CAPITAL: ₹{capital}")
    print("=" * 60)

    start_idx = 200
    cfg = engine.config

    for i in range(start_idx, len(df_m5)):
        current_time = df_m5.index[i]
        candle = df_m5.iloc[i]

        m15_idx = bisect_left(m15_index, current_time)
        h4_idx = bisect_left(h4_index, current_time)
        d1_idx = bisect_left(d1_index, current_time)

        if (
            i < cfg.min_m5_candles
            or m15_idx < cfg.min_m15_candles
            or h4_idx < cfg.min_h4_candles
            or d1_idx < cfg.min_d1_candles
        ):
            continue

        m5 = df_m5.iloc[max(0, i - 500): i + 50]
        m15 = df_m15.iloc[max(0, m15_idx - 100): m15_idx]
        h4 = df_h4.iloc[max(0, h4_idx - 50): h4_idx]
        d1 = df_d1.iloc[max(0, d1_idx - 30): d1_idx]

        result = engine.evaluate(
            m5_df=m5,
            m15_df=m15,
            h4_df=h4,
            d1_df=d1,
            now_utc=current_time,
        )

        # =========================================================
        # 🔵 ENTRY
        # =========================================================
        if result.action == "ENTER" and active_trade is None:
            entry = result.entry_price
            sl = result.sl_price
            tp = result.tp_price
            direction = result.direction

            if not all([entry, sl, tp, direction]):
                continue

            if last_entry_price is not None:
                if abs(entry - last_entry_price) < 0.5:
                    continue

            rr = abs(tp - entry) / abs(entry - sl)
            if rr < 1.5:
                continue

            last_entry_price = entry
            risk = capital * RISK_PER_TRADE

            active_trade = {
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "direction": direction,
                "risk": risk,
            }

            logger.log_trade_open(
                str(current_time),
                direction,
                entry,
                sl,
                tp,
                risk,
            )

            print("\n[ENTRY]")
            print(
                f"{direction} @ {entry} | SL: {sl} | TP: {tp} | RR: {round(rr, 2)}"
            )

        # =========================================================
        # 🔴 EXIT
        # =========================================================
        if active_trade is not None:
            direction = active_trade["direction"]
            entry = active_trade["entry"]
            sl = active_trade["sl"]
            tp = active_trade["tp"]
            risk = active_trade["risk"]

            if (direction == "BULLISH" and candle["low"] <= sl) or (
                direction == "BEARISH" and candle["high"] >= sl
            ):
                capital -= risk

                if capital < peak_capital:
                    dd = (peak_capital - capital) / peak_capital
                    if dd > max_dd:
                        max_dd = dd

                logger.log_trade_close(
                    str(current_time),
                    "SL_HIT",
                    -risk,
                )

                print(
                    f"[EXIT] {current_time} | LOSS | ₹{round(capital, 2)} | DD: {round(max_dd * 100, 2)}%"
                )
                active_trade = None
                continue

            if (direction == "BULLISH" and candle["high"] >= tp) or (
                direction == "BEARISH" and candle["low"] <= tp
            ):
                rr = abs(tp - entry) / abs(entry - sl)
                pnl = risk * rr
                capital += pnl

                if capital > peak_capital:
                    peak_capital = capital

                logger.log_trade_close(
                    str(current_time),
                    "TP_HIT",
                    pnl,
                )

                print(
                    f"[EXIT] {current_time} | WIN | ₹{round(capital, 2)} | Peak: ₹{round(peak_capital, 2)}"
                )
                active_trade = None

    summary = logger.finalize_run(capital, INITIAL_CAPITAL, max_dd)

    print("\n" + "=" * 60)
    print(f"💰 FINAL CAPITAL: ₹{round(capital, 2)}")
    print(f"📉 MAX DRAWDOWN: {round(max_dd * 100, 2)}%")
    print(
        f"📊 SUMMARY: {summary['total_trades']} trades | {summary['win_rate']:.1f}% win rate"
    )
    print("=" * 60)

    engine.print_gate_summary()

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)

    bias_path = out_dir / "htf_bias_debug_2023.csv"
    rows = getattr(engine, "_bias_debug_rows", [])

    if rows:
        fieldnames = ["time", "d1_bias", "h4_bias", "direction", "reason"]
        with bias_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"HTF bias debug written to {bias_path}")
    else:
        print("No HTF bias debug rows recorded.")

    return summary


if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Run TradingBot backtest")

    parser.add_argument(
        "--data",
        required=True,
        help="Path to CSV data file",
    )

    args = parser.parse_args()

    data_path = Path(args.data)

    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")

    print(f"\n📂 Loading data from: {data_path}")

    df = load_data(data_path)

    print(f"✅ Loaded {len(df)} candles")

    t0 = time.time()
    run_backtest(df)
    print(f"\n⏱ Total runtime: {time.time() - t0:.2f}s")