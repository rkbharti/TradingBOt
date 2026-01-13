from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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

# ============================================================================== 
# 🎨 DASHBOARD V3.7+: GUARDEER "COMMAND OS" (PRODUCTION-BOUND)
# Changes: Reduced broadcast chatter, state-delta broadcasts, enhanced PnL tracking
# Added: Today / Week / Total realized P&L to main widget and positions widget
# NOTE: Small defensive logging left in place — remove if you want quieter logs.
# ==============================================================================

# --- BACKGROUND BROADCASTER ---
async def broadcast_loop():
    """
    Broadcast only when state changes (stable hashing) and throttle frequency.
    """
    last_state_hash = None
    while True:
        if active_connections and bot_state:
            # stable digest of json sorted keys
            state_json = json.dumps(bot_state, sort_keys=True, default=str)
            state_hash = hashlib.sha256(state_json.encode()).hexdigest()
            if state_hash != last_state_hash:
                payload = state_json
                # log a lightweight message for diagnostics
                try:
                    print(f"📡 Broadcasting state update ({len(active_connections)} clients) size={len(payload)} bytes")
                except:
                    pass
                for conn in active_connections[:]:
                    try:
                        await conn.send_text(payload)
                    except:
                        # silent ignore; pruning will happen on disconnect
                        pass
                last_state_hash = state_hash
        # Reduced frequency: 10s (was 3s). Only sends on change.
        await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

active_connections = []
bot_state = {}

# --- ENHANCED PnL TRACKER ---
class DailyPnLTracker:
    def __init__(self):
        # realized_pnl for current day
        self.realized_pnl = 0.0
        self.last_reset_date = datetime.now().date()
        # history keyed by ISO date string "YYYY-MM-DD"
        self.history = {}  # e.g. {"2026-01-12": 123.45, ...}
        # cumulative realized since tracker started
        self.total_realized = 0.0
        # track processed closed-deal tickets to avoid duplicates
        self.processed_ticket_ids = set()

    def _ensure_today(self):
        today = datetime.now().date()
        if today > self.last_reset_date:
            # rotate/reset per-day bookkeeping
            self.last_reset_date = today
            self.realized_pnl = 0.0

    def add_closed_trade(self, pnl: float, when: datetime = None, ticket: str = None):
        """
        Record a closed trade profit. 'when' can be specified (datetime), otherwise now.
        If ticket is provided, skip if already processed.
        """
        # dedupe by ticket if provided
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
        # ensure daily reset for tracker
        self._ensure_today()
        ds = d.isoformat()
        self.history[ds] = self.history.get(ds, 0.0) + float(pnl)
        if d == datetime.now().date():
            self.realized_pnl += float(pnl)
        self.total_realized += float(pnl)

    def get_daily(self):
        # today's realized (from history/tracker) as float
        self._ensure_today()
        return float(self.realized_pnl)

    def get_weekly(self):
        # sum realized for current ISO week (Mon-Sun) from history
        today = datetime.now().date()
        # find Monday of current week
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

