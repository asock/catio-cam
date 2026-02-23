/* ═══════════════════════════════════════════════════════════════════════════
   hellsy.tube — Client-side JavaScript
   Upload, player, likes, WebSocket, interactions
   ═══════════════════════════════════════════════════════════════════════════ */

// ── WebSocket ─────────────────────────────────────────────────────────────
let ws = null;

function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
        console.log('[ws] connected');
        setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    };

    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'new_video' || msg.type === 'video_published') {
                // Subtle notification — don't force reload
                console.log('[ws] new content:', msg);
            }
        } catch (_) {}
    };

    ws.onclose = () => {
        console.log('[ws] disconnected');
        setTimeout(connectWS, 5000);
    };

    ws.onerror = () => ws.close();
}

// ── Stats ─────────────────────────────────────────────────────────────────
async function updateStats() {
    try {
        const res = await fetch('/api/stats');
        const s = await res.json();
        const el = document.getElementById('footer-stats');
        if (el) {
            el.textContent = `${s.videos} videos · ${s.users} users · ${fmtNum(s.views)} views · ${s.active} online`;
        }
    } catch (_) {}
}

function fmtNum(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
}

function fmtSize(bytes) {
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
    if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + ' MB';
    if (bytes >= 1e3) return (bytes / 1e3).toFixed(1) + ' KB';
    return bytes + ' B';
}

// ── Like Toggle ───────────────────────────────────────────────────────────
async function toggleLike(videoId) {
    try {
        const res = await fetch(`/api/like/${videoId}`, { method: 'POST' });
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await res.json();
        const btn = document.getElementById('likeBtn');
        const count = document.getElementById('likeCount');
        if (btn) btn.classList.toggle('liked', data.action === 'liked');
        if (count) count.textContent = data.likes;
    } catch (e) {
        console.error('Like failed:', e);
    }
}

// ── Upload System ─────────────────────────────────────────────────────────
let selectedFile = null;

function initUpload() {
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    if (!dropzone || !fileInput) return;

    // Drag events
    ['dragenter', 'dragover'].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });
    });

    ['dragleave', 'drop'].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
        });
    });

    dropzone.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files.length > 0) handleFile(files[0]);
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) handleFile(fileInput.files[0]);
    });
}

function handleFile(file) {
    const allowed = ['video/mp4', 'video/webm', 'video/ogg', 'video/quicktime',
                     'video/x-matroska', 'video/x-msvideo'];
    if (!file.type.startsWith('video/') && !allowed.includes(file.type)) {
        alert('Please select a video file (MP4, WebM, OGG, MOV)');
        return;
    }

    const maxSize = 500 * 1024 * 1024;
    if (file.size > maxSize) {
        alert('File too large. Maximum size is 500MB.');
        return;
    }

    selectedFile = file;

    // Show form
    document.getElementById('dropzoneContent').style.display = 'none';
    document.getElementById('uploadForm').style.display = 'grid';
    document.getElementById('uploadActions').style.display = 'flex';

    // Preview
    const preview = document.getElementById('previewVideo');
    if (preview) {
        preview.src = URL.createObjectURL(file);
        preview.load();
    }

    // Auto-fill title from filename
    const titleInput = document.getElementById('title');
    if (titleInput && !titleInput.value) {
        const name = file.name.replace(/\.[^/.]+$/, '').replace(/[_-]/g, ' ');
        titleInput.value = name.charAt(0).toUpperCase() + name.slice(1);
    }
}

async function submitUpload() {
    if (!selectedFile) return;

    const title = document.getElementById('title').value.trim();
    if (!title) {
        alert('Please enter a title');
        document.getElementById('title').focus();
        return;
    }

    const description = document.getElementById('description').value.trim();
    const tags = document.getElementById('tags').value.trim();

    // Show progress
    document.getElementById('uploadForm').style.display = 'none';
    document.getElementById('uploadActions').style.display = 'none';
    document.getElementById('dropzone').style.display = 'block';
    document.getElementById('dropzoneContent').style.display = 'none';
    document.getElementById('uploadProgress').style.display = 'block';
    document.getElementById('progressFilename').textContent = selectedFile.name;

    const formData = new FormData();
    formData.append('video', selectedFile);
    formData.append('title', title);
    formData.append('description', description);
    formData.append('tags', tags);

    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            document.getElementById('progressFill').style.width = pct + '%';
            document.getElementById('progressPercent').textContent = pct + '%';
            document.getElementById('progressSize').textContent =
                `${fmtSize(e.loaded)} / ${fmtSize(e.total)}`;
        }
    });

    xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
            try {
                const data = JSON.parse(xhr.responseText);
                // Show success
                document.getElementById('dropzone').style.display = 'none';
                document.getElementById('uploadSuccess').style.display = 'block';
                document.getElementById('watchLink').href = '/watch/' + data.video_id;
            } catch (_) {
                alert('Upload completed but response was unexpected');
            }
        } else {
            alert('Upload failed: ' + xhr.statusText);
            resetUpload();
        }
    });

    xhr.addEventListener('error', () => {
        alert('Upload failed. Please try again.');
        resetUpload();
    });

    const submitBtn = document.getElementById('submitBtn');
    if (submitBtn) submitBtn.disabled = true;

    xhr.open('POST', '/api/upload');
    xhr.send(formData);
}

function resetUpload() {
    selectedFile = null;
    document.getElementById('dropzone').style.display = 'block';
    document.getElementById('dropzoneContent').style.display = 'block';
    document.getElementById('uploadProgress').style.display = 'none';
    document.getElementById('uploadForm').style.display = 'none';
    document.getElementById('uploadActions').style.display = 'none';
    document.getElementById('uploadSuccess').style.display = 'none';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressPercent').textContent = '0%';
    document.getElementById('progressSize').textContent = '';

    const fileInput = document.getElementById('fileInput');
    if (fileInput) fileInput.value = '';

    const preview = document.getElementById('previewVideo');
    if (preview) {
        URL.revokeObjectURL(preview.src);
        preview.src = '';
    }

    const submitBtn = document.getElementById('submitBtn');
    if (submitBtn) submitBtn.disabled = false;
}

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    connectWS();
    updateStats();
    setInterval(updateStats, 30000);
    initUpload();
});
