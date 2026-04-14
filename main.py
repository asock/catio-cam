"""
Catio.cam - Full-featured livestreaming hub
OAuth + Admin Panel + Tags + Favorites + Live Stats
"""
from fastapi import FastAPI, Request, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from authlib.integrations.starlette_client import OAuth
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import Optional, List
from contextlib import contextmanager
import sqlite3
import secrets
import os
import sys
import logging
import asyncio
import time
from datetime import datetime
import json
import re

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# --- App setup ---

SITE_DOMAIN = os.getenv("SITE_DOMAIN", "localhost")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Catio.cam")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Session secret
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    logger.warning("SESSION_SECRET not set. Sessions will not persist across restarts.")
    logger.warning("Set SESSION_SECRET in your .env file for production use.")
    SESSION_SECRET = secrets.token_urlsafe(32)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "frame-src https://player.twitch.tv https://www.youtube.com; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "img-src 'self' https: data:; "
        "connect-src 'self' ws: wss:;"
    )
    return response

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
DEFAULT_PER_PAGE = 12

ALLOWED_URL_PATTERNS = {
    "twitch": re.compile(r'^https?://(www\.)?twitch\.tv/[\w-]+$'),
    "youtube": re.compile(r'^https?://(www\.)?(youtube\.com|youtu\.be)/[\w./?&=-]+$'),
}

# --- WebSocket manager ---

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
        if not self.active_connections:
            return
        async def _send(conn: WebSocket):
            try:
                await conn.send_json(message)
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                self.disconnect(conn)
        await asyncio.gather(*[_send(c) for c in self.active_connections[:]])

manager = ConnectionManager()