# --- HTML CONTENT (updated to display Today / Week / Total P&L) ---
html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta http-equiv="Content-Security-Policy" content="default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;">
    <title>Guardeer OS v3</title>
    
    <script src="https://unpkg.com/lightweight-charts@4.0.0/dist/lightweight-charts.standalone.production.js"></script>
    <script src="https://unpkg.com/@phosphor-icons/web"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/gridstack@7.2.3/dist/gridstack.min.css" rel="stylesheet"/>
    <script src="https://cdn.jsdelivr.net/npm/gridstack@7.2.3/dist/gridstack-all.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    
    <style>
        :root { --bg: #000000; --card-bg: #1c1c1e; --card-border: #2c2c2e; --text-main: #ffffff; --text-sub: #8e8e93; --green: #30d158; --red: #ff453a; --blue: #0a84ff; --gold: #ffd60a; --radius: 16px; }
        * { box-sizing: border-box; touch-action: manipulation; -webkit-tap-highlight-color: transparent; }
        body { background: var(--bg); color: var(--text-main); font-family: 'SF Pro Display', sans-serif; margin: 0; padding: 10px; overflow-x: hidden; }
        .grid-stack-item-content { background: var(--card-bg); border-radius: var(--radius); border: 1px solid rgba(255,255,255,0.08); overflow: hidden !important; box-shadow: 0 4px 12px rgba(0,0,0,0.3); display: flex; flex-direction: column; }
        .w-header { padding: 12px 14px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.03); cursor: grab; }
        .w-header:active { cursor: grabbing; }
        .w-title { font-size: 11px; text-transform: uppercase; font-weight: 700; color: var(--text-sub); display: flex; align-items: center; gap: 6px; }
        .w-body { flex: 1; padding: 14px; overflow-y: auto; position: relative; }
        .val-xl { font-size: clamp(24px, 4vw, 32px); font-weight: 700; font-family: 'JetBrains Mono', monospace; letter-spacing: -1px; }
        .val-lg { font-size: 18px; font-weight: 600; }
        .mono { font-family: 'JetBrains Mono', monospace; }
        .text-green { color: var(--green); } .text-red { color: var(--red); } .text-blue { color: var(--blue); }
        .flip-card { perspective: 1000px; width: 100%; height: 100%; }
        .flip-inner { position: relative; width: 100%; height: 100%; text-align: center; transition: transform 0.6s; transform-style: preserve-3d; }
        .flipped .flip-inner { transform: rotateY(180deg); }
        .flip-front, .flip-back { position: absolute; width: 100%; height: 100%; backface-visibility: hidden; display: flex; flex-direction: column; justify-content: center; }
        .flip-back { transform: rotateY(180deg); background: #252528; border-radius: var(--radius); }
        #chart-container { width: 100%; height: 100%; }
        .chart-overlay { position: absolute; top: 10px; left: 10px; z-index: 20; display: flex; gap: 6px; pointer-events: none; }
        .badge { background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); padding: 4px 8px; border-radius: 6px; font-size: 10px; font-weight: 600; border: 1px solid rgba(255,255,255,0.1); }
        .pos-item { background: rgba(255,255,255,0.03); border-radius: 8px; padding: 10px; margin-bottom: 8px; border-left: 3px solid var(--text-sub); display: flex; justify-content: space-between; align-items: center; }
        .pos-item.BUY { border-left-color: var(--green); } .pos-item.SELL { border-left-color: var(--red); }
        .pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }
        .pos-footer { margin-top: 8px; display:flex; justify-content:space-between; align-items:center; font-size:12px; color:var(--text-sub); }
    </style>
