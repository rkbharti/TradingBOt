# server.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import json
from datetime import datetime, date, timedelta
import hashlib
import traceback
import math
# from dotenv import load_dotenv
# import os

# load_dotenv()

# VPS_BASE_URL = os.getenv("VPS_BASE_URL", "http://127.0.0.1:8000")

# ============================================================================== 
# 🎨 DASHBOARD v3 - Webhook Bridge ready FastAPI server
# - Accepts POST /webhook from trading bot (main.py)
# - Cleans payload (NaN -> null)
# - Updates internal bot_state and PnL tracker
# - Broadcasts to dashboard clients via WebSocket
# - Preserves original HTML/CSS layout in html_content
# ==============================================================================

active_connections = []
bot_state = {}
bot_paused = False

# --- ENHANCED PnL TRACKER ---
class DailyPnLTracker:
    def __init__(self):
        self.realized_pnl = 0.0
        self.last_reset_date = datetime.now().date()
        self.history = {}
        self.total_realized = 0.0
        self.processed_ticket_ids = set()

    def _ensure_today(self):
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.last_reset_date = today
            self.realized_pnl = 0.0

    def add_closed_trade(self, pnl: float, when: datetime = None, ticket: str = None):
        if ticket:
            try:
                if ticket in self.processed_ticket_ids:
                    return
                self.processed_ticket_ids.add(ticket)
            except Exception:
                pass

        if when is None:
            when = datetime.now()
        d = when.date()
        self._ensure_today()
        ds = d.isoformat()
        self.history[ds] = self.history.get(ds, 0.0) + float(pnl)
        if d == datetime.now().date():
            self.realized_pnl += float(pnl)
        self.total_realized += float(pnl)

    def get_daily(self):
        self._ensure_today()
        return float(self.realized_pnl)

    def get_weekly(self):
        today = datetime.now().date()
        start = today - timedelta(days=today.weekday())
        total = 0.0
        d = start
        for i in range(7):
            ds = d.isoformat()
            total += float(self.history.get(ds, 0.0))
            d = d + timedelta(days=1)
        return float(total)

    def get_total(self):
        return float(self.total_realized)

pnl_tracker = DailyPnLTracker()

# --- BROADCAST LOOP ---
async def broadcast_loop():
    last_state_hash = None
    while True:
        if active_connections and bot_state:
            try:
                # Sanitize JSON to avoid NaN issues; browser rejects NaN
                state_json = json.dumps(bot_state, sort_keys=True, default=str).replace("NaN", "null")
                state_hash = hashlib.sha256(state_json.encode()).hexdigest()
                if state_hash != last_state_hash:
                    payload = state_json
                    try:
                        print(f"📡 Broadcasting state update ({len(active_connections)} clients) size={len(payload)} bytes")
                    except Exception:
                        pass
                    for conn in active_connections[:]:
                        try:
                            await conn.send_text(payload)
                        except Exception:
                            # per-connection exceptions ignored; websocket handler will remove dead connections
                            pass
                    last_state_hash = state_hash
            except Exception as e:
                print(f"⚠️ Broadcast serialization error: {e}")
        await asyncio.sleep(3)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return html_content

