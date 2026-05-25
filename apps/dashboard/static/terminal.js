/*
 * GUARDEER OS — terminal.js [PRODUCTION READY]
 * Handles WebSocket, chart, bot control, and palettes.
 */

function updateClocks() {
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
}
setInterval(updateClocks, 1000);
updateClocks();

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
    'stranger-things',   // default
    'breaking-bad',
    'dark',
    'demon-slayer',
    'pirates'
];

function _cssVar(name, fallback = '') {
    const value = getComputedStyle(document.body).getPropertyValue(name).trim();
    return value || fallback;
}

function _getChartTheme(themeMode) {
    const isDark = themeMode !== 'light';
    return {
        bg: isDark ? _cssVar('--paper', '#1a0f0a') : _cssVar('--paper', '#fefaf2'),
        text: isDark ? _cssVar('--ink', '#e6d5b8') : _cssVar('--ink', '#2c2825'),
        grid: isDark ? _cssVar('--border-soft', 'rgba(230,213,184,0.2)') : _cssVar('--border-soft', 'rgba(44,40,37,0.2)'),
        border: isDark ? _cssVar('--border-strong', 'rgba(230,213,184,0.5)') : _cssVar('--border-strong', 'rgba(44,40,37,0.6)'),
        crosshair: isDark ? _cssVar('--accent', '#b53b2a') : _cssVar('--accent', '#3b3530'),
        up: _cssVar('--accent', '#b53b2a'),
        down: _cssVar('--danger', '#c0392b'),
    };
}

let _chart = null;
let _series = null;
let _chartInitialized = false;
let _overlaySeries = [];

function _clearOverlays() {
    _overlaySeries.forEach(s => {
        try { _chart.removeSeries(s); } catch(e) {}
    });
    _overlaySeries = [];
}

function _drawOverlays(overlaysData, chartObjects) {
    if (!_chart) return;
    _clearOverlays();
    function addLine(price, color, width = 1, style = 2, text = '') {
        const lineSeries = _chart.addLineSeries({
            color: color,
            lineWidth: width,
            lineStyle: style,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        lineSeries.setData([
            { time: _chart.timeScale().getVisibleLogicalRange()?.from || 0, value: price },
            { time: _chart.timeScale().getVisibleLogicalRange()?.to || 100, value: price }
        ]);
        if (text) {
            const marker = _chart.addPriceMarker({
                price: price,
                text: text,
                color: color,
                labelTextColor: 'white',
                fontFamily: 'Courier Prime',
                fontSize: 10,
            });
            _overlaySeries.push(marker);
        }
        _overlaySeries.push(lineSeries);
    }
    function addMarker(price, text, color) {
        const marker = _chart.addPriceMarker({
            price: price,
            text: text,
            color: color,
            labelTextColor: 'white',
            fontFamily: 'Courier Prime',
            fontSize: 10,
        });
        _overlaySeries.push(marker);
    }
    if (Array.isArray(overlaysData)) {
        overlaysData.forEach(poi => {
            if (poi.type === 'pdh') addLine(poi.price, '#ffaa44', 1, 2, 'PDH');
            else if (poi.type === 'pdl') addLine(poi.price, '#88aaff', 1, 2, 'PDL');
            else if (poi.type === 'fvg' && poi.top && poi.bottom) {
                addLine(poi.top, '#ff66aa', 1, 2, 'FVG TOP');
                addLine(poi.bottom, '#ff66aa', 1, 2, 'FVG BOT');
            }
            else if (poi.type === 'order_block' && poi.high && poi.low) {
                addLine(poi.high, '#66ffcc', 1, 2, 'OB HIGH');
                addLine(poi.low, '#66ffcc', 1, 2, 'OB LOW');
            }
            else if (poi.type === 'choch') addMarker(poi.price, 'CHoCH', '#ffaa88');
            else if (poi.type === 'liquidity_sweep') addMarker(poi.price, 'LIQ SWEEP', '#ff8855');
            else if (poi.price) addMarker(poi.price, poi.label || 'POI', '#aaaaff');
        });
    }
    if (chartObjects && typeof chartObjects === 'object') {
        for (const [key, value] of Object.entries(chartObjects)) {
            if (key === 'pdh' && value) addLine(value, '#ffaa44', 1, 2, 'PDH');
            if (key === 'pdl' && value) addLine(value, '#88aaff', 1, 2, 'PDL');
            if (key === 'order_block_high' && value) addLine(value, '#66ffcc', 1, 2, 'OB HIGH');
            if (key === 'order_block_low' && value) addLine(value, '#66ffcc', 1, 2, 'OB LOW');
            if (key === 'fvg_top' && value) addLine(value, '#ff66aa', 1, 2, 'FVG TOP');
            if (key === 'fvg_bottom' && value) addLine(value, '#ff66aa', 1, 2, 'FVG BOT');
            if (key === 'choch_level' && value) addMarker(value, 'CHoCH', '#ffaa88');
            if (key === 'liquidity_sweep_level' && value) addMarker(value, 'LIQ SWEEP', '#ff8855');
        }
    }
}

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
        upColor: t.up, downColor: t.down, borderVisible: false, wickUpColor: t.up, wickDownColor: t.down,
    });
    const ro = new ResizeObserver(entries => {
        if (!entries.length || !entries[0].contentRect) return;
        _chart.resize(entries[0].contentRect.width, entries[0].contentRect.height);
        if (window._lastOverlaysData || window._lastChartObjects) {
            _drawOverlays(window._lastOverlaysData, window._lastChartObjects);
        }
    });
    ro.observe(container);
}

