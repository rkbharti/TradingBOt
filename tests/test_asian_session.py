import pytest
import pandas as pd
from datetime import datetime, date, timezone
import pytz
from unittest.mock import MagicMock

from apps.trader.main import XAUUSDTradingBot
from tradingbot.strategy.smc.signal_engine import SignalEngine, SignalResult

# Helper to create naive UTC datetime given NY hour/minute
def make_utc_time(year=2026, month=6, day=25, hour_ny=15, minute_ny=30):
    ny_tz = pytz.timezone("America/New_York")
    dt_ny = ny_tz.localize(datetime(year, month, day, hour_ny, minute_ny))
    return dt_ny.astimezone(pytz.utc).replace(tzinfo=None)

def test_scan_presession_pois_ob_and_fvg():
    # Setup bot instance
    bot = XAUUSDTradingBot()
    bot.asian_session_pois = []
    
    # 1. Create M5 candles inside the 15:30-20:00 NY window (EDT: UTC-4)
    # 15:30 NY is 19:30 UTC.
    times_utc = [
        make_utc_time(hour_ny=15, minute_ny=30),  # index 0
        make_utc_time(hour_ny=15, minute_ny=35),  # index 1
        make_utc_time(hour_ny=15, minute_ny=40),  # index 2
        make_utc_time(hour_ny=15, minute_ny=45),  # index 3
        make_utc_time(hour_ny=15, minute_ny=50),  # index 4
    ]
    
    # Bullish FVG at index 1: low[3] > high[1]
    # Candle 1: high = 100.0, close = 99.0, open = 99.5 (bearish OB candidate)
    # Candle 2: low = 100.5, high = 101.5
    # Candle 3: low = 101.0, high = 102.0
    m5_data = {
        "time": times_utc,
        "open":  [99.5, 99.5, 100.5, 101.2, 102.0],
        "close": [99.0, 99.0, 101.0, 101.8, 102.2],
        "high":  [100.0, 100.0, 101.5, 102.2, 102.5],
        "low":   [98.5, 98.5, 100.2, 100.8, 101.5]
    }
    m5_df = pd.DataFrame(m5_data)
    m15_df = pd.DataFrame()  # Empty is fine for scanner
    
    ny_tz = pytz.timezone("America/New_York")
    now_ny = make_utc_time(hour_ny=17, minute_ny=0).replace(tzinfo=pytz.utc).astimezone(ny_tz)
    
    # Trigger scanner
    bot.scan_presession_pois(m5_df, m15_df, now_ny)
    
    assert len(bot.asian_session_pois) > 0
    # Should find FVG and OB
    types = [poi["type"] for poi in bot.asian_session_pois]
    assert "FVG" in types
    assert "OB" in types
    
    # Check that they have direction "bull"
    for poi in bot.asian_session_pois:
        assert poi["direction"] == "bull"

def test_scan_presession_pois_liquidity_fallback():
    bot = XAUUSDTradingBot()
    bot.asian_session_pois = []
    
    times_utc = [
        make_utc_time(hour_ny=15, minute_ny=30),  # index 0
        make_utc_time(hour_ny=15, minute_ny=35),  # index 1
        make_utc_time(hour_ny=15, minute_ny=40),  # index 2 (swing low / high)
        make_utc_time(hour_ny=15, minute_ny=45),  # index 3
        make_utc_time(hour_ny=15, minute_ny=50),  # index 4
    ]
    
    # No FVG, but index 2 is a swing low and high
    m5_data = {
        "time": times_utc,
        "open":  [100.0, 100.0, 100.0, 100.0, 100.0],
        "close": [100.0, 100.0, 100.0, 100.0, 100.0],
        "high":  [101.0, 101.0, 102.0, 101.0, 101.0],  # index 2 is swing high
        "low":   [99.0, 99.0, 98.0, 99.0, 99.0]       # index 2 is swing low
    }
    m5_df = pd.DataFrame(m5_data)
    m15_df = pd.DataFrame()
    
    ny_tz = pytz.timezone("America/New_York")
    now_ny = make_utc_time(hour_ny=17, minute_ny=0).replace(tzinfo=pytz.utc).astimezone(ny_tz)
    
    bot.scan_presession_pois(m5_df, m15_df, now_ny)
    
    assert len(bot.asian_session_pois) > 0
    types = [poi["type"] for poi in bot.asian_session_pois]
    assert "LIQUIDITY" in types
    
    # Verify both bull and bear directions exist for liquidity wicks
    directions = [poi["direction"] for poi in bot.asian_session_pois]
    assert "bull" in directions
    assert "bear" in directions

