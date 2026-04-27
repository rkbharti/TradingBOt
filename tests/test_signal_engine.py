import pandas as pd
from tradingbot.strategy.smc.signal_engine import SignalEngine


# =========================
# DATA HELPERS
# =========================

def create_bullish_structure():
    return pd.DataFrame([
        {"open": 100, "high": 105, "low": 99, "close": 104},
        {"open": 104, "high": 106, "low": 103, "close": 105},
        {"open": 105, "high": 107, "low": 104, "close": 106},
        {"open": 106, "high": 106.5, "low": 102, "close": 103},
        {"open": 103, "high": 108, "low": 102, "close": 107},
    ])


def create_bearish_structure():
    return pd.DataFrame([
        {"open": 107, "high": 108, "low": 103, "close": 104},
        {"open": 104, "high": 105, "low": 102, "close": 103},
        {"open": 103, "high": 104, "low": 101, "close": 102},
        {"open": 102, "high": 106, "low": 101, "close": 105},
        {"open": 105, "high": 106, "low": 100, "close": 101},
    ])


def create_sweep_data_bullish():
    data = []
    price = 100

    for i in range(15):
        open_p = price
        close_p = price + 1 if i % 2 == 0 else price - 0.5

        data.append({
            "open": open_p,
            "high": max(open_p, close_p) + 1,
            "low": min(open_p, close_p) - 1,
            "close": close_p,
        })

        price = close_p

    data.append({
        "open": price,
        "high": price + 2,
        "low": price - 5,
        "close": price - 1,
    })

    return pd.DataFrame(data)


# =========================
# TESTS
# =========================

def test_htf_bias_pass():
    engine = SignalEngine()
    engine._infer_bias = lambda df, w: "BULLISH"

    d1_df = create_bullish_structure()
    h4_df = create_bullish_structure()

    result = engine._step_htf_bias(d1_df, h4_df)

    assert result["passed"] is True


def test_htf_bias_fail():
    engine = SignalEngine()

    calls = ["BULLISH", "BEARISH"]

    def mock_bias(df, w):
        return calls.pop(0)

    engine._infer_bias = mock_bias

    d1_df = create_bullish_structure()
    h4_df = create_bullish_structure()

    result = engine._step_htf_bias(d1_df, h4_df)

    assert result["passed"] is False


def test_external_liquidity_sweep_bullish():
    engine = SignalEngine()

    engine._find_pivots = lambda df, w: ([2], [1])

    def mock_sweep(df, highs, lows):
        from tradingbot.strategy.smc.signal_engine import SweepEvent
        return SweepEvent(
            direction="BULLISH",
            sweep_side="SELL_SIDE",
            reference_index=1,
            reference_level=100,
            candle_index=3,
            sweep_price=98,
            close_back_inside=101,
            target_external_liquidity=110,
            atr_at_sweep=2.0,
        )

    engine._find_bullish_sweep = mock_sweep

    df = create_sweep_data_bullish()

    result, sweep = engine._step_external_liquidity_sweep(df, "BULLISH")

    assert result["passed"] is True
    assert sweep is not None


def test_choch_bullish():
    engine = SignalEngine()

    # Mock pivots (internal structure)
    engine._find_pivots = lambda df, w: ([1], [0])
    # Mock time mapping
    engine._get_candle_time = lambda df, idx: None
    engine._find_bar_at_or_after = lambda df, t: 0

    # Create dummy sweep
    from tradingbot.strategy.smc.signal_engine import SweepEvent

    sweep = SweepEvent(
        direction="BULLISH",
        sweep_side="SELL_SIDE",
        reference_index=1,
        reference_level=100,
        candle_index=2,
        sweep_price=98,
        close_back_inside=101,
        target_external_liquidity=110,
        atr_at_sweep=2.0,
    )

    # Create df with breakout candle
    df = pd.DataFrame([
        {"open": 100, "high": 105, "low": 99, "close": 104},
        {"open": 104, "high": 106, "low": 103, "close": 105},
        {"open": 105, "high": 107, "low": 104, "close": 106},
        {"open": 106, "high": 108, "low": 105, "close": 107},  # break candle
    ])

    result, bos = engine._step_choch_mss_body_close(df, sweep, df)

    assert result["passed"] is True
    assert bos is not None
    assert bos.direction == "BULLISH"

