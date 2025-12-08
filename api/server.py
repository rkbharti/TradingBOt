from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
import json
import asyncio
from datetime import datetime
from typing import List
import os

app = FastAPI(title="Trading Bot Dashboard API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active WebSocket connections
active_connections: List[WebSocket] = []

# Global bot state
bot_state = {
    "running": False,
    "balance": 100000,
    "current_price": {"bid": 0, "ask": 0, "spread": 0},
    "last_signal": "HOLD",
    "last_update": "",
    "smc_indicators": {
        "fvg_bullish": False,
        "fvg_bearish": False,
        "bos": None,
        "session": "CLOSED"
    },
    "technical_levels": {
        "ma20": 0,
        "ma50": 0,
        "ema200": 0,
        "support": 0,
        "resistance": 0,
        "atr": 0
    },
    "market_structure": "NEUTRAL",
    "zone": "EQUILIBRIUM",
    "trades": []
}

@app.get("/")
async def home():
    return {
        "message": "Trading Bot API is running!", 
        "status": "online",
        "dashboard_url": "/dashboard"
    }

@app.get("/dashboard")
async def serve_dashboard():
    """Serve the dashboard HTML"""
    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return {"error": "Dashboard not found"}

@app.get("/api/status")
async def get_status():
    """Get current bot status"""
    return bot_state

@app.post("/api/trade/manual")
async def manual_trade(trade_type: str, lot_size: float = 0.01):
    """Execute manual trade"""
    try:
        from main import execute_manual_trade
        
        trade_record = {
            "id": len(bot_state["trades"]) + 1,
            "type": trade_type.upper(),
            "lot_size": lot_size,
            "entry": bot_state["current_price"]["bid" if trade_type.upper() == "SELL" else "ask"],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
            "status": "PENDING"
        }
        
        # Execute trade through bot
        result = execute_manual_trade(trade_type, lot_size)
        
        if result:
            trade_record["status"] = "EXECUTED"
            bot_state["trades"].insert(0, trade_record)
            
            # Keep only last 50 trades
            if len(bot_state["trades"]) > 50:
                bot_state["trades"] = bot_state["trades"][:50]
            
            await broadcast_update()
            return {"status": "success", "trade": trade_record}
        else:
            return {"status": "error", "message": "Trade execution failed"}
            
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates"""
    await websocket.accept()
    active_connections.append(websocket)
    
    try:
        # Send initial state
        await websocket.send_json(bot_state)
        
        # Keep connection alive and listen
        while True:
            try:
                data = await websocket.receive_text()
                # Echo back for heartbeat
                await websocket.send_text("pong")
            except:
                break
                
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)

async def broadcast_update():
    """Broadcast update to all connected clients"""
    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(bot_state)
        except:
            disconnected.append(connection)
    
    # Remove disconnected clients
    for conn in disconnected:
        if conn in active_connections:
            active_connections.remove(conn)

def update_bot_state(new_state: dict):
    """Called by trading bot to update state"""
    global bot_state
    bot_state.update(new_state)
    bot_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    
    # Broadcast to all connected clients (non-blocking)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(broadcast_update())
    except:
        pass

def get_bot_state():
    """Get current bot state"""
    return bot_state

if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print("\n" + "="*60)
    print("🚀 Trading Bot Dashboard API Server")
    print("="*60)
    print(f"📱 Local Access:    http://localhost:8000/dashboard")
    print(f"🌐 Network Access:  http://{local_ip}:8000/dashboard")
    print(f"📊 API Docs:        http://localhost:8000/docs")
    print("="*60)
    print("✨ Dashboard is ready! Open the URL in your browser")
    print("📱 Access from phone using the Network Access URL")
    print("="*60 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
