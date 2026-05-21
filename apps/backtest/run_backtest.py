import pandas as pd
import csv
from pathlib import Path
from bisect import bisect_left
from tradingbot.strategy.smc.signal_engine import SignalEngine
from apps.backtest.backtest_logger import BacktestLogger
import uuid
from collections import defaultdict


INITIAL_CAPITAL = 1000000.0
RISK_PER_TRADE = 0.01

# ─────────────────────────────────────────────
# PROP FIRM RULES (Leveraged Turbo Trade, one-step)
# ─────────────────────────────────────────────
PROP_MAX_DAILY_LOSS_PCT   = 0.03   # 3% daily loss limit
PROP_MAX_TOTAL_LOSS_PCT   = 0.06   # 6% max drawdown
PROP_PROFIT_TARGET_PCT    = 0.06   # 6% profit target
PROP_MIN_TRADING_DAYS     = 3      # one-step; set higher only if Leveraged requires it
PROP_CONSISTENCY_MAX_PCT  = 1.00   # disable unless your account model has a consistency rule


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


def _print_prop_report(
    capital,
    initial_capital,
    max_dd,
    daily_pnl,       # dict: date_str -> float (closed trade pnl for that day)
    daily_min_equity, # dict: date_str -> float (lowest equity seen intraday)
    trade_dates,     # set of date strings where at least one trade closed
):
    """Print a prop-firm style pass/fail report."""

    print("\n" + "=" * 60)
    print("🏦  PROP FIRM SIMULATION REPORT")
    print("=" * 60)

    # ── 1. Profit Target ──────────────────────────────────────
    profit_pct = (capital - initial_capital) / initial_capital * 100
    target_pct = PROP_PROFIT_TARGET_PCT * 100
    profit_hit = capital >= initial_capital * (1 + PROP_PROFIT_TARGET_PCT)
    print(f"\n📈 PROFIT TARGET ({target_pct:.0f}%)")
    print(f"   Result : {profit_pct:+.2f}%  →  {'✅ PASSED' if profit_hit else '❌ NOT REACHED'}")

    # ── 2. Max Total Loss ─────────────────────────────────────
    total_loss_pct  = max_dd * 100
    total_loss_lim  = PROP_MAX_TOTAL_LOSS_PCT * 100
    total_loss_safe = max_dd < PROP_MAX_TOTAL_LOSS_PCT
    print(f"\n📉 MAX TOTAL DRAWDOWN (limit {total_loss_lim:.0f}%)")
    print(f"   Result : {total_loss_pct:.2f}%  →  {'✅ SAFE' if total_loss_safe else '❌ BREACHED — ACCOUNT BLOWN'}")

    # ── 3. Max Daily Loss ─────────────────────────────────────
    daily_loss_limit = initial_capital * PROP_MAX_DAILY_LOSS_PCT
    daily_loss_limit_pct = PROP_MAX_DAILY_LOSS_PCT * 100

    worst_day_pnl  = min(daily_pnl.values()) if daily_pnl else 0.0
    worst_day_date = min(daily_pnl, key=daily_pnl.get) if daily_pnl else "N/A"
    worst_day_pct  = abs(worst_day_pnl) / initial_capital * 100

    # equity-based daily breach: lowest intraday equity vs start-of-day equity
    equity_breach_days = []
    for d, low_eq in daily_min_equity.items():
        start_eq = initial_capital   # simplified: compare vs initial
        drop = (start_eq - low_eq) / initial_capital * 100
        if drop >= daily_loss_limit_pct:
            equity_breach_days.append((d, drop))

    daily_loss_safe = worst_day_pnl > -daily_loss_limit and len(equity_breach_days) == 0
    print(f"\n🗓️  MAX DAILY LOSS (limit {daily_loss_limit_pct:.0f}% = ₹{daily_loss_limit:,.0f})")
    print(f"   Worst closed-PnL day : {worst_day_date}  →  ₹{worst_day_pnl:+,.2f}  ({-worst_day_pct:.2f}%)")
    if equity_breach_days:
        for bd, bdrop in equity_breach_days:
            print(f"   ⚠️  Equity breach on  : {bd}  ({bdrop:.2f}% intraday drop)")
    print(f"   Status : {'✅ SAFE' if daily_loss_safe else '❌ DAILY LOSS RULE BREACHED'}")

    # ── 4. Minimum Trading Days ───────────────────────────────
    n_trading_days = len(trade_dates)
    min_days_met   = n_trading_days >= PROP_MIN_TRADING_DAYS
    print(f"\n📅 MINIMUM TRADING DAYS (required {PROP_MIN_TRADING_DAYS})")
    print(f"   Result : {n_trading_days} days  →  {'✅ MET' if min_days_met else '❌ NOT MET'}")
    if trade_dates:
        print(f"   Dates  : {sorted(trade_dates)}")

    # ── 5. Consistency Rule ───────────────────────────────────
    total_profit = sum(v for v in daily_pnl.values() if v > 0)
    if total_profit > 0 and daily_pnl:
        best_day_pnl  = max(daily_pnl.values())
        best_day_date = max(daily_pnl, key=daily_pnl.get)
        best_day_share = best_day_pnl / total_profit * 100
        consistency_ok = best_day_share <= PROP_CONSISTENCY_MAX_PCT * 100
        print(f"\n⚖️  CONSISTENCY (no single day > {PROP_CONSISTENCY_MAX_PCT*100:.0f}% of total profit)")
        print(f"   Best day : {best_day_date}  →  ₹{best_day_pnl:+,.2f}  ({best_day_share:.1f}% of total profit)")
        print(f"   Status   : {'✅ CONSISTENT' if consistency_ok else '❌ CONSISTENCY RULE BREACHED'}")
    else:
        consistency_ok = True
        print(f"\n⚖️  CONSISTENCY : N/A (no profitable days)")

    # ── 6. Overall Verdict ────────────────────────────────────
    all_passed = profit_hit and total_loss_safe and daily_loss_safe and min_days_met and consistency_ok
    print("\n" + "─" * 60)
    print(f"🏆  OVERALL PROP CHALLENGE VERDICT:")
    if all_passed:
        print("    ✅  CHALLENGE PASSED — Bot would survive this run")
    else:
        fails = []
        if not profit_hit:       fails.append("Profit target not reached")
        if not total_loss_safe:  fails.append("Max total drawdown breached")
        if not daily_loss_safe:  fails.append("Max daily loss breached")
        if not min_days_met:     fails.append("Minimum trading days not met")
        if not consistency_ok:   fails.append("Consistency rule breached")
        for f in fails:
            print(f"    ❌  {f}")
    print("─" * 60)