</head>
<body x-data="dashboardStore()">

    <div class="grid-stack">
        <div class="grid-stack-item" gs-id="capital" gs-w="3" gs-h="2">
            <div class="grid-stack-item-content" @click="flipCapital = !flipCapital" :class="{ 'flipped': flipCapital }">
                <div class="flip-card">
                    <div class="flip-inner">
                        <div class="flip-front w-body" style="align-items: flex-start; text-align: left;">
                            <div class="w-title" style="width:100%; justify-content:space-between;">
                                <span><i class="ph-fill ph-wallet"></i> Capital</span>
                                <div class="pulse" style="width:6px; height:6px; background:var(--green); border-radius:50%"></div>
                            </div>
                            <div style="margin-top:auto">
                                <div class="text-sub" style="font-size:11px;">Total Equity</div>
                                <div class="val-xl" x-text="fmt(equity)">$0.00</div>
                                <div style="display:flex; gap:8px; margin-top:8px; align-items:center;">
                                    <div class="mono" style="font-size:12px;" :class="pnl_today >= 0 ? 'text-green' : 'text-red'">
                                        <div style="font-size:10px; color:var(--text-sub)">Today</div>
                                        <div style="font-weight:700" x-text="(pnl_today >= 0 ? '+' : '') + fmt(pnl_today)"></div>
                                    </div>
                                    <div class="mono" style="font-size:12px;" :class="pnl_week >= 0 ? 'text-green' : 'text-red'">
                                        <div style="font-size:10px; color:var(--text-sub)">This Week</div>
                                        <div style="font-weight:700" x-text="(pnl_week >= 0 ? '+' : '') + fmt(pnl_week)"></div>
                                    </div>
                                    <div class="mono" style="font-size:12px;" :class="pnl_total >= 0 ? 'text-green' : 'text-red'">
                                        <div style="font-size:10px; color:var(--text-sub)">Total</div>
                                        <div style="font-weight:700" x-text="(pnl_total >= 0 ? '+' : '') + fmt(pnl_total)"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="flip-back w-body">
                            <div class="w-title"><i class="ph-fill ph-chart-line-up"></i> Balance</div>
                            <div class="val-xl" x-text="fmt(balance)">$0.00</div>
                            <div class="mono text-sub" style="font-size:12px; margin-top:6px;">Open P&L: <span :class="open_pnl >= 0 ? 'text-green' : 'text-red' " x-text="(open_pnl >= 0 ? '+' : '') + fmt(open_pnl)"></span></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="grid-stack-item" gs-id="smc" gs-w="3" gs-h="2">
            <div class="grid-stack-item-content">
                <div class="w-header">
                    <span class="w-title"><i class="ph-fill ph-brain"></i> SMC AI</span>
                    <span class="badge text-blue" x-text="session">--</span>
                </div>
                <div class="w-body">
                    <div style="margin-bottom:15px;">
                        <div class="text-sub" style="font-size:11px">Structure</div>
                        <div class="val-lg" x-text="structure" :class="structure.includes('UP') ? 'text-green' : structure.includes('DOWN') ? 'text-red' : ''">--</div>
                    </div>
                    <div>
                        <div class="w-title" style="justify-content:space-between;">
                            <span>Zone Strength</span>
                            <span x-text="zone_strength + '%'">0%</span>
                        </div>
                        <div style="height:4px; background:#333; border-radius:2px; margin-top:4px; overflow:hidden;">
                            <div :style="`width: ${zone_strength}%; background: ${zone_strength > 50 ? 'var(--green)' : 'var(--blue)'}; height:100%`"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="grid-stack-item" gs-id="news" gs-w="6" gs-h="1">
            <div class="grid-stack-item-content">
                <div class="w-body" style="display:flex; align-items:center; gap:12px; padding:0 14px;">
                    <i class="ph-fill ph-info text-blue" style="font-size:20px;"></i>
                    <div style="flex:1; overflow:hidden;">
                        <div style="font-weight:600; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" x-text="news.title">Scanning...</div>
                        <div class="text-sub" style="font-size:10px;" x-text="news.time">--</div>
                    </div>
                    <div style="text-align:right;">
                        <div class="mono" style="font-weight:700;" x-text="price">--.--</div>
                        <div class="text-sub" style="font-size:10px;">XAUUSD</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="grid-stack-item" gs-id="positions" gs-w="3" gs-h="3">
            <div class="grid-stack-item-content">
                <div class="w-header">
                    <span class="w-title"><i class="ph-fill ph-list-dashes"></i> Positions</span>
                    <span class="badge" x-text="trades.length + '/3'">0/3</span>
                </div>
                <div class="w-body">
                    <template x-for="t in trades" :key="t.id">
                        <div class="pos-item" :class="t.type">
                            <div>
                                <div style="font-weight:700; font-size:13px;" :class="t.type=='BUY'?'text-green':'text-red'">
                                    <span x-text="t.type"></span> <span x-text="t.lot_size"></span>
                                </div>
                                <div class="mono text-sub" style="font-size:10px; margin-top:2px;">@ <span x-text="t.entry"></span></div>
                            </div>
                            <div class="mono" style="font-weight:700;" :class="t.pnl >= 0 ? 'text-green' : 'text-red'">
                                <span x-text="t.pnl >= 0 ? '+' : ''"></span><span x-text="fmt(t.pnl)"></span>
                            </div>
                        </div>
                    </template>
                    <div x-show="trades.length === 0" style="text-align:center; padding:20px; color:var(--text-sub); font-size:12px;">
                        <i class="ph ph-robot" style="font-size:24px; margin-bottom:5px;"></i><br>AI Scanning...
                    </div>

                    <!-- Positions summary footer: Open P&L + Today/Week/Total -->
                    <div class="pos-footer">
                        <div>
                            <div style="font-size:11px; color:var(--text-sub)">Open P&L</div>
                            <div :class="open_pnl >= 0 ? 'text-green' : 'text-red'" style="font-weight:700;" x-text="(open_pnl >= 0 ? '+' : '') + fmt(open_pnl)"></div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:11px; color:var(--text-sub)">Today / Week / Total</div>
                            <div style="font-weight:700;">
                                <span :class="pnl_today >= 0 ? 'text-green' : 'text-red' " x-text="(pnl_today >= 0 ? '+' : '') + fmt(pnl_today)"></span>
                                <span style="color:var(--text-sub)"> / </span>
                                <span :class="pnl_week >= 0 ? 'text-green' : 'text-red' " x-text="(pnl_week >= 0 ? '+' : '') + fmt(pnl_week)"></span>
                                <span style="color:var(--text-sub)"> / </span>
                                <span :class="pnl_total >= 0 ? 'text-green' : 'text-red' " x-text="(pnl_total >= 0 ? '+' : '') + fmt(pnl_total)"></span>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        </div>

        <div class="grid-stack-item" gs-id="chart" gs-w="9" gs-h="3">
            <div class="grid-stack-item-content">
                <div class="chart-overlay">
                    <div class="badge"><i class="ph-fill ph-chart-bar"></i> M15</div>
                    <div class="badge" x-text="'STR: ' + structure"></div>
                    <div class="badge text-gold" x-text="'ZONE: ' + zone"></div>
                </div>
                <div id="chart-container"></div>
            </div>
        </div>
    </div>

    <div style="position:fixed; bottom:10px; right:10px; z-index:99; opacity:0.3; transition:opacity 0.2s;" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.3">
        <button onclick="resetLayout()" style="background:#2c2c2e; border:1px solid #444; color:#fff; padding:6px 12px; border-radius:8px; cursor:pointer; font-size:10px;">Reset Layout</button>
    </div>

    <script>
        const chart = LightweightCharts.createChart(document.getElementById('chart-container'), {
            layout: { background: { type: 'solid', color: '#1c1c1e' }, textColor: '#8e8e93' },
            grid: { vertLines: { color: 'rgba(255, 255, 255, 0.05)' }, horzLines: { color: 'rgba(255, 255, 255, 0.05)' } },
            rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.1)' },
            timeScale: { borderColor: 'rgba(255, 255, 255, 0.1)', timeVisible: true },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        });
        const series = chart.addCandlestickSeries({ upColor: '#30d158', downColor: '#ff453a', borderVisible: false, wickUpColor: '#30d158', wickDownColor: '#ff453a' });
        let pdhLine = series.createPriceLine({ price: 0, color: '#8e8e93', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: 'PDH' });
        let pdlLine = series.createPriceLine({ price: 0, color: '#8e8e93', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: 'PDL' });
        let eqLine = series.createPriceLine({ price: 0, color: '#ffffff', lineWidth: 1, lineStyle: 3, axisLabelVisible: false, title: 'EQ' });

        function dashboardStore() {
            return {
                equity: 0, balance: 0, pnl_daily: 0, price: 0,
                structure: 'NEUTRAL', zone_strength: 0, zone: '--', session: '--',
                news: { title: 'Scanning...', time: '--' },
                trades: [], flipCapital: false,

                // New PnL fields
                pnl_today: 0, pnl_week: 0, pnl_total: 0, open_pnl: 0,

                fmt(n) { return (n !== null && n !== undefined) ? '$' + Number(n).toLocaleString(undefined,{minimumFractionDigits:2}) : '$0.00' },
                init() { this.connect(); },
                connect() {
                    const ws = new WebSocket((window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws');
                    ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);

                        this.equity = data.equity || this.equity;
                        this.balance = data.balance || this.balance;
                        // maintain backwards compat: if server still sends pnl_daily
                        this.pnl_daily = data.pnl_daily ?? this.pnl_daily;

                        // new fields
                        this.pnl_today = data.pnl_today ?? this.pnl_today;
                        this.pnl_week = data.pnl_week ?? this.pnl_week;
                        this.pnl_total = data.pnl_total ?? this.pnl_total;
                        this.open_pnl = data.open_pnl ?? this.open_pnl;

                        this.price = data.price || this.price;
                        this.structure = data.market_structure || 'NEUTRAL';
                        this.zone_strength = data.zone_strength || 0; this.zone = data.zone || '--';
                        this.session = data.session || '--'; this.news = data.news_event || this.news;
                        this.trades = data.trades || [];

                        if (data.chart_data?.length) {
                            const unique = [...new Map(data.chart_data.map(i => [i['time'], i])).values()].sort((a, b) => a.time - b.time);
                            series.setData(unique);
                        }
                        if (data.chart_overlays) {
                            const ov = data.chart_overlays;
                            if(ov.levels?.pdh) pdhLine.applyOptions({ price: ov.levels.pdh, axisLabelVisible: true });
                            if(ov.levels?.pdl) pdlLine.applyOptions({ price: ov.levels.pdl, axisLabelVisible: true });
                            if(ov.zones?.equilibrium) eqLine.applyOptions({ price: ov.zones.equilibrium, axisLabelVisible: true });
                        }
                    };
                    ws.onclose = () => setTimeout(() => this.connect(), 3000);
                }
            }
        }

        let grid = GridStack.init({ column: 12, cellHeight: 70, margin: 6, float: true, disableOneColumnMode: false });
        const savedLayout = localStorage.getItem('guardeer_v3_layout');
        if (savedLayout) grid.load(JSON.parse(savedLayout));
        grid.on('change', function(event, items) {
            localStorage.setItem('guardeer_v3_layout', JSON.stringify(grid.save(false)));
        });
        grid.on('resizestop', function(event, el) {
            if (el.querySelector('#chart-container')) {
                let rect = el.getBoundingClientRect();
                chart.applyOptions({ width: rect.width, height: rect.height - 20 });
            }
        });
        new ResizeObserver(entries => {
            if(entries.length) chart.applyOptions({ width: entries[0].contentRect.width, height: entries[0].contentRect.height });
        }).observe(document.getElementById('chart-container'));
        
        function resetLayout() { localStorage.removeItem('guardeer_v3_layout'); location.reload(); }
    </script>