def test_scan_presession_reset():
    bot = XAUUSDTradingBot()
    bot.asian_session_pois = [{"type": "OB", "high": 100, "low": 99, "direction": "bull", "timestamp": "dummy"}]
    bot.last_presession_reset_date = date(2026, 6, 24)
    
    m5_df = pd.DataFrame()
    m15_df = pd.DataFrame()
    ny_tz = pytz.timezone("America/New_York")
    now_ny = make_utc_time(day=25, hour_ny=15, minute_ny=30).replace(tzinfo=pytz.utc).astimezone(ny_tz)
    
    # Trigger scanner
    bot.scan_presession_pois(m5_df, m15_df, now_ny)
    
    # Should reset date and clear POIs
    assert bot.last_presession_reset_date == date(2026, 6, 25)
    assert len(bot.asian_session_pois) == 0

def test_asian_kz_no_poi_rejection():
    engine = SignalEngine()
    
    # Create M5 df
    times_utc = [make_utc_time(hour_ny=21, minute_ny=0)]
    m5_df = pd.DataFrame({"time": times_utc, "close": [100.0]})
    m15_df = pd.DataFrame()
    h4_df = pd.DataFrame()
    d1_df = pd.DataFrame()
    
    # ASIAN session time NY 21:00
    now_utc = make_utc_time(hour_ny=21, minute_ny=0)
    
    # Evaluate with empty POIs
    result = engine.evaluate(
        m5_df=m5_df, m15_df=m15_df, h4_df=h4_df, d1_df=d1_df,
        now_utc=now_utc, asian_session_pois=[], m1=pd.DataFrame()
    )
    
    assert result.action == "NO_TRADE"
    assert result.reason == "ASIAN_NO_PRESESSION_POI"

def test_asian_kz_not_at_poi_rejection():
    engine = SignalEngine()
    
    times_utc = [make_utc_time(hour_ny=21, minute_ny=0)]
    m5_df = pd.DataFrame({"time": times_utc, "close": [105.0]}) # Price is 105.0
    m15_df = pd.DataFrame()
    h4_df = pd.DataFrame()
    d1_df = pd.DataFrame()
    
    # POI zone is 99.0 to 101.0 (no overlap with 105.0)
    pois = [{"type": "OB", "high": 101.0, "low": 99.0, "direction": "bull", "timestamp": "dummy"}]
    now_utc = make_utc_time(hour_ny=21, minute_ny=0)
    
    result = engine.evaluate(
        m5_df=m5_df, m15_df=m15_df, h4_df=h4_df, d1_df=d1_df,
        now_utc=now_utc, asian_session_pois=pois, m1=pd.DataFrame()
    )
    
    assert result.action == "NO_TRADE"
    assert result.reason == "ASIAN_PRICE_NOT_AT_POI"

