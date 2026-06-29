import MetaTrader5 as mt5

def main():
    print("🔄 Connecting to MT5 Terminal...")
    if not mt5.initialize():
        print("❌ Failed to initialize MT5")
        return

    print("✅ Connected!")
    print("\n🔍 Scanning for Gold, Euro, Bitcoin, and US30 symbols...")
    
    symbols = mt5.symbols_get()
    if not symbols:
        print("❌ Could not retrieve symbols from MT5")
        mt5.shutdown()
        return

    targets = ["XAU", "GOLD", "EURUSD", "BTC", "US30", "WS30"]
    
    found = []
    for s in symbols:
        name = s.name.upper()
        if any(t in name for t in targets):
            tick = mt5.symbol_info_tick(s.name)
            if tick:
                found.append(f"   ➤ {s.name:<15} Live Price: {tick.bid}")
            else:
                found.append(f"   ➤ {s.name:<15} Live Price: N/A (No tick data)")

    for f in sorted(found):
        print(f)
        
    print("\n💡 VERDICT: If the symbols above have a suffix (like .pro, .raw, .a), you must update your ecosystem.config.js to use that EXACT name!")
    mt5.shutdown()

if __name__ == "__main__":
    main()
