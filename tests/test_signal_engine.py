import pandas as pd
from tradingbot.strategy.smc.signal_engine import SignalEngine
from unittest.mock import MagicMock

# Monkeypatch SignalEngine.__init__ to supply a dummy active news filter by default in tests
_original_init = SignalEngine.__init__
def _patched_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    mock_nf = MagicMock()
    mock_nf.is_news_blackout.return_value = (False, "NO_ACTIVE_NEWS_BLACKOUT")
    self.news_filter = mock_nf

SignalEngine.__init__ = _patched_init

# =========================
# DATA HELPERS
# =========================

def generate_candles(n=60, base=100):
    data = []
    price = base

    for i in range(n):
        open_p = price
        close_p = price + 1 if i % 2 == 0 else price - 0.5

        data.append({
            "open": open_p,
            "high": max(open_p, close_p) + 1,
            "low": min(open_p, close_p) - 1,
            "close": close_p,
        })

        price = close_p

    return pd.DataFrame(data)


# =========================
# TESTS
# =========================

def test_htf_bias_pass():
    engine = SignalEngine()
    engine._infer_bias = lambda df, w, *a, **k: "BULLISH"

    df = generate_candles()

    result = engine._step_htf_bias(df, df)

    assert result["passed"] is True


def test_htf_bias_fail():
    engine = SignalEngine()

    engine._step_htf_bias = lambda *a, **k: {
        "passed": False,
        "direction": None,
        "reason": "HTF_BIAS_MISMATCH"
    }

    df = generate_candles()

    result = engine._step_htf_bias(df, df)

    assert result["passed"] is False


def test_external_liquidity_sweep_bullish():
    engine = SignalEngine()

    mock_sweep = type("Sweep", (), {
        "direction": "BULLISH"
    })()

    engine._step_external_liquidity_sweep = lambda df, d: (
        {"passed": True, "reason": "OK"},
        mock_sweep
    )

    df = generate_candles()

    result, sweep = engine._step_external_liquidity_sweep(df, "BULLISH")

    assert result["passed"] is True
    assert sweep is not None


def test_choch_bullish():
    engine = SignalEngine()

    mock_bos = type("BOS", (), {
        "direction": "BULLISH"
    })()

    engine._step_choch_mss_body_close = lambda m5, sw, m15: (
        {"passed": True, "reason": "OK"},
        mock_bos
    )

    df = generate_candles()

    result, bos = engine._step_choch_mss_body_close(df, None, df)

    assert result["passed"] is True
    assert bos is not None
    assert bos.direction == "BULLISH"


def test_fvg_entry_bullish():
    engine = SignalEngine()

    from tradingbot.strategy.smc.signal_engine import POI, FVG

    poi = POI("EXTREME_OB", 1, 100, 104)
    poi_candidates = [poi]

    fvg = FVG("BULLISH", 2, 102, 106)

    engine._step_ob_fvg_confluence = lambda df, d, sw, sb, pois: (
        {"passed": True, "reason": "OK"},
        poi,
        fvg,
        103
    )

    df = generate_candles()

    result, selected_poi, selected_fvg, entry = engine._step_ob_fvg_confluence(
        df,
        "BULLISH",
        None,
        None,
        poi_candidates
    )

    assert result["passed"] is True
    assert entry is not None


