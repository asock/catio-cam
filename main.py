"""
Catio.cam - Full-featured livestreaming hub
OAuth + Admin Panel + Tags + Favorites + Live Stats
"""
from fastapi import FastAPI, Request, Form, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from typing import Optional, List
from contextlib import contextmanager
from urllib.parse import urlparse
import sqlite3
import secrets
import os
from datetime import datetime
import json
import re

app = FastAPI(title="Catio.cam")

# Session secret
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# OAuth configuration
oauth = OAuth()

oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID', 'YOUR_GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET', 'YOUR_GOOGLE_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

oauth.register(
    name='github',
    client_id=os.getenv('GITHUB_CLIENT_ID', 'YOUR_GITHUB_CLIENT_ID'),
    client_secret=os.getenv('GITHUB_CLIENT_SECRET', 'YOUR_GITHUB_SECRET'),
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'}
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_PATH = "catio.db"

ALLOWED_PLATFORMS = {"twitch", "youtube"}
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 500
MAX_LOCATION_LENGTH = 100
MAX_TAGS_LENGTH = 200
MAX_COMMENT_LENGTH = 1000

ALLOWED_URL_PATTERNS = {
    "twitch": re.compile(r'^https?://(www\.)?twitch\.tv/[\w-]+$'),
    "youtube": re.compile(r'^https?://(www\.)?(youtube\.com|youtu\.be)/[\w./?&=-]+$'),
}

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections[:]:
            try:
                await connection.send_json(message)
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                self.active_connections.remove(connection)

manager = ConnectionManager()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()

        # Users table
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            avatar_url TEXT,
            provider TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # Streams table
        c.execute("""CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            platform TEXT NOT NULL,
            channel_url TEXT NOT NULL,
            thumbnail_url TEXT,
            location TEXT,
            tags TEXT,
            status TEXT DEFAULT 'pending',
            is_featured INTEGER DEFAULT 0,
            viewers INTEGER DEFAULT 0,
            total_views INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )""")

        # Favorites table
        c.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stream_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, stream_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (stream_id) REFERENCES streams (id)
        )""")

        # Comments table
        c.execute("""CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stream_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (stream_id) REFERENCES streams (id)
        )""")

        conn.commit()

        # Seed admin user and featured stream
        c.execute('SELECT COUNT(*) FROM users')
        if c.fetchone()[0] == 0:
            c.execute("""INSERT INTO users (email, name, provider, provider_id, is_admin)
                         VALUES (?, ?, ?, ?, ?)""",
                      ('admin@catio.cam', 'Admin', 'system', 'admin', 1))
            user_id = c.lastrowid

            c.execute("""INSERT INTO streams (user_id, title, description, platform, channel_url,
                         location, tags, status, is_featured, viewers)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (user_id, 'Featured Catio Stream', 'Watch cats in their outdoor paradise!',
                       'twitch', 'https://www.twitch.tv/twitchdev', 'San Francisco, CA',
                       'outdoor,sunny,playful', 'approved', 1, 42))

            conn.commit()

@app.on_event("startup")
async def startup():
    init_db()

# --- CSRF Protection ---

def generate_csrf_token(request: Request) -> str:
    if 'csrf_token' not in request.session:
        request.session['csrf_token'] = secrets.token_urlsafe(32)
    return request.session['csrf_token']

