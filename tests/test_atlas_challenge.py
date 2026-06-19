import pytest
import os
import json
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from datetime import datetime, date, timezone
import pytz

# Mock MT5 connection and standard imports for testing
from src.tradingbot.risk.challenge_policy import ChallengePolicy
from apps.trader.main import XAUUSDTradingBot

@patch("apps.trader.main.MT5Connection")
@patch("apps.trader.main.MultiTimeframeFractal")
@patch("apps.trader.main.IdeaMemory")
@patch("apps.trader.main.HTFMemory")
@patch("apps.trader.main.AuditLogger")
def test_atlas_challenge_monitoring_and_floors(
    mock_audit, mock_htf, mock_idea, mock_mtf, mock_mt5_conn, tmp_path
):
    # Setup env variables for Atlas Funded Challenge
    env_vars = {
        "PROP_FIRM": "AtlasFunded",
        "ACCOUNT_SIZE": "5000",
        "PROFIT_TARGET_PCT": "4.0",
        "DAILY_DRAWDOWN_PCT": "5.0",
        "MAX_DRAWDOWN_PCT": "7.0",
        "TRADING_MODE": "CHALLENGE",
        "DAILY_RESET_TZ": "UTC",
    }
    with patch.dict(os.environ, env_vars):
        # Override the state file path
        state_file = tmp_path / "session_state.json"
        
        # Patch Path so it references our temp state file
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.write_text") as mock_write, \
             patch("pathlib.Path.read_text", return_value="{}"):
             
            # Create bot instance
            bot = XAUUSDTradingBot(config_path="tests/config.json")
            bot.mt5_get_account = MagicMock(return_value=Mock(balance=5000.0, equity=5000.0, login=99789073, server="MetaQuotes-Demo"))
            bot.mt5_get_all_positions = MagicMock(return_value=[])
            bot.send_telegram = MagicMock()
            bot.mt5_close_position = MagicMock()
            
            # Initial peaks should equal starting account size
            assert bot.all_time_highest_balance == 5000.0
            assert bot.all_time_highest_equity == 5000.0
            
            # 1. Update values and run monitoring -> check peak tracking
            bot.mt5_get_account.return_value = Mock(balance=5100.0, equity=5050.0)
            bot.run_risk_and_pnl_monitoring()
            
            assert bot.all_time_highest_balance == 5100.0
            assert bot.all_time_highest_equity == 5050.0
            assert bot.daily_highest_balance == 5100.0
            assert bot.daily_highest_equity == 5050.0
            
            # 2. Recompute floors dynamically and verify
            # daily floor should be previous day peak (starts at 5000) * 0.95 = 4750.0
            # max overall floor should be all time peak (5100) * 0.93 = 4743.0
            assert bot.challenge_policy.daily_floor == 4750.0
            assert bot.challenge_policy.max_overall_floor == 4743.0
            
            # 3. Simulate Daily Drawdown breach -> Equity drops below daily_floor
            # Let's set previous day peaks to 5100 first so daily floor is 5100 * 0.95 = 4845.0
            bot.previous_day_highest_balance = 5100.0
            bot.previous_day_highest_equity = 5100.0
            
            # Drop equity below 4845.0 -> 4840.0
            bot.mt5_get_account.return_value = Mock(balance=5100.0, equity=4840.0)
            bot.run_risk_and_pnl_monitoring()
            
            assert bot.daily_halted is True
            assert bot.challenge_policy.daily_halted is True
            
            # 4. Verify daily reset boundary logic resets daily_halted on date change
            bot._session_date = date(2026, 6, 17) # mock yesterday
            
            # Reset tz is UTC
            tz = pytz.timezone("UTC")
            # We mock the current UTC date to 2026-06-18 past midnight
            mock_now = datetime(2026, 6, 18, 1, 0, 0, tzinfo=timezone.utc)
            
            with patch("apps.trader.main.datetime") as mock_datetime:
                mock_datetime.now.return_value = mock_now
                mock_datetime.fromtimestamp = datetime.fromtimestamp
                
                # Mock account info for reset
                bot.mt5_get_account.return_value = Mock(balance=5100.0, equity=4900.0)
                bot._maybe_reset_daily_state()
                
                # Daily halt should reset
                assert bot.daily_halted is False
                assert bot.challenge_policy.daily_halted is False
                
                # Previous day highest watermarks should copy yesterday's daily watermarks (5100, 5050)
                assert bot.previous_day_highest_balance == 5100.0
                assert bot.previous_day_highest_equity == 5050.0
                
                # Today's daily highest watermarks should start from current account balance/equity (5100, 4900)
                assert bot.daily_highest_balance == 5100.0
                assert bot.daily_highest_equity == 4900.0