def test_full_pipeline_enter():
    engine = SignalEngine()

    from tradingbot.strategy.smc.signal_engine import SweepEvent, StructureBreak, POI, FVG

    # Bypass ATR regime filter (inline check uses _calc_atr + min_atr_threshold)
    engine._step_atr_regime = lambda df: {"passed": True, "reason": "OK"}
    engine.config.min_atr_threshold = 0.0

    engine._step_htf_bias = lambda *a, **k: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    sweep = SweepEvent("BULLISH", "SELL_SIDE", 1, 100, 2, 98, 101, 110, 2.0)
    engine._step_external_liquidity_sweep = lambda df, d: ({"passed": True, "reason": "OK"}, sweep)

    bos = StructureBreak("BULLISH", "CHOCH", 105, 3, 106)
    engine._step_choch_mss_body_close = lambda m5, sw, m15: ({"passed": True, "reason": "OK"}, bos)

    poi = POI("EXTREME_OB", 2, 100, 104)
    engine._step_valid_poi = lambda *a, **k: ({"passed": True, "reason": "OK"}, [poi])

    fvg = FVG("BULLISH", 2, 102, 106)
    engine._step_ob_fvg_confluence = lambda df, m15, d, sw, sb, pois: (
        {"passed": True, "reason": "OK"}, poi, fvg, 103
    )

    engine._step_dealing_range = lambda d, e, sw: {"passed": True, "reason": "OK"}
    engine._step_killzone = lambda now, df: {"passed": True, "reason": "OK"}
    engine._step_rr = lambda *a, **k: ({"passed": True, "reason": "OK"}, 99, 110)

    df = generate_candles()

    result = engine.evaluate(df, df, df, df)

    assert result.action == "ENTER"
    assert result.direction == "BULLISH"
    assert result.entry_price == 103


def test_no_trade_htf_bias_fail():
    engine = SignalEngine()

    # Bypass ATR regime filter
    engine._step_atr_regime = lambda df: {"passed": True}
    engine.config.min_atr_threshold = 0.0

    engine._step_htf_bias = lambda *a, **k: {
        "passed": False,
        "reason": "HTF_BIAS_MISMATCH",
        "direction": None
    }

    df = generate_candles()

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "HTF_BIAS_MISMATCH"


def test_no_trade_sweep_fail():
    engine = SignalEngine()

    # Bypass ATR regime filter
    engine._step_atr_regime = lambda df: {"passed": True}
    engine.config.min_atr_threshold = 0.0

    engine._step_htf_bias = lambda *a, **k: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    engine._step_external_liquidity_sweep = lambda df, d: (
        {"passed": False, "reason": "EXTERNAL_LIQUIDITY_NOT_SWEPT"},
        None
    )

    df = generate_candles()

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "EXTERNAL_LIQUIDITY_NOT_SWEPT"


def test_no_trade_choch_fail():
    engine = SignalEngine()

    # Bypass ATR regime filter
    engine._step_atr_regime = lambda df: {"passed": True}
    engine.config.min_atr_threshold = 0.0

    engine._step_htf_bias = lambda *a, **k: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    mock_sweep = object()

    engine._step_external_liquidity_sweep = lambda df, d: (
        {"passed": True, "reason": "OK"},
        mock_sweep
    )

    engine._step_choch_mss_body_close = lambda m5, sw, m15: (
        {"passed": False, "reason": "CHOCH_BODY_CLOSE_NOT_CONFIRMED"},
        None
    )

    df = generate_candles()

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "CHOCH_BODY_CLOSE_NOT_CONFIRMED"


def test_no_trade_rr_fail():
    engine = SignalEngine()

    # Bypass ATR regime filter
    engine._step_atr_regime = lambda df: {"passed": True}
    engine.config.min_atr_threshold = 0.0

    engine._step_htf_bias = lambda *a, **k: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    mock_sweep = object()
    engine._step_external_liquidity_sweep = lambda df, d: ({"passed": True, "reason": "OK"}, mock_sweep)

    mock_bos = object()
    engine._step_choch_mss_body_close = lambda m5, sw, m15: ({"passed": True, "reason": "OK"}, mock_bos)

    mock_poi = object()
    engine._step_valid_poi = lambda *a, **k: ({"passed": True, "reason": "OK"}, [mock_poi])

    engine._step_ob_fvg_confluence = lambda df, m15, d, sw, sb, p: (
        {"passed": True, "reason": "OK"}, mock_poi, object(), 103
    )

    engine._step_dealing_range = lambda d, e, sw: {"passed": True, "reason": "OK"}
    engine._step_killzone = lambda n, df: {"passed": True, "reason": "OK"}

    engine._step_rr = lambda *a, **k: (
        {"passed": False, "reason": "RR_BELOW_MINIMUM"},
        None,
        None
    )

    df = generate_candles()

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "RR_BELOW_MINIMUM"

