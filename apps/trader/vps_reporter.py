import requests
from datetime import datetime


VPS_BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 5


def _post(endpoint: str, payload: dict) -> bool:
    try:
        resp = requests.post(f"{VPS_BASE_URL}{endpoint}", json=payload, timeout=TIMEOUT)
        if resp.status_code == 200:
            print(f"✅ VPS {endpoint} posted")
            return True
        print(f"⚠️ VPS {endpoint} returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"⚠️ VPS {endpoint} failed (bot continues): {e}")
        return False


def ping_health() -> bool:
    try:
        resp = requests.get(f"{VPS_BASE_URL}/health", timeout=TIMEOUT)
        if resp.status_code == 200:
            print(f"✅ VPS health OK: {resp.json()}")
            return True
        print(f"⚠️ VPS health returned {resp.status_code}")
        return False
    except Exception as e:
        print(f"⚠️ VPS unreachable at startup (bot continues): {e}")
        return False


def post_signal(
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    gate_summary: str = "",
) -> bool:
    try:
        payload = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "gate_summary": gate_summary,
            "timestamp": datetime.utcnow().isoformat(),
        }
        resp = requests.post(f"{VPS_BASE_URL}/signal", json=payload, timeout=TIMEOUT)
        if resp.status_code == 200:
            print(f"✅ VPS signal posted: {direction} {symbol} @ {entry}")
            return True
        print(f"⚠️ VPS /signal returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"⚠️ VPS post_signal failed (bot continues): {e}")
        return False


def post_trade_result(
    symbol: str,
    direction: str,
    result: str,
    pnl: float,
    note: str = "",
) -> bool:
    try:
        payload = {
            "symbol": symbol,
            "direction": direction,
            "result": result,
            "pnl": pnl,
            "note": note,
            "close_time": datetime.utcnow().isoformat(),
        }
        resp = requests.post(f"{VPS_BASE_URL}/trade-result", json=payload, timeout=TIMEOUT)
        if resp.status_code == 200:
            print(f"✅ VPS trade result posted: {result.upper()} PnL={pnl}")
            return True
        print(f"⚠️ VPS /trade-result returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"⚠️ VPS post_trade_result failed (bot continues): {e}")
        return False


def post_daily_summary(
    total_trades: int,
    wins: int,
    losses: int,
    net_pnl: float,
    max_drawdown: float,
    session: str = "ALL",
) -> bool:
    payload = {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total_trades * 100), 1) if total_trades else 0.0,
        "net_pnl": round(net_pnl, 2),
        "max_drawdown": round(max_drawdown, 2),
        "session": session,
    }
    return _post("/daily-summary", payload)


def check_bot_active() -> bool:
    try:
        resp = requests.get(f"{VPS_BASE_URL}/bot/status", timeout=3)
        if resp.status_code == 200:
            return resp.json().get("trading", True)
        print(f"⚠️ VPS /bot/status returned {resp.status_code}")
        return True
    except Exception as e:
        print(f"⚠️ VPS bot status check failed (bot continues): {e}")
        return True