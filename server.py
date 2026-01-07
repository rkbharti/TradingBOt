from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
from datetime import datetime, timedelta

# --- THE EMBEDDED DASHBOARD UI ---
html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Guardeer Institutional Terminal</title>
    <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
    <link href="https://unpkg.com/@phosphor-icons/web" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --bg: #050505; --glass: rgba(20, 22, 28, 0.75); --border: 1px solid rgba(255, 255, 255, 0.08); --green: #00E396; --red: #FF4560; --gold: #FFB300; }
        body { background: var(--bg); color: #fff; font-family: 'Inter', sans-serif; height: 100vh; margin: 0; padding: 15px; overflow: hidden; }
        .grid { display: grid; grid-template-columns: 65fr 35fr; gap: 15px; height: 100%; }
        .panel { background: var(--glass); border: var(--border); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; backdrop-filter: blur(10px); }
        .head { padding: 12px 15px; border-bottom: var(--border); font-size: 13px; font-weight: 600; color: #888; text-transform: uppercase; display: flex; justify-content: space-between; }
        #chart { width: 100%; height: 100%; }
        .overlay { position: absolute; top: 50px; left: 30px; z-index: 10; display: flex; flex-direction: column; gap: 8px; }
        .badge { background: rgba(0,0,0,0.8); border: var(--border); padding: 5px 10px; border-radius: 4px; font-size: 11px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
        .dot { width: 8px; height: 8px; border-radius: 50%; }
        .widgets { display: grid; grid-template-rows: auto auto 1fr; gap: 15px; height: 100%; }
        .metric-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .metric { background: rgba(255,255,255,0.03); padding: 15px; border-radius: 8px; text-align: center; border: var(--border); }
        .val { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
        .lbl { font-size: 11px; color: #888; }
        .news-item { padding: 10px 15px; border-bottom: var(--border); display: flex; justify-content: space-between; align-items: center; }
        .tag { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 3px; }
        .high { background: rgba(255,69,96,0.2); color: var(--red); }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; display: flex; flex-direction: column; } body { overflow: auto; } #chart { height: 400px; } }
    </style>
</head>
<body>
    <div class="grid">
        <div class="panel">
            <div class="head">
                <span><i class="ph ph-chart-line-up"></i> Institutional Chart</span>
                <span style="color: var(--green); display: flex; align-items: center; gap: 6px;"><span class="dot" style="background:var(--green)"></span> LIVE</span>
            </div>
            <div style="flex-grow: 1; position: relative;">
                <div class="overlay">
                    <div class="badge">BIAS: <span id="bias" style="color:#fff">--</span></div>
                    <div class="badge">ZONE: <span id="zone" style="color:#fff">--</span></div>
                    <div class="badge" style="border-color: var(--gold);"><i class="ph ph-lightning" style="color:var(--gold)"></i> IDM: <span id="idm" style="color:#fff">Scanning...</span></div>
                </div>
                <div id="chart"></div>
            </div>
        </div>
        <div class="widgets">
            <div class="panel">
                <div class="head">Capital</div>
                <div style="padding: 15px;">
                    <div class="metric-row">
                        <div class="metric"><div class="val" id="bal">$0.00</div><div class="lbl">Balance</div></div>
                        <div class="metric"><div class="val" id="pnl">$0.00</div><div class="lbl">Floating PnL</div></div>
                    </div>
                </div>
            </div>
            <div class="panel">
                <div class="head">Analysis Matrix</div>
                <div style="padding: 15px; font-size: 13px;">
                    <div style="display:flex; justify-content:space-between; margin-bottom:10px; border-bottom:1px solid #222; padding-bottom:5px;">
                        <span style="color:#888">Structure</span><span id="struct">--</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-bottom:10px; border-bottom:1px solid #222; padding-bottom:5px;">
                        <span style="color:#888">Session</span><span id="sess">--</span>
                    </div>
                    <div style="display:flex; justify-content:space-between;">
                        <span style="color:#888">Zone Strength</span><span id="strength">0%</span>
                    </div>
                </div>
            </div>
            <div class="panel" style="flex-grow:1; min-height:200px;">
                <div class="head">News (Gold/USD)</div>
                <div id="news" style="overflow-y:auto; flex-grow:1;"></div>
            </div>
        </div>
    </div>
    <script>
        const chart = LightweightCharts.createChart(document.getElementById('chart'), {
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#888' },
            grid: { vertLines: { color: '#111' }, horzLines: { color: '#111' } },
            rightPriceScale: { borderColor: '#222' }, timeScale: { borderColor: '#222', timeVisible: true }
        });
        const series = chart.addCandlestickSeries({ upColor: '#00E396', downColor: '#FF4560', borderVisible: false, wickUpColor: '#00E396', wickDownColor: '#FF4560' });
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
        
        ws.onmessage = (e) => {
            const d = JSON.parse(e.data);
            
            // UPDATE CHART (Only if new data exists)
            if (d.chart_data && d.chart_data.length > 0) {
                // Bulk update the chart with the new data from main.py
                series.setData(d.chart_data);
            }

            if (d.balance) document.getElementById('bal').innerText = `$${d.balance.toLocaleString()}`;
            if (d.pnl !== undefined) { 
                const p = parseFloat(d.pnl); 
                const el = document.getElementById('pnl');
                el.innerText = (p>=0?'+':'') + `$${p.toFixed(2)}`;
                el.style.color = p>=0?'#00E396':'#FF4560';
            }
            document.getElementById('bias').innerText = (d.market_structure||"NEUTRAL").includes("UP")?"BULLISH":(d.market_structure||"NEUTRAL").includes("DOWN")?"BEARISH":"NEUTRAL";
            document.getElementById('zone').innerText = d.zone || "--";
            document.getElementById('struct').innerText = d.market_structure || "--";
            document.getElementById('sess').innerText = d.smc_indicators?.session || "--";
            document.getElementById('strength').innerText = (d.zone_strength||0) + "%";
            
            const ind = d.smc_indicators?.inducement_data || {};
            if (ind.inducement) {
                document.getElementById('idm').innerText = `FOUND @ ${ind.level}`;
                document.getElementById('idm').style.color = "#FFB300";
            } else {
                document.getElementById('idm').innerText = "Scanning...";
                document.getElementById('idm').style.color = "#fff";
            }

            if (d.news?.length) {
                const c = document.getElementById('news'); c.innerHTML = "";
                d.news.forEach(n => {
                    const div = document.createElement('div'); div.className = 'news-item';
                    div.innerHTML = `<div><div style="font-weight:500">${n.title}</div><div style="font-size:10px; color:#666">${n.time}</div></div><div class="tag high">${n.impact}</div>`;
                    c.appendChild(div);
                });
            }
        };
        new ResizeObserver(e => { if(e.length) chart.applyOptions({ width: e[0].contentRect.width, height: e[0].contentRect.height }); }).observe(document.getElementById('chart'));
    </script>
</body>
</html>
"""

# --- SERVER BACKEND ---
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
active_connections = []
bot_state = {"balance": 0, "pnl": 0, "chart_data": [], "news": []}

def get_news():
    now = datetime.now()
    return [
        {"title": "USD Core PCE Price Index", "impact": "HIGH", "time": (now + timedelta(hours=2)).strftime("%H:%M")},
        {"title": "USD Unemployment Claims", "impact": "HIGH", "time": (now + timedelta(hours=5)).strftime("%H:%M")},
        {"title": "USD ISM Services PMI", "impact": "HIGH", "time": (now + timedelta(hours=8)).strftime("%H:%M")}
    ]

@app.get("/dashboard")
async def dashboard(): return HTMLResponse(content=html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        await websocket.send_json(bot_state)
        while True: await websocket.receive_text()
    except WebSocketDisconnect: active_connections.remove(websocket)

# --- THREAD-SAFE UPDATE FUNCTION ---
def update_bot_state(new_state):
    """
    Called from main.py (Main Thread). 
    Updates the global state. 
    Does NOT attempt to broadcast immediately to avoid threading issues.
    """
    global bot_state
    bot_state.update(new_state)
    bot_state["news"] = get_news()

# --- BACKGROUND BROADCASTER (Runs on Server Thread) ---
@app.on_event("startup")
async def start_broadcasting():
    async def broadcast_loop():
        while True:
            if active_connections:
                # Create a copy to avoid iteration errors
                for conn in active_connections[:]: 
                    try: 
                        await conn.send_json(bot_state)
                    except: 
                        if conn in active_connections:
                            active_connections.remove(conn)
            await asyncio.sleep(1) # Broadcast every 1 second
            
    asyncio.create_task(broadcast_loop())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="critical")