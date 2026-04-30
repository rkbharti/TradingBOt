# apps/backtest/backtest_logger.py
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import pandas as pd

class BacktestLogger:
    def __init__(self, reset: bool = False, base_path: str = "backtest_logs"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(exist_ok=True)
        
        self.summary_file = self.base_path / "run_summary.csv"
        self.trade_file = self.base_path / "trade_log.csv"
        
        self.reset = reset
        self.run_id = None
        self.run_started_at = None
        self.trades = []
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        
    def start_run(self, run_id: str):
        self.run_id = run_id
        self.run_started_at = datetime.now().isoformat()
        self.trades = []
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        
        if self.reset:
            self.summary_file.unlink(missing_ok=True)
            self.trade_file.unlink(missing_ok=True)
    
    def log_trade_open(self, timestamp: str, direction: str, entry: float, 
                      sl: float, tp: float, risk_amount: float):
        self.total_trades += 1
        self.trades.append({
            'run_id': self.run_id,
            'trade_id': len(self.trades) + 1,
            'entry_time': timestamp,
            'direction': direction,
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'risk_amount': risk_amount,
            'exit_time': None,
            'exit_reason': None,
            'pnl': 0.0,
            'rr': 0.0
        })
    
    def log_trade_close(self, timestamp: str, exit_reason: str, pnl: float):
        if self.trades:
            last_trade = self.trades[-1]
            last_trade['exit_time'] = timestamp
            last_trade['exit_reason'] = exit_reason
            last_trade['pnl'] = pnl
            
            # Calculate RR from last trade
            entry = last_trade['entry']
            sl = last_trade['sl']
            tp = last_trade['tp']
            if sl and entry and tp:
                last_trade['rr'] = abs(tp - entry) / abs(entry - sl)
            
            if pnl > 0:
                self.wins += 1
            else:
                self.losses += 1
    
    def finalize_run(self, final_capital: float, initial_capital: float, max_dd: float = 0.0):
        net_pnl = final_capital - initial_capital
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0

        run_summary = {
            'run_id': self.run_id,
            'started_at': self.run_started_at,
            'finished_at': datetime.now().isoformat(),
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'final_capital': final_capital,
            'net_pnl': net_pnl,
            'max_dd': round(max_dd, 4)
        }

        summary_df = pd.DataFrame([run_summary])
        if self.summary_file.exists():
            summary_df.to_csv(self.summary_file, mode='a', header=False, index=False)
        else:
            summary_df.to_csv(self.summary_file, index=False)

        if self.trades:
            trades_df = pd.DataFrame(self.trades)
            if self.trade_file.exists():
                trades_df.to_csv(self.trade_file, mode='a', header=False, index=False)
            else:
                trades_df.to_csv(self.trade_file, index=False)

        print(f"📊 Logged {self.total_trades} trades to {self.trade_file}")
        print(f"📈 Run summary saved to {self.summary_file}")

        return run_summary