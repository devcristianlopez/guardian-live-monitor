class GuardianMonitor {
    constructor() {
        this.ws = null;
        this.totalAlerts = 0;
        this.reconnectAttempts = 0;
        this.maxReconnectDelay = 30000;
        this.wsUrl = `ws://${window.location.hostname}:8000/ws/events`;

        this.init();
    }

    init() {
        this.connectWebSocket();
    }

    // ------------------------------------------------------------------
    // WebSocket
    // ------------------------------------------------------------------

    connectWebSocket() {
        if (this.ws) this.ws.close();
        this.updateConnectionStatus('connecting');

        try {
            this.ws = new WebSocket(this.wsUrl);
        } catch (e) {
            console.error('WS error:', e);
            this.scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WS connected');
            this.reconnectAttempts = 0;
            this.updateConnectionStatus('online');
        };

        this.ws.onmessage = (event) => {
            try {
                this.handleEvent(JSON.parse(event.data));
            } catch (e) {
                console.error('Parse error:', e);
            }
        };

        this.ws.onclose = () => {
            console.log('WS disconnected');
            this.updateConnectionStatus('offline');
            this.scheduleReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WS error:', error);
            this.ws.close();
        };
    }

    scheduleReconnect() {
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
        this.reconnectAttempts++;
        setTimeout(() => this.connectWebSocket(), delay);
    }

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    handleEvent(data) {
        this.totalAlerts++;
        document.getElementById('total-alerts').textContent = this.totalAlerts;

        const camLabel = document.getElementById('cam-label');
        if (camLabel && data.camera_id) camLabel.textContent = data.camera_id;

        this.flashOverlay(data.severity);

        const sev = document.getElementById('last-severity');
        const colors = { low: 'text-green-400', medium: 'text-yellow-400', high: 'text-red-400' };
        sev.textContent = data.severity.toUpperCase();
        sev.className = `text-3xl font-bold ${colors[data.severity] || 'text-gray-400'}`;

        const time = data.timestamp
            ? new Date(data.timestamp + 'Z').toLocaleTimeString()
            : new Date().toLocaleTimeString();
        document.getElementById('last-event-time').textContent = time;

        const empty = document.getElementById('empty-feed');
        if (empty) empty.remove();

        const feed = document.getElementById('event-feed');
        const card = document.createElement('div');
        card.className = `event-card bg-gray-900 rounded-lg p-4 border border-gray-700 severity-${data.severity}`;

        const badge = {
            low: 'bg-green-900 text-green-300',
            medium: 'bg-yellow-900 text-yellow-300',
            high: 'bg-red-900 text-red-300',
        };
        const pct = Math.round((data.confidence || 0) * 100);

        card.innerHTML = `
            <div class="flex justify-between items-start mb-3">
                <div>
                    <span class="text-sm font-mono text-gray-400">${data.camera_id || 'N/A'}</span>
                    <span class="mx-2 text-gray-600">|</span>
                    <span class="text-sm">${data.event_type || 'motion_detected'}</span>
                </div>
                <span class="px-2 py-0.5 rounded text-xs font-medium ${badge[data.severity] || 'bg-gray-700 text-gray-300'}">${(data.severity || '?').toUpperCase()}</span>
            </div>
            <div class="mb-2">
                <div class="flex justify-between text-xs text-gray-400 mb-1"><span>Confianza</span><span>${pct}%</span></div>
                <div class="w-full bg-gray-700 rounded-full h-2"><div class="confidence-bar bg-blue-500 h-2 rounded-full" style="width:${pct}%"></div></div>
            </div>
            <div class="text-xs text-gray-500 font-mono">${time}</div>`;

        feed.insertBefore(card, feed.firstChild);
        while (feed.children.length > 100) feed.removeChild(feed.lastChild);
    }

    flashOverlay(severity) {
        const overlay = document.getElementById('alert-overlay');
        if (!overlay) return;
        overlay.className = severity === 'high' ? 'flash' : `flash-${severity}`;
        setTimeout(() => { overlay.className = ''; }, 400);
    }

    updateConnectionStatus(status) {
        const indicator = document.getElementById('status-indicator');
        const text = document.getElementById('connection-status');
        switch (status) {
            case 'online':
                indicator.className = 'w-3 h-3 rounded-full bg-green-500 shadow-lg shadow-green-500/50';
                text.textContent = '🟢 Online';
                text.className = 'text-sm font-medium text-green-400';
                break;
            case 'offline':
                indicator.className = 'w-3 h-3 rounded-full bg-red-500';
                text.textContent = '🔴 Offline';
                text.className = 'text-sm font-medium text-red-400';
                break;
            case 'connecting':
                indicator.className = 'w-3 h-3 rounded-full bg-yellow-500 animate-pulse';
                text.textContent = '🟡 Conectando...';
                text.className = 'text-sm font-medium text-yellow-400';
                break;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new GuardianMonitor();
});
