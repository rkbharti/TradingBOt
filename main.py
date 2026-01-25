# main.py
DRY_RUN = True

import os
import time
import json
import threading
from datetime import datetime, timedelta
import pandas as pd
import pytz
import requests
import signal
import sys
from utils.observation_logger import ObservationLogger
obs_logger = ObservationLogger()
obs_logger.bot_started()


# Local modules (must exist per your directory structure)
from utils.mt5_connection import MT5Connection
from strategy.multi_timeframe_fractal import MultiTimeframeFractal
from strategy.market_structure import MarketStructureDetector
from strategy.smc_enhanced.zones import ZoneCalculator
from strategy.idea_memory import IdeaMemory
from utils.volume_analyzer_gold import GoldVolumeAnalyzer





# Note: we no longer rely on direct in-process server imports.
# Communication to the dashboard is done via webhook POST to the server.
# (This keeps bot and server processes decoupled and robust.)

# Telegram config (optional)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "False").lower() == "true"

def send_telegram(message, silent=False):
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": silent
        }, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        print(f" ❌ Telegram error: {e}")
        return None

# ---------- Utilities ----------

def compute_atr_from_df(df: pd.DataFrame, period: int = 14) -> float:
    try:
        if df is None or len(df) == 0:
            return 0.0
        for c in ("high", "low", "close"):
            if c not in df.columns:
                return 0.0
        d = df[['high', 'low', 'close']].astype(float).copy()
        d['prev_close'] = d['close'].shift(1)
        d['tr1'] = d['high'] - d['low']
        d['tr2'] = (d['high'] - d['prev_close']).abs()
        d['tr3'] = (d['low'] - d['prev_close']).abs()
        d['tr'] = d[['tr1', 'tr2', 'tr3']].max(axis=1)
        if len(d['tr'].dropna()) >= period:
            atr = d['tr'].rolling(window=period, min_periods=1).mean().iloc[-1]
        else:
            atr = float(d['tr'].dropna().mean() or 0.0)
        if not atr or atr <= 0:
            recent = d.tail(max(3, len(d)))
            atr = float((recent['high'] - recent['low']).abs().mean() or 0.0)
        return float(atr)
    except Exception as e:
        print(f"❌ compute_atr_from_df error: {e}")
        return 0.0

def is_trading_session():
    now = datetime.now(pytz.utc)
    
    # 🛑 NEW: Check if it's Saturday (5) or Sunday (6)
    # This prevents the bot from thinking it's "New York Session" on a Sunday
    if now.weekday() >= 5: 
        return False, "WEEKEND_MARKET_CLOSED"

    t = now.hour + now.minute / 60.0
    if 8.0 <= t < 16.0:
        if 13.0 <= t < 16.0:
            return True, "NY_OVERLAP"
        return True, "LONDON"
    if 16.0 <= t < 21.0:
        return True, "NY_SESSION"
    
    # If it's a weekday but outside trading hours (e.g., 22:00 UTC)
    return False, "ASIAN"

def map_session_for_filter(session_name: str) -> str:
    if session_name is None:
        return "ASIAN"
    s = session_name.upper()
    if s in ("NY_OVERLAP", "NY-OVERLAP", "NY_OVER", "OVERLAP"):
        return "OVERLAP"
    if s in ("NY_SESSION", "NY-SESSION", "NY", "NEW_YORK", "NYSESSION"):
        return "NEW_YORK"
    if "LONDON" in s:
        return "LONDON"
    if "ASIAN" in s:
        return "ASIAN"
    return s

