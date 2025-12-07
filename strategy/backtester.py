import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json

class Backtester:
    """Backtest trading strategy on historical data"""
    
    def __init__(self, initial_balance=10000, risk_per_trade=1.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.trades = []
        self.equity_curve = []
        
    def run_backtest(self, historical_data, strategy):
        """Run backtest on historical data"""
        print("🔬 Starting Backtest...")
        print("=" * 60)
        
        self.balance = self.initial_balance
        self.trades = []
        self.equity_curve = [{'date': historical_data.index[0], 'equity': self.balance}]
        
        open_position = None
        
        for i in range(200, len(historical_data)):
            # Get data up to current point
            df_slice = historical_data.iloc[:i+1].copy()
            current_bar = historical_data.iloc[i]
            
            # Check if we have an open position
            if open_position:
                # Check stop loss
                if (open_position['signal'] == 'BUY' and current_bar['low'] <= open_position['stop_loss']) or \
                   (open_position['signal'] == 'SELL' and current_bar['high'] >= open_position['stop_loss']):
                    # Stop loss hit
                    exit_price = open_position['stop_loss']
                    self._close_position(open_position, exit_price, current_bar.name, 'STOP_LOSS')
                    open_position = None
                    
                # Check take profit
                elif (open_position['signal'] == 'BUY' and current_bar['high'] >= open_position['take_profit']) or \
                     (open_position['signal'] == 'SELL' and current_bar['low'] <= open_position['take_profit']):
                    # Take profit hit
                    exit_price = open_position['take_profit']
                    self._close_position(open_position, exit_price, current_bar.name, 'TAKE_PROFIT')
                    open_position = None
            
            # If no position, check for new signals
            if not open_position:
                signal, reason = strategy.generate_signal(df_slice)
                
                if signal in ['BUY', 'SELL']:
                    stats = strategy.get_strategy_stats(df_slice)
                    entry_price = current_bar['close']
                    atr = stats.get('atr', entry_price * 0.01)
                    
                    # Calculate position details (simplified for backtest)
                    from strategy.stoploss_calc import StopLossCalculator
                    risk_calc = StopLossCalculator(self.balance, self.risk_per_trade)
                    
                    stop_loss, take_profit = risk_calc.calculate_stop_loss_take_profit(
                        signal, entry_price, atr,
                        zone=stats.get('zone', 'EQUILIBRIUM'),
                        market_structure=stats.get('market_structure', 'NEUTRAL')
                    )
                    
                    lot_size = risk_calc.calculate_position_size(entry_price, stop_loss)
                    
                    open_position = {
                        'signal': signal,
                        'entry_price': entry_price,
                        'entry_date': current_bar.name,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'lot_size': lot_size,
                        'reason': reason
                    }
            
            # Record equity
            self.equity_curve.append({
                'date': current_bar.name,
                'equity': self.balance
            })
        
        # Close any remaining position at final price
        if open_position:
            final_price = historical_data.iloc[-1]['close']
            self._close_position(open_position, final_price, historical_data.index[-1], 'END_OF_DATA')
        
        return self.get_performance_metrics()
    
    def _close_position(self, position, exit_price, exit_date, exit_reason):
        """Close position and calculate P&L"""
        entry_price = position['entry_price']
        lot_size = position['lot_size']
        
        if position['signal'] == 'BUY':
            pnl = (exit_price - entry_price) * lot_size * 100
        else:  # SELL
            pnl = (entry_price - exit_price) * lot_size * 100
        
        self.balance += pnl
        
        trade_record = {
            'entry_date': position['entry_date'],
            'exit_date': exit_date,
            'signal': position['signal'],
            'entry_price': entry_price,
            'exit_price': exit_price,
            'stop_loss': position['stop_loss'],
            'take_profit': position['take_profit'],
            'lot_size': lot_size,
            'pnl': pnl,
            'pnl_percent': (pnl / self.initial_balance) * 100,
            'exit_reason': exit_reason,
            'reason': position['reason']
        }
        
        self.trades.append(trade_record)
    
    def get_performance_metrics(self):
        """Calculate performance metrics"""
        if not self.trades:
            return {"error": "No trades executed"}
        
        df_trades = pd.DataFrame(self.trades)
        
        # Basic metrics
        total_trades = len(df_trades)
        winning_trades = df_trades[df_trades['pnl'] > 0]
        losing_trades = df_trades[df_trades['pnl'] < 0]
        
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
        
        avg_win = winning_trades['pnl'].mean() if len(winning_trades) > 0 else 0
        avg_loss = losing_trades['pnl'].mean() if len(losing_trades) > 0 else 0
        
        profit_factor = abs(winning_trades['pnl'].sum() / losing_trades['pnl'].sum()) if len(losing_trades) > 0 and losing_trades['pnl'].sum() != 0 else 0
        
        # Equity curve analysis
        df_equity = pd.DataFrame(self.equity_curve)
        max_equity = df_equity['equity'].max()
        max_drawdown = ((df_equity['equity'] - max_equity) / max_equity * 100).min()
        
        # Returns
        total_return = ((self.balance - self.initial_balance) / self.initial_balance) * 100
        
        metrics = {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': round(win_rate, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'total_return': round(total_return, 2),
            'final_balance': round(self.balance, 2),
            'max_drawdown': round(max_drawdown, 2),
            'sharpe_ratio': self._calculate_sharpe_ratio(df_equity),
            'best_trade': round(df_trades['pnl'].max(), 2),
            'worst_trade': round(df_trades['pnl'].min(), 2)
        }
        
        return metrics
    
    def _calculate_sharpe_ratio(self, df_equity, risk_free_rate=0.02):
        """Calculate Sharpe Ratio"""
        if len(df_equity) < 2:
            return 0
        
        returns = df_equity['equity'].pct_change().dropna()
        if len(returns) == 0 or returns.std() == 0:
            return 0
        
        excess_returns = returns.mean() - (risk_free_rate / 252)  # Daily risk-free rate
        sharpe = (excess_returns / returns.std()) * np.sqrt(252)  # Annualized
        
        return round(sharpe, 2)
    
    def print_results(self, metrics):
        """Print backtest results in formatted manner"""
        print("\n📊 BACKTEST RESULTS")
        print("=" * 60)
        print(f"Total Trades: {metrics['total_trades']}")
        print(f"Win Rate: {metrics['win_rate']}%")
        print(f"Winning Trades: {metrics['winning_trades']} | Losing Trades: {metrics['losing_trades']}")
        print(f"Average Win: ${metrics['avg_win']:.2f} | Average Loss: ${metrics['avg_loss']:.2f}")
        print(f"Profit Factor: {metrics['profit_factor']}")
        print("=" * 60)
        print(f"Initial Balance: ${self.initial_balance:,.2f}")
        print(f"Final Balance: ${metrics['final_balance']:,.2f}")
        print(f"Total Return: {metrics['total_return']}%")
        print(f"Max Drawdown: {metrics['max_drawdown']}%")
        print(f"Sharpe Ratio: {metrics['sharpe_ratio']}")
        print("=" * 60)
        print(f"Best Trade: ${metrics['best_trade']:.2f}")
        print(f"Worst Trade: ${metrics['worst_trade']:.2f}")
        print("=" * 60)
    
    def save_results(self, filename="backtest_results.json"):
        """Save backtest results to file"""
        results = {
            'metrics': self.get_performance_metrics(),
            'trades': self.trades,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"\n💾 Results saved to {filename}")