def run_backtest(df):
    engine = SignalEngine()
    capital = INITIAL_CAPITAL
    active_trade = None
    last_entry_price = None

    peak_capital = INITIAL_CAPITAL
    max_dd = 0.0

    # ── Prop firm tracking ─────────────────────────────────────
    daily_pnl         = defaultdict(float)   # date → net closed pnl
    daily_min_equity  = defaultdict(lambda: float("inf"))  # date → lowest equity
    trade_dates       = set()                # dates where a trade closed

    logger = BacktestLogger(reset=False)
    run_id = str(uuid.uuid4())[:8]
    logger.start_run(run_id)

    df_d1, df_h4, df_m15, df_m5 = create_timeframes(df)

    m15_index = list(df_m15.index)
    h4_index  = list(df_h4.index)
    d1_index  = list(df_d1.index)

    print(f"\n🚀 START BACKTEST | RUN_ID: {run_id} | CAPITAL: ₹{capital}")
    print("=" * 60)

    start_idx = 200
    cfg = engine.config

    for i in range(start_idx, len(df_m5)):
        current_time = df_m5.index[i]
        candle       = df_m5.iloc[i]
        today_str    = str(current_time.date())

        # track lowest intraday equity for daily loss check
        daily_min_equity[today_str] = min(daily_min_equity[today_str], capital)

        m15_idx = bisect_left(m15_index, current_time)
        h4_idx  = bisect_left(h4_index,  current_time)
        d1_idx  = bisect_left(d1_index,  current_time)

        if (
            i < cfg.min_m5_candles
            or m15_idx < cfg.min_m15_candles
            or h4_idx  < cfg.min_h4_candles
            or d1_idx  < cfg.min_d1_candles
        ):
            continue

        m5  = df_m5.iloc[max(0, i - 500) : i + 50]
        m15 = df_m15.iloc[max(0, m15_idx - 100) : m15_idx]
        h4  = df_h4.iloc[max(0, h4_idx - 50)  : h4_idx]
        d1  = df_d1.iloc[max(0, d1_idx - 30)  : d1_idx]

        result = engine.evaluate(
            m5_df=m5,
            m15_df=m15,
            h4_df=h4,
            d1_df=d1,
            now_utc=current_time,
        )

        # =====================================================
        # 🔵 ENTRY
        # =====================================================
        if result.action == "ENTER" and active_trade is None:
            entry     = result.entry_price
            sl        = result.sl_price
            tp        = result.tp_price
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
                "entry":     entry,
                "sl":        sl,
                "tp":        tp,
                "direction": direction,
                "risk":      risk,
            }

            logger.log_trade_open(
                str(current_time), direction, entry, sl, tp, risk,
            )

            print("\n[ENTRY]")
            print(f"{direction} @ {entry} | SL: {sl} | TP: {tp} | RR: {round(rr, 2)}")

        # =====================================================
        # 🔴 EXIT
        # =====================================================
        if active_trade is not None:
            direction = active_trade["direction"]
            entry     = active_trade["entry"]
            sl        = active_trade["sl"]
            tp        = active_trade["tp"]
            risk      = active_trade["risk"]

            # ── STOP LOSS ──────────────────────────────────
            if (direction == "BULLISH" and candle["low"] <= sl) or (
                direction == "BEARISH" and candle["high"] >= sl
            ):
                capital -= risk
                daily_pnl[today_str]        -= risk
                trade_dates.add(today_str)
                daily_min_equity[today_str]  = min(daily_min_equity[today_str], capital)

                if capital < peak_capital:
                    dd = (peak_capital - capital) / peak_capital
                    if dd > max_dd:
                        max_dd = dd

                logger.log_trade_close(str(current_time), "SL_HIT", -risk)
                print(
                    f"[EXIT] {current_time} | LOSS | ₹{round(capital, 2)} | DD: {round(max_dd * 100, 2)}%"
                )
                active_trade = None
                continue

            # ── TAKE PROFIT ────────────────────────────────
            if (direction == "BULLISH" and candle["high"] >= tp) or (
                direction == "BEARISH" and candle["low"] <= tp
            ):
                rr  = abs(tp - entry) / abs(entry - sl)
                pnl = risk * rr
                capital += pnl
                daily_pnl[today_str]       += pnl
                trade_dates.add(today_str)

                if capital > peak_capital:
                    peak_capital = capital

                logger.log_trade_close(str(current_time), "TP_HIT", pnl)
                print(
                    f"[EXIT] {current_time} | WIN | ₹{round(capital, 2)} | Peak: ₹{round(peak_capital, 2)}"
                )
                active_trade = None

    summary = logger.finalize_run(capital, INITIAL_CAPITAL, max_dd)

    # ── Standard summary ──────────────────────────────────────
    print("\n" + "=" * 60)
    if hasattr(engine, "_htf_poi_debug_totals"):
        print(f"[HTF POI DEBUG TOTALS] {engine._htf_poi_debug_totals}")
    print(f"💰 FINAL CAPITAL: ₹{round(capital, 2)}")
    print(f"📉 MAX DRAWDOWN: {round(max_dd * 100, 2)}%")
    print(
        f"📊 SUMMARY: {summary['total_trades']} trades | {summary['win_rate']:.1f}% win rate"
    )
    print("=" * 60)

    engine.print_gate_summary()

    # ── Prop firm report ──────────────────────────────────────
    _print_prop_report(
        capital=capital,
        initial_capital=INITIAL_CAPITAL,
        max_dd=max_dd,
        daily_pnl=dict(daily_pnl),
        daily_min_equity=dict(daily_min_equity),
        trade_dates=trade_dates,
    )

    # ── HTF bias debug CSV ────────────────────────────────────
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
    parser.add_argument("--data", required=True, help="Path to CSV data file")
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