def test_asian_kz_valid_tap_and_m1_choch():
    engine = SignalEngine()
    engine._calc_atr = MagicMock(return_value=1.0)
    engine._step_htf_bias = MagicMock(return_value={"passed": True, "direction": "BULLISH"})
    
    # Mock news filter to pass
    mock_nf = MagicMock()
    mock_nf.is_news_blackout.return_value = (False, "NO_HIGH_IMPACT_NEWS")
    engine.news_filter = mock_nf
    
    # 21:00 NY Time
    now_utc = make_utc_time(hour_ny=21, minute_ny=0)
    
    # M5 has price inside POI (100.0)
    m5_df = pd.DataFrame({
        "time": [make_utc_time(hour_ny=21, minute_ny=0)],
        "open": [100.0], "high": [100.5], "low": [99.5], "close": [100.0]
    })
    
    # Pre-session POI: Bullish OB in 99.0 to 101.0
    pois = [{"type": "OB", "high": 101.0, "low": 99.0, "direction": "bull", "timestamp": "dummy"}]
    
    # M1 candles showing a Bullish CHoCH (break of M1 pivot high with displacement FVG)
    # Let's construct a 20-bar M1 DataFrame
    m1_times = [make_utc_time(hour_ny=20 + (40 + idx) // 60, minute_ny=(40 + idx) % 60) for idx in range(25)]
    
    # We want a swing high (pivot high) at index 10: high = 100.5
    # Break above at index 15: close = 101.0
    # Bullish FVG formed at index 13: low[15] = 100.6 > high[13] = 100.4
    m1_data = {
        "time": m1_times,
        "open":  [100.0]*25,
        "close": [100.0]*25,
        "high":  [100.1]*25,
        "low":   [99.9]*25
    }
    # Set pivot high at index 10
    m1_data["high"][10] = 100.5
    m1_data["high"][9] = 100.2
    m1_data["high"][11] = 100.2
    
    # Set break candle at index 16
    m1_data["close"][16] = 101.0
    
    # Set FVG displacement at index 14
    m1_data["high"][14] = 100.3
    m1_data["low"][16] = 100.5  # low[16] > high[14]
    
    m1_df = pd.DataFrame(m1_data)
    
    # Dummy M15 / H4 / D1
    m15_df = pd.DataFrame({"time": [now_utc], "open": [100], "high": [105], "low": [95], "close": [100]})
    h4_df = pd.DataFrame({"time": [now_utc], "open": [100], "high": [105], "low": [95], "close": [100]})
    d1_df = pd.DataFrame({"time": [now_utc], "open": [100], "high": [105], "low": [95], "close": [100]})
    
    result = engine.evaluate(
        m5_df=m5_df, m15_df=m15_df, h4_df=h4_df, d1_df=d1_df,
        now_utc=now_utc, asian_session_pois=pois, m1=m1_df
    )
    
    assert result.action == "ENTER"
    assert result.direction == "BULLISH"
    assert result.entry_price > 0.0
    assert result.sl_price < result.entry_price
    assert result.tp_price > result.entry_price

def test_asian_kz_news_filter_block():
    engine = SignalEngine()
    engine._calc_atr = MagicMock(return_value=1.0)
    engine._step_htf_bias = MagicMock(return_value={"passed": True, "direction": "BULLISH"})
    
    # Mock news filter to block
    mock_nf = MagicMock()
    mock_nf.is_news_blackout.return_value = (True, "HIGH_IMPACT_NEWS_BLACKOUT")
    engine.news_filter = mock_nf
    
    now_utc = make_utc_time(hour_ny=21, minute_ny=0)
    m5_df = pd.DataFrame({
        "time": [make_utc_time(hour_ny=21, minute_ny=0)],
        "open": [100.0], "high": [100.5], "low": [99.5], "close": [100.0]
    })
    
    pois = [{"type": "OB", "high": 101.0, "low": 99.0, "direction": "bull", "timestamp": "dummy"}]
    
    m1_times = [make_utc_time(hour_ny=20 + (40 + idx) // 60, minute_ny=(40 + idx) % 60) for idx in range(25)]
    m1_data = {
        "time": m1_times,
        "open":  [100.0]*25,
        "close": [100.0]*25,
        "high":  [100.1]*25,
        "low":   [99.9]*25
    }
    m1_data["high"][10] = 100.5
    m1_data["high"][9] = 100.2
    m1_data["high"][11] = 100.2
    m1_data["close"][16] = 101.0
    m1_data["high"][14] = 100.3
    m1_data["low"][16] = 100.5
    m1_df = pd.DataFrame(m1_data)
    
    m15_df = pd.DataFrame({"time": [now_utc], "open": [100], "high": [105], "low": [95], "close": [100]})
    h4_df = pd.DataFrame({"time": [now_utc], "open": [100], "high": [105], "low": [95], "close": [100]})
    d1_df = pd.DataFrame({"time": [now_utc], "open": [100], "high": [105], "low": [95], "close": [100]})
    
    result = engine.evaluate(
        m5_df=m5_df, m15_df=m15_df, h4_df=h4_df, d1_df=d1_df,
        now_utc=now_utc, asian_session_pois=pois, m1=m1_df
    )
    
    assert result.action == "NO_TRADE"
    assert result.reason == "HIGH_IMPACT_NEWS_BLACKOUT"
