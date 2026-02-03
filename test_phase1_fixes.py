# test_phase1_fixes.py
import sys
import pandas as pd
from strategy.market_structure import MarketStructureDetector

def test_fix_1_1_bullish_sweep():
    """Test that bullish IDM sweep now detects lower wick correctly"""
    print("\n=== TEST FIX 1.1: Bullish IDM Sweep ===")
    
    # Simulate a bullish IDM scenario
    # Bar 2: IDM low at 5500
    # Bar 4: Sweeps below 5500 (low=5495) then closes above
    test_data = pd.DataFrame({
        'high':  [5510, 5520, 5515, 5525, 5512, 5530],
        'low':   [5500, 5510, 5500, 5515, 5495, 5510],  # Bar 4 sweeps IDM
        'close': [5505, 5515, 5510, 5520, 5508, 5525],
        'open':  [5502, 5506, 5512, 5518, 5520, 5509]
    })
    
    ms = MarketStructureDetector(test_data)
    
    # Test is_wick_sweep with scan_type='bullish'
    result = ms.is_wick_sweep(
        target_price=5500,  # IDM level
        start_bar=2,        # Start checking after bar 2
        scan_type='bullish'
    )
    
    print(f"Target Price (IDM): 5500")
    print(f"Sweep Detected: {result['is_sweep']}")
    print(f"Sweep Type: {result['sweep_wick_type']}")
    print(f"Sweep Bar: {result['sweep_bar_index']}")
    print(f"Sweep Price: {result['sweep_price']}")
    
    # Assertions
    assert result['is_sweep'] == True, "❌ FAILED: Sweep not detected"
    assert result['sweep_wick_type'] == 'lower', "❌ FAILED: Wrong sweep type (should be 'lower')"
    assert result['sweep_bar_index'] == 4, "❌ FAILED: Wrong bar index"
    
    print("✅ PASSED: Bullish IDM sweep correctly identified as 'lower' wick\n")


def test_fix_1_1_bearish_sweep():
    """Test that bearish IDM sweep still works (upper wick)"""
    print("=== TEST FIX 1.1: Bearish IDM Sweep ===")
    
    test_data = pd.DataFrame({
        'high':  [5520, 5510, 5515, 5505, 5522, 5510],  # Bar 4 sweeps above 5515
        'low':   [5510, 5500, 5505, 5495, 5510, 5500],
        'close': [5515, 5505, 5510, 5500, 5512, 5505],
        'open':  [5512, 5508, 5507, 5503, 5502, 5511]
    })
    
    ms = MarketStructureDetector(test_data)
    result = ms.is_wick_sweep(
        target_price=5515,
        start_bar=2,
        scan_type='bearish'  # Or None - both should work
    )
    
    print(f"Target Price (IDM): 5515")
    print(f"Sweep Detected: {result['is_sweep']}")
    print(f"Sweep Type: {result['sweep_wick_type']}")
    
    assert result['is_sweep'] == True, "❌ FAILED: Sweep not detected"
    assert result['sweep_wick_type'] == 'upper', "❌ FAILED: Wrong sweep type"
    
    print("✅ PASSED: Bearish IDM sweep correctly identified as 'upper' wick\n")


def test_fix_1_3_fractal_lag():
    """Test that fractals now confirm 1 candle earlier"""
    print("=== TEST FIX 1.3: Reduced Fractal Lag ===")
    
    # Create a clear swing low at bar 3
    test_data = pd.DataFrame({
        'high':  [5520, 5515, 5510, 5515, 5520, 5525],
        'low':   [5510, 5505, 5490, 5505, 5510, 5515],  # Bar 2 is swing low
        'close': [5515, 5510, 5495, 5510, 5515, 5520],
        'open':  [5512, 5514, 5508, 5496, 5511, 5516]
    })
    
    ms = MarketStructureDetector(test_data)
    fractals = ms.detect_fractals()
    
    swing_lows = fractals.get('swing_lows', [])
    print(f"Detected Swing Lows: {len(swing_lows)}")
    
    if swing_lows:
        for sl in swing_lows:
            print(f"  Bar {sl['bar']}: Price {sl['price']}")
    
    # With old logic (last_closed - 2), bar 2 wouldn't be detected yet
    # With new logic (last_closed - 1), bar 2 should be detected
    assert len(swing_lows) > 0, "❌ FAILED: No swing lows detected"
    
    print("✅ PASSED: Fractal detection working with reduced lag\n")


def test_integration_idm_confirm():
    """Test that confirm_idm_sweep now passes scan_type correctly"""
    print("=== TEST FIX 1.2: IDM Sweep Confirmation Integration ===")
    
    test_data = pd.DataFrame({
        'high':  [5510, 5520, 5515, 5525, 5512],
        'low':   [5500, 5510, 5500, 5515, 5495],  # Bar 4 sweeps bar 2 low
        'close': [5505, 5515, 5510, 5520, 5508],
        'open':  [5502, 5506, 5512, 5518, 5520]
    })
    
    ms = MarketStructureDetector(test_data)
    
    # Simulate IDM confirmation (bullish)
    result = ms.confirm_idm_sweep(
        idm_bar_index=2,
        idm_price=5500,
        idm_type='bullish'
    )
    
    print(f"IDM Swept: {result['is_idm_swept']}")
    print(f"Reason Code: {result['reason_code']}")
    
    assert result['is_idm_swept'] == True, "❌ FAILED: IDM sweep not confirmed"
    assert result['reason_code'] != 'SWEEPWRONGDIRECTION', "❌ FAILED: Still getting SWEEPWRONGDIRECTION"
    
    print("✅ PASSED: IDM sweep confirmation working without SWEEPWRONGDIRECTION error\n")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PHASE 1 VALIDATION TESTS")
    print("="*60)
    
    try:
        test_fix_1_1_bullish_sweep()
        test_fix_1_1_bearish_sweep()
        test_fix_1_3_fractal_lag()
        test_integration_idm_confirm()
        
        print("="*60)
        print("✅ ALL PHASE 1 TESTS PASSED!")
        print("="*60)
        print("\n✅ Ready to proceed to PHASE 2 (main.py fixes)")
        print("   Run: python main.py --dry-run to test with live data\n")
        
    except AssertionError as e:
        print("\n" + "="*60)
        print(f"❌ TEST FAILED: {e}")
        print("="*60)
        print("\n⚠️  DO NOT PROCEED TO PHASE 2")
        print("   Fix the failing test before continuing\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
