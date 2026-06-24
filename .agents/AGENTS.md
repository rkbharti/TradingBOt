# SMC Trading Bot Project Rules

## 1. Strategy Constraints
- **Stop Loss Safety:** Always ensure that Stop Loss calculations place standard OB/FVG trades with a `0.3x ATR` buffer and sweep-based trades with a `0.8x ATR` buffer. Never remove the minimum SL distance cap of **35 pips (3.5 points)** on Gold.
- **Mean Threshold (MT) Breach:** When scanning candle bodies, if any body close goes past the 50% midpoint of the OB, invalidate the POI.
- **Near-Miss POIs:** Allow candidate M15 POIs that are within `1.5 * ATR` near-miss buffer of the HTF POI to align as valid.
- **TP structural Extremes:** Trend-following Take Profit targets must query H4 Intermediate-Term Highs (ITH) for Bullish setups and Intermediate-Term Lows (ITL) for Bearish setups.
- **Killzones:** Strictly use New York Local Time (America/New_York DST-aware) for London (02:00 - 05:00 NY), New York (07:00 - 12:00 NY), and Asian session checks. Hard block 00:00-02:00 NY Dead Zone only. Asian KZ (20:00-00:00 NY) is a valid entry window.

## 2. Risk Safeguards
- **Daily Floor Trailing:** Recalculated at Midnight UTC as `max(previous_day_highest_balance, previous_day_highest_equity) * 0.95`.
- **Max Trailing Floor:** Recalculated in real-time as `max(all_time_highest_balance, all_time_highest_equity) * 0.93`.
- **Profit Target halt:** Lock in challenge passes when account balance >= $5,200 (for $5K challenge) or 4% profit.

## 3. Dynamic Multi-Symbol parallel Framework
- **Dynamic Symbol Resolution:** Always resolve the active trading symbol from the environment `SYMBOL` variable and update position sizer contract size dynamically from MT5.
- **Separate Files:** Maintain dynamic names for state (`logs/session_state_{symbol}.json`) and audit logs (`logs/decisions/audit_{symbol}.jsonl`).
- **Dynamic News Blackout:** Extract base and quote currencies from the active symbol (e.g. `EUR` and `USD` from `EURUSD`) and block news events for both.