# --- Database ---

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db() -> None:
    with get_db() as conn:
        c = conn.cursor()

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

        c.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stream_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, stream_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (stream_id) REFERENCES streams (id)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stream_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (stream_id) REFERENCES streams (id)
        )""")

        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_streams_status ON streams(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_streams_is_featured ON streams(is_featured)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_streams_user_id ON streams(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_comments_stream_id ON comments(stream_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user_id ON favorites(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_favorites_stream_id ON favorites(stream_id)")

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

    logger.info("Database initialized")

@app.on_event("startup")
async def startup():
    init_db()

# --- Custom error handler ---

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    error_map = {
        401: ("Unauthorized", "You need to log in to access this page."),
        403: ("Forbidden", "You don't have permission to access this page."),
        404: ("Not Found", "The page you're looking for doesn't exist."),
        500: ("Server Error", "Something went wrong on our end."),
    }
    title, message = error_map.get(exc.status_code, ("Error", str(exc.detail)))
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse("error.html", {
        "request": request,
        "user": get_current_user(request),
        "status_code": exc.status_code,
        "error_title": title,
        "error_message": message,
    }, status_code=exc.status_code)

# --- CSRF Protection ---

def generate_csrf_token(request: Request) -> str:
    if 'csrf_token' not in request.session:
        request.session['csrf_token'] = secrets.token_urlsafe(32)
    return request.session['csrf_token']

def validate_csrf_token(request: Request, token: str) -> None:
    session_token = request.session.get('csrf_token')
    if not session_token or not secrets.compare_digest(session_token, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

# --- Validation helpers ---

def validate_url_for_platform(platform: str, url: str) -> bool:
    if platform not in ALLOWED_URL_PATTERNS:
        return False
    return bool(ALLOWED_URL_PATTERNS[platform].match(url))

def validate_stream_fields(title: str, description: str, location: str, tags: str) -> None:
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

# --- User helpers ---

def get_current_user(request: Request) -> Optional[dict]:
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
    return dict(user) if user else None

def require_auth(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

def require_admin(request: Request) -> dict:
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

# --- Stats cache ---

_stats_cache: dict = {"data": None, "expires": 0.0}

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, tag: Optional[str] = None, search: Optional[str] = None, page: int = 1):
    page = max(1, page)
    offset = (page - 1) * DEFAULT_PER_PAGE

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

        params: list = []
        if tag:
            query += """ AND (',' || s.tags || ',' LIKE ?)"""
            params.append(f"%,{tag},%")
        if search:
            query += " AND (s.title LIKE ? OR s.description LIKE ? OR s.location LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        query += " ORDER BY s.viewers DESC, s.created_at DESC"
        query += " LIMIT ? OFFSET ?"
        params.extend([DEFAULT_PER_PAGE + 1, offset])

        c.execute(query, params)
        results = c.fetchall()
        has_next = len(results) > DEFAULT_PER_PAGE
        community = results[:DEFAULT_PER_PAGE]

        # Get all unique tags
        c.execute("SELECT DISTINCT tags FROM streams WHERE status = 'approved' AND tags IS NOT NULL")
        all_tags: set = set()
        for row in c.fetchall():
            if row['tags']:
                for t in row['tags'].split(','):
                    stripped = t.strip()
                    if stripped:
                        all_tags.add(stripped)

    user = get_current_user(request)
    csrf_token = generate_csrf_token(request)

    return templates.TemplateResponse("home.html", {
        "request": request,
        "user": user,
        "featured_stream": dict(featured) if featured else None,
        "community_streams": [dict(s) for s in community],
        "all_tags": sorted(all_tags),
        "current_tag": tag,
        "search_query": search,
        "page": page,
        "has_next": has_next,
        "csrf_token": csrf_token,
        "site_domain": SITE_DOMAIN,
    })

@app.get("/stream/{stream_id}", response_class=HTMLResponse)
async def view_stream(request: Request, stream_id: int):
    with get_db() as conn:
        c = conn.cursor()

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
        "csrf_token": csrf_token,
        "site_domain": SITE_DOMAIN,
    })

@app.post("/stream/{stream_id}/comment")
@limiter.limit("10/minute")
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
@limiter.limit("30/minute")
async def toggle_favorite(request: Request, stream_id: int):
    # Accept CSRF from header (AJAX) or form field
    csrf_header = request.headers.get("X-CSRF-Token", "")
    validate_csrf_token(request, csrf_header)
    user = require_auth(request)

    with get_db() as conn:
        c = conn.cursor()
        # Atomic: try insert first
        c.execute('INSERT OR IGNORE INTO favorites (user_id, stream_id) VALUES (?, ?)',
                  (user['id'], stream_id))
        if c.rowcount > 0:
            action = "added"
        else:
            c.execute('DELETE FROM favorites WHERE user_id = ? AND stream_id = ?',
                      (user['id'], stream_id))
            action = "removed"
        conn.commit()

    return JSONResponse({"status": "success", "action": action})

@app.get("/my-favorites", response_class=HTMLResponse)
async def my_favorites(request: Request, page: int = 1):
    user = require_auth(request)
    page = max(1, page)
    offset = (page - 1) * DEFAULT_PER_PAGE

    with get_db() as conn:
        c = conn.cursor()
        c.execute("""SELECT s.*, u.name as owner_name
                     FROM favorites f
                     JOIN streams s ON f.stream_id = s.id
                     JOIN users u ON s.user_id = u.id
                     WHERE f.user_id = ? AND s.status = 'approved'
                     ORDER BY f.created_at DESC
                     LIMIT ? OFFSET ?""", (user['id'], DEFAULT_PER_PAGE + 1, offset))
        results = c.fetchall()
        has_next = len(results) > DEFAULT_PER_PAGE
        favorites = [dict(f) for f in results[:DEFAULT_PER_PAGE]]

    return templates.TemplateResponse("favorites.html", {
        "request": request,
        "user": user,
        "favorites": favorites,
        "page": page,
        "has_next": has_next,
        "site_domain": SITE_DOMAIN,
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
@limiter.limit("5/minute")
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

    if platform not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Platform must be one of: {', '.join(ALLOWED_PLATFORMS)}")

    validate_stream_fields(title, description, location, tags)

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
async def my_streams(request: Request, page: int = 1):
    user = require_auth(request)
    page = max(1, page)
    offset = (page - 1) * DEFAULT_PER_PAGE

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM streams WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
                  (user['id'], DEFAULT_PER_PAGE + 1, offset))
        results = c.fetchall()
        has_next = len(results) > DEFAULT_PER_PAGE
        streams = [dict(s) for s in results[:DEFAULT_PER_PAGE]]

    csrf_token = generate_csrf_token(request)

    return templates.TemplateResponse("my_streams.html", {
        "request": request,
        "user": user,
        "streams": streams,
        "csrf_token": csrf_token,
        "page": page,
        "has_next": has_next,
    })

# --- Edit/Delete stream ---

@app.get("/stream/{stream_id}/edit", response_class=HTMLResponse)
async def edit_stream_form(request: Request, stream_id: int):
    user = require_auth(request)
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM streams WHERE id = ? AND user_id = ?', (stream_id, user['id']))
        stream = c.fetchone()
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("edit_stream.html", {
        "request": request,
        "user": user,
        "stream": dict(stream),
        "csrf_token": csrf_token,
    })

@app.post("/stream/{stream_id}/edit")
@limiter.limit("10/minute")
async def edit_stream(
    request: Request,
    stream_id: int,
    title: str = Form(...),
    description: str = Form(""),
    location: str = Form(""),
    tags: str = Form(""),
    csrf_token: str = Form(...)
):
    validate_csrf_token(request, csrf_token)
    user = require_auth(request)

    validate_stream_fields(title, description, location, tags)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM streams WHERE id = ? AND user_id = ?', (stream_id, user['id']))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Stream not found")
        c.execute("""UPDATE streams SET title=?, description=?, location=?, tags=?, status='pending'
                     WHERE id = ? AND user_id = ?""",
                  (title.strip(), description.strip(), location.strip(), tags.strip(),
                   stream_id, user['id']))
        conn.commit()

    return RedirectResponse("/my-streams", status_code=303)

@app.post("/stream/{stream_id}/delete")
async def delete_stream(request: Request, stream_id: int, csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    user = require_auth(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM streams WHERE id = ? AND user_id = ?', (stream_id, user['id']))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Stream not found")
        c.execute('DELETE FROM comments WHERE stream_id = ?', (stream_id,))
        c.execute('DELETE FROM favorites WHERE stream_id = ?', (stream_id,))
        c.execute('DELETE FROM streams WHERE id = ? AND user_id = ?', (stream_id, user['id']))
        conn.commit()

    return RedirectResponse("/my-streams", status_code=303)

# --- Admin ---

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
    require_admin(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE streams SET status = ? WHERE id = ?', ('approved', stream_id))
        conn.commit()

    await manager.broadcast({"type": "stream_approved", "stream_id": stream_id})

    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/reject/{stream_id}")
async def reject_stream(request: Request, stream_id: int, csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    require_admin(request)

    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE streams SET status = ? WHERE id = ?', ('rejected', stream_id))
        conn.commit()

    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/feature/{stream_id}")
async def feature_stream(request: Request, stream_id: int, csrf_token: str = Form(...)):
    validate_csrf_token(request, csrf_token)
    require_admin(request)

    with get_db() as conn:
        c = conn.cursor()
        # Atomic: single statement to swap featured stream
        c.execute('UPDATE streams SET is_featured = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE is_featured = 1 OR id = ?',
                  (stream_id, stream_id))
        conn.commit()

    await manager.broadcast({"type": "featured_changed", "stream_id": stream_id})

    return RedirectResponse("/admin", status_code=303)

# --- Auth ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/auth/{provider}/login")
@limiter.limit("10/minute")
async def oauth_login(request: Request, provider: str):
    if provider not in ('google', 'github'):
        raise HTTPException(status_code=400, detail="Unsupported provider")
    redirect_uri = request.url_for('oauth_callback', provider=provider)
    return await oauth.create_client(provider).authorize_redirect(request, redirect_uri)

@app.get("/auth/{provider}/callback")
@limiter.limit("10/minute")
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
    except Exception:
        logger.exception("OAuth callback failed")
        return RedirectResponse("/login?error=auth_failed")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get('type') == 'ping':
                await websocket.send_json({"type": "pong", "connections": len(manager.active_connections)})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        logger.exception("WebSocket error")
        manager.disconnect(websocket)

# --- API ---

@app.get("/api/stats")
async def get_stats():
    now = time.time()
    if _stats_cache["data"] and now < _stats_cache["expires"]:
        cached = _stats_cache["data"].copy()
        cached["active_connections"] = len(manager.active_connections)
        return cached

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

    result = {
        "approved_streams": approved,
        "pending_streams": pending,
        "users": users,
        "total_viewers": viewers,
        "active_connections": len(manager.active_connections)
    }
    _stats_cache["data"] = result
    _stats_cache["expires"] = now + 10
    return result

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "3.0.0"}

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /my-\n"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
