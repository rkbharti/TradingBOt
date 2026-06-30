const fs = require('fs');
const https = require('http');

https.get('http://68.233.99.145:8001/api/state?symbol=XAGUSD', (res) => {
    let data = '';
    res.on('data', chunk => data += chunk);
    res.on('end', () => {
        const state = JSON.parse(data);
        const rawData = state.chart_data || [];
        console.log(`Raw data length: ${rawData.length}`);
        
        const mapped = rawData
            .filter(c => c && c.time != null)
            .map(c => {
                let t = c.time;
                if (typeof t === 'string') {
                    if (/^\d+$/.test(t)) {
                        t = parseInt(t);
                    } else {
                        t = Math.floor(new Date(t).getTime() / 1000);
                    }
                }
                if (typeof t === 'number' && t > 5000000000) {
                    t = Math.floor(t / 1000);
                }
                return {
                    time: t,
                    open: parseFloat(c.open || c.o || 0),
                    high: parseFloat(c.high || c.h || 0),
                    low: parseFloat(c.low || c.l || 0),
                    close: parseFloat(c.close || c.c || 0),
                };
            })
            .filter(c => c.open > 0)
            .sort((a,b) => a.time - b.time);
            
        console.log(`Mapped data length: ${mapped.length}`);
        if (mapped.length > 0) {
            console.log(`First candle:`, mapped[0]);
            console.log(`Last candle:`, mapped[mapped.length - 1]);
        }
        
        // check for duplicates
        let duplicates = 0;
        for (let i = 1; i < mapped.length; i++) {
            if (mapped[i].time === mapped[i-1].time) duplicates++;
        }
        console.log(`Duplicates: ${duplicates}`);
    });
});