# ---------------------------------------------------------------------------
# Keep the dashboard HTML/CSS content intact below (unchanged semantics/style)
# ---------------------------------------------------------------------------
html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>GUARDEER OS v4.0 - Institutional SMC Terminal</title>

    <script src="https://unpkg.com/lightweight-charts@4.0.0/dist/lightweight-charts.standalone.production.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">

    <style>
        :root {
            --bg-black: #000000;
            --bg-panel: #060607;
            --bg-panel-header: #0c0c0e;
            --border-color: #16161a;
            --text-main: #d1d1d6;
            --text-muted: #636366;
            --neon-green: #00ff88;
            --neon-red: #ff4d4d;
            --neon-amber: #ff9f0a;
            --neon-blue: #00d4ff;
            --terminal-gold: #c5a880;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
        }

        body {
            background-color: var(--bg-black);
            color: var(--text-main);
            font-family: 'Rajdhani', sans-serif;
            font-size: 13px;
            letter-spacing: 0.5px;
            overflow-x: hidden;
            padding-bottom: 30px;
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Utility Classes */
        .txt-green { color: var(--neon-green) !important; }
        .txt-red { color: var(--neon-red) !important; }
        .txt-amber { color: var(--neon-amber) !important; }
        .txt-muted { color: var(--text-muted) !important; }
        .txt-gold { color: var(--terminal-gold) !important; }

        /* HEADER */
        .terminal-header {
            background-color: var(--bg-black);
            border-bottom: 1px solid var(--border-color);
            display: grid;
            grid-template-columns: 1fr auto 1fr;
            align-items: center;
            padding: 8px 16px;
            height: 65px;
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 24px;
        }

        .timezone-block {
            display: flex;
            flex-direction: column;
        }
        .timezone-label {
            font-size: 10px;
            color: var(--text-muted);
            font-weight: 700;
            margin-bottom: 2px;
        }
        .time-row {
            display: flex;
            gap: 16px;
            font-size: 14px;
            font-weight: 600;
        }

        .header-center {
            text-align: center;
        }
        .header-center h1 {
            font-size: 24px;
            color: var(--neon-green);
            font-weight: 700;
            letter-spacing: 3px;
            line-height: 1.1;
        }
        .header-center .subtitle {
            font-size: 10px;
            color: var(--text-main);
            letter-spacing: 1.5px;
            font-weight: 600;
        }

        .header-right {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 20px;
        }
        .control-panel {
            display: flex;
            align-items: center;
            gap: 8px;
            border: 1px solid var(--border-color);
            padding: 4px 8px;
            border-radius: 4px;
            background: #09090b;
        }
        .btn-toggle {
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 700;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            cursor: pointer;
            border-radius: 3px;
            transition: all 0.2s ease;
        }
        .btn-toggle.active-on {
            background: rgba(0, 255, 136, 0.1);
            border-color: var(--neon-green);
            color: var(--neon-green);
        }
        .btn-toggle.active-off {
            background: rgba(255, 77, 77, 0.1);
            border-color: var(--neon-red);
            color: var(--neon-red);
        }

        .meta-item {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
        }
        .meta-label { font-size: 9px; color: var(--text-muted); font-weight: bold; }
        .meta-val { font-size: 12px; font-weight: 600; }

        /* DASHBOARD LAYOUT */
        .dashboard-container {
            display: flex;
            flex-direction: column;
            gap: 12px;
            padding: 12px;
        }

        .top-row-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
        }

        /* PANELS */
        .terminal-panel {
            background-color: var(--bg-panel);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            display: flex;
            flex-direction: column;
        }

        .panel-header {
            background-color: var(--bg-panel-header);
            border-bottom: 1px solid var(--border-color);
            padding: 6px 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .panel-title {
            font-size: 11px;
            font-weight: 700;
            color: var(--terminal-gold);
            text-transform: uppercase;
            letter-spacing: 1px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .panel-title::before {
            content: '';
            display: inline-block;
            width: 3px;
            height: 11px;
            background: var(--terminal-gold);
        }

        .panel-body {
            padding: 12px;
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        /* DATA ROWS & LISTS */
        .data-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }
        .data-label { color: var(--text-muted); font-weight: 500; font-size: 12px; }
        .data-value { font-weight: 600; font-size: 13px; }

        /* NEWS FILTER SPECIFIC */
        .filter-tabs {
            display: flex;
            gap: 6px;
            margin-bottom: 8px;
            font-size: 11px;
            align-items: center;
        }
        .filter-tab {
            padding: 1px 6px;
            border: 1px solid transparent;
            color: var(--text-muted);
            font-weight: 600;
            cursor: pointer;
        }
        .filter-tab.active {
            border: 1px solid var(--neon-red);
            color: var(--neon-red);
            background: rgba(255, 77, 77, 0.05);
        }

        /* NARRATIVE ENGINE SPECIFIC */
        .progress-container {
            display: flex;
            align-items: center;
            gap: 8px;
            width: 100%;
        }
        .progress-bar-bg {
            background: #141416;
            height: 8px;
            flex-grow: 1;
            border-radius: 2px;
            overflow: hidden;
        }
        .progress-bar-fill {
            background: var(--neon-green);
            height: 100%;
            transition: width 0.4s ease;
        }
        .narrative-box {
            background: rgba(197, 168, 128, 0.04);
            border: 1px dashed rgba(197, 168, 128, 0.15);
            padding: 6px 8px;
            font-size: 11px;
            color: #ebdcb9;
            margin-top: 8px;
            line-height: 1.3;
        }

        /* SYSTEM STATUS SPECIFIC */
        .status-indicator-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            font-size: 12px;
        }
        .status-dot-group {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .status-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--neon-green);
            box-shadow: 0 0 6px var(--neon-green);
        }
        .status-dot.inactive {
            background: var(--neon-red) !important;
            box-shadow: 0 0 6px var(--neon-red) !important;
        }

        /* CHART REGION */
        .chart-controls-bar {
            background: var(--bg-panel-header);
            border-bottom: 1px solid var(--border-color);
            padding: 6px 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .chart-meta-left {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .chart-symbol { font-size: 14px; font-weight: 700; color: #fff; }
        .badge {
            font-size: 11px;
            padding: 1px 6px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .badge-green { background: rgba(0,255,136,0.1); border: 1px solid var(--neon-green); color: var(--neon-green); }
        .badge-gold { background: rgba(197,168,128,0.1); border: 1px solid var(--terminal-gold); color: var(--terminal-gold); }
        
        .timeframe-selector {
            display: flex;
            gap: 2px;
        }
        .tf-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 2px 6px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.1s ease;
        }
        .tf-btn.active {
            background: #1c1c1f;
            color: #fff;
            border-radius: 2px;
        }

        .chart-container-shell {
            height: 380px;
            position: relative;
            background-color: #050505;
        }

        /* TABLES WORKSPACE FOR LOWER TIERS */
        .bottom-row-grid {
            display: grid;
            grid-template-columns: 2.2fr 2fr 1.8fr;
            gap: 12px;
        }

        .table-wrapper {
            width: 100%;
            overflow-x: auto;
        }
        .terminal-table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 12px;
        }
        .terminal-table th {
            background: var(--bg-panel-header);
            color: var(--text-muted);
            font-weight: 600;
            padding: 6px 8px;
            border-bottom: 1px solid var(--border-color);
            font-size: 10px;
            text-transform: uppercase;
        }
        .terminal-table td {
            padding: 6px 8px;
            border-bottom: 1px solid rgba(255,255,255,0.02);
            white-space: nowrap;
        }
        .table-footer {
            padding: 6px 8px;
            text-align: right;
            border-top: 1px solid var(--border-color);
            font-weight: 700;
            font-size: 12px;
        }

        /* LOWER RIGHT SPLIT: ACTIONS & GATES */
        .gates-split-container {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            height: 100%;
        }
        .gate-list-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            padding: 3px 0;
            border-bottom: 1px solid rgba(255,255,255,0.02);
        }

        /* FOOTER STATUS STRIP */
        .terminal-footer {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            background: #020202;
            border-top: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            padding: 4px 16px;
            font-size: 10px;
            color: var(--text-muted);
            font-weight: 600;
            z-index: 1000;
        }
        .footer-left-meta span, .footer-right-meta span {
            margin-right: 14px;
        }
    </style>
</head>

<body x-data="dashboardStore()">

    <header class="terminal-header">
        <div class="header-left">
            <div class="timezone-block">
                <span class="timezone-label">TIMEZONE</span>
                <div class="time-row mono">
                    <div><span class="txt-amber">IST</span> <span id="ist-clock">--:--:--</span></div>
                    <div><span class="txt-muted">UTC</span> <span id="utc-clock" class="txt-main">--:--:--</span></div>
                </div>
            </div>
        </div>

        <div class="header-center">
            <h1>GUARDEER OS</h1>
            <div class="subtitle">INSTITUTIONAL SMC COMMAND TERMINAL</div>
        </div>

        <div class="header-right">
            <div class="control-panel">
                <span class="meta-label" style="margin-right:4px;">CONTROL</span>
                <span style="font-size:11px; font-weight:700; color:var(--text-main)">BOT STATUS</span>
                <span class="status-dot" :class="bot_status ? '' : 'inactive'" style="margin: 0 4px;"></span>
                <span :class="bot_status ? 'txt-green' : 'txt-red'" style="font-size:11px; font-weight:700; margin-right:6px;" x-text="bot_status ? 'RUNNING' : 'STOPPED'">RUNNING</span>
                
                <button class="btn-toggle" :class="bot_status ? 'active-on' : ''" @click="toggleBot(true)">ON</button>
                <button class="btn-toggle" :class="!bot_status ? 'active-off' : ''" @click="toggleBot(false)">OFF</button>
            </div>
            <div class="meta-item">
                <span class="meta-label">SERVER</span>
                <span class="meta-val mono">VPS-01 <span class="txt-muted" style="font-size:10px;">v6.3</span></span>
            </div>
            <div class="meta-item">
                <span class="meta-label">ACCOUNT</span>
                <span class="meta-val mono txt-amber">REAL-01</span>
            </div>
        </div>
    </header>

    <main class="dashboard-container">
        
        <section class="top-row-grid">
            
            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Account Overview</div>
                </div>
                <div class="panel-body">
                    <div class="data-row"><span class="data-label">LOGIN</span><span class="data-value mono">210045678</span></div>
                    <div class="data-row"><span class="data-label">SERVER</span><span class="data-value mono">Exness-MT5Real</span></div>
                    <div class="data-row"><span class="data-label">EQUITY</span><span class="data-value mono txt-green" x-text="fmt(equity)">$0.00</span></div>
                    <div class="data-row"><span class="data-label">BALANCE</span><span class="data-value mono txt-green" x-text="fmt(balance)">$0.00</span></div>
                    <div class="data-row">
                        <span class="data-label">DAILY P&L</span>
                        <span class="data-value mono" :class="pnl_today >= 0 ? 'txt-green' : 'txt-red'" x-text="fmt(pnl_today)">$0.00</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">WEEKLY P&L</span>
                        <span class="data-value mono" :class="pnl_week >= 0 ? 'txt-green' : 'txt-red'" x-text="fmt(pnl_week)">$0.00</span>
                    </div>
                    <div class="data-row" style="margin-bottom:0;">
                        <span class="data-label">OVERALL P&L</span>
                        <span class="data-value mono" :class="pnl_total >= 0 ? 'txt-green' : 'txt-red'" x-text="fmt(pnl_total)">$0.00</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">News Filter</div>
                </div>
                <div class="panel-body">
                    <div class="filter-tabs">
                        <span class="txt-muted" style="font-size:10px; font-weight:700;">IMPACT:</span>
                        <span class="filter-tab">ALL</span>
                        <span class="filter-tab active">HIGH</span>
                        <span class="filter-tab">MED</span>
                        <span class="filter-tab">LOW</span>
                    </div>
                    <div style="flex-grow:1; display:flex; flex-direction:column; gap:4px;">
                        <template x-for="item in news_items">
                            <div class="data-row mono" style="font-size:12px; margin-bottom:2px;">
                                <span class="txt-muted" x-text="item.time">00:00</span>
                                <span :class="item.impact === 'HIGH' ? 'txt-red' : item.impact === 'MED' ? 'txt-amber' : 'txt-muted'" style="width:35px;font-weight:700" x-text="item.impact">HIGH</span>
                                <span class="txt-main" style="flex-grow:1; text-align:right" x-text="item.title">Event Loading...</span>
                            </div>
                        </template>
                        <div x-show="news_items.length === 0" class="txt-muted mono" style="font-size:11px; text-align:center; margin-top:20px;">
                            NO MARKET EVENT DATA
                        </div>
                    </div>
                    <div class="data-row mono" style="margin-top:6px; margin-bottom:0; font-size:11px;">
                        <span class="txt-muted">NEXT EVENT IN:</span>
                        <span class="txt-amber" x-text="news.time">COUNTING DOWN</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Narrative Engine</div>
                </div>
                <div class="panel-body">
                    <div style="display:grid; grid-template-columns: 1fr 1.2fr; gap:12px; width:100%;">
                        <div>
                            <div class="data-row" style="margin-bottom:4px;">
                                <span class="data-label">D1 BIAS</span>
                                <span class="data-value" :class="d1_bias === 'BULLISH' ? 'txt-green' : d1_bias === 'BEARISH' ? 'txt-red' : 'txt-muted'" x-text="d1_bias">--</span>
                            </div>
                            <div class="data-row" style="margin-bottom:4px;">
                                <span class="data-label">H4 BIAS</span>
                                <span class="data-value" :class="h4_bias === 'BULLISH' ? 'txt-green' : h4_bias === 'BEARISH' ? 'txt-red' : 'txt-muted'" x-text="h4_bias">--</span>
                            </div>
                            <div class="data-row" style="margin-bottom:0;">
                                <span class="data-label">STRUCTURE</span>
                                <span class="data-value" :class="structure === 'BULLISH' ? 'txt-green' : structure === 'BEARISH' ? 'txt-red' : 'txt-muted'" x-text="structure">--</span>
                            </div>
                        </div>
                        <div style="display:flex; flex-direction:column; justify-content:center;">
                            <div class="data-row" style="margin-bottom:2px;">
                                <span class="data-label" style="font-size:10px;">SMC CONFIDENCE</span>
                                <span class="data-value mono txt-green" style="font-size:12px;" x-text="signal_engine.confidence + '%'">0%</span>
                            </div>
                            <div class="progress-container" style="margin-bottom:6px;">
                                <div class="progress-bar-bg">
                                    <div class="progress-bar-fill" :style="`width: ${signal_engine.confidence}%`" style="width: 0%;"></div>
                                </div>
                            </div>
                            <div class="data-row" style="margin-bottom:0;">
                                <span class="data-label" style="font-size:10px;">GATES PASSED</span>
                                <span class="data-value mono" style="font-size:12px;" x-text="getPassedGatesCount() + ' / 8'">0 / 8</span>
                            </div>
                        </div>
                    </div>
                    <div class="narrative-box">
                        <span class="txt-gold" style="font-weight:700">NARRATIVE:</span> 
                        <span x-text="signal_engine.reason">Parsing stream telemetry...</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">System Status</div>
                </div>
                <div class="panel-body" style="justify-content: flex-start;">
                    <div class="status-indicator-row"><span>MT5 CONNECTED</span><div class="status-dot-group"><div class="status-dot" :class="ws_connected ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row"><span>DATA FETCHING (main.py)</span><div class="status-dot-group"><div class="status-dot" :class="ws_connected ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row"><span>VPS RECEIVING DATA</span><div class="status-dot-group"><div class="status-dot" :class="ws_connected ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row"><span>DASHBOARD ANALYSIS</span><div class="status-dot-group"><div class="status-dot" :class="ws_connected ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row" style="margin-bottom:8px;"><span>SYNC STATUS</span><div class="status-dot-group"><div class="status-dot" :class="ws_connected ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'WORKING' : 'HALTED'">HALTED</span></div></div>
                    <div class="data-row mono" style="margin-top:auto; margin-bottom:0; border-top:1px solid rgba(255,255,255,0.03); padding-top:4px; font-size:11px;">
                        <span class="txt-muted">LAST UPDATE:</span><span class="txt-main" id="last-update-ts">--:--:--</span>
                    </div>
                </div>
            </div>
        </section>

        <section class="terminal-panel">
            <div class="chart-controls-bar">
                <div class="chart-meta-left">
                    <span class="chart-symbol mono">XAUUSD <span class="txt-muted" style="font-size:11px;">•</span> <span x-text="current_tf">M15</span></span>
                    <div class="badge badge-green">LIVE</div>
                    <div class="badge badge-green" style="font-size:10px;" x-text="'STRUCTURE: ' + structure">STRUCTURE: --</div>
                    <div class="badge badge-gold" style="font-size:10px;" x-text="'SESSION: ' + session">SESSION: --</div>
                </div>
                
                <div class="timeframe-selector mono">
                    <template x-for="tf in ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']">
                        <button class="tf-btn" :class="current_tf === tf ? 'active' : ''" @click="changeTimeframe(tf)" x-text="tf"></button>
                    </template>
                </div>
                <div class="txt-muted mono" style="font-size:11px;">CHART CONTROLS</div>
            </div>
            <div class="chart-container-shell">
                <div id="chart-container" style="width: 100%; height: 100%;"></div>
            </div>
        </section>

        <section class="bottom-row-grid">
            
            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Open Positions (<span x-text="trades.length">0</span>)</div>
                </div>
                <div class="panel-body" style="padding:0;">
                    <div class="table-wrapper">
                        <table class="terminal-table mono">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>TYPE</th>
                                    <th>ENTRY</th>
                                    <th>SL</th>
                                    <th>TP</th>
                                    <th>VOL</th>
                                    <th>P&L</th>
                                    <th>STATUS</th>
                                </tr>
                            </thead>
                            <tbody>
                                <template x-for="t in trades">
                                    <tr>
                                        <td style="font-weight:700;" x-text="t.symbol || 'XAUUSD'">XAUUSD</td>
                                        <td :class="t.type === 'BUY' ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="t.type">BUY</td>
                                        <td x-text="t.entry">0.00</td>
                                        <td class="txt-muted" x-text="t.sl">0.00</td>
                                        <td class="txt-muted" x-text="t.tp">0.00</td>
                                        <td x-text="t.volume || t.vol || '0.10'">0.10</td>
                                        <td :class="t.pnl >= 0 ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="fmt(t.pnl)">$0.00</td>
                                        <td class="txt-green" style="font-size:11px;">OPEN</td>
                                    </tr>
                                </template>
                                <tr x-show="trades.length === 0">
                                    <td colspan="8" class="txt-muted" style="text-align:center; padding: 20px 0;">NO ACTIVE TRADES DETECTED</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="table-footer mono" :class="getOpenTotalPnl() >= 0 ? 'txt-green' : 'txt-red'">
                        TOTAL P&L: <span x-text="fmt(getOpenTotalPnl())">$0.00</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Closed Positions (<span x-text="closed_trades.length">0</span>)</div>
                </div>
                <div class="panel-body" style="padding:0;">
                    <div class="table-wrapper">
                        <table class="terminal-table mono">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>TYPE</th>
                                    <th>ENTRY</th>
                                    <th>EXIT</th>
                                    <th>P&L</th>
                                </tr>
                            </thead>
                            <tbody>
                                <template x-for="ct in closed_trades">
                                    <tr>
                                        <td style="font-weight:700;" x-text="ct.symbol">--</td>
                                        <td :class="ct.type === 'BUY' ? 'txt-green' : 'txt-red'" x-text="ct.type">--</td>
                                        <td x-text="ct.entry">0.00</td>
                                        <td x-text="ct.exit">0.00</td>
                                        <td :class="ct.pnl >= 0 ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="fmt(ct.pnl)">$0.00</td>
                                    </tr>
                                </template>
                                <tr x-show="closed_trades.length === 0">
                                    <td colspan="5" class="txt-muted" style="text-align:center; padding: 20px 0;">HISTORY IS EMPTY</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="table-footer mono" :class="getClosedTotalPnl() >= 0 ? 'txt-green' : 'txt-red'">
                        TOTAL CLOSED P&L: <span x-text="fmt(getClosedTotalPnl())">$0.00</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Signal Engine / Gateways</div>
                </div>
                <div class="panel-body" style="padding:10px;">
                    <div class="gates-split-container">
                        
                        <div style="display:flex; flex-direction:column; justify-content:space-between; background:rgba(255,255,255,0.01); padding:8px; border:1px solid var(--border-color)">
                            <div>
                                <div style="font-size:10px; color:var(--text-muted); font-weight:700; margin-bottom:4px;">SIGNAL ACTION</div>
                                <div class="mono">
                                    <div class="txt-muted" style="font-size:9px;">ACTION</div>
                                    <div style="font-size:16px; font-weight:700;" :class="signal_engine.action === 'BUY' ? 'txt-green' : signal_engine.action === 'SELL' ? 'txt-red' : 'txt-muted'" x-text="signal_engine.action">NO_TRADE</div>
                                </div>
                                <div class="mono" style="margin-top:4px;">
                                    <div class="txt-muted" style="font-size:9px;">DIRECTION</div>
                                    <div class="txt-gold" style="font-size:13px; font-weight:700;" x-text="signal_engine.direction || '--'">--</div>
                                </div>
                            </div>
                            <div class="mono" style="margin-top:auto;">
                                <div class="txt-muted" style="font-size:9px;">REASON</div>
                                <div class="txt-amber" style="font-size:11px; font-weight:600; line-height:1.2" x-text="signal_engine.reason_code || 'SCANNING'">SCANNING</div>
                            </div>
                        </div>

                        <div class="mono" style="display:flex; flex-direction:column; justify-content:space-between;">
                            <div style="font-size:10px; color:var(--text-muted); font-weight:700; margin-bottom:2px;">SMC GATES</div>
                            <div style="flex-grow:1;">
                                <template x-for="(passed, name, index) in signal_engine.gates">
                                    <div class="gate-list-item">
                                        <span x-text="name">Gate Name</span>
                                        <span :class="passed ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="passed ? '✔' : '✘'">✘</span>
                                    </div>
                                </template>
                                <div x-show="Object.keys(signal_engine.gates).length === 0" class="txt-muted" style="font-size:11px; padding-top:20px; text-align:center;">
                                    NO GATES REGISTERED
                                </div>
                            </div>
                            <div style="margin-top:4px;">
                                <div class="progress-bar-bg" style="height:4px; margin-bottom:2px;">
                                    <div class="progress-bar-fill" :style="`width: ${signal_engine.confidence}%`"></div>
                                </div>
                                <div style="font-size:9px; display:flex; justify-content:space-between" class="txt-muted">
                                    <span>MATCH: <span x-text="getPassedGatesCount()">0</span>/8</span>
                                    <span x-text="signal_engine.confidence + '%'">0%</span>
                                </div>
                            </div>
                        </div>

                    </div>
                </div>
            </div>

        </section>
    </main>

    <footer class="terminal-footer mono">
        <div class="footer-left-meta">
            <span>GUARDEER OS v4.0</span>
            <span>INSTITUTIONAL SMC COMMAND TERMINAL</span>
        </div>
        <div class="footer-right-meta">
            <span>VPS: <span :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'ONLINE' : 'OFFLINE'">OFFLINE</span></span>
            <span>WEBSOCKET: <span :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'ACTIVE' : 'DISCONNECTED'">DISCONNECTED</span></span>
        </div>
    </footer>

    <script>
        function updateClocks() {
            const now = new Date();
            document.getElementById('utc-clock').innerText = now.toUTCString().split(' ')[4];
            document.getElementById('ist-clock').innerText = now.toLocaleTimeString('en-IN', {
                timeZone: 'Asia/Kolkata',
                hour12: false
            });
        }
        setInterval(updateClocks, 1000);
        updateClocks();

        // High-Fidelity Modern Dark-Theme Lightweight Chart initialization
        const container = document.getElementById('chart-container');
        const chart = LightweightCharts.createChart(container, {
            layout: {
                background: { color: '#050505' },
                textColor: '#a0a0a5',
                fontSize: 11,
                fontFamily: 'JetBrains Mono'
            },
            grid: {
                vertLines: { color: '#121215' },
                horzLines: { color: '#121215' }
            },
            rightPriceScale: { borderColor: '#1f1f24', alignLabels: true },
            timeScale: { borderColor: '#1f1f24', timeVisible: true, secondsVisible: false },
            crosshair: {
                vertLine: { color: '#3a3a42', style: 3 },
                horzLine: { color: '#3a3a42', style: 3 }
            }
        });

        const series = chart.addCandlestickSeries({
            upColor: '#00ff88',
            downColor: '#ff4d4d',
            borderVisible: false,
            wickUpColor: '#00ff88',
            wickDownColor: '#ff4d4d'
        });

        const resizeObserver = new ResizeObserver(entries => {
            if (entries.length === 0 || !entries[0].contentRect) return;
            chart.resize(entries[0].contentRect.width, entries[0].contentRect.height);
        });
        resizeObserver.observe(container);

        // Global Alpine.js Reactive Controller Block
        function dashboardStore() {
            return {
                ws: null,
                ws_connected: false,
                bot_status: true,
                current_tf: 'M15',
                
                // Account metrics arrays
                equity: 0,
                balance: 0,
                pnl_today: 0,
                pnl_week: 0,
                pnl_total: 0,
                
                // Narrative matrix properties
                structure: '--',
                session: '--',
                d1_bias: '--',
                h4_bias: '--',
                
                // Subordinate structural arrays
                trades: [],
                closed_trades: [],
                news_items: [],
                
                signal_engine: {
                    action: 'NO_TRADE',
                    confidence: 0,
                    direction: '--',
                    reason: 'Parsing stream data...',
                    reason_code: 'SCANNING',
                    gates: {} // Bound via keys: {"HTF BIAS": true, "LIQUIDITY SWEEP": false, ...}
                },
                news: { time: '--' },

                fmt(v) {
                    return '$' + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                },
                getPassedGatesCount() {
                    return Object.values(this.signal_engine.gates).filter(v => v === true).length;
                },
                getOpenTotalPnl() {
                    return this.trades.reduce((sum, t) => sum + Number(t.pnl || 0), 0);
                },
                getClosedTotalPnl() {
                    return this.closed_trades.reduce((sum, ct) => sum + Number(ct.pnl || 0), 0);
                },

                // B) FIX: Bot Status toggle communication outbound
                toggleBot(statusState) {
                    this.bot_status = statusState;
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send(JSON.stringify({
                            action: "toggle_bot",
                            status: statusState ? "ON" : "OFF"
                        }));
                    }
                },

                // C) FIX: Timeframe mutation click handler with payload push
                changeTimeframe(selectedTf) {
                    this.current_tf = selectedTf;
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send(JSON.stringify({
                            action: "change_timeframe",
                            timeframe: selectedTf
                        }));
                    }
                },

                init() {
                    this.connect();
                },

                // A) FIX: Dynamic pipeline ingestion handling
                connect() {
                    // Force direct alignment to your VPS streaming instance endpoint
                    this.ws = new WebSocket('ws://68.233.99.145:8000/ws');

                    this.ws.onopen = () => {
                        this.ws_connected = true;
                    };

                    this.ws.onopen = () => {
                        this.ws_connected = true;
                    };

                    this.ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);
                        document.getElementById('last-update-ts').innerText = new Date().toLocaleTimeString('en-IN', { hour12: false });

                        // Core assignments mapping fields streaming from vps_reporter
                        if (data.equity !== undefined) this.equity = data.equity;
                        if (data.balance !== undefined) this.balance = data.balance;
                        if (data.pnl_today !== undefined) this.pnl_today = data.pnl_today;
                        if (data.pnl_week !== undefined) this.pnl_week = data.pnl_week;
                        if (data.pnl_total !== undefined) this.pnl_total = data.pnl_total;
                        if (data.market_structure !== undefined) this.structure = data.market_structure;
                        if (data.session !== undefined) this.session = data.session;
                        if (data.d1_bias !== undefined) this.d1_bias = data.d1_bias;
                        if (data.h4_bias !== undefined) this.h4_bias = data.h4_bias;
                        if (data.bot_status !== undefined) this.bot_status = (data.bot_status === true || data.bot_status === "ON");
                        
                        // Array overrides
                        if (data.trades) this.trades = data.trades;
                        if (data.closed_trades) this.closed_trades = data.closed_trades;
                        if (data.news_items) this.news_items = data.news_items;
                        if (data.news_countdown) this.news.time = data.news_countdown;

                        if (data.signal_engine) {
                            this.signal_engine = { ...this.signal_engine, ...data.signal_engine };
                        }

                        // Feed candlestick changes into canvas
                        if (data.chart_data?.length) {
                            series.setData(data.chart_data);
                        }
                    };

                    this.ws.onclose = () => {
                        this.ws_connected = false;
                        setTimeout(() => this.connect(), 3000);
                    };
                }
            }
        }
    </script>