def test_fvg_entry_bullish():
    engine = SignalEngine()

    from tradingbot.strategy.smc.signal_engine import StructureBreak, POI, FVG

    # Mock sweep
    sweep = type("Sweep", (), {
        "direction": "BULLISH",
        "candle_index": 1,
        "sweep_price": 98,
        "target_external_liquidity": 110
    })()

    # Structure break
    bos = StructureBreak(
        direction="BULLISH",
        level=105,
        candle_index=2,
        choch_label="CHOCH",
        close_price=106
    )

    # Mock POI
    poi = POI(
        poi_type="EXTREME_OB",
        candle_index=1,
        low=100,
        high=104
    )

    poi_candidates = [poi]

    # Mock FVG detection
    engine._find_fvgs = lambda df, direction, start, end: [
        FVG(direction="BULLISH", candle_index=1, low=102, high=106)
    ]

    # Mock displacement (important!)
    engine.is_displacement_after_poi = lambda poi, df, direction: True

    # Dummy df
    df = pd.DataFrame([
        {"open": 100, "high": 102, "low": 99, "close": 101},
        {"open": 101, "high": 103, "low": 100, "close": 102},
        {"open": 104, "high": 106, "low": 104, "close": 105},
        {"open": 105, "high": 108, "low": 103, "close": 107},
    ])

    result, selected_poi, selected_fvg, entry = engine._step_ob_fvg_confluence(
        df,
        "BULLISH",
        sweep,
        bos,
        poi_candidates
    )

    assert result["passed"] is True
    assert entry is not None

def test_full_pipeline_enter():
    engine = SignalEngine()

    import pandas as pd
    from tradingbot.strategy.smc.signal_engine import SweepEvent, StructureBreak, POI, FVG

    # ----------------------------
    # MOCK ALL STEPS (force pass)
    # ----------------------------

    # Step 1
    engine._step_htf_bias = lambda d1, h4: {
        "passed": True,
        "direction": "BULLISH",
        "reason": "OK"
    }

    # Step 2
    sweep = SweepEvent(
        direction="BULLISH",
        sweep_side="SELL_SIDE",
        reference_index=1,
        reference_level=100,
        candle_index=2,
        sweep_price=98,
        close_back_inside=101,
        target_external_liquidity=110,
        atr_at_sweep=2.0,
    )

    engine._step_external_liquidity_sweep = lambda df, direction: (
        {"passed": True, "reason": "OK"},
        sweep
    )

    # Step 3
    bos = StructureBreak(
        direction="BULLISH",
        level=105,
        candle_index=3,
        choch_label="CHOCH",
        close_price=106
    )

    engine._step_choch_mss_body_close = lambda m5, sw, m15: (
        {"passed": True, "reason": "OK"},
        bos
    )

    # Step 4
    poi = POI("EXTREME_OB", 2, 100, 104)

    engine._step_valid_poi = lambda df, sw, sb: (
        {"passed": True, "reason": "OK"},
        [poi]
    )

    # Step 5
    fvg = FVG("BULLISH", 2, 102, 106)

    engine._step_ob_fvg_confluence = lambda df, dir, sw, sb, pois: (
        {"passed": True, "reason": "OK"},
        poi,
        fvg,
        103  # entry price
    )

    # Step 6
    engine._step_dealing_range = lambda d, e, sw: {"passed": True, "reason": "OK"}

    # Step 7
    engine._step_killzone = lambda now, df: {"passed": True, "reason": "OK"}

    # Step 8
    engine._step_rr = lambda d, e, sw: (
        {"passed": True, "reason": "OK"},
        99,   # SL
        110   # TP
    )

    # ----------------------------
    # Dummy DataFrames
    # ----------------------------
    data = []
    price = 100

    for i in range(20):  # ≥15 candles required
        open_p = price
        close_p = price + 1 if i % 2 == 0 else price - 0.5

        data.append({
            "open": open_p,
            "high": max(open_p, close_p) + 1,
            "low": min(open_p, close_p) - 1,
            "close": close_p,
        })

        price = close_p

    df = pd.DataFrame(data)

    result = engine.evaluate(df, df, df, df)

    assert result.action == "ENTER"
    assert result.direction == "BULLISH"
    assert result.entry_price == 103