function DS() {
    return {
        ws: null,
        ws_connected: false,
        bot_status: true,
        current_tf: 'M15',
        theme_mode: localStorage.getItem('gos_theme') || 'dark',
        palette: localStorage.getItem('gos_palette') || 'stranger-things',
        available_palettes: GOS_PALETTES,
        webhook_age_seconds: 0,
        account_login: '--', account_server: '--', account_name: 'REAL-01',
        equity: 0, balance: 0, pnl_today: 0, pnl_today_pct: 0, pnl_week: 0, pnl_total: 0, current_price: 0,
        structure: '--', session: '--', d1_bias: '--', h4_bias: '--',
        trades: [], closed_trades: [], news_items: [], news_filter: 'HIGH',
        signal_engine: { action: 'NO_TRADE', confidence: 0, direction: '--', reason: 'Parsing stream data...', reason_code: 'SCANNING', entry_price: null, sl_price: null, tp_price: null, gates: {} },
        news: { time: '--' },
        _health: { mt5_connected: 'down', websocket_active: false, trader_alive: false, strategy_engine: false, data_feed: false, vps_uptime_seconds: 0, webhook_age_seconds: 999 },
        chart_data_raw: [], poi_overlays: [], chart_objects: {}, _wsRetryCount: 0,

        init() {
            this._applyThemeMode();
            this._applyPalette();
            this.$nextTick(() => {
                _initChart(this.theme_mode);
                _applyChartTheme(this.theme_mode);
            });
            this._connectWS();
            this._healthPoll();
        },

        _applyThemeMode() {
            document.body.classList.toggle('light-theme', this.theme_mode === 'light');
        },

        _applyPalette() {
            let paletteName = this.palette;
            if (!GOS_PALETTES.includes(paletteName) && paletteName !== 'custom') paletteName = 'stranger-things';
            document.body.setAttribute('data-palette', paletteName);
        },

        setPalette(paletteName) {
            if (paletteName === 'custom') {
                // handled by colour picker separately
                return;
            }
            if (!GOS_PALETTES.includes(paletteName)) paletteName = 'stranger-things';
            this.palette = paletteName;
            localStorage.setItem('gos_palette', paletteName);
            this._applyPalette();
            _applyChartTheme(this.theme_mode);
        },

        cyclePalette() {
            const idx = GOS_PALETTES.indexOf(this.palette);
            const next = idx === -1 ? 0 : (idx + 1) % GOS_PALETTES.length;
            this.setPalette(GOS_PALETTES[next]);
        },

        paletteLabel(name) {
            const map = {
                'stranger-things': 'ST',
                'breaking-bad': 'BB',
                'dark': 'DK',
                'demon-slayer': 'DS',
                'pirates': 'PR',
            };
            return map[name] || 'ST';
        },

        fmt(v) { return '$' + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
        fmt_price(v) { if (!v) return '$--'; return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },

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
        getOpenTotalPnl() { return this.trades.reduce((sum, t) => sum + Number(t.pnl || 0), 0); },
        getClosedTotalPnl() { return this.closed_trades.reduce((sum, ct) => sum + Number(ct.pnl || 0), 0); },

        toggleTheme() {
            this.theme_mode = this.theme_mode === 'dark' ? 'light' : 'dark';
            localStorage.setItem('gos_theme', this.theme_mode);
            this._applyThemeMode();
            _applyChartTheme(this.theme_mode);
        },

        toggleBot(statusState) {
            this.bot_status = statusState;
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ action: 'toggle_bot', status: statusState ? 'ON' : 'OFF' }));
            }
            const ep = statusState ? '/bot/resume' : '/bot/pause';
            fetch(ep, { method: 'POST' }).catch(e => console.error('[GOS] toggleBot HTTP err:', e));
        },

        changeTimeframe(tf) {
            this.current_tf = tf;
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ action: 'change_tf', tf }));
            }
            if (_series && this.chart_data_raw) this._renderChart(this.chart_data_raw);
        },

        _renderChart(rawData) {
            if (!_series || !Array.isArray(rawData) || rawData.length === 0) return;
            try {
                const mapped = rawData.filter(c => c && c.time != null).map(c => ({
                    time: typeof c.time === 'number' ? c.time : Math.floor(new Date(c.time).getTime() / 1000),
                    open: parseFloat(c.open || c.o || 0),
                    high: parseFloat(c.high || c.h || 0),
                    low: parseFloat(c.low || c.l || 0),
                    close: parseFloat(c.close || c.c || 0),
                })).filter(c => c.open > 0).sort((a,b) => a.time - b.time);
                if (mapped.length > 0) {
                    _series.setData(mapped);
                    _chart.timeScale().fitContent();
                }
            } catch(e) { console.error('[GOS] Chart render error:', e); }
        },

        _drawChartOverlays() {
            if (!_chart) return;
            _drawOverlays(this.poi_overlays, this.chart_objects);
            window._lastOverlaysData = this.poi_overlays;
            window._lastChartObjects = this.chart_objects;
        },

        _connectWS() {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${proto}//${location.host}/ws`;
            try {
                this.ws = new WebSocket(url);
            } catch(e) {
                console.error('[GOS] WS create failed:', e);
                this._wsReconnect();
                return;
            }
            this.ws.onopen = () => {
                this.ws_connected = true;
                this._wsRetryCount = 0;
                console.log('[GOS] WebSocket connected');
                this.ws.send(JSON.stringify({ action: 'ping' }));
            };
            this.ws.onmessage = (evt) => {
                try {
                    const data = JSON.parse(evt.data);
                    if (data.action === 'pong') return;
                    if (data.equity != null) this.equity = data.equity;
                    if (data.balance != null) this.balance = data.balance;
                    if (data.pnl_today != null) this.pnl_today = data.pnl_today;
                    if (data.pnl_week != null) this.pnl_week = data.pnl_week;
                    if (data.pnl_total != null) this.pnl_total = data.pnl_total;
                    if (data.price != null) this.current_price = data.price;
                    if (data.session != null) this.session = data.session;
                    if (data.market_structure != null) this.structure = data.market_structure;
                    if (data.d1_bias != null) this.d1_bias = data.d1_bias;
                    if (data.h4_bias != null) this.h4_bias = data.h4_bias;
                    if (data.account_login != null) this.account_login = data.account_login;
                    if (data.account_server != null) this.account_server = data.account_server;
                    if (data.account_name != null) this.account_name = data.account_name;
                    if (data.trades != null) this.trades = data.trades;
                    if (data.closed_trades != null) this.closed_trades = data.closed_trades;
                    if (data.trading != null) this.bot_status = data.trading;
                    if (data.webhook_age_seconds != null) this.webhook_age_seconds = data.webhook_age_seconds;
                    if (this.balance > 0) this.pnl_today_pct = (this.pnl_today / this.balance) * 100;
                    if (data.signal_engine != null) {
                        this.signal_engine = {
                            action: data.signal_engine.action || 'NO_TRADE',
                            confidence: data.signal_engine.confidence || 0,
                            direction: data.signal_engine.direction || '--',
                            reason: data.signal_engine.reason || '--',
                            reason_code: data.signal_engine.reason_code || null,
                            entry_price: data.signal_engine.entry_price || null,
                            sl_price: data.signal_engine.sl_price || null,
                            tp_price: data.signal_engine.tp_price || null,
                            gates: data.signal_engine.gates || {},
                        };
                    }
                    if (data.poi_overlays != null) this.poi_overlays = data.poi_overlays;
                    if (data.chart_objects != null) this.chart_objects = data.chart_objects;
                    if (data.chart_data && Array.isArray(data.chart_data)) {
                        this.chart_data_raw = data.chart_data;
                        this._renderChart(data.chart_data);
                        this._drawChartOverlays();
                    } else {
                        this._drawChartOverlays();
                    }
                    const el = document.getElementById('last-update-ts');
                    if (el) el.innerText = new Date().toLocaleTimeString('en-IN', { hour12: false });
                } catch(e) { console.error('[GOS] WS message parse error:', e); }
            };
            this.ws.onclose = () => {
                this.ws_connected = false;
                console.warn('[GOS] WebSocket closed');
                this._wsReconnect();
            };
            this.ws.onerror = (e) => {
                console.error('[GOS] WebSocket error:', e);
                this.ws_connected = false;
            };
        },

        _wsReconnect() {
            const delay = Math.min(1000 * Math.pow(2, this._wsRetryCount), 30000);
            this._wsRetryCount++;
            console.log(`[GOS] WS reconnect in ${delay}ms (attempt ${this._wsRetryCount})`);
            setTimeout(() => this._connectWS(), delay);
        },

        _healthPoll() {
            const poll = () => {
                fetch('/health').then(r => r.json()).then(h => {
                    this._health = h;
                    if (!this.ws_connected && h.mt5_connected === 'ok') {
                        fetch('/api/state').then(r => r.json()).then(state => {
                            if (state && state.equity != null) {
                                if (state.equity != null) this.equity = state.equity;
                                if (state.balance != null) this.balance = state.balance;
                                if (state.pnl_today != null) this.pnl_today = state.pnl_today;
                                if (state.price != null) this.current_price = state.price;
                                if (state.trades != null) this.trades = state.trades;
                                if (state.signal_engine) this.signal_engine = state.signal_engine;
                                if (state.poi_overlays) this.poi_overlays = state.poi_overlays;
                                if (state.chart_objects) this.chart_objects = state.chart_objects;
                                if (state.chart_data && Array.isArray(state.chart_data)) {
                                    this.chart_data_raw = state.chart_data;
                                    this._renderChart(state.chart_data);
                                }
                                this._drawChartOverlays();
                            }
                        }).catch(() => {});
                    }
                }).catch(() => {});
            };
            poll();
            setInterval(poll, 15000);
        },
    };
}