</body>
</html>
"""

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return html_content
# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)

    try:
        client_info = f"{websocket.client}" if hasattr(websocket, "client") else "unknown"
        try:
            print("🔌 WS CONNECTED:", client_info)
        except Exception:
            pass

        # Send initial state immediately
        if bot_state:
            try:
                safe_json = json.dumps(bot_state, default=str).replace("NaN", "null")
                await websocket.send_text(safe_json)
                try:
                    eq = bot_state.get("equity", 0.0)
                    bal = bot_state.get("balance", 0.0)
                    chart_len = len(bot_state.get("chart_data", []) or [])
                    print(f"📤 Sent initial state: Equity=${float(eq):,.2f} Balance=${float(bal):,.2f} ChartCandles={chart_len}")
                except Exception:
                    pass
            except Exception as e:
                print("❌ Error sending initial state:", e)

        # Keep connection alive: don't require client to send messages
        while True:
            try:
                _ = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"❌ WS receive error: {e}")
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"❌ WS Exception: {e}")
    finally:
        try:
            if websocket in active_connections:
                active_connections.remove(websocket)
        except Exception:
            pass
        try:
            print("🔌 WS DISCONNECTED:", client_info)
        except Exception:
            pass

# Core state ingestion used by webhook
def update_bot_state_v2(bot_instance, analysis_data):
    global bot_paused

    global bot_state, pnl_tracker

    def get_val(obj, key, default=0.0):
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default

    def parse_profit(x):
        if x is None:
            return 0.0
        try:
            if isinstance(x, (int, float)):
                val = float(x)
            else:
                s = str(x).strip()
                for ch in ("$", "€", ","):
                    s = s.replace(ch, "")
                s = s.replace("(", "-").replace(")", "")
                val = float(s)
            if math.isnan(val):
                return 0.0
            return val
        except Exception:
            return 0.0

    equity = float(get_val(bot_instance, "equity", 0.0) or 0.0)
    balance = float(get_val(bot_instance, "balance", 0.0) or 0.0)

    open_pnl = 0.0
    formatted_trades = []

    try:
        # === FIX: COMBINE BOT + MANUAL POSITIONS ===
        bot_pos = get_val(bot_instance, "open_positions", []) or []
        man_pos = get_val(bot_instance, "manual_positions", []) or []
        
        # Ensure they are lists before concatenating
        if not isinstance(bot_pos, list): bot_pos = []
        if not isinstance(man_pos, list): man_pos = []
        
        # Combine lists so dashboard sees ALL active trades
        positions = bot_pos + man_pos

        current_price = float(get_val(bot_instance, "last_price", 0.0) or 0.0)

        for p in positions:
            profit_raw = get_val(p, "profit", None)
            if profit_raw is None:
                profit_raw = get_val(p, "pnl", None)
            profit = parse_profit(profit_raw)
            entry = parse_profit(get_val(p, "price", get_val(p, "entry_price", 0.0)))
            lot = parse_profit(get_val(p, "lot_size", get_val(p, "volume", 0.0)))
            signal = get_val(p, "signal", get_val(p, "type", "N/A"))

            # Auto-calculate PnL if missing (common for manual trades via API)
            if (profit == 0.0) and (current_price > 0 and entry > 0 and lot > 0):
                try:
                    if str(signal).upper() == "BUY":
                        profit = (current_price - entry) * lot * 100
                    else:
                        profit = (entry - current_price) * lot * 100
                except Exception:
                    profit = 0.0

            open_pnl += float(profit)
            formatted_trades.append({
                "id": str(get_val(p, "ticket", get_val(p, "id", "000")))[:12],
                "type": str(signal).upper(),
                "lot_size": round(float(lot or 0.0), 3),
                "entry": round(float(entry or 0.0), 5) if entry else entry,
                "pnl": round(float(profit), 2),
                "tp": get_val(p, "tp", 0), "sl": get_val(p, "sl", 0)
            })
    except Exception as e:
        print(f"⚠️ update_bot_state_v2 positions parsing error: {e}")

    try:
        closed = get_val(bot_instance, "closed_trades", []) or []
        today_sum = 0.0
        for ct in closed:
            symbol = get_val(ct, "symbol", "") or get_val(ct, "instrument", "") or ""
            if symbol:
                try:
                    if str(symbol).upper() != "XAUUSD":
                        continue
                except:
                    pass

            profit_raw = None
            for k in ("profit", "pnl", "profit_usd", "profit_usd_str", "deal_profit"):
                profit_raw = get_val(ct, k, None)
                if profit_raw is not None:
                    break
            if profit_raw is None and isinstance(ct, dict):
                for v in ct.values():
                    if isinstance(v, (int, float)) and abs(v) > 0:
                        profit_raw = v
                        break

            profit = parse_profit(profit_raw)
            if abs(profit) < 0.01:
                continue

            when = None
            ts = get_val(ct, "time", None) or get_val(ct, "close_time", None) or get_val(ct, "timestamp", None)
            if ts:
                try:
                    if isinstance(ts, str):
                        try:
                            when = datetime.fromisoformat(ts)
                        except:
                            try:
                                when = datetime.strptime(ts, "%Y.%m.%d %H:%M:%S")
                            except:
                                when = None
                    elif isinstance(ts, (int, float)):
                        if ts > 1e12:
                            when = datetime.fromtimestamp(float(ts) / 1000.0)
                        else:
                            when = datetime.fromtimestamp(float(ts))
                except Exception:
                    when = None

            try:
                ticket_id = get_val(ct, "ticket", get_val(ct, "order", None) or get_val(ct, "id", None))
                ticket_str = str(ticket_id) if ticket_id is not None else None
                pnl_tracker.add_closed_trade(float(profit or 0.0), when=when, ticket=ticket_str)
            except Exception as e:
                print(f"⚠️ pnl_tracker add error: {e}")

            try:
                if when is None or when.date() == datetime.now().date():
                    today_sum += float(profit or 0.0)
            except Exception:
                today_sum += float(profit or 0.0)

        if pnl_tracker.get_daily() == 0.0 and today_sum != 0.0:
            today_key = datetime.now().date().isoformat()
            pnl_tracker.history[today_key] = pnl_tracker.history.get(today_key, 0.0) + today_sum
            pnl_tracker.realized_pnl += float(today_sum)
            pnl_tracker.total_realized += float(today_sum)

    except Exception as e:
        print(f"⚠️ closed_trades ingestion error: {e}")

    # --- SIGNALS INGESTION ---
    formatted_signals = []
    try:
        raw_signals = get_val(bot_instance, "signals", []) or []
        if not isinstance(raw_signals, list):
            raw_signals = []
        for sig in raw_signals[-10:]:
            ts_raw = get_val(sig, "time", get_val(sig, "timestamp", None))
            ts_str = str(ts_raw) if ts_raw is not None else "--"
            direction = str(get_val(sig, "direction", get_val(sig, "signal", get_val(sig, "type", "N/A")))).upper()
            entry_val = get_val(sig, "entry", get_val(sig, "price", None))
            sl_val = get_val(sig, "sl", get_val(sig, "stop_loss", None))
            tp_val = get_val(sig, "tp", get_val(sig, "take_profit", None))
            formatted_signals.append({
                "time": ts_str,
                "direction": direction,
                "entry": round(float(entry_val), 5) if entry_val is not None else None,
                "sl": round(float(sl_val), 5) if sl_val is not None else None,
                "tp": round(float(tp_val), 5) if tp_val is not None else None,
            })
    except Exception as e:
        print(f"⚠️ signals ingestion error: {e}")

    # --- TRADE RESULTS INGESTION ---
    formatted_results = []
    try:
        raw_results = get_val(bot_instance, "trade_results", []) or []
        if not isinstance(raw_results, list):
            raw_results = []
        # Fall back to closed_trades if trade_results not explicitly provided
        if not raw_results:
            raw_results = get_val(bot_instance, "closed_trades", []) or []
            if not isinstance(raw_results, list):
                raw_results = []
        for tr in raw_results[-10:]:
            pnl_raw = None
            for k in ("pnl", "profit", "profit_usd", "deal_profit"):
                pnl_raw = get_val(tr, k, None)
                if pnl_raw is not None:
                    break
            pnl_val = parse_profit(pnl_raw)
            direction = str(get_val(tr, "direction", get_val(tr, "signal", get_val(tr, "type", "N/A")))).upper()
            ts_raw = get_val(tr, "time", get_val(tr, "close_time", get_val(tr, "timestamp", None)))
            ts_str = str(ts_raw) if ts_raw is not None else "--"
            result_raw = get_val(tr, "result", None)
            if result_raw is not None:
                result = str(result_raw).upper()
            else:
                result = "WIN" if pnl_val >= 0 else "LOSS"
            if abs(pnl_val) < 0.01:
                continue
            formatted_results.append({
                "time": ts_str,
                "direction": direction,
                "result": result,
                "pnl": round(float(pnl_val), 2),
            })
    except Exception as e:
        print(f"⚠️ trade_results ingestion error: {e}")

    try:
        pdh_val = analysis_data.get("pdh") if isinstance(analysis_data, dict) else getattr(analysis_data, "pdh", None)
        pdl_val = analysis_data.get("pdl") if isinstance(analysis_data, dict) else getattr(analysis_data, "pdl", None)
        zones_val = analysis_data.get("zones", {}) if isinstance(analysis_data, dict) else getattr(analysis_data, "zones", {})
        
        # Phase-7: Extract POI overlays from incoming payload
        poi_overlays = analysis_data.get("poi_overlays", []) if isinstance(analysis_data, dict) else getattr(analysis_data, "poi_overlays", [])

        # Extract chart_objects from analysis_data
        chart_objects = analysis_data.get("chart_objects", {})
        

        bot_state.update({
            "equity": equity,
            "balance": balance,
            "pnl_daily": round(pnl_tracker.get_daily() + open_pnl, 2),
            "pnl_today": round(pnl_tracker.get_daily() + open_pnl, 2),
            "pnl_week": round(pnl_tracker.get_weekly() + open_pnl, 2),
            "pnl_total": round(pnl_tracker.get_total() + open_pnl, 2),
            "open_pnl": round(open_pnl, 2),
            "price": get_val(bot_instance, "last_price", 0.0),
            "market_structure": analysis_data.get("market_structure", {}).get("current_trend", "NEUTRAL") if isinstance(analysis_data, dict) else getattr(analysis_data, "market_structure", {}).get("current_trend", "NEUTRAL"),
            "zone_strength": analysis_data.get("zone_strength", 0) if isinstance(analysis_data, dict) else getattr(analysis_data, "zone_strength", 0),
            "session": get_val(bot_instance, "current_session", "ASIAN"),
            "zone": analysis_data.get("current_zone", "EQ") if isinstance(analysis_data, dict) else getattr(analysis_data, "current_zone", "EQ"),
            "trades": formatted_trades,
            "chart_overlays": {
                "levels": {"pdh": pdh_val, "pdl": pdl_val},
                "zones": {"equilibrium": zones_val.get("equilibrium") if isinstance(zones_val, dict) else None}
            },
            # Phase-7: Add to state
            "poi_overlays": poi_overlays,
            "chart_objects": chart_objects,  # <-- Added key for chart_objects
            "news_event": {"title": "No major events scheduled", "time": "Market Calm"},
            "chart_data": get_val(bot_instance, "chart_data", [])[-300:],
            "signals": formatted_signals,
            "trade_results": formatted_results,
            "trading": not bot_paused,
            # --- HTF Biases ---
            "d1_bias": analysis_data.get("market_structure", {}).get("d1_bias", "NEUTRAL") if isinstance(analysis_data, dict) else "NEUTRAL",
            "h4_bias": analysis_data.get("market_structure", {}).get("h4_bias", "NEUTRAL") if isinstance(analysis_data, dict) else "NEUTRAL",

            # --- Signal Engine ---
            "signal_engine": {
                "action":     analysis_data.get("signal_engine", {}).get("action", "NO_TRADE") if isinstance(analysis_data, dict) else "NO_TRADE",
                "direction":  analysis_data.get("signal_engine", {}).get("direction", "NEUTRAL") if isinstance(analysis_data, dict) else "NEUTRAL",
                "confidence": analysis_data.get("signal_engine", {}).get("confidence", 0) if isinstance(analysis_data, dict) else 0,
                "reason":     analysis_data.get("signal_engine", {}).get("reason", "--") if isinstance(analysis_data, dict) else "--",
                "entry_price": analysis_data.get("signal_engine", {}).get("entry_price", None) if isinstance(analysis_data, dict) else None,
                "sl_price":    analysis_data.get("signal_engine", {}).get("sl_price", None) if isinstance(analysis_data, dict) else None,
                "tp_price":    analysis_data.get("signal_engine", {}).get("tp_price", None) if isinstance(analysis_data, dict) else None,
                "gates":       analysis_data.get("signal_engine", {}).get("gates", {}) if isinstance(analysis_data, dict) else {},
            },
        })
    except Exception as e:
        print(f"⚠️ Error building bot_state: {e}")

# POST /webhook: receives payload from bot
@app.post("/webhook")
async def webhook(payload: dict = Body(...)):
    try:
        if "bot_instance" in payload and "analysis_data" in payload:
            bot_inst = payload["bot_instance"]
            analysis = payload["analysis_data"]
        elif "bot" in payload and "analysis" in payload:
            bot_inst = payload["bot"]
            analysis = payload["analysis"]
        else:
            bot_inst = {}
            analysis = {}
            for k in ("market_structure", "zone_strength", "current_zone", "pdh", "pdl", "zones", "poi_overlays"):
                if k in payload:
                    analysis[k] = payload[k]
            for k, v in payload.items():
                if k not in analysis:
                    bot_inst[k] = v
            
        # Update internal state and PnL tracker
        update_bot_state_v2(bot_inst, analysis)
        return {"status": "ok"}
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "reason": str(e)}

# --- BOT CONTROL ENDPOINTS ---
@app.get("/bot/status")
async def bot_status():
    global bot_paused
    return {"status": "PAUSED" if bot_paused else "ACTIVE"}

@app.post("/bot/pause")
async def pause_bot():
    global bot_paused
    bot_paused = True
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post("http://localhost:8000/bot/pause")
    except Exception:
        pass
    print("⏸️  Bot PAUSED via dashboard")
    return {"status": "PAUSED"}

@app.post("/bot/resume")
async def resume_bot():
    global bot_paused
    bot_paused = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post("http://localhost:8000/bot/resume")
    except Exception:
        pass
    print("▶️  Bot RESUMED via dashboard")
    return {"status": "ACTIVE"}

@app.get('/bot/logs')
def get_logs():
    try:
        with open(r'C:\Python_Project\tradingbot\TradingBOt\logs\bot.log', 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return {'logs': lines[-50:]}
    except Exception as e:
        return {'error': str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
