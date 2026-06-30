import MetaTrader5 as mt5
if not mt5.initialize():
    print('Failed to init')
else:
    for sym in ['EURUSD', 'BTCUSD', 'US30', 'XAUUSD']:
        tick = mt5.symbol_info_tick(sym)
        if tick:
            print(f'{sym}: {tick.bid}')
        else:
            print(f'{sym}: None (Not found)')
mt5.shutdown()
