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
