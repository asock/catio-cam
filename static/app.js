// WebSocket connection for real-time updates
let ws = null;
let pingInterval = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

    ws.onopen = () => {
        console.log('WebSocket connected');
        reconnectDelay = 1000;
        pingInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type: 'ping'}));
            }
        }, 30000);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'pong') {
                updateLiveStats();
            } else if (data.type === 'stream_approved' || data.type === 'featured_changed') {
                if (window.location.pathname === '/' || window.location.pathname === '/admin') {
                    location.reload();
                }
            }
        } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
        }
    };

    ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting in ' + (reconnectDelay / 1000) + 's...');
        if (pingInterval) {
            clearInterval(pingInterval);
            pingInterval = null;
        }
        setTimeout(connectWebSocket, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        ws.close();
    };
}

async function updateLiveStats() {
    try {
        const response = await fetch('/api/stats');
        const stats = await response.json();

        const statsEl = document.getElementById('live-stats');
        if (statsEl) {
            statsEl.textContent = `${stats.approved_streams} streams \u2022 ${stats.users} users \u2022 ${stats.total_viewers} viewers \u2022 ${stats.active_connections} live`;
        }
    } catch (error) {
        console.error('Failed to fetch stats:', error);
    }
}

function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content');
    const input = document.querySelector('input[name="csrf_token"]');
    if (input) return input.value;
    return '';
}

async function toggleFavorite(streamId) {
    try {
        const response = await fetch(`/stream/${streamId}/favorite`, {
            method: 'POST',
            headers: {
                'X-CSRF-Token': getCsrfToken()
            }
        });

        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }

        const result = await response.json();
        if (result.status === 'success') {
            location.reload();
        }
    } catch (error) {
        console.error('Failed to toggle favorite:', error);
        alert('Please login to favorite streams');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    updateLiveStats();

    // Form double-submit protection
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function() {
            const btn = form.querySelector('button[type="submit"]');
            if (btn && !btn.disabled) {
                btn.disabled = true;
                btn.dataset.originalText = btn.textContent;
                btn.textContent = btn.textContent.trim() + '...';
            }
        });
    });
});
