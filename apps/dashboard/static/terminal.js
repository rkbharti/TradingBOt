/* ============================================================
   GUARDEER OS – terminal.js v5 (Production Ready)
   ============================================================ */

// ------------------------------------------------------------------
//  Global helpers & constants
// ------------------------------------------------------------------
const GATE_NAMES = {
    'step_1_htf_bias': '1. HTF BIAS',
    'step_2_external_liquidity_sweep': '2. LIQ. SWEEP',
    'step_3_choch_mss_body_close': '3. CHOCH / MSS',
    'step_4_valid_poi': '4. VALID POI',
    'step_5_ob_fvg_confluence': '5. OB / FVG CONF.',
    'step_6_dealing_range': '6. DEALING RANGE',
    'step_7_killzone': '7. KILLZONE',
    'step_8_risk_reward': '8. RISK / REWARD',
};

const GOS_PALETTES = [
    'stranger-things',
    'breaking-bad',
    'dark',
    'demon-slayer',
    'pirates'
];

let _chart = null;
let _series = null;
let _chartInitialized = false;
let _overlayPriceLines = [];   // stores objects returned by createPriceLine()
let _resizeObserver = null;
let _lastOverlaysHash = null;

// Helper: get CSS variable (with fallback for light/dark)
function _cssVar(name, fallback = '') {
    const val = getComputedStyle(document.body).getPropertyValue(name).trim();
    return val || fallback;
}

// Helper: build chart theme from current palette + light/dark mode
function _getChartTheme(themeMode) {
    const isDark = themeMode !== 'light';
    return {
        bg:       isDark ? _cssVar('--paper', '#1a0f0a') : _cssVar('--paper', '#fefaf2'),
        text:     isDark ? _cssVar('--ink', '#e6d5b8') : _cssVar('--ink', '#2c2825'),
        grid:     isDark ? _cssVar('--border-soft', 'rgba(230,213,184,0.2)') : _cssVar('--border-soft', 'rgba(44,40,37,0.2)'),
        border:   isDark ? _cssVar('--border-strong', 'rgba(230,213,184,0.5)') : _cssVar('--border-strong', 'rgba(44,40,37,0.6)'),
        crosshair: isDark ? _cssVar('--accent', '#b53b2a') : _cssVar('--accent', '#3b3530'),
        up:       _cssVar('--accent', '#b53b2a'),
        down:     _cssVar('--danger', '#c0392b'),
    };
}

// ------------------------------------------------------------------
//  Chart overlay management (using createPriceLine, no addPriceMarker)
// ------------------------------------------------------------------
function _clearOverlays() {
    if (_overlayPriceLines.length) {
        _overlayPriceLines.forEach(line => {
            try { line.remove(); } catch(e) {}
        });
        _overlayPriceLines = [];
    }
}

function _drawOverlays(overlaysData, chartObjects) {
    if (!_chart || !_series) return;
    _clearOverlays();

    // Helper: add a price line (horizontal line with optional label)
    function addPriceLine(price, color, title = '', lineWidth = 1, lineStyle = 0) {
        if (!price || isNaN(price)) return;
        try {
            const line = _series.createPriceLine({
                price: price,
                color: color,
                lineWidth: lineWidth,
                lineStyle: lineStyle,
                axisLabelVisible: !!title,
                title: title,
            });
            _overlayPriceLines.push(line);
        } catch(e) { console.warn('[OVERLAY] addPriceLine error:', e); }
    }

    // Process POI overlays (array from backend)
    if (Array.isArray(overlaysData)) {
        overlaysData.forEach(poi => {
            if (poi.type === 'pdh') {
                addPriceLine(poi.price, '#ffaa44', 'PDH');
            } else if (poi.type === 'pdl') {
                addPriceLine(poi.price, '#88aaff', 'PDL');
            } else if (poi.type === 'fvg' && poi.top && poi.bottom) {
                addPriceLine(poi.top, '#ff66aa', 'FVG TOP');
                addPriceLine(poi.bottom, '#ff66aa', 'FVG BOT');
            } else if (poi.type === 'order_block' && poi.high && poi.low) {
                addPriceLine(poi.high, '#66ffcc', 'OB HIGH');
                addPriceLine(poi.low, '#66ffcc', 'OB LOW');
            } else if (poi.type === 'choch') {
                addPriceLine(poi.price, '#ffaa88', 'CHoCH');
            } else if (poi.type === 'liquidity_sweep') {
                addPriceLine(poi.price, '#ff8855', 'LIQ SWEEP');
            } else if (poi.price) {
                addPriceLine(poi.price, '#aaaaff', poi.label || 'POI');
            }
        });
    }

    // Process chartObjects (legacy dict from backend)
    if (chartObjects && typeof chartObjects === 'object') {
        for (const [key, value] of Object.entries(chartObjects)) {
            if (key === 'pdh' && value) addPriceLine(value, '#ffaa44', 'PDH');
            if (key === 'pdl' && value) addPriceLine(value, '#88aaff', 'PDL');
            if (key === 'order_block_high' && value) addPriceLine(value, '#66ffcc', 'OB HIGH');
            if (key === 'order_block_low' && value) addPriceLine(value, '#66ffcc', 'OB LOW');
            if (key === 'fvg_top' && value) addPriceLine(value, '#ff66aa', 'FVG TOP');
            if (key === 'fvg_bottom' && value) addPriceLine(value, '#ff66aa', 'FVG BOT');
            if (key === 'choch_level' && value) addPriceLine(value, '#ffaa88', 'CHoCH');
            if (key === 'liquidity_sweep_level' && value) addPriceLine(value, '#ff8855', 'LIQ SWEEP');
        }
    }
}

