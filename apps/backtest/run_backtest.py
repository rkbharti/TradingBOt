import sys
import pathlib

# Reconfigure stdout/stderr encoding on Windows to prevent UnicodeEncodeError on terminal emoji prints
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure 'src' is on the path so 'tradingbot' package is importable
# regardless of how the script is invoked (python -m, direct, IDE, etc.)
_src_path = str(pathlib.Path(__file__).resolve().parents[2] / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

import pandas as pd
import csv
import json
from pathlib import Path
from bisect import bisect_left, bisect_right
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

# FIX #2: MIN_RR removed as a hardcoded constant.
# Old value was 1.5 — creator calls this "low expectation."
# RR minimum is now read from SignalEngineConfig.rr_min (currently 3.0).
# This ensures backtest and live bot use the SAME RR threshold.
# MIN_RR = 1.5  ← REMOVED

# ── Warmup: D1 candles needed before first signal ─────────────
# Creator uses 500 bars history (max_bars_back=500) on Daily
# window=50 needs 100 bars minimum for first confirmed pivot
# 120 used as safe buffer
D1_WARMUP_CANDLES = 120


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
    print("\nDEBUG CSV COLUMNS:")
    print(df.columns.tolist())
    if "date" in df.columns:
        df["datetime"] = pd.to_datetime(
            df["date"].astype(str).str.replace(".", "-", regex=False) + " " + df["time"].astype(str),
            errors="coerce"
        )
    else:
        df["datetime"] = pd.to_datetime(
            df["time"],
            errors="coerce"
        )
    df = df.dropna(subset=["datetime"])
    df.set_index("datetime", inplace=True)
    return df[["open", "high", "low", "close"]].sort_index()


def create_timeframes(df: pd.DataFrame):
    df_m1 = df.copy()
    if "time" not in df_m1.columns:
        df_m1["time"] = df_m1.index

    df_m5 = (
        df_m1.resample("5min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_m5["time"] = df_m5.index

    df_m15 = (
        df_m1.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_m15["time"] = df_m15.index

    df_h1 = (
        df_m1.resample("1h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_h1["time"] = df_h1.index

    df_h4 = (
        df_m1.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_h4["time"] = df_h4.index

    df_d1 = (
        df_m1.resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_d1["time"] = df_d1.index

    df_w1 = (
        df_m1.resample("1W")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_w1["time"] = df_w1.index

    return df_d1, df_h4, df_h1, df_m15, df_m5, df_w1  # ✅ Fix 1: Added H1


def scan_presession_pois_sim(m5_df: pd.DataFrame, ny_time) -> list:
    import pytz
    ny_tz = pytz.timezone("America/New_York")
    utc_tz = pytz.utc
    
    t_val = ny_time.hour + ny_time.minute / 60.0
    current_date = ny_time.date()
    
    valid_indices = []
    for idx in range(len(m5_df)):
        c_time_utc = m5_df["time"].iat[idx]
        if getattr(c_time_utc, "tzinfo", None) is not None:
            c_time_ny = c_time_utc.astimezone(ny_tz)
        else:
            c_time_ny = utc_tz.localize(c_time_utc).astimezone(ny_tz)
            
        if c_time_ny.date() == current_date:
            c_t = c_time_ny.hour + c_time_ny.minute / 60.0
            if 15.5 <= c_t <= 20.0:
                valid_indices.append(idx)
                
    if not valid_indices:
        return []
        
    pois = []
    for j in valid_indices:
        if j + 2 >= len(m5_df) or j + 2 not in valid_indices:
            continue
            
        # Check for Bullish FVG (displacement)
        if float(m5_df["low"].iat[j+2]) > float(m5_df["high"].iat[j]):
            pois.append({
                "type": "FVG",
                "high": float(m5_df["low"].iat[j+2]),
                "low": float(m5_df["high"].iat[j]),
                "direction": "bull",
                "timestamp": m5_df["time"].iat[j].isoformat()
            })
            # Bullish OB: last bearish candle at or before j
            for k in range(j, max(-1, j - 5), -1):
                if float(m5_df["close"].iat[k]) < float(m5_df["open"].iat[k]):
                    pois.append({
                        "type": "OB",
                        "high": float(m5_df["high"].iat[k]),
                        "low": float(m5_df["low"].iat[k]),
                        "direction": "bull",
                        "timestamp": m5_df["time"].iat[k].isoformat()
                    })
                    break
                    
        # Check for Bearish FVG (displacement)
        if float(m5_df["high"].iat[j+2]) < float(m5_df["low"].iat[j]):
            pois.append({
                "type": "FVG",
                "high": float(m5_df["low"].iat[j]),
                "low": float(m5_df["high"].iat[j+2]),
                "direction": "bear",
                "timestamp": m5_df["time"].iat[j].isoformat()
            })
            # Bearish OB: last bullish candle at or before j
            for k in range(j, max(-1, j - 5), -1):
                if float(m5_df["close"].iat[k]) > float(m5_df["open"].iat[k]):
                    pois.append({
                        "type": "OB",
                        "high": float(m5_df["high"].iat[k]),
                        "low": float(m5_df["low"].iat[k]),
                        "direction": "bear",
                        "timestamp": m5_df["time"].iat[k].isoformat()
                    })
                    break
                    
    # If no OB or FVG is formed, check for clean Swing Highs and Swing Lows as Liquidity Pools
    if not pois:
        for k in valid_indices:
            if k - 2 not in valid_indices or k + 2 not in valid_indices:
                continue
            is_swing_high = all(float(m5_df["high"].iat[k]) >= float(m5_df["high"].iat[x]) for x in [k-2, k-1, k+1, k+2])
            if is_swing_high:
                pois.append({
                    "type": "LIQUIDITY",
                    "high": float(m5_df["high"].iat[k]),
                    "low": float(m5_df["low"].iat[k]),
                    "direction": "bear",
                    "timestamp": m5_df["time"].iat[k].isoformat()
                })
            is_swing_low = all(float(m5_df["low"].iat[k]) <= float(m5_df["low"].iat[x]) for x in [k-2, k-1, k+1, k+2])
            if is_swing_low:
                pois.append({
                    "type": "LIQUIDITY",
                    "high": float(m5_df["high"].iat[k]),
                    "low": float(m5_df["low"].iat[k]),
                    "direction": "bull",
                    "timestamp": m5_df["time"].iat[k].isoformat()
                })
                
    # De-deduplicate
    seen_pois = set()
    unique_pois = []
    for p in pois:
        key = (p["type"], p["direction"], p["timestamp"])
        if key not in seen_pois:
            seen_pois.add(key)
            unique_pois.append(p)
            
    return unique_pois


# ─────────────────────────────────────────────────────────────
def _print_config_snapshot(engine: SignalEngine):
    cfg = engine.config
    print("\n" + "=" * 60)
    print("⚙️  ENGINE CONFIG SNAPSHOT")
    print("=" * 60)
    config_fields = [
        ("internal_swing_window",   "M5 CHoCH pivot window (creator=5)"),
        ("external_swing_window",   "HTF swing pivot window (creator=50)"),
        ("atr_period",              "ATR period"),
        ("min_atr_threshold",       "Min ATR for trade (volatility gate)"),
        ("sweep_atr_tolerance",     "Sweep wick tolerance (ATR multiplier)"),
        ("min_m5_candles",          "Min M5 candles required"),
        ("min_m15_candles",         "Min M15 candles required"),
        ("min_h4_candles",          "Min H4 candles required"),
        ("min_d1_candles",          "Min D1 candles required"),
        ("liquidity_lookback",      "Liquidity lookback window"),
        ("d1_weight",               "D1 bias weight"),
        ("h4_weight",               "H4 bias weight"),
    ]
    for field, description in config_fields:
        val  = getattr(cfg, field, "N/A")
        flag = ""
        if field == "internal_swing_window" and isinstance(val, int) and val != 5:
            flag = f"  ⚠️  Creator uses 5 — current={val}"
        if field == "external_swing_window" and isinstance(val, int) and val != 50:
            flag = f"  ⚠️  Creator uses 50 — current={val}"
        print(f"  {field:<30} = {val:<10}  # {description}{flag}")
    print(f"\n  {'D1_WARMUP_CANDLES':<30} = {D1_WARMUP_CANDLES:<10}  # D1 bars before first signal")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────
def _print_prop_report(
    capital, initial_capital, max_dd,
    daily_pnl, daily_start_equity, daily_min_equity, trade_dates,
):
    print("\n" + "=" * 60)
    print("🏦  PROP FIRM SIMULATION REPORT")
    print("=" * 60)

    profit_pct = (capital - initial_capital) / initial_capital * 100
    profit_hit = capital >= initial_capital * (1 + PROP_PROFIT_TARGET_PCT)
    print(f"\n📈 PROFIT TARGET ({PROP_PROFIT_TARGET_PCT*100:.0f}%)")
    print(f"   Result : {profit_pct:+.2f}%  →  {'✅ PASSED' if profit_hit else '❌ NOT REACHED'}")

    total_loss_safe = max_dd < PROP_MAX_TOTAL_LOSS_PCT
    print(f"\n📉 MAX TOTAL DRAWDOWN (limit {PROP_MAX_TOTAL_LOSS_PCT*100:.0f}%)")
    print(f"   Result : {max_dd*100:.2f}%  →  {'✅ SAFE' if total_loss_safe else '❌ BREACHED'}")

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

    daily_loss_safe = worst_day_pnl > -daily_loss_limit_abs and len(equity_breach_days) == 0
    print(f"\n🗓️  MAX DAILY LOSS (limit {daily_loss_limit_pct:.0f}% = ₹{daily_loss_limit_abs:,.0f})")
    print(f"   Worst day : {worst_day_date}  →  ₹{worst_day_pnl:+,.2f}  ({-worst_day_pct:.2f}%)")
    for bd, bdrop in equity_breach_days:
        print(f"   ⚠️  Equity breach : {bd}  ({bdrop:.2f}% intraday)")
    print(f"   Status : {'✅ SAFE' if daily_loss_safe else '❌ BREACHED'}")

    n_days   = len(trade_dates)
    days_met = n_days >= PROP_MIN_TRADING_DAYS
    print(f"\n📅 MINIMUM TRADING DAYS (required {PROP_MIN_TRADING_DAYS})")
    print(f"   Result : {n_days} days  →  {'✅ MET' if days_met else '❌ NOT MET'}")
    if trade_dates:
        print(f"   Dates  : {sorted(trade_dates)}")

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
def _print_diagnostic_report(engine, trade_log, data_year):
    print("\n" + "=" * 60)
    print(f"🔬  DIAGNOSTIC REPORT — {data_year}")
    print("=" * 60)

    if not trade_log:
        print("  No trades to analyze.")
        return

    bull_trades = [t for t in trade_log if t["direction"] == "BULLISH"]
    bear_trades = [t for t in trade_log if t["direction"] == "BEARISH"]
    bull_wins   = [t for t in bull_trades if t["outcome"] == "WIN"]
    bear_wins   = [t for t in bear_trades if t["outcome"] == "WIN"]

    print(f"\n📊 DIRECTION BREAKDOWN:")
    if bull_trades:
        print(f"   BULLISH : {len(bull_trades)} trades | {len(bull_wins)/len(bull_trades)*100:.1f}% WR")
    else:
        print(f"   BULLISH : 0 trades")
    if bear_trades:
        print(f"   BEARISH : {len(bear_trades)} trades | {len(bear_wins)/len(bear_trades)*100:.1f}% WR")
    else:
        print(f"   BEARISH : 0 trades")

    rr_values = [t["rr"] for t in trade_log if t.get("rr")]
    if rr_values:
        print(f"\n📐 RR ANALYSIS:")
        print(f"   Avg RR : {sum(rr_values)/len(rr_values):.2f}")
        print(f"   Min RR : {min(rr_values):.2f}")
        print(f"   Max RR : {max(rr_values):.2f}")

    monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trade_log:
        m = t["date"][:7]
        monthly[m]["trades"] += 1
        if t["outcome"] == "WIN":
            monthly[m]["wins"] += 1
        monthly[m]["pnl"] += t["pnl"]

    print(f"\n📅 MONTHLY BREAKDOWN:")
    print(f"   {'Month':<10} {'Trades':>7} {'WR':>8} {'PnL':>12}")
    print(f"   {'-'*40}")
    for month in sorted(monthly.keys()):
        m    = monthly[month]
        wr   = m["wins"] / m["trades"] * 100 if m["trades"] > 0 else 0
        flag = "✅" if m["pnl"] > 0 else "❌"
        print(f"   {month:<10} {m['trades']:>7} {wr:>7.1f}% {m['pnl']:>+12,.2f}  {flag}")

    rejection_counts = getattr(engine, "rejection_counts", {})
    total_rejections = sum(rejection_counts.values())
    total_signals    = total_rejections + len(trade_log)
    print(f"\n🚦 SIGNAL EFFICIENCY:")
    print(f"   Total signals : {total_signals:,}")
    print(f"   Trades taken  : {len(trade_log)}")
    if total_signals > 0:
        print(f"   Conversion    : {len(trade_log)/total_signals*100:.4f}%")

    max_cl = cc = 0
    for t in trade_log:
        cc = cc + 1 if t["outcome"] == "LOSS" else 0
        max_cl = max(max_cl, cc)
    max_cw = cw = 0
    for t in trade_log:
        cw = cw + 1 if t["outcome"] == "WIN" else 0
        max_cw = max(max_cw, cw)

    print(f"\n📉 STREAK ANALYSIS:")
    print(f"   Max consecutive losses : {max_cl}")
    print(f"   Max consecutive wins   : {max_cw}")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────
class DummyNewsFilter:
    def is_news_blackout(self, now_utc, symbol=None):
        return False, "BACKTEST_NO_NEWS"

def run_backtest(df: pd.DataFrame, data_label: str = "", start_date: str = None):
    engine       = SignalEngine()
    engine.news_filter = DummyNewsFilter()
    capital      = INITIAL_CAPITAL
    peak_capital = INITIAL_CAPITAL
    max_dd       = 0.0

    _print_config_snapshot(engine)

    active_trade     = None
    last_entry_price = None
    last_entry_dir   = None

    daily_pnl          = defaultdict(float)
    daily_start_equity = {}
    daily_min_equity   = defaultdict(lambda: float("inf"))
    trade_dates        = set()
    trade_log          = []

    logger = BacktestLogger(reset=False)
    run_id = str(uuid.uuid4())[:8]
    logger.start_run(run_id)

    df_d1, df_h4, df_h1, df_m15, df_m5, df_w1 = create_timeframes(df)  # ✅ Fix 1: Added H1

    m1_index  = list(df.index)
    m15_index = list(df_m15.index)
    h1_index  = list(df_h1.index)  # ✅ Fix 1: H1 index for bisect
    h4_index  = list(df_h4.index)
    d1_index  = list(df_d1.index)
    w1_index  = list(df_w1.index)

    print(f"\n📊 DATA QUALITY REPORT:")
    print(f"   M5  candles : {len(df_m5):,}")
    print(f"   M15 candles : {len(df_m15):,}")
    print(f"   H4  candles : {len(df_h4):,}")
    print(f"   D1  candles : {len(df_d1):,}")
    print(f"   W1  candles : {len(df_w1):,}")
    print(f"   Date range  : {df_m5.index[0].date()} → {df_m5.index[-1].date()}")

    # ✅ Find warmup point — where D1 has D1_WARMUP_CANDLES available
    warmup_start_idx = 200
    for idx in range(200, len(df_m5)):
        t      = df_m5.index[idx]
        d1_pos = bisect_left(d1_index, t)
        h4_pos = bisect_left(h4_index, t)
        if d1_pos >= D1_WARMUP_CANDLES and h4_pos >= 50:
            warmup_start_idx = idx
            break

    # ✅ If start_date given — skip to that date but warmup still respected
    eval_start_idx = warmup_start_idx
    if start_date:
        start_dt = pd.Timestamp(start_date)
        for idx in range(warmup_start_idx, len(df_m5)):
            if df_m5.index[idx] >= start_dt:
                eval_start_idx = idx
                break
        print(f"⏩ Signals evaluated from {start_date} "
              f"(warmup at bar {warmup_start_idx} = {df_m5.index[warmup_start_idx].date()})")
    else:
        print(f"⏩ Warmup complete at bar {warmup_start_idx} "
              f"({df_m5.index[warmup_start_idx].date()}) — evaluating from here")

    print(f"\n🚀 START BACKTEST | RUN_ID: {run_id} | CAPITAL: ₹{capital:,.0f}")
    print("=" * 60)

    cfg               = engine.config
    first_iter_logged = False
    asian_session_pois = []
    last_presession_reset_date = None

    for i in range(eval_start_idx, len(df_m5)):
        current_time = df_m5.index[i]
        candle       = df_m5.iloc[i]
        today_str    = str(current_time.date())

        if today_str not in daily_start_equity:
            daily_start_equity[today_str] = capital
        daily_min_equity[today_str] = min(daily_min_equity[today_str], capital)

        # Define completion-time check intervals
        m15_interval = pd.Timedelta(minutes=15)
        h1_interval  = pd.Timedelta(hours=1)    # ✅ Fix 1
        h4_interval  = pd.Timedelta(hours=4)
        d1_interval  = pd.Timedelta(days=1)
        w1_interval  = pd.Timedelta(days=7)

        # Use bisect_right to find the first candle starting after (current_time - interval).
        # This includes only candles that have fully completed at or before current_time.
        m15_end_idx = bisect_right(m15_index, current_time - m15_interval)
        h1_end_idx  = bisect_right(h1_index,  current_time - h1_interval)   # ✅ Fix 1
        h4_end_idx  = bisect_right(h4_index,  current_time - h4_interval)
        d1_end_idx  = bisect_right(d1_index,  current_time - d1_interval)
        w1_end_idx  = bisect_right(w1_index,  current_time - w1_interval)

        if (
            i           < cfg.min_m5_candles  or
            m15_end_idx < cfg.min_m15_candles or
            h4_end_idx  < 50                  or
            d1_end_idx  < D1_WARMUP_CANDLES
        ):
            continue

        # ✅ Slices aligned with creator's 500-bar max_bars_back, using completed end index
        m5  = df_m5.iloc[max(0, i       - 500) : i + 1]
        m15 = df_m15.iloc[max(0, m15_end_idx - 200) : m15_end_idx]
        h1  = df_h1.iloc[max(0, h1_end_idx  - 200) : h1_end_idx]   # ✅ Fix 1: H1 slice (200 H1 bars = ~8 days)
        h4  = df_h4.iloc[max(0, h4_end_idx  - 300)  : h4_end_idx]
        d1  = df_d1.iloc[max(0, d1_end_idx  - 300)  : d1_end_idx]
        w1  = df_w1.iloc[max(0, w1_end_idx  - 100)  : w1_end_idx]

        # ── ASIAN pre-session POI scanning ──
        import pytz
        ny_tz = pytz.timezone("America/New_York")
        current_time_ny = current_time.replace(tzinfo=pytz.utc).astimezone(ny_tz)
        t_val = current_time_ny.hour + current_time_ny.minute / 60.0
        current_date = current_time_ny.date()

        # 1. Reset check at 15:30 NY time daily
        if t_val >= 15.5:
            if last_presession_reset_date is None or last_presession_reset_date != current_date:
                asian_session_pois = []
                last_presession_reset_date = current_date

        # 2. Scanner simulation (15:30 to 20:00 NY)
        if 15.5 <= t_val < 20.0:
            pois = scan_presession_pois_sim(m5, current_time_ny)
            if pois:
                asian_session_pois = pois

        # ── Get M1 slice up to current_time (inclusive), keeping last 120 bars
        pos = bisect_right(m1_index, current_time)
        m1_slice = df.iloc[max(0, pos - 120) : pos]
        if not m1_slice.empty and "time" not in m1_slice.columns:
            m1_slice = m1_slice.copy()
            m1_slice["time"] = m1_slice.index

        if not first_iter_logged:
            print(f"[SLICE CHECK] m5={len(m5)} m15={len(m15)} "
                  f"h4={len(h4)} d1={len(d1)} w1={len(w1)} @ {current_time.date()}")
            first_iter_logged = True

        result = engine.evaluate(
            m5_df=m5, m15_df=m15, h1_df=h1,   # ✅ Fix 1: Pass H1 to evaluate
            h4_df=h4, d1_df=d1, w1_df=w1, now_utc=current_time,
            asian_session_pois=asian_session_pois, m1=m1_slice
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

            # FIX #2: Use engine config RR minimum (3.0) — not hardcoded 1.5.
            # Backtest now matches live bot exactly.
            if rr < cfg.rr_min:
                continue

            if last_entry_price is not None and last_entry_dir == direction:
                atr      = engine._calc_atr(m5, len(m5) - 1, cfg.atr_period)
                min_dist = (atr * 0.5) if atr else 0.5
                if abs(entry - last_entry_price) < min_dist:
                    continue

            # Resolve entry module to distinguish session
            entry_module = getattr(result, "entry_module", "GENERIC")
            entry_time_ny = current_time.replace(tzinfo=pytz.utc).astimezone(ny_tz)
            t_entry = entry_time_ny.hour + entry_time_ny.minute / 60.0
            
            if entry_module == "ASIAN_KZ":
                resolved_module = "ASIAN_KZ"
            elif 2.0 <= t_entry < 5.0:
                resolved_module = "LONDON_KZ"
            elif 7.0 <= t_entry < 12.0:
                resolved_module = "NY_KZ"
            else:
                resolved_module = f"OFF_KZ_{entry_module}"

            risk             = capital * RISK_PER_TRADE
            last_entry_price = entry
            last_entry_dir   = direction
            active_trade     = {
                "entry": entry, "sl": sl, "tp": tp,
                "direction": direction, "risk": risk, "rr": rr,
                "date": today_str, "open_time": str(current_time),
                "entry_module": resolved_module,
            }
            logger.log_trade_open(str(current_time), direction, entry, sl, tp, risk)
            print(f"\n[ENTRY] {current_time}")
            print(f"  {direction} @ {entry} | SL: {sl} | TP: {tp} | RR: {round(rr,2)} | Module: {resolved_module}")

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
                capital -= risk
                daily_pnl[today_str] -= risk
                trade_dates.add(today_str)
                daily_min_equity[today_str] = min(daily_min_equity[today_str], capital)
                dd = (peak_capital - capital) / peak_capital
                if dd > max_dd:
                    max_dd = dd
                logger.log_trade_close(str(current_time), "SL_HIT", -risk)
                print(f"[EXIT] {current_time} | LOSS | ₹{capital:,.2f} | DD: {max_dd*100:.2f}%")
                trade_log.append({
                    "date": active_trade["date"], "open_time": active_trade["open_time"],
                    "close_time": str(current_time), "direction": direction,
                    "entry": entry, "sl": sl, "tp": tp, "rr": round(rr, 2),
                    "outcome": "LOSS", "pnl": round(-risk, 2),
                    "entry_module": active_trade.get("entry_module", "GENERIC"),
                })
                active_trade = None
                continue

            if tp_hit:
                pnl = risk * rr
                capital += pnl
                daily_pnl[today_str] += pnl
                trade_dates.add(today_str)
                if capital > peak_capital:
                    peak_capital = capital
                logger.log_trade_close(str(current_time), "TP_HIT", pnl)
                print(f"[EXIT] {current_time} | WIN  | ₹{capital:,.2f} | Peak: ₹{peak_capital:,.2f}")
                trade_log.append({
                    "date": active_trade["date"], "open_time": active_trade["open_time"],
                    "close_time": str(current_time), "direction": direction,
                    "entry": entry, "sl": sl, "tp": tp, "rr": round(rr, 2),
                    "outcome": "WIN", "pnl": round(pnl, 2),
                    "entry_module": active_trade.get("entry_module", "GENERIC"),
                })
                active_trade = None

    summary = logger.finalize_run(capital, INITIAL_CAPITAL, max_dd)

    print("\n" + "=" * 60)
    if hasattr(engine, "_htf_poi_debug_totals"):
        print(f"[HTF POI DEBUG TOTALS] {engine._htf_poi_debug_totals}")
    if hasattr(engine, "_bias_filter_counts"):
        print(f"[BIAS FILTER COUNTS] {engine._bias_filter_counts}")
    print(f"💰 FINAL CAPITAL: ₹{capital:,.2f}")
    print(f"📉 MAX DRAWDOWN: {max_dd*100:.2f}%")
    print(f"📊 SUMMARY: {summary['total_trades']} trades | {summary['win_rate']:.1f}% win rate")
    print("=" * 60)

    engine.print_gate_summary()
    _print_prop_report(
        capital=capital, initial_capital=INITIAL_CAPITAL, max_dd=max_dd,
        daily_pnl=dict(daily_pnl), daily_start_equity=daily_start_equity,
        daily_min_equity=dict(daily_min_equity), trade_dates=trade_dates,
    )
    _print_diagnostic_report(engine, trade_log, data_label)

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = getattr(engine, "_bias_debug_rows", [])
    if rows:
        bias_path  = out_dir / f"htf_bias_debug_{data_label}.csv"
        fieldnames = ["time", "w1_bias", "d1_bias", "h4_bias",
                      "direction", "reason", "is_pullback"]
        with bias_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"HTF bias debug  → {bias_path}")

    if trade_log:
        trade_path   = out_dir / f"trade_log_{data_label}.csv"
        trade_fields = ["date", "open_time", "close_time", "direction",
                        "entry", "sl", "tp", "rr", "outcome", "pnl", "entry_module"]
        with trade_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade_fields)
            writer.writeheader()
            writer.writerows(trade_log)
        print(f"Trade log       → {trade_path}")

    rejection_counts = getattr(engine, "rejection_counts", {})
    gate_path = out_dir / f"gate_rejections_{data_label}.json"
    with gate_path.open("w") as f:
        json.dump(rejection_counts, f, indent=2)
    print(f"Gate rejections → {gate_path}")

    if hasattr(engine, "_htf_poi_debug_totals"):
        poi_path = out_dir / f"htf_poi_debug_{data_label}.json"
        with poi_path.open("w") as f:
            json.dump(engine._htf_poi_debug_totals, f, indent=2)
        print(f"HTF POI debug   → {poi_path}")

    return summary


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, time

    parser = argparse.ArgumentParser()
    parser.add_argument("--data",       required=True,  help="Path to CSV data file")
    parser.add_argument("--label",      required=False, default="",   help="Label e.g. 2023")
    parser.add_argument("--start_date", required=False, default=None,
                        help="Evaluate from this date e.g. 2023-06-01 (warmup still loads)")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")

    label = args.label or data_path.stem.replace("data_", "").replace("data", "")

    print(f"\n📂 Loading data from: {data_path}")
    df = load_data(data_path)
    print(f"✅ Loaded {len(df)} candles")

    t0 = time.time()
    run_backtest(df, data_label=label, start_date=args.start_date)
    print(f"\n⏱ Total runtime: {time.time() - t0:.2f}s")
    