</body>
</html>
"""

@app.get("/dashboard")
async def dashboard(): return HTMLResponse(content=html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        # log client connection for easier debugging
        try:
            client = websocket.client if hasattr(websocket, "client") else None
            print("🔌 WS CONNECTED:", client)
        except:
            print("🔌 WS CONNECTED (unknown client)")
        if bot_state:
            await websocket.send_json(bot_state)
        while True:
            # client pings can be ignored; keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        try:
            client = websocket.client if hasattr(websocket, "client") else None
            print("🔌 WS DISCONNECTED:", client)
        except:
            print("🔌 WS DISCONNECTED")
        if websocket in active_connections: active_connections.remove(websocket)

# --- BOT UPDATE INTERFACE (DATA RECEIVER) ---
def update_bot_state_v2(bot_instance, analysis_data):
    """Called by main.py. Reads FORCE-FED data and updates enhanced PnL fields.

    Expected:
      - bot_instance may contain:
          - equity, balance, last_price, open_positions (list), closed_trades (list)
      - analysis_data may contain pdh/pdl/zones/market_structure etc.
    This function:
      - aggregates open PnL from open_positions (best-effort)
      - ingests any closed_trades list and feeds the local pnl_tracker
      - populates bot_state with pnl_today, pnl_week, pnl_total, open_pnl (floating)
    """
    global bot_state, pnl_tracker

    def get_val(obj, key, default=0.0):
        if hasattr(obj, key): return getattr(obj, key)
        if isinstance(obj, dict): return obj.get(key, default)
        return default

    # ---- Debug-entry: show caller & payload shape ----
    try:
        is_dict = isinstance(bot_instance, dict)
        open_positions_raw = bot_instance.get("open_positions", None) if is_dict else getattr(bot_instance, "open_positions", None)
        closed_trades_raw = bot_instance.get("closed_trades", None) if is_dict else getattr(bot_instance, "closed_trades", None)
        ops_len = len(open_positions_raw) if open_positions_raw else 0
        cls_len = len(closed_trades_raw) if closed_trades_raw else 0
    except Exception:
        ops_len = cls_len = 0
        open_positions_raw = closed_trades_raw = None

    # Disabled noisy entry log
    # print("🔔 update_bot_state_v2 called | type:", "dict" if is_dict else type(bot_instance), f"| open_positions={ops_len} closed_trades={cls_len}")

    # sample profits for quick inspection (commented out to reduce noise)
    try:
        if open_positions_raw:
            sample = []
            for p in (open_positions_raw[:3] if hasattr(open_positions_raw, "__len__") else []):
                sample.append(get_val(p, "profit", get_val(p, "pnl", "<missing>")))
            # print("  sample position profits:", sample)
    except Exception as e:
        # keep the inspect error log (small) for diagnostics
        print("  sample inspect error:", e)
    # short traceback to find caller (commented out to reduce noise)
    try:
        tb = "".join(traceback.format_stack(limit=6)[:-1])
        # print("  caller stack (short):\n", tb)
    except Exception:
        pass

    # ---- helper: robust profit parser ----
    def parse_profit(x):
        if x is None: return 0.0
        try:
            if isinstance(x, (int, float)) and not math.isnan(x): return float(x)
            s = str(x).strip()
            # remove typical currency/format chars
            for ch in ("$", "€", ","):
                s = s.replace(ch, "")
            s = s.replace("(", "-").replace(")", "")
            return float(s)
        except Exception:
            return 0.0

    # 1. READ FORCE-FED DATA
    equity = float(get_val(bot_instance, "equity", 0.0) or 0.0)
    balance = float(get_val(bot_instance, "balance", 0.0) or 0.0)

    # 2. OPEN PNL & POSITIONS
    open_pnl = 0.0
    formatted_trades = []

    try:
        positions = get_val(bot_instance, "open_positions", []) or []
        current_price = float(get_val(bot_instance, "last_price", 0.0) or 0.0)

        for p in positions:
            # profit might be string/number; parse robustly
            profit_raw = get_val(p, "profit", None)
            if profit_raw is None:
                profit_raw = get_val(p, "pnl", None)
            profit = parse_profit(profit_raw)

            # Auto-calc if missing (fallback)
            entry = parse_profit(get_val(p, "price", get_val(p, "entry_price", 0.0)))
            lot = parse_profit(get_val(p, "lot_size", get_val(p, "volume", 0.0)))
            signal = get_val(p, "signal", get_val(p, "type", "N/A"))

            if (profit == 0.0) and (current_price > 0 and entry > 0 and lot > 0):
                try:
                    if str(signal).upper() == "BUY":
                        profit = (current_price - entry) * lot * 100
                    else:
                        profit = (entry - current_price) * lot * 100
                except:
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
        # defensive: keep formatted_trades empty on failure
        print(f"⚠️ update_bot_state_v2 positions parsing error: {e}")

    # debug-friendly closed trades ingestion with symbol filter and tiny-profit skip
    try:
        closed = get_val(bot_instance, "closed_trades", []) or []
        if closed:
            # print(f"ℹ️ Received {len(closed)} closed trades — sample: {repr(closed[:3])}")
            pass
        today_sum = 0.0
        processed_tickets_this_call = []
        for ct in closed:
            # filter by symbol (only count XAUUSD by default)
            symbol = get_val(ct, "symbol", "") or get_val(ct, "instrument", "") or ""
            if symbol:
                try:
                    if str(symbol).upper() != "XAUUSD":
                        continue
                except:
                    pass

            # try many possible profit keys
            profit_raw = None
            for k in ("profit", "pnl", "profit_usd", "profit_usd_str", "deal_profit"):
                profit_raw = get_val(ct, k, None)
                if profit_raw is not None:
                    break
            # fallback: maybe nested object (e.g., {'deal': {...}})
            if profit_raw is None and isinstance(ct, dict):
                for v in ct.values():
                    if isinstance(v, (int, float)) and abs(v) > 0:
                        profit_raw = v
                        break

            profit = parse_profit(profit_raw)

            # skip tiny/noise profits (commissions etc.)
            if abs(profit) < 0.01:
                continue

            # determine timestamp (optional) - robust detection
            when = None
            ts = get_val(ct, "time", None) or get_val(ct, "close_time", None) or get_val(ct, "timestamp", None)
            if ts:
                try:
                    if isinstance(ts, str):
                        try:
                            when = datetime.fromisoformat(ts)
                        except:
                            try:
                                # try common formats
                                when = datetime.strptime(ts, "%Y.%m.%d %H:%M:%S")
                            except:
                                when = None
                    elif isinstance(ts, (int, float)):
                        # detect milliseconds vs seconds
                        if ts > 1e12:
                            when = datetime.fromtimestamp(float(ts) / 1000.0)
                        else:
                            when = datetime.fromtimestamp(float(ts))
                except Exception:
                    when = None

            # record into tracker (pass ticket for dedupe)
            try:
                ticket_id = get_val(ct, "ticket", get_val(ct, "order", None) or get_val(ct, "id", None))
                ticket_str = str(ticket_id) if ticket_id is not None else None
                before_count = len(pnl_tracker.processed_ticket_ids)
                pnl_tracker.add_closed_trade(float(profit or 0.0), when=when, ticket=ticket_str)
                after_count = len(pnl_tracker.processed_ticket_ids)
                # if processed, record locally for log
                if ticket_str and (after_count > before_count):
                    processed_tickets_this_call.append((ticket_str, float(profit or 0.0)))
            except Exception as e:
                print(f"⚠️ pnl_tracker add error: {e}")

            # if trade closed today, add to today_sum
            try:
                if when is None or when.date() == datetime.now().date():
                    today_sum += float(profit or 0.0)
            except Exception:
                today_sum += float(profit or 0.0)

        # if tracker didn't have today data for some reason, ensure today is seeded
        if pnl_tracker.get_daily() == 0.0 and today_sum != 0.0:
            # add a synthetic 'today' entry so get_daily returns a value
            today_key = datetime.now().date().isoformat()
            pnl_tracker.history[today_key] = pnl_tracker.history.get(today_key, 0.0) + today_sum
            pnl_tracker.realized_pnl += float(today_sum)
            pnl_tracker.total_realized += float(today_sum)

        if processed_tickets_this_call:
            total_added = sum(p for _, p in processed_tickets_this_call)
            # print(f"✅ Closed trades processed: tickets={len(processed_tickets_this_call)} total_added={total_added:.2f} details={processed_tickets_this_call}")
            pass
        else:
            # nothing new processed in this call (all were filtered or duplicates)
            if closed:
                # print("ℹ️ Closed trades received but none passed filters or all were duplicates.")
                pass
    except Exception as e:
        print(f"⚠️ closed_trades ingestion error: {e}")

    # 4. DEBUG LOG
    # print(f"🔎 DASHBOARD DEBUG: Equity=${equity:.2f} | Balance=${balance:.2f} | Trades={len(formatted_trades)} | OpenPnL={open_pnl:.2f}")

    # 5. BUILD STATE (include today/week/total/open)
    bot_state.update({
        "equity": equity,
        "balance": balance,
        # compatibility: pnl_daily remains (today)
        "pnl_daily": round(pnl_tracker.get_daily() + open_pnl, 2),
        # Explicit fields requested
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
            "levels": {"pdh": analysis_data.get("pdh") if isinstance(analysis_data, dict) else getattr(analysis_data, "pdh", None), "pdl": analysis_data.get("pdl") if isinstance(analysis_data, dict) else getattr(analysis_data, "pdl", None)},
            "zones": {"equilibrium": analysis_data.get("zones", {}).get("equilibrium") if isinstance(analysis_data, dict) else getattr(analysis_data, "zones", {}).get("equilibrium")}
        },
        "news_event": {"title": "USD CPI Data (High Impact)", "time": "In 3h 45m"} if datetime.now().hour == 14 else {"title": "No major events scheduled", "time": "Market Calm"},
        "chart_data": get_val(bot_instance, "chart_data", [])[-100:]
    })

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")