def validate_csrf_token(request: Request, token: str):
    session_token = request.session.get('csrf_token')
    if not session_token or not secrets.compare_digest(session_token, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

# --- Validation helpers ---

def validate_url_for_platform(platform: str, url: str) -> bool:
    if platform not in ALLOWED_URL_PATTERNS:
        return False
    return bool(ALLOWED_URL_PATTERNS[platform].match(url))

def get_current_user(request: Request) -> Optional[dict]:
    user_id = request.session.get('user_id')
    if not user_id:
        return None

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()

    return dict(user) if user else None

def require_auth(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

def require_admin(request: Request):
    user = get_current_user(request)
    if not user or not user.get('is_admin'):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def get_or_create_user(email: str, name: str, avatar_url: str, provider: str, provider_id: str) -> int:
    with get_db() as conn:
        c = conn.cursor()

        c.execute('SELECT id FROM users WHERE email = ? AND provider = ?', (email, provider))
        user = c.fetchone()

        if user:
            user_id = user[0]
            c.execute('UPDATE users SET name = ?, avatar_url = ? WHERE id = ?',
                      (name, avatar_url, user_id))
            conn.commit()
        else:
            c.execute("""INSERT INTO users (email, name, avatar_url, provider, provider_id)
                         VALUES (?, ?, ?, ?, ?)""",
                      (email, name, avatar_url, provider, provider_id))
            user_id = c.lastrowid
            conn.commit()

    return user_id

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, tag: Optional[str] = None, search: Optional[str] = None):
    with get_db() as conn:
        c = conn.cursor()

        # Featured stream
        c.execute("""SELECT s.*, u.name as owner_name, u.avatar_url as owner_avatar
                     FROM streams s
                     JOIN users u ON s.user_id = u.id
                     WHERE s.is_featured = 1 AND s.status = 'approved'
                     LIMIT 1""")
        featured = c.fetchone()

        # Community streams with filters
        query = """SELECT s.*, u.name as owner_name, u.avatar_url as owner_avatar
                   FROM streams s
                   JOIN users u ON s.user_id = u.id
                   WHERE s.is_featured = 0 AND s.status = 'approved'"""

        params = []
        if tag:
            # Match exact tags within comma-separated list
            query += """ AND (',' || s.tags || ',' LIKE ?)"""
            params.append(f"%,{tag},%")
        if search:
            query += " AND (s.title LIKE ? OR s.description LIKE ? OR s.location LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        query += " ORDER BY s.viewers DESC, s.created_at DESC"

        c.execute(query, params)
        community = c.fetchall()

        # Get all unique tags
        c.execute("SELECT DISTINCT tags FROM streams WHERE status = 'approved' AND tags IS NOT NULL")
        all_tags = set()
        for row in c.fetchall():
            if row['tags']:
                for t in row['tags'].split(','):
                    stripped = t.strip()
                    if stripped:
                        all_tags.add(stripped)

    user = get_current_user(request)

    return templates.TemplateResponse("home.html", {
        "request": request,
        "user": user,
        "featured_stream": dict(featured) if featured else None,
        "community_streams": [dict(s) for s in community],
        "all_tags": sorted(all_tags),
        "current_tag": tag,
        "search_query": search
    })

@app.get("/stream/{stream_id}", response_class=HTMLResponse)
async def view_stream(request: Request, stream_id: int):
    with get_db() as conn:
        c = conn.cursor()

        # Get stream
        c.execute("""SELECT s.*, u.name as owner_name, u.avatar_url as owner_avatar, u.id as owner_id
                     FROM streams s
                     JOIN users u ON s.user_id = u.id
                     WHERE s.id = ? AND s.status = 'approved'""", (stream_id,))
        stream = c.fetchone()

        if not stream:
            raise HTTPException(status_code=404, detail="Stream not found")

        stream = dict(stream)

        # Session-based view count deduplication
        viewed_streams = request.session.get('viewed_streams', [])
        if stream_id not in viewed_streams:
            c.execute('UPDATE streams SET total_views = total_views + 1 WHERE id = ?', (stream_id,))
            viewed_streams.append(stream_id)
            # Keep only last 100 viewed stream IDs in session
            request.session['viewed_streams'] = viewed_streams[-100:]

        # Get comments
        c.execute("""SELECT c.*, u.name as user_name, u.avatar_url as user_avatar
                     FROM comments c
                     JOIN users u ON c.user_id = u.id
                     WHERE c.stream_id = ?
                     ORDER BY c.created_at DESC
                     LIMIT 50""", (stream_id,))
        comments = [dict(row) for row in c.fetchall()]

        # Check if favorited
        user = get_current_user(request)
        is_favorited = False
        if user:
            c.execute('SELECT 1 FROM favorites WHERE user_id = ? AND stream_id = ?',
                      (user['id'], stream_id))
            is_favorited = c.fetchone() is not None

        conn.commit()

    csrf_token = generate_csrf_token(request)

    return templates.TemplateResponse("stream.html", {
        "request": request,
        "user": user,
        "stream": stream,
        "comments": comments,
        "is_favorited": is_favorited,
        "csrf_token": csrf_token
    })

@app.post("/stream/{stream_id}/comment")
async def add_comment(request: Request, stream_id: int, comment: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    user = require_auth(request)

    if len(comment) > MAX_COMMENT_LENGTH:
        raise HTTPException(status_code=400, detail=f"Comment must be under {MAX_COMMENT_LENGTH} characters")
    if not comment.strip():
        raise HTTPException(status_code=400, detail="Comment cannot be empty")

    with get_db() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO comments (user_id, stream_id, comment) VALUES (?, ?, ?)',
                  (user['id'], stream_id, comment.strip()))
        conn.commit()

    return RedirectResponse(f"/stream/{stream_id}", status_code=303)

@app.post("/stream/{stream_id}/favorite")
async def toggle_favorite(request: Request, stream_id: int):
    user = require_auth(request)

    with get_db() as conn:
        c = conn.cursor()

        c.execute('SELECT 1 FROM favorites WHERE user_id = ? AND stream_id = ?',
                  (user['id'], stream_id))

        if c.fetchone():
            c.execute('DELETE FROM favorites WHERE user_id = ? AND stream_id = ?',
                      (user['id'], stream_id))
            action = "removed"
        else:
            c.execute('INSERT INTO favorites (user_id, stream_id) VALUES (?, ?)',
                      (user['id'], stream_id))
            action = "added"

        conn.commit()

    return JSONResponse({"status": "success", "action": action})

@app.get("/my-favorites", response_class=HTMLResponse)
async def my_favorites(request: Request):
    user = require_auth(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute("""SELECT s.*, u.name as owner_name
                     FROM favorites f
                     JOIN streams s ON f.stream_id = s.id
                     JOIN users u ON s.user_id = u.id
                     WHERE f.user_id = ? AND s.status = 'approved'
                     ORDER BY f.created_at DESC""", (user['id'],))
        favorites = [dict(f) for f in c.fetchall()]

    return templates.TemplateResponse("favorites.html", {
        "request": request,
        "user": user,
        "favorites": favorites
    })

@app.get("/add", response_class=HTMLResponse)
async def add_stream_form(request: Request):
    user = require_auth(request)
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("add_stream.html", {
        "request": request,
        "user": user,
        "csrf_token": csrf_token
    })

@app.post("/add")
async def add_stream(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    platform: str = Form(...),
    channel_url: str = Form(...),
    location: str = Form(""),
    tags: str = Form(""),
    csrf_token: str = Form(...)
):
    validate_csrf_token(request, csrf_token)
    user = require_auth(request)

    # Validate platform
    if platform not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {', '.join(ALLOWED_PLATFORMS)}")

    # Validate lengths
    if len(title) > MAX_TITLE_LENGTH:
        raise HTTPException(status_code=400, detail=f"Title must be under {MAX_TITLE_LENGTH} characters")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(status_code=400, detail=f"Description must be under {MAX_DESCRIPTION_LENGTH} characters")
    if len(location) > MAX_LOCATION_LENGTH:
        raise HTTPException(status_code=400, detail=f"Location must be under {MAX_LOCATION_LENGTH} characters")
    if len(tags) > MAX_TAGS_LENGTH:
        raise HTTPException(status_code=400, detail=f"Tags must be under {MAX_TAGS_LENGTH} characters")

    # Validate URL
    if not validate_url_for_platform(platform, channel_url):
        raise HTTPException(status_code=400, detail=f"Invalid URL for {platform}. Please provide a valid {platform} channel URL.")

    with get_db() as conn:
        c = conn.cursor()
        c.execute("""INSERT INTO streams (user_id, title, description, platform, channel_url,
                     location, tags, status)
                     VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                  (user['id'], title.strip(), description.strip(), platform, channel_url,
                   location.strip(), tags.strip()))
        conn.commit()

    await manager.broadcast({"type": "new_stream", "title": title.strip()})

    return RedirectResponse("/my-streams", status_code=303)

@app.get("/my-streams", response_class=HTMLResponse)
async def my_streams(request: Request):
    user = require_auth(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM streams WHERE user_id = ? ORDER BY created_at DESC',
                  (user['id'],))
        streams = [dict(s) for s in c.fetchall()]

    return templates.TemplateResponse("my_streams.html", {
        "request": request,
        "user": user,
        "streams": streams
    })

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    user = require_admin(request)

    with get_db() as conn:
        c = conn.cursor()

        c.execute("""SELECT s.*, u.name as owner_name, u.email as owner_email
                     FROM streams s
                     JOIN users u ON s.user_id = u.id
                     WHERE s.status = 'pending'
                     ORDER BY s.created_at DESC""")
        pending = [dict(s) for s in c.fetchall()]

        c.execute("""SELECT s.*, u.name as owner_name
                     FROM streams s
                     JOIN users u ON s.user_id = u.id
                     WHERE s.status = 'approved'
                     ORDER BY s.is_featured DESC, s.viewers DESC""")
        approved = [dict(s) for s in c.fetchall()]

        c.execute('SELECT COUNT(*) as count FROM streams WHERE status = ?', ('approved',))
        stats_approved = c.fetchone()['count']

        c.execute('SELECT COUNT(*) as count FROM streams WHERE status = ?', ('pending',))
        stats_pending = c.fetchone()['count']

        c.execute('SELECT COUNT(*) as count FROM users')
        stats_users = c.fetchone()['count']

    csrf_token = generate_csrf_token(request)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user": user,
        "pending_streams": pending,
        "approved_streams": approved,
        "stats": {
            "approved": stats_approved,
            "pending": stats_pending,
            "users": stats_users,
            "live_connections": len(manager.active_connections)
        },
        "csrf_token": csrf_token
    })

@app.post("/admin/approve/{stream_id}")
async def approve_stream(request: Request, stream_id: int, csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    user = require_admin(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE streams SET status = ? WHERE id = ?', ('approved', stream_id))
        conn.commit()

    await manager.broadcast({"type": "stream_approved", "stream_id": stream_id})

    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/reject/{stream_id}")
async def reject_stream(request: Request, stream_id: int, csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    user = require_admin(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE streams SET status = ? WHERE id = ?', ('rejected', stream_id))
        conn.commit()

    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/feature/{stream_id}")
async def feature_stream(request: Request, stream_id: int, csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    user = require_admin(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE streams SET is_featured = 0')
        c.execute('UPDATE streams SET is_featured = 1 WHERE id = ?', (stream_id,))
        conn.commit()

    await manager.broadcast({"type": "featured_changed", "stream_id": stream_id})

    return RedirectResponse("/admin", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/auth/{provider}/login")
async def oauth_login(request: Request, provider: str):
    if provider not in ('google', 'github'):
        raise HTTPException(status_code=400, detail="Unsupported provider")
    redirect_uri = request.url_for('oauth_callback', provider=provider)
    return await oauth.create_client(provider).authorize_redirect(request, redirect_uri)

@app.get("/auth/{provider}/callback")
async def oauth_callback(request: Request, provider: str):
    if provider not in ('google', 'github'):
        raise HTTPException(status_code=400, detail="Unsupported provider")
    try:
        token = await oauth.create_client(provider).authorize_access_token(request)

        if provider == 'google':
            user_info = token.get('userinfo')
            email = user_info['email']
            name = user_info.get('name', email.split('@')[0])
            avatar_url = user_info.get('picture', '')
            provider_id = user_info['sub']
        elif provider == 'github':
            resp = await oauth.create_client(provider).get('user', token=token)
            user_info = resp.json()
            email = user_info.get('email') or f"{user_info['login']}@github.local"
            name = user_info.get('name') or user_info['login']
            avatar_url = user_info.get('avatar_url', '')
            provider_id = str(user_info['id'])

        user_id = get_or_create_user(email, name, avatar_url, provider, provider_id)
        request.session['user_id'] = user_id

        return RedirectResponse("/")
    except Exception as e:
        print(f"OAuth error: {e}")
        return RedirectResponse("/login?error=auth_failed")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            # Handle viewer count updates
            if msg.get('type') == 'ping':
                await websocket.send_json({"type": "pong", "connections": len(manager.active_connections)})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/stats")
async def get_stats():
    with get_db() as conn:
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM streams WHERE status = ?', ('approved',))
        approved = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM streams WHERE status = ?', ('pending',))
        pending = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM users')
        users = c.fetchone()[0]

        c.execute('SELECT SUM(viewers) FROM streams WHERE status = ?', ('approved',))
        viewers = c.fetchone()[0] or 0

    return {
        "approved_streams": approved,
        "pending_streams": pending,
        "users": users,
        "total_viewers": viewers,
        "active_connections": len(manager.active_connections)
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "2.1.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
