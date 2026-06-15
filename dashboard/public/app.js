class GuardianMonitor {
    constructor() {
        this.ws = null;
        this.totalAlerts = 0;
        this.reconnectAttempts = 0;
        this.maxReconnectDelay = 30000;
        this.wsUrl = `ws://${window.location.hostname}:8000/ws/events`;
        this.webcamStream = null;
        this.camStarted = false;

        this.init();
    }

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------

    async init() {
        this.connectWebSocket();
        this.setupButtons();
        await this.tryWebcam();
    }

    setupButtons() {
        const btn = document.getElementById('btn-start-cam');
        if (btn) {
            btn.addEventListener('click', () => {
                btn.classList.add('hidden');
                this.setCamStatus('🔄 Solicitando cámara...', 'text-yellow-400');
                this.tryWebcam();
            });
        }
    }

    // ------------------------------------------------------------------
    // Webcam via getUserMedia (primary)
    // ------------------------------------------------------------------

    async tryWebcam() {
        const video = document.getElementById('live-video');
        if (!video) return;

        // Check if getUserMedia is available
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            this.setCamStatus('❌ getUserMedia no disponible en este navegador', 'text-red-400');
            this.showStartButton();
            this.fallbackToMjpeg(video);
            return;
        }

        this.setCamStatus('📷 Solicitando permiso de cámara...', 'text-yellow-400');

        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    width: { ideal: 640 },
                    height: { ideal: 480 },
                },
                audio: false,
            });

            this.webcamStream = stream;
            video.srcObject = stream;

            try {
                await video.play();
                this.camStarted = true;
                this.setCamStatus('✅ Webcam conectada', 'text-green-400');
                console.log('[cam] getUserMedia OK');
            } catch (playErr) {
                console.warn('[cam] play() blocked, need user gesture:', playErr.message);
                this.setCamStatus('⚠️ Haz clic en "Iniciar Cámara" para activar el video', 'text-yellow-400');
                this.showStartButton();
                // Keep the stream but show the button
                this.camStarted = true;
            }
        } catch (err) {
            console.warn('[cam] getUserMedia failed:', err.message);
            this.setCamStatus(`⚠️ Webcam: ${err.message}`, 'text-red-400');
            this.showStartButton();
            this.fallbackToMjpeg(video);
        }
    }

    showStartButton() {
        const btn = document.getElementById('btn-start-cam');
        if (btn) btn.classList.remove('hidden');
    }

    setCamStatus(msg, colorClass = 'text-gray-500') {
        const el = document.getElementById('cam-status');
        if (el) {
            el.textContent = msg;
            el.className = `px-4 py-2 text-xs border-t border-gray-700 ${colorClass}`;
        }
        console.log('[cam]', msg);
    }

    // ------------------------------------------------------------------
    // MJPEG fallback (direct to detector port)
    // ------------------------------------------------------------------

    fallbackToMjpeg(video) {
        // Hide the video element
        video.style.display = 'none';

        const wrapper = document.getElementById('video-wrapper');
        if (!wrapper) return;

        // Check if we already added a fallback img
        if (document.getElementById('mjpeg-fallback')) return;

        const img = document.createElement('img');
        img.id = 'mjpeg-fallback';
        img.alt = 'MJPEG Stream';

        // Try direct detector port first, then nginx proxy
        const urls = [
            `http://${window.location.hostname}:8081/stream`,
            '/stream',
        ];

        let urlIdx = 0;

        const loadStream = () => {
            if (urlIdx >= urls.length) {
                this.setCamStatus('❌ No se pudo conectar al stream de video', 'text-red-400');
                return;
            }
            img.src = urls[urlIdx] + '?_=' + Date.now();
        };

        img.addEventListener('load', () => {
            this.setCamStatus('📺 Stream MJPEG conectado', 'text-green-400');
            console.log('[cam] MJPEG stream loaded from', urls[urlIdx]);
        });

        img.addEventListener('error', () => {
            console.warn('[cam] MJPEG failed from', urls[urlIdx]);
            urlIdx++;
            setTimeout(loadStream, 500);
        });

        // Insert before the overlay
        const overlay = document.getElementById('alert-overlay');
        wrapper.insertBefore(img, overlay);

        loadStream();
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
            console.error('WebSocket connection error:', e);
            this.scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.updateConnectionStatus('online');
        };

        this.ws.onmessage = (event) => {
            try {
                this.handleEvent(JSON.parse(event.data));
            } catch (e) {
                console.error('Error parsing event:', e);
            }
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.updateConnectionStatus('offline');
            this.scheduleReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.ws.close();
        };
    }

    scheduleReconnect() {
        const delay = Math.min(
            1000 * Math.pow(2, this.reconnectAttempts),
            this.maxReconnectDelay
        );
        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts + 1})`);
        this.reconnectAttempts++;
        setTimeout(() => this.connectWebSocket(), delay);
    }

    // ------------------------------------------------------------------
    // Event handling
    // ------------------------------------------------------------------

    handleEvent(data) {
        this.totalAlerts++;
        document.getElementById('total-alerts').textContent = this.totalAlerts;

        const camLabel = document.getElementById('cam-label');
        if (camLabel && data.camera_id) camLabel.textContent = data.camera_id;

        this.flashOverlay(data.severity);

        const severityEl = document.getElementById('last-severity');
        const severityColors = {
            low: 'text-green-400', medium: 'text-yellow-400', high: 'text-red-400',
        };
        severityEl.textContent = data.severity.toUpperCase();
        severityEl.className = `text-3xl font-bold ${severityColors[data.severity] || 'text-gray-400'}`;

        const time = data.timestamp
            ? new Date(data.timestamp + 'Z').toLocaleTimeString()
            : new Date().toLocaleTimeString();
        document.getElementById('last-event-time').textContent = time;

        const emptyFeed = document.getElementById('empty-feed');
        if (emptyFeed) emptyFeed.remove();

        const feed = document.getElementById('event-feed');
        const card = document.createElement('div');
        card.className = `event-card bg-gray-900 rounded-lg p-4 border border-gray-700 severity-${data.severity}`;

        const badgeColors = {
            low: 'bg-green-900 text-green-300',
            medium: 'bg-yellow-900 text-yellow-300',
            high: 'bg-red-900 text-red-300',
        };

        const pct = Math.round((data.confidence || 0) * 100);
        const badge = data.severity || 'unknown';

        card.innerHTML = `
            <div class="flex justify-between items-start mb-3">
                <div>
                    <span class="text-sm font-mono text-gray-400">${data.camera_id || 'N/A'}</span>
                    <span class="mx-2 text-gray-600">|</span>
                    <span class="text-sm">${data.event_type || 'motion_detected'}</span>
                </div>
                <span class="px-2 py-0.5 rounded text-xs font-medium ${badgeColors[badge]}">${badge.toUpperCase()}</span>
            </div>
            <div class="mb-2">
                <div class="flex justify-between text-xs text-gray-400 mb-1">
                    <span>Confianza</span>
                    <span>${pct}%</span>
                </div>
                <div class="w-full bg-gray-700 rounded-full h-2">
                    <div class="confidence-bar bg-blue-500 h-2 rounded-full" style="width:${pct}%"></div>
                </div>
            </div>
            <div class="text-xs text-gray-500 font-mono">${time}</div>
        `;

        feed.insertBefore(card, feed.firstChild);
        while (feed.children.length > 100) feed.removeChild(feed.lastChild);
    }

    flashOverlay(severity) {
        const overlay = document.getElementById('alert-overlay');
        if (!overlay) return;
        const cls = severity === 'high' ? 'flash' : `flash-${severity}`;
        overlay.className = cls;
        setTimeout(() => { overlay.className = ''; }, 400);
    }

    updateConnectionStatus(status) {
        const indicator = document.getElementById('status-indicator');
        const statusText = document.getElementById('connection-status');

        switch (status) {
            case 'online':
                indicator.className = 'w-3 h-3 rounded-full bg-green-500 shadow-lg shadow-green-500/50';
                statusText.textContent = '🟢 Online';
                statusText.className = 'text-sm font-medium text-green-400';
                break;
            case 'offline':
                indicator.className = 'w-3 h-3 rounded-full bg-red-500';
                statusText.textContent = '🔴 Offline / Reconectando';
                statusText.className = 'text-sm font-medium text-red-400';
                break;
            case 'connecting':
                indicator.className = 'w-3 h-3 rounded-full bg-yellow-500 animate-pulse';
                statusText.textContent = '🟡 Conectando...';
                statusText.className = 'text-sm font-medium text-yellow-400';
                break;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new GuardianMonitor();
});
