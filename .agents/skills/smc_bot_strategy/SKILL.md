---
name: smc_bot_strategy
description: Details of the Smart Money Concepts (SMC) trading bot configuration, Stop Loss refinements, Atlas Funded challenge trailing floor policies, dynamic Forex news filters, near-miss POI entries, ITH/ITL TP targets, and multi-symbol instances.
---

# SMC Bot Strategy & Customizations Summary

This document serves as a persistent knowledge base summarizing all strategy modifications, funded account safeguards, and multi-symbol configurations implemented in the bot. It is designed to help any AI agent or LLM (such as NotebookLM) instantly understand the codebase state and verify its alignment with the creator's video teachings.

---

## 1. Core SMC Trading Rules (ICT-Aligned)

* **Killzone Windows (America/New_York DST-Aware):**
  * **London session:** 02:00 - 05:00 NY Local Time (07:00 - 10:00 UTC during standard time).
  * **New York session:** 07:00 - 12:00 NY Local Time (12:00 - 17:00 UTC during standard time).
  * **Dead Zone / Asian session:** 00:00 - 02:00 NY is hard-blocked. Asian session is blocked globally due to wide broker spreads, low volume, and trap wicks on XAUUSD.
* **Structural Swing pivots:**
  * **Short-Term Highs (STH) / Lows (STL):** A 3-candle pivot where the middle high is higher than left/right highs, or middle low is lower than left/right lows.
  * **Intermediate-Term Highs (ITH) / Lows (ITL):** An STH flanked by a lower STH on both its left and right sides. An ITL is flanked by a higher STL on both sides.
* **Order Block (OB) & Fair Value Gap (FVG) Confluence:**
  * **OB alone or FVG alone is rejected** (retail trap). A valid trade signal requires an active M15 OB and an adjacent M5 FVG confluence area.
  * **50% Mean Threshold (MT) Rule:** If any candle body close penetrates past the 50% midpoint of the OB, the setup is immediately invalidated.

---

## 2. Stop Loss Safety & Mitigation Rules

* **Stop Loss Cushioning:**
  * Standard OB/FVG trades place the SL at the refined OB high/low with a `0.3x ATR` buffer (refined to prevent wide stop-outs).
  * Direct sweep trades (aggressive entry without CHoCH confirmation) place the SL at the sweep wick with a wider `0.8x ATR` buffer to withstand wicks.
* **Slippage & Spread Hard Cap:**
  * Enforces a minimum SL distance of **35 pips (3.5 points)** on Gold. If calculations result in a tighter SL, it is padded to 3.5 points to prevent premature stop-outs from broker spread spikes.

---

## 3. Near-Miss POI Entries

* **Rule Alignment:** Rigidly waiting for an exact touch of the HTF POI line misses massive momentum moves.
* **Implementation:** If price enters the Discount zone (below 0.5 Equilibrium dealing range) and shows a strong rejection, we allow the candidate M15 POI to align with the HTF POI within a **volatility-aware near-miss buffer of `1.5 * ATR`**.
* **Retest Entry:** The trade is executed on an M5 retest of the M15 OB / M5 FVG confluence area (which lies outside the HTF POI), allowing the bot to "take the trade" (उठा लेना) without waiting for a perfect tap of the HTF POI line itself.

---

## 4. Take Profit Placement (Fractal & Structural Extremes)

* **Trend-Following Setup:** When trading in alignment with the HTF bias, primary Take Profits target H4 structural extremes to maximize RR.
  * **Bullish trades:** Target the highest H4 Intermediate-Term High (ITH) above the entry price: `max(tp_erl, max_ith_above_entry)`.
  * **Bearish trades:** Target the lowest H4 Intermediate-Term Low (ITL) below the entry price: `min(tp_erl, min_itl_below_entry)`.
* **Counter-Trend Setup:** Target placement is conservative-fractal, capped at the next-higher timeframe (the 15-minute ERL or the nearest unmitigated HTF POI).

---

## 5. Funded Account drawdown policies (Atlas Funded $5K Challenge)

* **Trailed Daily Floor (5% Max Loss):** reculated at Midnight UTC as `max(previous_day_highest_balance, previous_day_highest_equity) * 0.95`. If equity hits or drops below this floor, all trades are closed, and the bot halts until the next daily reset.
* **Trailed Max Drawdown Floor (7% Overall Loss):** recalculated in real-time as `max(all_time_highest_balance, all_time_highest_equity) * 0.93`. If equity hits this trailing floor, the bot closes all trades and halts permanently.
* **Profit Target Lock ($5,200):** If balance hits or exceeds the $5,200 target (4%), it closes all positions, sends a Telegram alert, and halts permanently to lock in the challenge phase pass.
* **Risk Spacing:** Capital risk is divided across 14 to 20 trades by setting `RISK_PER_TRADE_PCT=0.25` in `.env` ($12.50 risk per trade).

---

## 6. Dynamic Multi-Symbol parallel framework

* **Dynamic Symbol Resolution:** Reads `SYMBOL` from `.env` (e.g. `SYMBOL=EURUSD`). Renames `resolve_gold_symbol()` to a generic `resolve_symbol()` to resolve suffixes (like `.pro`, `.raw`, `.a`, `.b`) on any broker.
* **Dynamic Contract Sizing:** Automatically queries MT5 symbol info at startup to retrieve the contract size (`trade_contract_size` e.g., 100,000 for EURUSD, 100 for XAUUSD) and dynamically updates the Position Sizer.
* **State & Log Separation:** Saves session state in `logs/session_state_{symbol}.json` and decisions audit logs in `logs/decisions/audit_{symbol}.jsonl` to prevent file conflicts when running parallel instances.
* **Universal news filter:** Economics news filter weekly JSON feed (`https://nfs.faireconomy.media/ff_calendar_thisweek.json`) automatically parses the symbol (e.g. `EURUSD` -> base `EUR` + quote `USD`) and blocks trades during news blackout periods (±15 mins) on both currencies.