def test_killzone_blocks_asian_session():
    from datetime import datetime, timezone
    engine = SignalEngine()
    df = generate_candles()
    asian_time = datetime(2025, 1, 15, 1, 30, 0, tzinfo=timezone.utc)
    result = engine._step_killzone(asian_time, df)
    assert result["passed"] is False
    assert result["session"] == "ASIAN"
    assert result["reason"] == "ASIAN_SESSION_BLOCKED"


def test_killzone_allows_london_session():
    from datetime import datetime, timezone
    engine = SignalEngine()
    df = generate_candles()
    london_time = datetime(2025, 1, 15, 7, 45, 0, tzinfo=timezone.utc)
    result = engine._step_killzone(london_time, df)
    assert result["passed"] is True
    assert result["session"] == "LONDON"
    assert result["reason"] == "INSIDE_LONDON_KILLZONE"


def test_killzone_allows_ny_session():
    from datetime import datetime, timezone
    engine = SignalEngine()
    df = generate_candles()
    ny_time = datetime(2025, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    result = engine._step_killzone(ny_time, df)
    assert result["passed"] is True
    assert result["session"] == "NEW_YORK"
    assert result["reason"] == "INSIDE_NEW_YORK_KILLZONE"


def test_killzone_blocks_dead_zone():
    from datetime import datetime, timezone
    engine = SignalEngine()
    df = generate_candles()
    dead_time = datetime(2025, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
    result = engine._step_killzone(dead_time, df)
    assert result["passed"] is False
    assert result["session"] == "DEAD_ZONE"
    assert result["reason"] == "DEAD_ZONE_HARD_BLOCK"


def test_killzone_dst_shifts():
    from datetime import datetime, timezone
    engine = SignalEngine()
    df = generate_candles()

    # 1. Summer (DST active: UTC-4) - June 15, 2025
    # London Killzone is 02:00 - 05:00 NY time.
    # In summer, 02:30 NY is 06:30 UTC.
    summer_london = datetime(2025, 6, 15, 6, 30, 0, tzinfo=timezone.utc)
    res_summer = engine._step_killzone(summer_london, df)
    assert res_summer["passed"] is True
    assert res_summer["session"] == "LONDON"

    # 2. Winter (Standard time: UTC-5) - December 15, 2025
    # London Killzone is 02:00 - 05:00 NY time.
    # In winter, 02:30 NY is 07:30 UTC. 06:30 UTC is 01:30 NY (Dead Zone/Asian).
    winter_london = datetime(2025, 12, 15, 7, 30, 0, tzinfo=timezone.utc)
    res_winter = engine._step_killzone(winter_london, df)
    assert res_winter["passed"] is True
    assert res_winter["session"] == "LONDON"

    # 06:30 UTC in winter is 01:30 NY, which is Dead Zone
    winter_dead = datetime(2025, 12, 15, 6, 30, 0, tzinfo=timezone.utc)
    res_dead = engine._step_killzone(winter_dead, df)
    assert res_dead["passed"] is False
    assert res_dead["reason"] == "DEAD_ZONE_HARD_BLOCK"


# =========================
# NEWS FILTER TESTS
# =========================


def test_news_filter_blocks_during_high_impact_event():
    from datetime import datetime, timezone, timedelta
    from tradingbot.infra.news.news_filter import NewsFilter

    nf = NewsFilter(api_key="test")

    # Inject a fake HIGH impact event 5 min from now
    event_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    nf._cache = [{
        "event": "FOMC Rate Decision",
        "country": "US",
        "impact": "high",
        "time": event_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }]
    nf._cache_ts = datetime.now(timezone.utc)

    blocked, reason = nf.is_news_blackout()
    assert blocked is True
    assert "HARD_NEWS_BLACKOUT" in reason
    assert "FOMC" in reason


def test_news_filter_allows_outside_blackout_window():
    from datetime import datetime, timezone, timedelta
    from tradingbot.infra.news.news_filter import NewsFilter

    nf = NewsFilter(api_key="test")

    # Inject event 2 hours ago — well outside 15 min window
    event_time = datetime.now(timezone.utc) - timedelta(hours=2)
    nf._cache = [{
        "event": "CPI m/m",
        "country": "US",
        "impact": "high",
        "time": event_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }]
    nf._cache_ts = datetime.now(timezone.utc)

    blocked, reason = nf.is_news_blackout()
    assert blocked is False
    assert reason is None


def test_news_filter_ignores_low_impact_events():
    from datetime import datetime, timezone, timedelta
    from tradingbot.infra.news.news_filter import NewsFilter

    nf = NewsFilter(api_key="test")

    # LOW impact event right now — should NOT block
    event_time = datetime.now(timezone.utc)
    nf._cache = [{
        "event": "Balance of Trade",
        "country": "US",
        "impact": "low",
        "time": event_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }]
    nf._cache_ts = datetime.now(timezone.utc)

    blocked, reason = nf.is_news_blackout()
    assert blocked is False


def test_news_filter_disabled_when_no_api_key():
    from tradingbot.infra.news.news_filter import NewsFilter

    nf = NewsFilter(api_key="")  # no key
    blocked, reason = nf.is_news_blackout()
    assert blocked is False
    assert reason is None


def test_is_poi_mt_breached():
    engine = SignalEngine()
    from tradingbot.strategy.smc.signal_engine import POI
    
    # Bullish POI with range 100 to 110, midpoint is 105
    poi = POI("OB", 0, 100.0, 110.0)
    
    # case 1: no breach (close is above 105)
    df_no_breach = pd.DataFrame([
        {"open": 108.0, "high": 111.0, "low": 107.0, "close": 109.0},
        {"open": 109.0, "high": 112.0, "low": 106.0, "close": 108.0},
    ])
    assert engine._is_poi_mt_breached(poi, df_no_breach, df_no_breach, df_no_breach, "BULLISH") is False
    
    # case 2: breach (one of the closes is below 105)
    df_breach = pd.DataFrame([
        {"open": 108.0, "high": 111.0, "low": 107.0, "close": 109.0},
        {"open": 109.0, "high": 112.0, "low": 103.0, "close": 104.0}, # closes at 104 (below 105)
    ])
    assert engine._is_poi_mt_breached(poi, df_breach, df_breach, df_breach, "BULLISH") is True


def test_ith_itl_detection():
    engine = SignalEngine()
    
    # Generate candles to create a Short Term Low (STL) flanked by higher STLs to form an ITL
    # Pivot low at index 3: STL
    # Pivot low at index 6: STL (lowest)
    # Pivot low at index 9: STL
    data = []
    # 0 to 2
    for p in [110, 108, 106]:
        data.append({"high": p + 2, "low": p, "close": p + 1, "open": p + 1})
    # index 3: STL (low = 104)
    data.append({"high": 106, "low": 104, "close": 105, "open": 105})
    # 4 to 5
    for p in [106, 108]:
        data.append({"high": p + 2, "low": p, "close": p + 1, "open": p + 1})
    # index 6: STL (lowest low = 102)
    data.append({"high": 104, "low": 102, "close": 103, "open": 103})
    # 7 to 8
    for p in [104, 106]:
        data.append({"high": p + 2, "low": p, "close": p + 1, "open": p + 1})
    # index 9: STL (low = 104)
    data.append({"high": 106, "low": 104, "close": 105, "open": 105})
    # 10 to 12
    for p in [106, 108, 110]:
        data.append({"high": p + 2, "low": p, "close": p + 1, "open": p + 1})
        
    df = pd.DataFrame(data)
    iths, itls = engine._find_ith_itl(df)
    
    # It should identify index 6 as an Intermediate-Term Low (ITL)
    assert 6 in itls


def test_pdh_pdl_bias_sweep():
    engine = SignalEngine()
    
    # d1_df with 3 candles:
    # Day -3: High 100, Low 90, Close 95
    # Day -2 (Yesterday): High 98, Low 89 (sweeps Day -3 Low), Close 91 (closes back above Day -3 Low)
    # Day -1 (Today): High 95, Low 92, Close 94
    df = pd.DataFrame([
        {"open": 95.0, "high": 100.0, "low": 90.0, "close": 95.0},
        {"open": 94.0, "high": 98.0, "low": 89.0, "close": 91.0}, # sweeps 90.0, closes above it
        {"open": 91.0, "high": 95.0, "low": 92.0, "close": 94.0},
    ])
    
    bias = engine._infer_bias(df, 1, "D1")
    assert bias == "BULLISH"
    assert engine.pdl_swept is True


def test_stop_loss_calculation_refinements():
    # Test atr_sl_multiplier_sweep and min_sl_distance_pips enforcement
    from src.tradingbot.strategy.smc.signal_engine import SignalEngineConfig, SignalEngine, SweepEvent, POI, StructureBreak
    
    cfg = SignalEngineConfig(
        atr_sl_multiplier=0.3,
        atr_sl_multiplier_sweep=0.8,
        min_sl_distance_pips=35.0,
    )
    engine = SignalEngine(cfg)
    
    # 1. Test Sweep Entry SL Buffer (uses atr_sl_multiplier_sweep = 0.8)
    sweep = SweepEvent(
        direction="BEARISH",
        sweep_side="BUY_SIDE",
        reference_index=10,
        reference_level=4300.0,
        candle_index=20,
        sweep_price=4310.0,
        close_back_inside=4295.0,
        target_external_liquidity=4250.0,
        atr_at_sweep=10.0,  # 10.0 points ATR
    )
    
    selected_poi = POI(poi_type="IDM_SWEEP", candle_index=20, low=4280.0, high=4300.0)
    structure_break = StructureBreak(direction="BEARISH", choch_label="CHOCH", level=4300.0, candle_index=20, close_price=4295.0)
    
    # Sell entry at 4295.0. Sweep high is 4310.0.
    # Calculated SL for BEARISH SWEEP = sweep_price + sl_buf = 4310.0 + (10.0 * 0.8) = 4318.0.
    # Distance: 4318.0 - 4295.0 = 23.0 points (which is > min_sl_distance_pips * 0.1 = 3.5 points).
    # So SL should be exactly 4318.0.
    res, sl, tp = engine._step_rr(
        direction="BEARISH",
        entry_price=4295.0,
        sweep=sweep,
        selected_poi=selected_poi,
        structure_break=structure_break,
    )
    assert res["passed"] is True
    assert sl == 4318.0
    
    # 2. Test Minimum SL Distance Enforcement
    # Suppose sweep price is 4296.0, entry is 4295.0, atr is 1.0.
    # Calculated SL for BEARISH SWEEP = 4296.0 + (1.0 * 0.8) = 4296.8.
    # Distance from entry (4295.0) is 1.8 points, which is LESS than min_sl_distance_pips * 0.1 = 3.5 points.
    # So SL should be padded to entry + min_dist = 4295.0 + 3.5 = 4298.5.
    sweep_tight = SweepEvent(
        direction="BEARISH",
        sweep_side="BUY_SIDE",
        reference_index=10,
        reference_level=4295.5,
        candle_index=20,
        sweep_price=4296.0,
        close_back_inside=4295.0,
        target_external_liquidity=4250.0,
        atr_at_sweep=1.0,
    )
    res_tight, sl_tight, tp_tight = engine._step_rr(
        direction="BEARISH",
        entry_price=4295.0,
        sweep=sweep_tight,
        selected_poi=selected_poi,
        structure_break=structure_break,
    )
    assert res_tight["passed"] is True
    assert sl_tight == 4298.5