// ------------------------------------------------------------------
//  Chart theme & initialization
// ------------------------------------------------------------------
function _applyChartTheme(themeMode) {
    if (!_chart || !_series) return;
    const t = _getChartTheme(themeMode);
    _chart.applyOptions({
        layout: { background: { color: t.bg }, textColor: t.text, fontSize: 11, fontFamily: 'Courier Prime, monospace' },
        grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
        rightPriceScale: { borderColor: t.border, alignLabels: true },
        timeScale: { borderColor: t.border, timeVisible: true, secondsVisible: false },
        crosshair: { vertLine: { color: t.crosshair, style: 3 }, horzLine: { color: t.crosshair, style: 3 } },
    });
    _series.applyOptions({ upColor: t.up, downColor: t.down, borderVisible: false, wickUpColor: t.up, wickDownColor: t.down });
}

function _initChart(themeMode = 'dark') {
    const container = document.getElementById('chart-container');
    if (!container || _chartInitialized) return;
    _chartInitialized = true;

    const t = _getChartTheme(themeMode);
    _chart = LightweightCharts.createChart(container, {
        layout: { background: { color: t.bg }, textColor: t.text, fontSize: 11, fontFamily: 'Courier Prime, monospace' },
        grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
        rightPriceScale: { borderColor: t.border, alignLabels: true },
        timeScale: { borderColor: t.border, timeVisible: true, secondsVisible: false },
        crosshair: { vertLine: { color: t.crosshair, style: 3 }, horzLine: { color: t.crosshair, style: 3 } },
    });

    _series = _chart.addCandlestickSeries({
        upColor: t.up, downColor: t.down, borderVisible: false,
        wickUpColor: t.up, wickDownColor: t.down,
    });

    if (_resizeObserver) _resizeObserver.disconnect();
    _resizeObserver = new ResizeObserver(() => {
        if (!_chart) return;
        _chart.resize(container.clientWidth, container.clientHeight);
        // redraw overlays after resize (re‑creates lines at correct positions)
        if (window._lastOverlaysData || window._lastChartObjects) {
            _drawOverlays(window._lastOverlaysData, window._lastChartObjects);
        }
    });
    _resizeObserver.observe(container);
}

