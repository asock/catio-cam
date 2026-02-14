// WebSocket connection for real-time updates
let ws = null;

function connectWebSocket() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);
    
    ws.onopen = () => {
        console.log('WebSocket connected');
        setInterval(() => {
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
        setTimeout(connectWebSocket, 3000);
    };
}

async function updateLiveStats() {
    try {
        const response = await fetch('/api/stats');
        const stats = await response.json();
        
        const viewersEl = document.getElementById('live-viewers');
        const streamsEl = document.getElementById('live-streams');
        
        if (viewersEl) viewersEl.textContent = stats.total_viewers;
        if (streamsEl) streamsEl.textContent = stats.approved_streams;
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
