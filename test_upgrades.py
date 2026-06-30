import os
import sys

sys.path.insert(0, r"c:\Python_Project\tradingbot\TradingBOt")

# Set environment variables for testing (Don't override user's SYMBOL)
# os.environ["SYMBOL"] = "XAUUSD"
os.environ["PROP_FIRM"] = "AtlasFunded"
os.environ["ACCOUNT_SIZE"] = "5000"

# Import the TradingBot
from apps.trader.main import XAUUSDTradingBot

def test_single_cycle():
    bot = XAUUSDTradingBot()
    try:
        bot.resolve_symbol()
        bot.mtf.symbol = bot.symbol
    except:
        pass
    print(f"🔄 Initializing Bot for {bot.symbol}...")
    
    print("🔄 Connecting to MT5 and fetching data...")
    # 1. Fetch data
    market_data, current_price = bot.fetch_and_prepare()
    if market_data is None or current_price is None:
        print("❌ Failed to fetch market data from MT5.")
        return
        
    print(f"✅ Market data fetched! Live bid: {current_price.get('bid')}")
    
    # 2. Fetch all timeframes (including W1)
    m1_raw  = bot.mtf.fetch_data("M1")
    m5_raw  = bot.mtf.fetch_data("M5")
    m15_raw = bot.mtf.fetch_data("M15")
    h1_raw  = bot.mtf.fetch_data("H1")
    h4_raw  = bot.mtf.fetch_data("H4")
    d1_raw  = bot.mtf.fetch_data("D1")
    w1_raw  = bot.mtf.fetch_data("W1")
    
    m5_df  = m5_raw.get("df") if isinstance(m5_raw, dict) else m5_raw
    m15_df = m15_raw.get("df") if isinstance(m15_raw, dict) else m15_raw
    h1_df  = h1_raw.get("df") if isinstance(h1_raw, dict) else h1_raw
    h4_df  = h4_raw.get("df") if isinstance(h4_raw, dict) else h4_raw
    d1_df  = d1_raw.get("df") if isinstance(d1_raw, dict) else d1_raw
    w1_df  = w1_raw.get("df") if isinstance(w1_raw, dict) else w1_raw
    
    print(f"📊 Timeframe Data Lengths:")
    print(f"   M5:  {len(m5_df) if m5_df is not None else 0}")
    print(f"   M15: {len(m15_df) if m15_df is not None else 0}")
    print(f"   H1:  {len(h1_df) if h1_df is not None else 0}")
    print(f"   H4:  {len(h4_df) if h4_df is not None else 0}")
    print(f"   D1:  {len(d1_df) if d1_df is not None else 0}")
    print(f"   W1:  {len(w1_df) if w1_df is not None else 0}")
    
    if any(df is None or len(df) == 0 for df in [m5_df, m15_df, h4_df, d1_df, w1_df]):
        print("❌ Missing required timeframe data!")
        return
        
    print("🧠 Passing data into Upgraded Signal Engine...")
    from datetime import datetime, timezone
    try:
        result = bot.signal_engine.evaluate(
            m5_df=m5_df,
            m15_df=m15_df,
            h1_df=h1_df,
            h4_df=h4_df,
            d1_df=d1_df,
            w1_df=w1_df,
            now_utc=datetime.now(timezone.utc),
            asian_session_pois=bot.asian_session_pois,
            m1=m1_raw,
            cbdr_levels=None,
            asian_range=None,
        )
        print("✅ Signal Engine evaluated successfully without crashing!")
        
        # Check bias and pullback mode
        step1 = result.gates.get("step_1_htf_bias", {}) if result.gates else {}
        print(f"\n🔍 UPGRADE 1 & 2 VERIFICATION (HTF Bias & Pullback):")
        print(f"   W1 Bias:       {step1.get('w1_bias', 'UNKNOWN')}")
        print(f"   D1 Bias:       {step1.get('d1_bias', 'UNKNOWN')}")
        print(f"   H4 Bias:       {step1.get('h4_bias', 'UNKNOWN')}")
        print(f"   Is Pullback:   {step1.get('is_pullback', False)}")
        print(f"   Direction:     {step1.get('direction', 'UNKNOWN')}")
        print(f"   Reason:        {step1.get('reason', 'UNKNOWN')}")
        
        # Check POI anchoring if applicable
        if "step_4_valid_poi" in result.gates and result.gates["step_4_valid_poi"].get("passed"):
            print(f"\n🔍 UPGRADE 3 VERIFICATION (POI Anchoring):")
            print("   ✅ M15 POI successfully anchored within HTF boundaries!")
            
    except Exception as e:
        print(f"❌ ERROR inside Signal Engine: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_single_cycle()