def test_no_trade_htf_bias_fail():
    engine = SignalEngine()

    # Force HTF mismatch
    engine._step_htf_bias = lambda d1, h4: {
        "passed": False,
        "reason": "HTF_BIAS_MISMATCH",
        "direction": None
    }

    import pandas as pd
    df = pd.DataFrame([{"open":1,"high":2,"low":0,"close":1}] * 20)

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "HTF_BIAS_MISMATCH"

def test_no_trade_sweep_fail():
    engine = SignalEngine()

    engine._step_htf_bias = lambda d1, h4: {
        "passed": True, "direction": "BULLISH", "reason": "OK"
    }

    engine._step_external_liquidity_sweep = lambda df, direction: (
        {"passed": False, "reason": "EXTERNAL_LIQUIDITY_NOT_SWEPT"},
        None
    )

    import pandas as pd
    df = pd.DataFrame([{"open":1,"high":2,"low":0,"close":1}] * 20)

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "EXTERNAL_LIQUIDITY_NOT_SWEPT"\

def test_no_trade_choch_fail():
    engine = SignalEngine()

    from tradingbot.strategy.smc.signal_engine import SweepEvent

    engine._step_htf_bias = lambda d1, h4: {
        "passed": True, "direction": "BULLISH", "reason": "OK"
    }

    sweep = SweepEvent(
        direction="BULLISH",
        sweep_side="SELL_SIDE",
        reference_index=1,
        reference_level=100,
        candle_index=2,
        sweep_price=98,
        close_back_inside=101,
        target_external_liquidity=110,
        atr_at_sweep=2.0,
    )

    engine._step_external_liquidity_sweep = lambda df, d: (
        {"passed": True, "reason": "OK"},
        sweep
    )

    engine._step_choch_mss_body_close = lambda m5, sw, m15: (
        {"passed": False, "reason": "CHOCH_BODY_CLOSE_NOT_CONFIRMED"},
        None
    )

    import pandas as pd
    df = pd.DataFrame([{"open":1,"high":2,"low":0,"close":1}] * 20)

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "CHOCH_BODY_CLOSE_NOT_CONFIRMED"

def test_no_trade_rr_fail():
    engine = SignalEngine()

    from tradingbot.strategy.smc.signal_engine import SweepEvent, StructureBreak, POI, FVG

    engine._step_htf_bias = lambda d1, h4: {"passed": True, "direction": "BULLISH", "reason": "OK"}

    sweep = SweepEvent("BULLISH","SELL_SIDE",1,100,2,98,101,110,2.0)

    engine._step_external_liquidity_sweep = lambda df, d: ({"passed": True,"reason":"OK"}, sweep)

    bos = StructureBreak("BULLISH", "CHOCH", 105, 3, 106)

    engine._step_choch_mss_body_close = lambda m5, sw, m15: ({"passed": True,"reason":"OK"}, bos)

    poi = POI("EXTREME_OB",2,100,104)

    engine._step_valid_poi = lambda df, sw, sb: ({"passed": True,"reason":"OK"}, [poi])

    fvg = FVG("BULLISH",2,102,106)

    engine._step_ob_fvg_confluence = lambda df,d,sw,sb,p: ({"passed": True,"reason":"OK"}, poi, fvg, 103)

    engine._step_dealing_range = lambda d,e,sw: {"passed": True,"reason":"OK"}
    engine._step_killzone = lambda n,df: {"passed": True,"reason":"OK"}

    # RR FAIL
    engine._step_rr = lambda d,e,sw: (
        {"passed": False, "reason": "RR_BELOW_MINIMUM"},
        None,
        None
    )

    import pandas as pd
    df = pd.DataFrame([{"open":1,"high":2,"low":0,"close":1}] * 20)

    result = engine.evaluate(df, df, df, df)

    assert result.action == "NO_TRADE"
    assert result.reason == "RR_BELOW_MINIMUM"