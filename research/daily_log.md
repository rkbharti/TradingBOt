# TradingBot Observation & Fix Log

**Repository:** rkbharti-tradingbot — SMC / Guardeer Methodology

---

## 2026-01-25 — Observation Phase Initiated

### Work Done

- Initialized research and observation workflow.
- Audited POI logic for live causality and removed look-ahead bias.
- Verified Order Block (OB) identification fixes.
- Started unattended overnight run for baseline data collection.

### Bot Status

- Mode: DRY_RUN
- Session Coverage: NY session into market close
- Observation logging: ENABLED

### Notes

- Bot intentionally left running overnight.
- No execution logic modified during observation phase.
- Shutdown summary captured automatically on graceful stop.

---

## 2026-01-26 — Observation Phase (Day 2)

### Status (09:27 PM IST)

- Bot running continuously.
- No manual restarts or interruptions.
- Monitoring stability and crash-resilience.

### Focus for the Day

- Collect full MONDAY trading data (London + NY sessions).
- Asian session intentionally excluded from execution.
- Observe:
  - Which Order Blocks are formed.
  - Which OBs are tapped but ignored.
  - Which OBs lead to confirmation states.

### Notes

- Bot left running without manual intervention.
- Primary goal: collect clean behavioral evidence, not profitability.
- No strategy or execution changes introduced today.

### End of Day

- Bot continues running without manual intervention.

---

## 2026-01-27 — Observation Phase (Day 3) — Infrastructure Upgrade

### Work Done

- **Passive Logger Integration:** Implemented `OBObservationLogger` and wired it into the main loop.
- **Safety Hardening:** Wrapped logger calls in `try/except` so observation cannot crash trading.
- **Bug Fix:** Corrected method mismatch (`.log` vs `.log_event`) in the observation layer.
- **Verification:**
  - Confirmed logs written to `research/ob_observations.json`.
  - Verified timestamp consistency (local IST vs UTC in logs).
  - Confirmed bot identifies PREMIUM zones and enters “Stalking Mode” (waiting for M5 CHoCH).

### Bot Status

- Mode: DRY_RUN
- Behavior: Stalking / Waiting for confirmation.
- Health: Stable. Analysis loop, zone calculation, and dashboard webhooks are functioning.

### Repository Maintenance

- Added a `.gitignore` to exclude venv, cache, and log files.
- Kept commits atomic:
  1. Observation logger tool.
  2. Integration into `main.py`.

---

## 2026-01-28 — Observation Phase (Day 4) — Validation & Signal Integrity

### Work Done

- Validated `OBObservationLogger` under live market conditions.
- Verified long-run stability: no crashes, freezes, or leaks.
- Reviewed dashboard: confirmed manual MT5 trades are intentionally not tracked yet.
- Confirmed design: only bot-initiated positions are tracked in the dashboard.

### Key Findings

- OB observation logging is safe and non-intrusive.
- Manual trades missing from dashboard is expected by design, not a bug.
- Bot correctly remains in **Stalking Mode** in PREMIUM zones without premature execution.

### Focus for the Day

- Ensure observation logs:
  - Capture enough context for future Decision / Extreme / Trap classification.
  - Explain why trades are NOT taken (analysis-state logging).
- No changes to strategy or execution logic.
- Continue data collection through London + NY.

### End of Day

- Bot continues running unattended.
- Observation layer confirmed production-safe.

---

## 2026-02-03 — PHASE 1 Fixes: IDM Sweep Detection

### Fix 1.1 — SWEEPWRONGDIRECTION Bug

- Corrected bullish IDM wick evaluation so “lower” wick sweeps are classified properly.
- Eliminated false `SWEEPWRONGDIRECTION` rejections for valid IDM sweeps.

### Fix 1.2 — IDM Sweep Confirmation Integration

- Passed the correct IDM type into sweep confirmation logic.
- Ensured IDM sweep confirmation no longer rejects valid sweeps due to direction mismatch.

### Fix 1.3 — Reduced Fractal Lag

- Reduced fractal confirmation lag from 2 candles to 1.
- Result: IDM detection becomes ~1 bar faster, reducing missed opportunities around HTF POIs.

### Status

- All Phase 1 tests passed (`test_phase1_fixes.py`).
- IDM detection now consistent and reliable; `IDM swept: True` correctly appears in logs instead of `SWEEPWRONGDIRECTION`.

---

## 2026-02-03 — PHASE 2 Fixes: Type 1 Entry & Confirmation Logic

### Fix 2.1 — Type 1 Direct Entry (Extreme POIs)

- Added **Type 1** entry path for Extreme POIs (zone_strength ≥ 70%):
  - Allows direct entries at Extreme Discount / Premium zones once IDM is swept.
  - Does not require M5 CHoCH for Extreme POIs.
- Existing **Type 2** logic (confirmation entries) preserved:
  - Requires M5 CHoCH + IDM swept + minimum zone_strength ≥ 30%.

