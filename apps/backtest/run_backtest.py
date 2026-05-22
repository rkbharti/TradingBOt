import pandas as pd
import csv
from pathlib import Path
from bisect import bisect_left
from tradingbot.strategy.smc.signal_engine import SignalEngine
from apps.backtest.backtest_logger import BacktestLogger
import uuid
from collections import defaultdict


INITIAL_CAPITAL          = 1_000_000.0
RISK_PER_TRADE           = 0.005

# ── Prop Firm Rules ───────────────────────────────────────────
PROP_MAX_DAILY_LOSS_PCT  = 0.03
PROP_MAX_TOTAL_LOSS_PCT  = 0.06
PROP_PROFIT_TARGET_PCT   = 0.06
PROP_MIN_TRADING_DAYS    = 3
PROP_CONSISTENCY_MAX_PCT = 1.00
MIN_RR                   = 1.5


# ─────────────────────────────────────────────────────────────
def load_data(path: Path) -> pd.DataFrame:
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
    return df[["open", "high", "low", "close"]].sort_index()


def create_timeframes(df: pd.DataFrame):
    df_m5 = df.copy()

    df_m15 = (
        df_m5.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_h4 = (
        df_m5.resample("4h")          # ✅ No arbitrary offset
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_d1 = (
        df_m5.resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    # ✅ W1 added — required for _step_htf_bias W1 tier
    df_w1 = (
        df_m5.resample("1W")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    return df_d1, df_h4, df_m15, df_m5, df_w1


# ─────────────────────────────────────────────────────────────
def _print_prop_report(
    capital: float,
    initial_capital: float,
    max_dd: float,
    daily_pnl: dict,
    daily_start_equity: dict,   # ✅ start-of-day equity per date
    daily_min_equity: dict,
    trade_dates: set,
):
    print("\n" + "=" * 60)
    print("🏦  PROP FIRM SIMULATION REPORT")
    print("=" * 60)

    # 1. Profit Target
    profit_pct = (capital - initial_capital) / initial_capital * 100
    profit_hit = capital >= initial_capital * (1 + PROP_PROFIT_TARGET_PCT)
    print(f"\n📈 PROFIT TARGET ({PROP_PROFIT_TARGET_PCT*100:.0f}%)")
    print(f"   Result : {profit_pct:+.2f}%  →  {'✅ PASSED' if profit_hit else '❌ NOT REACHED'}")

    # 2. Max Total Drawdown
    total_loss_safe = max_dd < PROP_MAX_TOTAL_LOSS_PCT
    print(f"\n📉 MAX TOTAL DRAWDOWN (limit {PROP_MAX_TOTAL_LOSS_PCT*100:.0f}%)")
    print(f"   Result : {max_dd*100:.2f}%  →  {'✅ SAFE' if total_loss_safe else '❌ BREACHED'}")

    # 3. Max Daily Loss — ✅ compare vs start-of-day equity
    daily_loss_limit_pct = PROP_MAX_DAILY_LOSS_PCT * 100
    daily_loss_limit_abs = initial_capital * PROP_MAX_DAILY_LOSS_PCT

    worst_day_pnl  = min(daily_pnl.values()) if daily_pnl else 0.0
    worst_day_date = min(daily_pnl, key=daily_pnl.get) if daily_pnl else "N/A"
    worst_day_pct  = abs(worst_day_pnl) / initial_capital * 100

    equity_breach_days = []
    for d, low_eq in daily_min_equity.items():
        start_eq = daily_start_equity.get(d, initial_capital)
        drop_pct = (start_eq - low_eq) / initial_capital * 100
        if drop_pct >= daily_loss_limit_pct:
            equity_breach_days.append((d, drop_pct))

    daily_loss_safe = (
        worst_day_pnl > -daily_loss_limit_abs and
        len(equity_breach_days) == 0
    )
    print(f"\n🗓️  MAX DAILY LOSS (limit {daily_loss_limit_pct:.0f}% = ₹{daily_loss_limit_abs:,.0f})")
    print(f"   Worst day : {worst_day_date}  →  ₹{worst_day_pnl:+,.2f}  ({-worst_day_pct:.2f}%)")
    for bd, bdrop in equity_breach_days:
        print(f"   ⚠️  Equity breach : {bd}  ({bdrop:.2f}% intraday)")
    print(f"   Status : {'✅ SAFE' if daily_loss_safe else '❌ BREACHED'}")

    # 4. Minimum Trading Days
    n_days    = len(trade_dates)
    days_met  = n_days >= PROP_MIN_TRADING_DAYS
    print(f"\n📅 MINIMUM TRADING DAYS (required {PROP_MIN_TRADING_DAYS})")
    print(f"   Result : {n_days} days  →  {'✅ MET' if days_met else '❌ NOT MET'}")
    if trade_dates:
        print(f"   Dates  : {sorted(trade_dates)}")

    # 5. Consistency
    total_profit = sum(v for v in daily_pnl.values() if v > 0)
    if total_profit > 0 and daily_pnl:
        best_day_pnl   = max(daily_pnl.values())
        best_day_date  = max(daily_pnl, key=daily_pnl.get)
        best_day_share = best_day_pnl / total_profit * 100
        consistency_ok = best_day_share <= PROP_CONSISTENCY_MAX_PCT * 100
        print(f"\n⚖️  CONSISTENCY (no single day > {PROP_CONSISTENCY_MAX_PCT*100:.0f}% of total profit)")
        print(f"   Best day : {best_day_date}  →  ₹{best_day_pnl:+,.2f}  ({best_day_share:.1f}%)")
        print(f"   Status   : {'✅ CONSISTENT' if consistency_ok else '❌ BREACHED'}")
    else:
        consistency_ok = True
        print("\n⚖️  CONSISTENCY : N/A")

    # 6. Verdict
    all_passed = all([profit_hit, total_loss_safe, daily_loss_safe, days_met, consistency_ok])
    print("\n" + "─" * 60)
    print("🏆  OVERALL PROP CHALLENGE VERDICT:")
    if all_passed:
        print("    ✅  CHALLENGE PASSED")
    else:
        for cond, msg in [
            (profit_hit,      "Profit target not reached"),
            (total_loss_safe, "Max drawdown breached"),
            (daily_loss_safe, "Daily loss rule breached"),
            (days_met,        "Minimum trading days not met"),
            (consistency_ok,  "Consistency rule breached"),
        ]:
            if not cond:
                print(f"    ❌  {msg}")
    print("─" * 60)


# ─────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame):
    engine  = SignalEngine()
    capital = INITIAL_CAPITAL

    active_trade     = None
    last_entry_price = None
    last_entry_dir   = None

    peak_capital = INITIAL_CAPITAL
    max_dd       = 0.0

    daily_pnl          = defaultdict(float)
    daily_start_equity = {}                          # ✅ track per-day start equity
    daily_min_equity   = defaultdict(lambda: float("inf"))
    trade_dates        = set()

    logger = BacktestLogger(reset=False)
    run_id = str(uuid.uuid4())[:8]
    logger.start_run(run_id)

    df_d1, df_h4, df_m15, df_m5, df_w1 = create_timeframes(df)  # ✅ W1 added

    m15_index = list(df_m15.index)
    h4_index  = list(df_h4.index)
    d1_index  = list(df_d1.index)
    w1_index  = list(df_w1.index)

    print(f"\n🚀 START BACKTEST | RUN_ID: {run_id} | CAPITAL: ₹{capital:,.0f}")
    print("=" * 60)

    cfg       = engine.config
    start_idx = 200

    for i in range(start_idx, len(df_m5)):
        current_time = df_m5.index[i]
        candle       = df_m5.iloc[i]
        today_str    = str(current_time.date())

        # ✅ Record start-of-day equity once per day
        if today_str not in daily_start_equity:
            daily_start_equity[today_str] = capital

        daily_min_equity[today_str] = min(daily_min_equity[today_str], capital)

        m15_idx = bisect_left(m15_index, current_time)
        h4_idx  = bisect_left(h4_index,  current_time)
        d1_idx  = bisect_left(d1_index,  current_time)
        w1_idx  = bisect_left(w1_index,  current_time)

        if (
            i          < cfg.min_m5_candles  or
            m15_idx    < cfg.min_m15_candles or
            h4_idx     < cfg.min_h4_candles  or
            d1_idx     < cfg.min_d1_candles
        ):
            continue

        # ✅ FIX: i+1 not i+50 — no look-ahead bias
        m5  = df_m5.iloc[max(0, i - 500) : i + 1]
        m15 = df_m15.iloc[max(0, m15_idx - 100) : m15_idx]
        h4  = df_h4.iloc[max(0,  h4_idx  - 50)  : h4_idx]
        d1  = df_d1.iloc[max(0,  d1_idx  - 30)  : d1_idx]
        w1  = df_w1.iloc[max(0,  w1_idx  - 20)  : w1_idx]  # ✅ W1 slice

        result = engine.evaluate(
            m5_df=m5,
            m15_df=m15,
            h4_df=h4,
            d1_df=d1,
            w1_df=w1,             # ✅ passed to engine
            now_utc=current_time,
        )

        # ── ENTRY ────────────────────────────────────────────
        if result.action == "ENTER" and active_trade is None:
            entry     = result.entry_price
            sl        = result.sl_price
            tp        = result.tp_price
            direction = result.direction

            if not all([entry, sl, tp, direction]):
                continue

            risk_pts = abs(entry - sl)
            if risk_pts == 0:
                continue

            rr = abs(tp - entry) / risk_pts
            if rr < MIN_RR:
                continue

            # ✅ FIX: ATR-based duplicate filter — not hardcoded 0.5
            if last_entry_price is not None and last_entry_dir == direction:
                atr = engine._calc_atr(m5, len(m5) - 1, cfg.atr_period)
                min_dist = (atr * 0.5) if atr else 0.5
                if abs(entry - last_entry_price) < min_dist:
                    continue

            risk          = capital * RISK_PER_TRADE
            last_entry_price = entry
            last_entry_dir   = direction

            active_trade = {
                "entry":     entry,
                "sl":        sl,
                "tp":        tp,
                "direction": direction,
                "risk":      risk,
                "rr":        rr,
            }

            logger.log_trade_open(
                str(current_time), direction, entry, sl, tp, risk,
            )
            print(f"\n[ENTRY] {current_time}")
            print(f"  {direction} @ {entry} | SL: {sl} | TP: {tp} | RR: {round(rr, 2)}")

        # ── EXIT ─────────────────────────────────────────────
        if active_trade is not None:
            direction = active_trade["direction"]
            entry     = active_trade["entry"]
            sl        = active_trade["sl"]
            tp        = active_trade["tp"]
            risk      = active_trade["risk"]
            rr        = active_trade["rr"]

            sl_hit = (
                (direction == "BULLISH" and candle["low"]  <= sl) or
                (direction == "BEARISH" and candle["high"] >= sl)
            )
            tp_hit = (
                (direction == "BULLISH" and candle["high"] >= tp) or
                (direction == "BEARISH" and candle["low"]  <= tp)
            )

            if sl_hit:
                capital                    -= risk
                daily_pnl[today_str]       -= risk
                trade_dates.add(today_str)
                daily_min_equity[today_str] = min(daily_min_equity[today_str], capital)

                dd = (peak_capital - capital) / peak_capital
                if dd > max_dd:
                    max_dd = dd

                logger.log_trade_close(str(current_time), "SL_HIT", -risk)
                print(f"[EXIT] {current_time} | LOSS | ₹{capital:,.2f} | DD: {max_dd*100:.2f}%")
                active_trade = None
                continue

            if tp_hit:
                pnl                  = risk * rr
                capital             += pnl
                daily_pnl[today_str] += pnl
                trade_dates.add(today_str)

                if capital > peak_capital:
                    peak_capital = capital

                logger.log_trade_close(str(current_time), "TP_HIT", pnl)
                print(f"[EXIT] {current_time} | WIN  | ₹{capital:,.2f} | Peak: ₹{peak_capital:,.2f}")
                active_trade = None

    summary = logger.finalize_run(capital, INITIAL_CAPITAL, max_dd)

    # ── Summary ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    if hasattr(engine, "_htf_poi_debug_totals"):
        print(f"[HTF POI DEBUG TOTALS] {engine._htf_poi_debug_totals}")
    print(f"💰 FINAL CAPITAL: ₹{capital:,.2f}")
    print(f"📉 MAX DRAWDOWN: {max_dd*100:.2f}%")
    print(f"📊 SUMMARY: {summary['total_trades']} trades | {summary['win_rate']:.1f}% win rate")
    print("=" * 60)

    engine.print_gate_summary()

    _print_prop_report(
        capital=capital,
        initial_capital=INITIAL_CAPITAL,
        max_dd=max_dd,
        daily_pnl=dict(daily_pnl),
        daily_start_equity=daily_start_equity,  # ✅
        daily_min_equity=dict(daily_min_equity),
        trade_dates=trade_dates,
    )

    # ── Bias debug CSV ────────────────────────────────────────
    out_dir  = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    bias_path = out_dir / "htf_bias_debug_2023.csv"
    rows      = getattr(engine, "_bias_debug_rows", [])

    if rows:
        # ✅ Updated fieldnames include w1_bias and is_pullback
        fieldnames = ["time", "w1_bias", "d1_bias", "h4_bias",
                      "direction", "reason", "is_pullback"]
        with bias_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"HTF bias debug written to {bias_path}")

    return summary


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, time

    parser = argparse.ArgumentParser()
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