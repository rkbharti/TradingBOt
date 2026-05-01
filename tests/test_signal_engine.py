import pandas as pd
from tradingbot.strategy.smc.signal_engine import SignalEngine


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
    engine._infer_bias = lambda df, w: "BULLISH"

    df = generate_candles()

    result = engine._step_htf_bias(df, df)

    assert result["passed"] is True


def test_htf_bias_fail():
    engine = SignalEngine()

    engine._step_htf_bias = lambda d1, h4: {
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

    engine._step_htf_bias = lambda d1, h4: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    sweep = SweepEvent("BULLISH", "SELL_SIDE", 1, 100, 2, 98, 101, 110, 2.0)
    engine._step_external_liquidity_sweep = lambda df, d: ({"passed": True, "reason": "OK"}, sweep)

    bos = StructureBreak("BULLISH", "CHOCH", 105, 3, 106)
    engine._step_choch_mss_body_close = lambda m5, sw, m15: ({"passed": True, "reason": "OK"}, bos)

    poi = POI("EXTREME_OB", 2, 100, 104)
    engine._step_valid_poi = lambda df, m5, h4, sw, sb: ({"passed": True, "reason": "OK"}, [poi])

    fvg = FVG("BULLISH", 2, 102, 106)
    engine._step_ob_fvg_confluence = lambda df, m15, d, sw, sb, pois: (
        {"passed": True, "reason": "OK"}, poi, fvg, 103
    )

    engine._step_dealing_range = lambda d, e, sw: {"passed": True, "reason": "OK"}
    engine._step_killzone = lambda now, df: {"passed": True, "reason": "OK"}
    engine._step_rr = lambda d, e, sw, poi: ({"passed": True, "reason": "OK"}, 99, 110)

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

    engine._step_htf_bias = lambda d1, h4: {
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

    engine._step_htf_bias = lambda d1, h4: {"passed": True, "direction": "BULLISH", "reason": "OK"}

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

    engine._step_htf_bias = lambda d1, h4: {"passed": True, "direction": "BULLISH", "reason": "OK"}

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

    engine._step_htf_bias = lambda d1, h4: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    mock_sweep = object()
    engine._step_external_liquidity_sweep = lambda df, d: ({"passed": True, "reason": "OK"}, mock_sweep)

    mock_bos = object()
    engine._step_choch_mss_body_close = lambda m5, sw, m15: ({"passed": True, "reason": "OK"}, mock_bos)

    mock_poi = object()
    engine._step_valid_poi = lambda df, m5, h4, sw, sb: ({"passed": True, "reason": "OK"}, [mock_poi])

    engine._step_ob_fvg_confluence = lambda df, m15, d, sw, sb, p: (
        {"passed": True, "reason": "OK"}, mock_poi, object(), 103
    )

    engine._step_dealing_range = lambda d, e, sw: {"passed": True, "reason": "OK"}
    engine._step_killzone = lambda n, df: {"passed": True, "reason": "OK"}

    engine._step_rr = lambda d, e, sw, poi: (
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
        "time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
    }]
    nf._cache_ts = datetime.now(timezone.utc)

    blocked, reason = nf.is_news_blackout()
    assert blocked is True
    assert "HIGH_IMPACT_NEWS_BLACKOUT" in reason
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
        "time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
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
        "time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
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