// ------------------------------------------------------------------
//  Alpine.js Data Store (DS)
// ------------------------------------------------------------------
function DS() {
    return {
        // --- WebSocket & connection ---
        ws: null,
        ws_connected: false,
        latency_ms: null,
        _lastPingTime: null,
        _wsRetryCount: 0,
        _heartbeatInterval: null,
        _healthInterval: null,

        // --- Multi-symbol states ---
        bot_states: {},
        active_symbol: '',
        _lastChartDataHash: null,

        // --- Bot control ---
        bot_status: true,
        current_tf: 'M15',
        theme_mode: localStorage.getItem('gos_theme') || 'dark',
        palette: localStorage.getItem('gos_palette') || 'stranger-things',
        available_palettes: GOS_PALETTES,

        // --- Account / equity ---
        account_login: '--',
        account_server: '--',
        account_name: 'REAL-01',
        equity: 0,
        balance: 0,
        pnl_today: 0,
        pnl_today_pct: 0,
        pnl_week: 0,
        pnl_total: 0,
        current_price: 0,

        // --- Market state ---
        structure: '--',
        session: '--',
        d1_bias: '--',
        h4_bias: '--',

        // --- Trades & news ---
        trades: [],
        closed_trades: [],
        news_items: [],
        news_filter: 'HIGH',
        news: { time: '--' },

        // --- Signal engine (from backend) ---
        signal_engine: {
            action: 'NO_TRADE',
            confidence: 0,
            direction: '--',
            reason: 'Parsing stream data...',
            reason_code: 'SCANNING',
            entry_price: null,
            sl_price: null,
            tp_price: null,
            gates: {}
        },

        // --- System health (polled) ---
        _health: {
            mt5_connected: 'down',
            websocket_active: false,
            trader_alive: false,
            strategy_engine: false,
            data_feed: false,
            vps_uptime_seconds: 0,
            webhook_age_seconds: 999
        },

        // --- Chart data & overlays ---
        chart_data_raw: [],
        poi_overlays: [],
        chart_objects: {},

        // ------------------------------------------------------------------
        //  Lifecycle
        // ------------------------------------------------------------------
        init() {
            this._applyThemeMode();
            this._applyPalette();
            this.$nextTick(() => {
                _initChart(this.theme_mode);
                _applyChartTheme(this.theme_mode);
            });
            this._connectWebSocket();
            this._startHealthPoll();
            this._startClocks();
        },

        // ------------------------------------------------------------------
        //  Theme & palette
        // ------------------------------------------------------------------
        _applyThemeMode() {
            document.body.classList.toggle('light-theme', this.theme_mode === 'light');
        },
        _applyPalette() {
            let pal = this.palette;
            if (!GOS_PALETTES.includes(pal) && pal !== 'custom') pal = 'stranger-things';
            document.body.setAttribute('data-palette', pal);
        },
        setPalette(paletteName) {
            if (paletteName === 'custom') return;
            if (!GOS_PALETTES.includes(paletteName)) paletteName = 'stranger-things';
            this.palette = paletteName;
            localStorage.setItem('gos_palette', paletteName);
            this._applyPalette();
            _applyChartTheme(this.theme_mode);
        },
        toggleTheme() {
            this.theme_mode = this.theme_mode === 'dark' ? 'light' : 'dark';
            localStorage.setItem('gos_theme', this.theme_mode);
            this._applyThemeMode();
            _applyChartTheme(this.theme_mode);
        },
        paletteLabel(name) {
            const map = { 'stranger-things': 'ST', 'breaking-bad': 'BB', 'dark': 'DK', 'demon-slayer': 'DS', 'pirates': 'PR' };
            return map[name] || 'ST';
        },

        // ------------------------------------------------------------------
        //  Clock updates (global, called from init)
        // ------------------------------------------------------------------
        _startClocks() {
            const update = () => {
                const now = new Date();
                const utcEl = document.getElementById('utc-clock');
                const istEl = document.getElementById('ist-clock');
                if (utcEl) utcEl.innerText = now.toUTCString().split(' ')[4];
                if (istEl) {
                    istEl.innerText = now.toLocaleTimeString('en-IN', {
                        timeZone: 'Asia/Kolkata',
                        hour12: false
                    });
                }
            };
            update();
            setInterval(update, 1000);
        },

        // ------------------------------------------------------------------
        //  Health polling (every 10 seconds)
        // ------------------------------------------------------------------
        _startHealthPoll() {
            const poll = () => {
                fetch('/health')
                    .then(r => r.json())
                    .then(h => {
                        this._health = { ...this._health, ...h };
                        // Also derive trader_alive from webhook age if needed
                        if (h.webhook_age_seconds !== undefined && h.webhook_age_seconds < 90) {
                            this._health.trader_alive = true;
                        } else if (h.webhook_age_seconds !== undefined) {
                            this._health.trader_alive = false;
                        }
                    })
                    .catch(e => console.warn('[HEALTH] poll error:', e));
            };
            poll();
            this._healthInterval = setInterval(poll, 10000);
        },

        // ------------------------------------------------------------------
        //  WebSocket with heartbeat & auto‑reconnect
        // ------------------------------------------------------------------
        _connectWebSocket() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${location.host}/ws`;
            try {
                this.ws = new WebSocket(url);
            } catch (e) {
                console.error('[WS] creation failed:', e);
                this._wsReconnect();
                return;
            }

            this.ws.onopen = () => {
                console.log('[WS] connected');
                this.ws_connected = true;
                this._wsRetryCount = 0;
                // start heartbeat
                if (this._heartbeatInterval) clearInterval(this._heartbeatInterval);
                this._heartbeatInterval = setInterval(() => {
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this._lastPingTime = Date.now();
                        this.ws.send(JSON.stringify({ action: 'ping' }));
                    }
                }, 10000);
                // request initial state (optional, server will send anyway)
                this.ws.send(JSON.stringify({ action: 'get_state' }));
            };

            this.ws.onmessage = (evt) => {
                try {
                    const data = JSON.parse(evt.data);
                    if (data.action === 'pong') {
                        if (this._lastPingTime) {
                            this.latency_ms = Date.now() - this._lastPingTime;
                        }
                        return;
                    }

                    // Store/merge all states
                    this.bot_states = { ...this.bot_states, ...data };
                    
                    const symbols = Object.keys(this.bot_states);
                    if (symbols.length > 0) {
                        if (!this.active_symbol || !this.bot_states[this.active_symbol]) {
                            this.active_symbol = symbols.includes('XAUUSD') ? 'XAUUSD' : symbols[0];
                        }
                        this.syncActiveState();
                    }

                    // update timestamp
                    const tsEl = document.getElementById('last-update-ts');
                    if (tsEl) tsEl.innerText = new Date().toLocaleTimeString('en-IN', { hour12: false });
                } catch (e) {
                    console.error('[WS] message parse error:', e);
                }
            };

            this.ws.onclose = () => {
                console.warn('[WS] closed');
                this.ws_connected = false;
                if (this._heartbeatInterval) clearInterval(this._heartbeatInterval);
                this._wsReconnect();
            };

            this.ws.onerror = (e) => {
                console.error('[WS] error:', e);
                this.ws_connected = false;
            };
        },

        _wsReconnect() {
            const delay = Math.min(1000 * Math.pow(2, this._wsRetryCount), 30000);
            this._wsRetryCount++;
            console.log(`[WS] reconnecting in ${delay}ms (attempt ${this._wsRetryCount})`);
            setTimeout(() => this._connectWebSocket(), delay);
        },

        selectSymbol(symbol) {
            if (symbol === this.active_symbol) return;
            this.active_symbol = symbol;
            _lastOverlaysHash = null;
            this._lastChartDataHash = null;
            this.syncActiveState();
        },

        syncActiveState() {
            if (!this.active_symbol || !this.bot_states[this.active_symbol]) return;
            const state = this.bot_states[this.active_symbol];

            // Account & risk
            if (state.equity !== undefined) this.equity = state.equity;
            if (state.balance !== undefined) this.balance = state.balance;
            if (state.pnl_today !== undefined) this.pnl_today = state.pnl_today;
            if (state.pnl_week !== undefined) this.pnl_week = state.pnl_week;
            if (state.pnl_total !== undefined) this.pnl_total = state.pnl_total;
            if (state.price !== undefined) this.current_price = state.price;
            if (state.session !== undefined) this.session = state.session;
            if (state.market_structure !== undefined) this.structure = state.market_structure;
            if (state.d1_bias !== undefined) this.d1_bias = state.d1_bias;
            if (state.h4_bias !== undefined) this.h4_bias = state.h4_bias;
            if (state.account_login !== undefined) this.account_login = state.account_login;
            if (state.account_server !== undefined) this.account_server = state.account_server;
            if (state.account_name !== undefined) this.account_name = state.account_name;
            if (state.trading !== undefined) this.bot_status = state.trading;

            // Trades & news
            if (state.trades !== undefined) this.trades = state.trades;
            if (state.closed_trades !== undefined) this.closed_trades = state.closed_trades;
            if (state.news_items !== undefined) this.news_items = state.news_items;
            if (state.news_time !== undefined) this.news.time = state.news_time;

            // Signal engine (merge)
            if (state.signal_engine !== undefined) {
                this.signal_engine = {
                    ...this.signal_engine,
                    ...state.signal_engine,
                    gates: { ...this.signal_engine.gates, ...(state.signal_engine.gates || {}) }
                };
                if (state.signal_engine.confidence_score !== undefined && this.signal_engine.confidence === undefined) {
                    this.signal_engine.confidence = state.signal_engine.confidence_score;
                }
            }

            // Health (webhook age, trader alive, etc.)
            if (state.webhook_age_seconds !== undefined) {
                this._health.webhook_age_seconds = state.webhook_age_seconds;
                this._health.trader_alive = state.webhook_age_seconds < 90;
                this._health.websocket_active = this.ws_connected;
            }

            // Chart data & overlays
            if (state.chart_data && Array.isArray(state.chart_data) && state.chart_data.length) {
                const lastIdx = state.chart_data.length - 1;
                const currentHash = `${state.chart_data.length}-${state.chart_data[lastIdx].time}-${state.chart_data[lastIdx].close}`;
                if (currentHash !== this._lastChartDataHash) {
                    this._lastChartDataHash = currentHash;
                    this.chart_data_raw = state.chart_data;
                    this._renderChart(state.chart_data);
                }
            }
            if (state.poi_overlays !== undefined) {
                this.poi_overlays = state.poi_overlays;
            }
            if (state.chart_objects !== undefined) {
                this.chart_objects = state.chart_objects;
            }
            this._drawChartOverlays();

            // calculate daily %
            if (this.balance > 0) this.pnl_today_pct = (this.pnl_today / this.balance) * 100;
        },

        // ------------------------------------------------------------------
        //  Chart rendering
        // ------------------------------------------------------------------
        _renderChart(rawData) {
            if (!_series || !Array.isArray(rawData) || rawData.length === 0) return;
            try {
                const mapped = rawData
                    .filter(c => c && c.time != null)
                    .map(c => ({
                        time: typeof c.time === 'number' ? c.time : Math.floor(new Date(c.time).getTime() / 1000),
                        open: parseFloat(c.open || c.o || 0),
                        high: parseFloat(c.high || c.h || 0),
                        low: parseFloat(c.low || c.l || 0),
                        close: parseFloat(c.close || c.c || 0),
                    }))
                    .filter(c => c.open > 0)
                    .sort((a,b) => a.time - b.time);
                if (mapped.length) {
                    _series.setData(mapped);
                    _chart.timeScale().fitContent();
                }
            } catch(e) { console.error('[CHART] render error:', e); }
        },

        _drawChartOverlays() {
            if (!_chart) return;
            // compute hash to avoid unnecessary redraws
            const hash = JSON.stringify({ overlays: this.poi_overlays, objects: this.chart_objects });
            if (hash === _lastOverlaysHash) return;
            _lastOverlaysHash = hash;
            _drawOverlays(this.poi_overlays, this.chart_objects);
            // persist for resize
            window._lastOverlaysData = this.poi_overlays;
            window._lastChartObjects = this.chart_objects;
        },

        // ------------------------------------------------------------------
        //  Bot control & timeframe switching
        // ------------------------------------------------------------------
        toggleBot(statusState) {
            this.bot_status = statusState;
            const sym = this.active_symbol || 'XAUUSD';
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ action: 'toggle_bot', status: statusState ? 'ON' : 'OFF', symbol: sym }));
            }
            fetch(`${statusState ? '/bot/resume' : '/bot/pause'}?symbol=${encodeURIComponent(sym)}`, { method: 'POST' }).catch(e => console.error(e));
        },

        changeTimeframe(tf) {
            this.current_tf = tf;
            const sym = this.active_symbol || 'XAUUSD';
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ action: 'change_tf', tf, symbol: sym }));
            }
        },

        // ------------------------------------------------------------------
        //  Computed helpers for Alpine templates
        // ------------------------------------------------------------------
        fmt(v) {
            return '$' + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        },
        fmt_price(v) {
            if (!v) return '$--';
            return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        },
        getPassedGatesCount() {
            try {
                return Object.values(this.signal_engine.gates || {}).filter(g => {
                    if (typeof g === 'boolean') return g;
                    if (typeof g === 'object' && g !== null) return g.passed === true;
                    return false;
                }).length;
            } catch(e) { return 0; }
        },
        getGatesArray() {
            try {
                return Object.entries(this.signal_engine.gates || {}).map(([key, value]) => ({
                    name: GATE_NAMES[key] || key,
                    passed: typeof value === 'boolean' ? value : (value?.passed === true),
                }));
            } catch(e) { return []; }
        },
        getFilteredNews() {
            if (this.news_filter === 'ALL') return this.news_items;
            return this.news_items.filter(item => item.impact === this.news_filter);
        },
        getOpenTotalPnl() {
            return this.trades.reduce((sum, t) => sum + Number(t.pnl || 0), 0);
        },
        getClosedTotalPnl() {
            return this.closed_trades.reduce((sum, ct) => sum + Number(ct.pnl || 0), 0);
        }
    };
}