### Fix 2.2 — Confirmation Flag Reset

- Ensured `waiting_for_confirmation` resets to `False` after trade execution (Type 1 or Type 2).
- Prevents the bot from getting stuck in permanent “waiting for M5 CHoCH confirmation” states.

### Status

- `test_phase2_integration.py` passed all checks.
- Live logic simulation confirmed:
  - With BEARISH bias, PREMIUM zone_strength = 75, IDM swept = True, CHoCH = False → Final signal: `SELL`, Reason: `Type 1 Entry: Extreme Premium (>70%) + IDM Swept (Direct)`.

---

## 2026-02-03 — PHASE 3.1 Fixes: POI Hierarchy (Decision / Extreme / Trap)

### Fix 3.1 — Order Block Hierarchy

- Updated `finalize_pois()` to:
  - Pre-validate all OBs (`is_valid_basic`).
  - Sort valid bullish and bearish OBs by price:
    - Highest bullish & lowest bearish: **DECISION** blocks (closest to current price).
    - Lowest bullish & highest bearish: **EXTREME** blocks (outer structural extremes).
    - All other valid OBs: classified as **TRAP**.
- Applied hierarchy tags:
  - `block_class` ∈ {`DECISION`, `EXTREME`, `TRAP`, `INVALID`}.
- Enforced:
  - `permission_to_trade = False` for `TRAP` blocks with reason code `SMART_MONEY_TRAP_MIDDLE_OB`.

### Status

- `test_phase3_poi.py` validation passed.
- OB logs now contain `block_class` with clear separation of DECISION/EXTREME vs TRAP.
- Traps are systematically filtered from trade candidates.

---

## 2026-02-03 — PHASE 3.2 Fixes: Liquidity-Based Targets

### Fix 3.2 — Liquidity-Based TP/SL

- Replaced pure ATR-based TP/SL with SMC-aligned logic:
  - **Stop Loss (SL):**
    - For BUY: below last 1–3 swing lows (structural low) with a small buffer.
    - For SELL: above last 1–3 swing highs (structural high) with a small buffer.
    - ATR-based fallback retained if swing points missing.
  - **Take Profit (TP):**
    - For BUY: nearest buy-side liquidity above entry (e.g., SWING_HIGH, EQUAL_HIGHS).
    - For SELL: nearest sell-side liquidity below entry (e.g., SWING_LOW, EQUAL_LOWS).
    - ATR-based fallback retained when no liquidity levels are found.
- Added debug logs to show whether TP/SL used:
  - Structural levels + liquidity pools, or
  - ATR fallbacks.

### Status

- `test_phase3_liquidity.py` passed (import + structural and liquidity logic verified).
- Bot now targets institutional liquidity rather than arbitrary ATR multiples.

---

## 2026-02-03 — IdeaMemory System (Current Status)

### Behavior Today

- `IdeaMemory` still loads and saves basic loss memory from `ideamemory.json`.
- It currently tracks setups by a coarse key (`direction_zone_session`).
- This system is recognized as **retail-style revenge-trading protection**, not SMC-aligned learning.

### Short-Term Decision

- For Week 1 of live testing, IdeaMemory checks are either disabled or not used for blocking entries.
- Goal: avoid blocking valid Extreme/Decision POI setups due to overly coarse memory keys.

### Future Plan

- After competition/paper-trade phase:
  - Redesign memory system around:
    - OB-level performance metrics (per OB ID and `block_class`).
    - Structure-based invalidation (clear memory after BOS/CHoCH, not time).
    - Loss-type classification (TRAP_OB vs SL_HUNTED vs STRUCTURAL_BREAK).
  - Integrate with POI hierarchy and liquidity context for truly SMC-aligned “brain”.

---

## Current Bot Status (End of 2026-02-03 Session)

- Mode: DRY_RUN (validation / monitoring)
- Structure:
  - Multi-timeframe fractals and MTF bias working.
  - IDM detection and sweep confirmation fixed.
  - Type 1 (Extreme) and Type 2 (Confirmation) entries wired in.
  - POI hierarchy (Decision/Extreme/Trap) active.
  - Liquidity-based TP/SL active with ATR safety fallback.
- Behavior:
  - Correctly in **Stalking Mode** when IDM not yet swept or conditions incomplete.
  - Ready to execute Type 1/2 entries when:
    - IDM swept = True
    - HTF bias aligns
    - Zone strength and CHoCH/Extreme conditions met.

---

## Next Steps

1. Let bot run continuously in DRY_RUN mode for 24–72 hours.
2. Capture first trades and log:
   - Entry type (Type 1 vs Type 2)
   - `block_class` (DECISION vs EXTREME)
   - TP/SL source (Liquidity vs ATR fallback).
3. Backtest Jan 25–30 data with new logic to compare against the zero-trade baseline.
4. After validation, prepare competition-ready configuration (0.01 lots, drawdown guards, session limits).
