// WebSocket connection for real-time updates
let ws = null;
let pingInterval = null;

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

    ws.onopen = () => {
        console.log('WebSocket connected');
        pingInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type: 'ping'}));
            }
        }, 30000);
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'pong') {
            updateLiveStats();
        } else if (data.type === 'stream_approved' || data.type === 'featured_changed') {
            if (window.location.pathname === '/' || window.location.pathname === '/admin') {
                location.reload();
            }
        }
    };

    ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting...');
        if (pingInterval) {
            clearInterval(pingInterval);
            pingInterval = null;
        }
        setTimeout(connectWebSocket, 3000);
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

async function toggleFavorite(streamId) {
    try {
        const response = await fetch(`/stream/${streamId}/favorite`, {
            method: 'POST'
        });
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
});