# ---------- Dashboard webhook sender ----------
def send_to_dashboard(bot_data: dict, analysis: dict, endpoint: str = "http://localhost:8000/webhook", timeout: float = 3.0):
    """
    Send a JSON snapshot to the dashboard server's /webhook endpoint.
    - Safe: catches exceptions and returns False on failure.
    - Replaces NaN values in serializable data by converting via json.dumps -> replace.
    """
    try:
        payload = {
            "bot_instance": bot_data,
            "analysis_data": analysis
        }
        # Attempt to serialize and replace NaN tokens to protect browser parsing
        try:
            json_payload = json.dumps(payload, default=str)
            if "NaN" in json_payload:
                json_payload = json_payload.replace("NaN", "null")
            resp = requests.post(endpoint, data=json_payload, headers={"Content-Type": "application/json"}, timeout=timeout)
        except TypeError:
            # fallback: send as json (requests will handle serialization)
            resp = requests.post(endpoint, json=payload, timeout=timeout)
        if resp.status_code == 200:
            return True
        else:
            print(f"   ⚠️ Dashboard POST returned status {resp.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"   ⚠️ Dashboard POST failed: {e}")
        return False
    except Exception as e:
        print(f"   ❌ Dashboard unexpected error: {e}")
        return False


def graceful_shutdown(signum=None, frame=None):
    print("🛑 Graceful shutdown initiated")
    try:
        obs_logger.bot_stopped()
    except Exception as e:
        print(f"⚠️ Failed to log bot stop: {e}")
    sys.exit(0)
signal.signal(signal.SIGINT, graceful_shutdown)   # Ctrl+C
signal.signal(signal.SIGTERM, graceful_shutdown)  # Kill / stop

# ---------- Bot ----------

class XAUUSDTradingBot:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.mt5 = MT5Connection(config_path)
        self.mtf = MultiTimeframeFractal(symbol="XAUUSD")
        self.idea_memory = IdeaMemory(expiry_minutes=30)
        self.volume_analyzer = None
        self.zone_calculator = ZoneCalculator
        self.running = False
        self.trade_log = []
        self.open_positions = []
        self.max_positions = 3
        self.max_lot_size = 2.0
        self.risk_per_trade_percent = 0.5
        self.current_session = "UNKNOWN"
        self.dry_run = DRY_RUN
        # Reaction logic: waiting for confirmation after HTF POI hit
        self.waiting_for_confirmation = False

    # MT5 wrappers
    def mt5_initialize(self):
        try:
            if hasattr(self.mt5, "initialize_mt5"):
                return self.mt5.initialize_mt5()
            if hasattr(self.mt5, "initialize"):
                return self.mt5.initialize()
        except Exception as e:
            print(f"❌ MT5 init error: {e}")
        return False

    def mt5_get_account(self):
        try:
            if hasattr(self.mt5, "get_account_info"):
                return self.mt5.get_account_info()
            if hasattr(self.mt5, "account_info"):
                return self.mt5.account_info()
        except Exception:
            pass
        return None

    def mt5_get_current_price(self):
        try:
            if hasattr(self.mt5, "get_current_price"):
                return self.mt5.get_current_price()
            if hasattr(self.mt5, "get_price"):
                return self.mt5.get_price()
        except Exception:
            pass
        return None

    def mt5_get_historical(self, bars=300):
        try:
            if hasattr(self.mt5, "get_historical_data"):
                return self.mt5.get_historical_data(bars=bars)
            if hasattr(self.mt5, "history"):
                return self.mt5.history(bars)
        except Exception:
            pass
        return None

    def mt5_place_order(self, side, lots, sl, tp):
        try:
            if self.dry_run:
                print(f" ⚠️ DRY_RUN enabled — simulated order: {side} {lots} lots SL={sl} TP={tp}")
                return f"DRY-{int(time.time())}"
            if hasattr(self.mt5, "place_order"):
                return self.mt5.place_order(side, lots, sl, tp)
            if hasattr(self.mt5, "order_send"):
                return self.mt5.order_send(side, lots, sl, tp)
        except Exception as e:
            print(f"❌ place_order error: {e}")
        return None

    def mt5_close_position(self, ticket, volume=None):
        try:
            if hasattr(self.mt5, "close_position"):
                return self.mt5.close_position(ticket, volume) if volume else self.mt5.close_position(ticket)
            if hasattr(self.mt5, "close_trade"):
                return self.mt5.close_trade(ticket, volume) if volume else self.mt5.close_trade(ticket)
        except Exception as e:
            print(f"❌ close position error: {e}")
        return False

    def mt5_modify_position(self, ticket, sl=None, tp=None):
        try:
            if hasattr(self.mt5, "modify_position"):
                return self.mt5.modify_position(ticket, sl, tp)
            if hasattr(self.mt5, "modify_trade"):
                return self.mt5.modify_trade(ticket, sl, tp)
        except Exception as e:
            print(f"❌ modify position error: {e}")
        return False

    def initialize(self):
        print("=== Initializing XAUUSDTradingBot ===")
        if not self.mt5_initialize():
            print("❌ MT5 initialization failed")
            return False

        acct = self.mt5_get_account()
        if acct:
            try:
                bal = float(acct.balance)
                self.risk_per_trade_percent = getattr(acct, "risk_per_trade", self.risk_per_trade_percent) or self.risk_per_trade_percent
                print(f"✅ Account balance: ${bal:,.2f}")
            except Exception:
                print("ℹ️ Could not read account balance cleanly")
        else:
            print("ℹ️ No account info available (continuing in read-only/test mode)")

        print("✅ Initialization complete")
        return True

    def fetch_and_prepare(self):
        market_data = self.mt5_get_historical(bars=300)
        if market_data is None:
            print("❌ Could not fetch historical data")
            return None, None
        if not isinstance(market_data, pd.DataFrame):
            try:
                market_data = pd.DataFrame(market_data)
            except Exception:
                print("❌ Historical data conversion failed")
                return None, None
        for c in ("high", "low", "close", "open", "tick_volume"):
            if c in market_data.columns:
                market_data[c] = pd.to_numeric(market_data[c], errors="coerce")

        current_price = self.mt5_get_current_price()
        if current_price is None:
            print("❌ Could not fetch current price")
            return market_data, None
        return market_data, current_price

    def analyze_once(self):
        is_active, session_name = is_trading_session()
        session_norm = map_session_for_filter(session_name)
        self.current_session = session_norm

        # ==============================================================================
        # NEW: WEEKEND/SLEEP HEARTBEAT
        # If market is closed, update dashboard with balance/status BEFORE sleeping
        # ==============================================================================
        if not is_active:
            print(f"⏸️ Market session '{session_norm}' not active for trading — sending heartbeat and sleeping")
            
            # 1. Get Account Balance so Dashboard isn't $0.00
            acct = self.mt5_get_account()
            equity = 0.0
            balance = 0.0
            if acct:
                try:
                    equity = float(getattr(acct, 'equity', 0.0))
                    balance = float(getattr(acct, 'balance', 0.0))
                except Exception:
                    pass
            
            # Apply your specific fallback if balance is read as 0
            if equity == 0.0 and balance == 0.0:
                equity = 77829.40
                balance = 77829.40

            # 2. Send "Sleep Mode" Data to Dashboard
            # We send empty chart data and "CLOSED" status to keep the UI alive
            bot_data = {
                "equity": equity,
                "balance": balance,
                "last_price": 0,
                "open_positions": self.open_positions,
                "closed_trades": [],
                "chart_data": [], 
                "current_session": session_norm 
            }
            
            analysis = {
                "market_structure": {"current_trend": "MARKET CLOSED"},
                "zone_strength": 0,
                "current_zone": "CLOSED",
                "pdh": 0,
                "pdl": 0,
                "zones": {}
            }
            
            # 3. Send the webhook
            send_to_dashboard(bot_data, analysis)
            return  # <--- STOP HERE (Do not run analysis logic)

        # ==============================================================================
        # ACTIVE TRADING LOGIC (Your Original Code)
        # ==============================================================================

        market_data, current_price = self.fetch_and_prepare()
        if market_data is None or current_price is None:
            return

        atr = compute_atr_from_df(market_data, period=14)

        spread = None
        if isinstance(current_price, dict):
            bid = float(current_price.get("bid", 0.0))
            ask = float(current_price.get("ask", 0.0))
            spread = abs(ask - bid)
            price_for_zones = bid
        else:
            bid = float(current_price)
            ask = bid
            spread = 0.0
            price_for_zones = bid

        mtf_conf = {"overall_bias": "NEUTRAL", "confidence": 0}
        try:
            mtf_conf = self.mtf.get_multi_tf_confluence()
        except Exception:
            pass

        vol_spike = 1.0
        volume_spike = False

        try:
            ms_detector = MarketStructureDetector(market_data)
            # note: older code tried to call get_market_structure_analysis; keep compatibility but prefer get_idm_state
            try:
                ms = ms_detector.get_market_structure_analysis()
            except Exception:
                ms = {"current_trend": "NEUTRAL", "choch_detected": False, "bos_level": None}
        except Exception:
            ms = {"current_trend": "NEUTRAL", "choch_detected": False, "bos_level": None}
            ms_detector = None

        try:
            latest_high = float(market_data['high'].max()) if 'high' in market_data.columns else price_for_zones
            latest_low = float(market_data['low'].min()) if 'low' in market_data.columns else price_for_zones
            zones = {}
            try:
                zones = ZoneCalculator.calculate_zones(latest_high, latest_low)
                current_zone = ZoneCalculator.classify_price_zone(price_for_zones, zones)
            except Exception:
                current_zone = "EQUILIBRIUM"
            try:
                zone_summary = ZoneCalculator.get_zone_summary(price_for_zones, zones)
                zone_strength = zone_summary.get("zone_strength", 0) if zone_summary else 0
            except Exception:
                zone_strength = 0
        except Exception:
            current_zone = "UNKNOWN"
            zone_strength = 0
            zone_summary = {}

        try:
            vol_an = GoldVolumeAnalyzer(market_data)
            self.volume_analyzer = vol_an
            recent = market_data.tail(20)
            avg_vol = float(recent['tick_volume'].iloc[:-1].mean()) if len(recent) > 1 else 0.0
            last_vol = float(recent['tick_volume'].iloc[-1]) if len(recent) >= 1 else 0.0
            vol_spike = (last_vol / avg_vol) if avg_vol > 0 else 1.0
            volume_spike = vol_spike > 1.5
        except Exception:
            volume_spike = False

        final_signal = "HOLD"
        reason = "No conditions met"

        print("\n" + "=" * 60)
        print(f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Session: {self.current_session}")
        print(f"💱 Price (bid): {bid:.2f} | Ask: {ask:.2f} | Spread: {spread:.4f}")
        print(f"📊 ATR: {atr:.4f} | Zone: {current_zone} (strength {zone_strength}%)")
        print(f"🔁 MTF Bias: {mtf_conf.get('overall_bias')} | Confidence: {mtf_conf.get('confidence')}")
        print(f"🏗 Market Structure: {ms.get('current_trend','NEUTRAL')} | BO S/CHOCH: {ms.get('choch_detected')}")
        print(f"📈 Volume spike: {volume_spike} (ratio {vol_spike:.2f} if available)")

        # Reaction logic: detect HTF POI hit and set waiting_for_confirmation
        try:
            hit_poi = False
            if zone_summary:
                # ZoneCalculator implementations often expose a 'hit_poi' or similar flag in the summary.
                hit_poi = bool(zone_summary.get('hit_poi') or zone_summary.get('price_hit_poi') or zone_summary.get('near_poi'))
            # Fallback conservative heuristic: strong zone and price is in DISCOUNT/PREMIUM
            if not hit_poi and zone_strength >= 30 and current_zone in ("DISCOUNT", "PREMIUM"):
                # This is a conservative proxy for "price hit meaningful HTF POI"
                hit_poi = True

            if hit_poi and not self.waiting_for_confirmation:
                self.waiting_for_confirmation = True
                print("⏳ HTF POI hit — waiting for M5 CHoCH confirmation before entering trades")

        except Exception:
            pass

        try:
            smc_state = {}
            if ms_detector is not None and hasattr(ms_detector, 'get_idm_state'):
                smc_state = ms_detector.get_idm_state()
            print(f"🌊 IDM present: {smc_state.get('is_idm_present', False)}")
            print(f"🪤 IDM swept: {smc_state.get('is_idm_swept', False)}")
            print(f"🧱 Structure confirmed: {smc_state.get('structure_confirmed', False)}")
            print(f"   MSS/CHOCH: {smc_state.get('mss_or_choch', 'NONE')} | reason: {smc_state.get('reason_code')}")
        except Exception as e:
            print(f"⚠️ SMC state unavailable: {e}")
            smc_state = {}

        print("-" * 60)

        try:
            # Determine whether we've seen a CHoCH on the timeframe used by the market-structure detector
            mss_or_choch = smc_state.get('mss_or_choch', '') or ''
            choch_detected = str(mss_or_choch).upper().startswith('CHOCH')

            idm_taken = smc_state.get('is_idm_swept', False)
            # prevent trading on a fractal/IDM that is still forming: enforce IDM bar is at least two bars older than last index
            idm_bar_index = smc_state.get('idm_bar_index')
            still_forming = False
            try:
                if idm_bar_index is not None and isinstance(idm_bar_index, int):
                    last_closed_idx = ms_detector._last_closed_index() if ms_detector is not None else (len(market_data)-1)
                    # require IDM bar to be at least two bars older than the most recent closed bar
                    if last_closed_idx is None or idm_bar_index > (last_closed_idx - 2):
                        still_forming = True
                        print("⚠️ IDM/fractal still forming — deferring trade until fractal confirmed by two subsequent closes")
            except Exception:
                still_forming = False

            bos_confirmed = smc_state.get('structure_confirmed', False)

            # Reaction logic: only trade if waiting_for_confirmation and CHoCH detected (M5 CHoCH requirement)
            # Additionally, ensure fractal/IDM isn't still forming
            can_execute_ch_confirmation = (self.waiting_for_confirmation and choch_detected and not still_forming)

            if (mtf_conf.get("overall_bias") == "BULLISH" and
                current_zone == "DISCOUNT" and
                zone_strength >= 30 and
                idm_taken == True and
                can_execute_ch_confirmation):
                final_signal = "BUY"
                reason = "Institutional Buy: Discount + Bullish + IDM Swept + M5 CHoCH confirmation"
            elif (mtf_conf.get("overall_bias") == "BEARISH" and
                  current_zone == "PREMIUM" and
                  zone_strength >= 30 and
                  idm_taken == True and
                  can_execute_ch_confirmation):
                final_signal = "SELL"
                reason = "Institutional Sell: Premium + Bearish + IDM Swept + M5 CHoCH confirmation"
            elif zone_strength >= 70 and idm_taken == True and can_execute_ch_confirmation:
                if current_zone == "DISCOUNT":
                    final_signal = "BUY"
                    reason = "Strong Discount zone + IDM confirmed + M5 CHoCH"
                elif current_zone == "PREMIUM":
                    final_signal = "SELL"
                    reason = "Strong Premium zone + IDM confirmed + M5 CHoCH"
            else:
                final_signal = "HOLD"
                if not self.waiting_for_confirmation:
                    reason = "Waiting for HTF POI hit (no confirmation flag set)"
                elif not choch_detected:
                    reason = "Waiting for M5 CHoCH confirmation"
                elif still_forming:
                    reason = "Fractal/IDM still forming - need 2 confirmed closes after candidate"
                elif zone_strength < 30:
                    reason = f"Zone too weak ({zone_strength:.0f}%) - need ≥30%"
                else:
                    reason = "No institutional confluence met"
        except Exception as e:
            final_signal = "HOLD"
            reason = f"Error evaluating signals: {e}"

        max_allowed_spread = 0.5
        if spread is None:
            spread = 0.0
        if spread > max_allowed_spread:
            reason = f"Spread too large ({spread:.4f})"
            final_signal = "HOLD"

        if len(self.open_positions) >= self.max_positions:
            reason = "Max positions reached"
            final_signal = "HOLD"

        if final_signal in ("BUY", "SELL"):
            entry_price = ask if final_signal == "BUY" else bid
            pip = 0.01
            sl_distance = max(atr * 2, 35 * pip)
            if final_signal == "BUY":
                sl = entry_price - sl_distance
                tp = entry_price + (atr * 3)
            else:
                sl = entry_price + sl_distance
                tp = entry_price - (atr * 3)

            acct = self.mt5_get_account()
            lot = 0.01
            if acct:
                try:
                    bal = float(acct.balance)
                    risk_amount = bal * (self.risk_per_trade_percent / 100.0)
                    pips_at_risk = abs(entry_price - sl) / pip
                    if pips_at_risk > 0:
                        lot_est = risk_amount / (pips_at_risk * 10)
                        lot = min(round(lot_est, 2), self.max_lot_size)
                        lot = max(lot, 0.01)
                except Exception:
                    lot = 0.01

            print(f"🔔 SIGNAL {final_signal} -> Entry {entry_price:.2f} | SL {sl:.2f} | TP {tp:.2f} | Lot {lot}")
            print(f"   Reason: {reason}")

            ticket = self.mt5_place_order(final_signal, lot, sl, tp)
            if ticket:
                pos = {
                    "ticket": ticket,
                    "signal": final_signal,
                    "entry_price": entry_price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "lot_size": lot,
                    "entry_time": datetime.now().isoformat(),
                    "zone": current_zone,
                    "session": self.current_session
                }
                self.open_positions.append(pos)
                log_entry = {"timestamp": datetime.now().isoformat(), "action": "OPEN", **pos}
                self.trade_log.append(log_entry)
                self.save_trade_log()
                print(f"✅ Tracked new position (ticket: {ticket})")
                # Clear waiting flag after we commit a trade so we don't immediately re-enter
                self.waiting_for_confirmation = False
            else:
                print("❌ Order failed or rejected")
        else:
            print(f"⏸ No trade executed: {reason}")
            self.trade_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "ANALYSIS",
                "price": bid,
                "spread": spread,
                "zone": current_zone,
                "zone_strength": zone_strength,
                "mtf": mtf_conf,
                "market_structure": ms,
                "smc_state": smc_state,
                "waiting_for_confirmation": self.waiting_for_confirmation,
                "reason": reason
            })
            if len(self.trade_log) > 1000:
                self.trade_log = self.trade_log[-1000:]
            self.save_trade_log()

        # ===== Dashboard webhook update (always try; fail silently) =====
        try:
            chart_data = []
            if market_data is not None and len(market_data) > 0:
                tail = market_data.tail(100)
                current_ts = int(time.time())
                for i, (idx, row) in enumerate(tail.iterrows()):
                    cand_time = current_ts - (len(tail) - i - 1) * 60
                    open_v = float(row.get('open', 0)) if hasattr(row, 'get') else float(row['open'] if 'open' in row else 0)
                    high_v = float(row.get('high', 0)) if hasattr(row, 'get') else float(row['high'] if 'high' in row else 0)
                    low_v = float(row.get('low', 0)) if hasattr(row, 'get') else float(row['low'] if 'low' in row else 0)
                    close_v = float(row.get('close', 0)) if hasattr(row, 'get') else float(row['close'] if 'close' in row else 0)
                    chart_data.append({
                        "time": cand_time,
                        "open": open_v,
                        "high": high_v,
                        "low": low_v,
                        "close": close_v
                    })

            acct = self.mt5_get_account()
            equity = 0.0
            balance = 0.0
            if acct:
                try:
                    equity = float(getattr(acct, 'equity', 0.0))
                    balance = float(getattr(acct, 'balance', 0.0))
                except Exception:
                    try:
                        equity = float(acct.equity) if hasattr(acct, 'equity') else 0.0
                        balance = float(acct.balance) if hasattr(acct, 'balance') else 0.0
                    except Exception:
                        pass

            if equity == 0.0 and balance == 0.0:
                equity = 77829.40
                balance = 77829.40

            bot_data = {
                "equity": equity,
                "balance": balance,
                "last_price": bid,
                "open_positions": self.open_positions,
                "closed_trades": [],
                "chart_data": chart_data,
                "current_session": self.current_session
            }

            analysis = {
                "market_structure": ms,
                "zone_strength": zone_strength,
                "current_zone": current_zone,
                "pdh": latest_high if 'latest_high' in locals() else (float(market_data['high'].max()) if 'high' in market_data.columns else 0),
                "pdl": latest_low if 'latest_low' in locals() else (float(market_data['low'].min()) if 'low' in market_data.columns else 0),
                "zones": zones if 'zones' in locals() else {}
            }

            # Send to dashboard (webhook)
            sent = send_to_dashboard(bot_data, analysis)
            if sent:
                print("📡 Dashboard webhook POST successful")
            else:
                print("📡 Dashboard webhook POST failed or not reachable (continuing)")

            print(f"   💰 Equity: ${equity:,.2f} | Balance: ${balance:,.2f}")
            print(f"   📊 Chart candles: {len(chart_data)} | Price: ${bid:.2f}")
            print(f"   🎯 Zone: {current_zone} | Strength: {zone_strength}%")

        except Exception as e:
            print(f"⚠️ Dashboard update failed: {e}")

        print("=" * 60 + "\n")

    # Persistence
    def save_trade_log(self, filename="tradelog.json"):
        try:
            with open(filename, "w") as f:
                json.dump(self.trade_log, f, indent=2, default=str)
        except Exception as e:
            print(f"❌ Error saving trade log: {e}")

    def load_trade_log(self, filename="tradelog.json"):
        try:
            if os.path.exists(filename):
                with open(filename, "r") as f:
                    self.trade_log = json.load(f)
                print(f"✅ Loaded {len(self.trade_log)} log entries")
        except Exception as e:
            print(f"❌ Error loading trade log: {e}")

    def cleanup(self):
        try:
            if hasattr(self.mt5, "shutdown"):
                self.mt5.shutdown()
            elif hasattr(self.mt5, "disconnect"):
                self.mt5.disconnect()
        except Exception as e:
            print(f"⚠️ Error shutting down MT5 connection: {e}")
        self.save_trade_log()
        print("✅ Bot cleaned up")

# CLI main
def main():
    bot = XAUUSDTradingBot()
    if not bot.initialize():
        print("❌ Bot initialization failed — exiting")
        return

    bot.load_trade_log()
    bot.running = True
    print("🚀 Bot started (DRY_RUN=%s). Press Ctrl+C to stop." % bot.dry_run)

    try:
        while bot.running:
            try:
                bot.analyze_once()
            except Exception as e:
                print(f"⚠️ Analysis exception (continuing): {e}")

            for _ in range(60):
                time.sleep(1)
                if not bot.running:
                    break

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    finally:
        bot.running = False
        try:
            obs_logger.bot_stopped()
        except Exception:
            pass
        bot.cleanup()


if __name__ == "__main__":
    main()