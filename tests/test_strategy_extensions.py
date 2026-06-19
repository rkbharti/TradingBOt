import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from tradingbot.infra.news.news_filter import NewsFilter
from tradingbot.strategy.smc.signal_engine import SignalEngine, POI, SweepEvent, StructureBreak

def test_dynamic_news_filter_currency_extraction():
    """Test that the news filter extracts base/quote currencies from symbols and blocks correctly."""
    nf = NewsFilter()
    # Cache dummy news events
    nf._cache = [
        {"event": "FOMC Meeting CPI", "country": "USD", "impact": "high", "time": datetime.now(timezone.utc).isoformat()},
        {"event": "EUR CPI", "country": "EUR", "impact": "high", "time": datetime.now(timezone.utc).isoformat()},
        {"event": "CHF CPI", "country": "CHF", "impact": "high", "time": datetime.now(timezone.utc).isoformat()}
    ]
    nf._cache_ts = datetime.now(timezone.utc)

    # 1. EURUSD should block on USD news
    blocked, reason = nf.is_news_blackout(symbol="EURUSD")
    assert blocked is True
    assert "CPI" in reason

    # 2. XAUUSD should block on USD news
    blocked, reason = nf.is_news_blackout(symbol="XAUUSD")
    assert blocked is True
    assert "CPI" in reason

    # 3. EURCHF should block on EUR and CHF news, not USD
    # Let's override cache to only have USD news and check it's not blocked
    nf._cache = [
        {"event": "FOMC Meeting CPI", "country": "USD", "impact": "high", "time": datetime.now(timezone.utc).isoformat()}
    ]
    blocked, reason = nf.is_news_blackout(symbol="EURCHF")
    assert blocked is False

    # 4. EURCHF with CHF news should block
    nf._cache = [
        {"event": "CHF CPI", "country": "CHF", "impact": "high", "time": datetime.now(timezone.utc).isoformat()}
    ]
    blocked, reason = nf.is_news_blackout(symbol="EURCHF")
    assert blocked is True
    assert "CHF" in reason


def test_tp_structural_extremes():
    """Test that trend-following trades target ITH/ITL extremes for higher RR."""
    engine = SignalEngine()
    
    # H4 data with 3 STHs, where index 4 is the ITH (high=112.0) flanked by index 1 (94.0) and index 7 (105.0)
    h4_data = [
        {"high": 90.0, "low": 85.0, "close": 88.0, "open": 86.0},
        {"high": 94.0, "low": 86.0, "close": 92.0, "open": 88.0}, # STH 1 (index 1)
        {"high": 91.0, "low": 87.0, "close": 89.0, "open": 91.0},
        {"high": 101.0, "low": 96.0, "close": 99.0, "open": 101.0},
        {"high": 112.0, "low": 98.0, "close": 107.0, "open": 99.0}, # STH 2 / ITH (index 4)
        {"high": 108.0, "low": 102.0, "close": 109.0, "open": 108.0},
        {"high": 104.0, "low": 101.0, "close": 104.0, "open": 106.0},
        {"high": 105.0, "low": 100.0, "close": 102.0, "open": 104.0}, # STH 3 (index 7)
        {"high": 103.0, "low": 99.0, "close": 101.0, "open": 103.0},
    ]
    h4_df = pd.DataFrame(h4_data)
    
    # Verify _find_ith_itl detects the ITH at index 4 (high = 112.0)
    iths, itls = engine._find_ith_itl(h4_df)
    assert len(iths) > 0
    assert iths[0] == 4
    
    sweep = SweepEvent(
        direction="BULLISH",
        sweep_side="LOW",
        reference_index=1,
        reference_level=86.0,
        candle_index=3,
        sweep_price=85.5,
        close_back_inside=87.0,
        target_external_liquidity=93.0,
        atr_at_sweep=1.0
    )
    
    selected_poi = POI(poi_type="OB", candle_index=2, low=87.0, high=89.0)
    structure_break = StructureBreak(direction="BULLISH", choch_label="BOS", level=90.0, candle_index=5, close_price=91.0)
    
    # Calculate step_rr with trend-aligned setup (BULLISH and HTF bias BULLISH)
    engine.htf_trend_direction = "BULLISH"
    
    # Call _step_rr (passing h4_df)
    res, sl, tp = engine._step_rr(
        direction="BULLISH",
        entry_price=91.0,
        sweep=sweep,
        selected_poi=selected_poi,
        structure_break=structure_break,
        htf_pois=[],
        h4_df=h4_df
    )
    
    # Primary TP should target the highest H4 ITH above entry (which is at index 4, high = 112.0)
    # Since tp_erl was 93.0 and highest ITH above entry is 112.0, TP should be 112.0!
    assert tp == 112.0
