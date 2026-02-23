"""
hellsy.tube — Fast HTML5 Video Upload & Streaming Platform
YouTube-lite built with FastAPI + SQLite + vanilla JS
"""
from fastapi import (
    FastAPI, Request, Form, File, UploadFile, HTTPException,
    WebSocket, WebSocketDisconnect, Query
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from typing import Optional, List
import sqlite3
import secrets
import os
import shutil
import hashlib
import subprocess
import json
import asyncio
import aiofiles
from datetime import datetime
from pathlib import Path

# ── App Setup ──────────────────────────────────────────────────────────────────

app = FastAPI(title="hellsy.tube")

SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
THUMB_DIR = UPLOAD_DIR / "thumbnails"
UPLOAD_DIR.mkdir(exist_ok=True)
THUMB_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXTENSIONS = {".mp4", ".webm", ".ogg", ".mov", ".mkv", ".avi"}
ALLOWED_MIME_PREFIXES = ("video/",)

# OAuth
oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
oauth.register(
    name="github",
    client_id=os.getenv("GITHUB_CLIENT_ID", "YOUR_GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET", "YOUR_GITHUB_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "user:email"},
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_PATH = "hellsy.db"

# ── WebSocket Manager ─────────────────────────────────────────────────────────

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
        for conn in self.active_connections[:]:
            try:
                await conn.send_json(message)
            except Exception:
                self.active_connections.remove(conn)

manager = ConnectionManager()

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        channel_name TEXT,
        avatar_url TEXT DEFAULT '',
        provider TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        filename TEXT NOT NULL,
        original_name TEXT,
        file_size INTEGER DEFAULT 0,
        duration REAL DEFAULT 0,
        width INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        mime_type TEXT DEFAULT 'video/mp4',
        thumbnail TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        status TEXT DEFAULT 'processing',
        is_featured INTEGER DEFAULT 0,
        views INTEGER DEFAULT 0,
        likes_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, video_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
    )""")

    c.execute("CREATE INDEX IF NOT EXISTS idx_videos_user ON videos(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_videos_created ON videos(created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_likes_video ON likes(video_id)")

    conn.commit()

    # Seed demo admin
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute(
            """INSERT INTO users (email, name, channel_name, provider, provider_id, is_admin)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("admin@hellsy.tube", "Admin", "hellsy", "system", "admin", 1),
        )
        conn.commit()

    conn.close()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_current_user(request: Request) -> Optional[dict]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def require_auth(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user

def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def get_or_create_user(email, name, avatar_url, provider, provider_id) -> int:
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE email = ? AND provider = ?", (email, provider)
    ).fetchone()
    if row:
        uid = row[0]
    else:
        channel = name.lower().replace(" ", "")[:20] if name else email.split("@")[0]
        cur = conn.execute(
            """INSERT INTO users (email, name, channel_name, avatar_url, provider, provider_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (email, name, channel, avatar_url, provider, provider_id),
        )
        uid = cur.lastrowid
        conn.commit()
    conn.close()
    return uid

def format_duration(seconds: float) -> str:
    if not seconds or seconds <= 0:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def format_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

def format_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def time_ago(dt_str: str) -> str:
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str
    diff = datetime.utcnow() - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m} min ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hr ago"
    if secs < 2592000:
        d = secs // 86400
        return f"{d} day{'s' if d != 1 else ''} ago"
    if secs < 31536000:
        mo = secs // 2592000
        return f"{mo} month{'s' if mo != 1 else ''} ago"
    y = secs // 31536000
    return f"{y} year{'s' if y != 1 else ''} ago"

# Register template filters
templates.env.filters["format_duration"] = format_duration
templates.env.filters["format_views"] = format_views
templates.env.filters["format_size"] = format_size
templates.env.filters["time_ago"] = time_ago

def probe_video(filepath: str) -> dict:
    """Extract video metadata using ffprobe if available."""
    info = {"duration": 0, "width": 0, "height": 0}
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", filepath,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            info["duration"] = float(fmt.get("duration", 0))
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    info["width"] = int(stream.get("width", 0))
                    info["height"] = int(stream.get("height", 0))
                    if not info["duration"]:
                        info["duration"] = float(stream.get("duration", 0))
                    break
    except Exception:
        pass
    return info

def generate_thumbnail(video_path: str, thumb_path: str) -> bool:
    """Generate thumbnail at 25% of video duration using ffmpeg."""
    try:
        info = probe_video(video_path)
        seek = max(1, info["duration"] * 0.25) if info["duration"] > 2 else 0
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(seek), "-i", video_path,
                "-vframes", "1", "-vf", "scale=640:-2",
                "-q:v", "3", thumb_path,
            ],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False

def create_placeholder_thumbnail(thumb_path: str):
    """Create a simple SVG placeholder thumbnail."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
  <rect width="640" height="360" fill="#1a1a2e"/>
  <polygon points="290,140 290,220 360,180" fill="#e94560" opacity="0.8"/>
  <text x="320" y="280" text-anchor="middle" fill="#666" font-family="sans-serif" font-size="16">hellsy.tube</text>
</svg>"""
    with open(thumb_path, "w") as f:
        f.write(svg)

# ── Routes: Pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    sort: str = "recent",
    tag: Optional[str] = None,
    page: int = 1,
):
    user = get_current_user(request)
    conn = get_db()
    per_page = 24
    offset = (page - 1) * per_page

    # Featured video
    featured = conn.execute(
        """SELECT v.*, u.name as uploader_name, u.channel_name, u.avatar_url as uploader_avatar
           FROM videos v JOIN users u ON v.user_id = u.id
           WHERE v.is_featured = 1 AND v.status = 'published'
           LIMIT 1"""
    ).fetchone()

    # Build query
    where = "v.status = 'published'"
    params: list = []
    if tag:
        where += " AND v.tags LIKE ?"
        params.append(f"%{tag}%")

    order = "v.created_at DESC"
    if sort == "popular":
        order = "v.views DESC, v.created_at DESC"
    elif sort == "trending":
        order = "v.likes_count DESC, v.views DESC"

    rows = conn.execute(
        f"""SELECT v.*, u.name as uploader_name, u.channel_name, u.avatar_url as uploader_avatar
            FROM videos v JOIN users u ON v.user_id = u.id
            WHERE {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    ).fetchall()

    total = conn.execute(
        f"SELECT COUNT(*) FROM videos v WHERE {where}", params
    ).fetchone()[0]

    # All tags
    tag_rows = conn.execute(
        "SELECT DISTINCT tags FROM videos WHERE status='published' AND tags != ''"
    ).fetchall()
    all_tags = sorted(
        {t.strip() for row in tag_rows for t in row["tags"].split(",") if t.strip()}
    )

    conn.close()

    return templates.TemplateResponse("home.html", {
        "request": request,
        "user": user,
        "featured": dict(featured) if featured else None,
        "videos": [dict(r) for r in rows],
        "all_tags": all_tags,
        "current_tag": tag,
        "sort": sort,
        "page": page,
        "total_pages": max(1, -(-total // per_page)),
        "total_videos": total,
    })

@app.get("/watch/{video_id}", response_class=HTMLResponse)
async def watch(request: Request, video_id: int):
    user = get_current_user(request)
    conn = get_db()

    video = conn.execute(
        """SELECT v.*, u.name as uploader_name, u.channel_name, u.avatar_url as uploader_avatar, u.id as uploader_id
           FROM videos v JOIN users u ON v.user_id = u.id
           WHERE v.id = ? AND v.status = 'published'""",
        (video_id,),
    ).fetchone()

    if not video:
        conn.close()
        raise HTTPException(status_code=404, detail="Video not found")

    video = dict(video)

    # Increment views
    conn.execute("UPDATE videos SET views = views + 1 WHERE id = ?", (video_id,))
    conn.commit()
    video["views"] += 1

    # Comments
    comments = [
        dict(r) for r in conn.execute(
            """SELECT c.*, u.name as user_name, u.avatar_url as user_avatar
               FROM comments c JOIN users u ON c.user_id = u.id
               WHERE c.video_id = ?
               ORDER BY c.created_at DESC LIMIT 100""",
            (video_id,),
        ).fetchall()
    ]

    # User liked?
    user_liked = False
    if user:
        user_liked = conn.execute(
            "SELECT 1 FROM likes WHERE user_id = ? AND video_id = ?",
            (user["id"], video_id),
        ).fetchone() is not None

    # Related videos
    related = [
        dict(r) for r in conn.execute(
            """SELECT v.*, u.name as uploader_name, u.channel_name, u.avatar_url as uploader_avatar
               FROM videos v JOIN users u ON v.user_id = u.id
               WHERE v.id != ? AND v.status = 'published'
               ORDER BY RANDOM() LIMIT 8""",
            (video_id,),
        ).fetchall()
    ]

    # Uploader video count
    uploader_video_count = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE user_id = ? AND status = 'published'",
        (video["uploader_id"],),
    ).fetchone()[0]

    conn.close()

    return templates.TemplateResponse("watch.html", {
        "request": request,
        "user": user,
        "video": video,
        "comments": comments,
        "user_liked": user_liked,
        "related": related,
        "uploader_video_count": uploader_video_count,
    })

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "user": user,
        "max_size": MAX_FILE_SIZE,
    })

@app.get("/channel/{channel_name}", response_class=HTMLResponse)
async def channel_page(request: Request, channel_name: str, page: int = 1):
    user = get_current_user(request)
    conn = get_db()
    per_page = 24
    offset = (page - 1) * per_page

    channel_user = conn.execute(
        "SELECT * FROM users WHERE channel_name = ?", (channel_name,)
    ).fetchone()
    if not channel_user:
        conn.close()
        raise HTTPException(status_code=404, detail="Channel not found")

    channel_user = dict(channel_user)

    videos = [
        dict(r) for r in conn.execute(
            """SELECT v.*, u.name as uploader_name, u.channel_name, u.avatar_url as uploader_avatar
               FROM videos v JOIN users u ON v.user_id = u.id
               WHERE v.user_id = ? AND v.status = 'published'
               ORDER BY v.created_at DESC LIMIT ? OFFSET ?""",
            (channel_user["id"], per_page, offset),
        ).fetchall()
    ]

    total = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE user_id = ? AND status = 'published'",
        (channel_user["id"],),
    ).fetchone()[0]

    total_views = conn.execute(
        "SELECT COALESCE(SUM(views), 0) FROM videos WHERE user_id = ? AND status = 'published'",
        (channel_user["id"],),
    ).fetchone()[0]

    conn.close()

    return templates.TemplateResponse("channel.html", {
        "request": request,
        "user": user,
        "channel": channel_user,
        "videos": videos,
        "total_videos": total,
        "total_views": total_views,
        "page": page,
        "total_pages": max(1, -(-total // per_page)),
    })

@app.get("/my-videos", response_class=HTMLResponse)
async def my_videos(request: Request):
    user = require_auth(request)
    conn = get_db()
    videos = [
        dict(r) for r in conn.execute(
            "SELECT * FROM videos WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    ]
    conn.close()
    return templates.TemplateResponse("my_videos.html", {
        "request": request,
        "user": user,
        "videos": videos,
    })

@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", page: int = 1):
    user = get_current_user(request)
    conn = get_db()
    per_page = 24
    offset = (page - 1) * per_page
    videos = []
    total = 0

    if q.strip():
        like = f"%{q}%"
        videos = [
            dict(r) for r in conn.execute(
                """SELECT v.*, u.name as uploader_name, u.channel_name, u.avatar_url as uploader_avatar
                   FROM videos v JOIN users u ON v.user_id = u.id
                   WHERE v.status = 'published'
                     AND (v.title LIKE ? OR v.description LIKE ? OR v.tags LIKE ? OR u.name LIKE ?)
                   ORDER BY v.views DESC, v.created_at DESC
                   LIMIT ? OFFSET ?""",
                (like, like, like, like, per_page, offset),
            ).fetchall()
        ]
        total = conn.execute(
            """SELECT COUNT(*) FROM videos v JOIN users u ON v.user_id = u.id
               WHERE v.status = 'published'
                 AND (v.title LIKE ? OR v.description LIKE ? OR v.tags LIKE ? OR u.name LIKE ?)""",
            (like, like, like, like),
        ).fetchone()[0]

    conn.close()

    return templates.TemplateResponse("search.html", {
        "request": request,
        "user": user,
        "query": q,
        "videos": videos,
        "total": total,
        "page": page,
        "total_pages": max(1, -(-total // per_page)) if total else 1,
    })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": None})

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    user = require_admin(request)
    conn = get_db()

    pending = [
        dict(r) for r in conn.execute(
            """SELECT v.*, u.name as uploader_name, u.email as uploader_email
               FROM videos v JOIN users u ON v.user_id = u.id
               WHERE v.status = 'processing'
               ORDER BY v.created_at DESC"""
        ).fetchall()
    ]

    published = [
        dict(r) for r in conn.execute(
            """SELECT v.*, u.name as uploader_name
               FROM videos v JOIN users u ON v.user_id = u.id
               WHERE v.status = 'published'
               ORDER BY v.is_featured DESC, v.views DESC"""
        ).fetchall()
    ]

    stats = {
        "total_videos": conn.execute("SELECT COUNT(*) FROM videos WHERE status='published'").fetchone()[0],
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_views": conn.execute("SELECT COALESCE(SUM(views),0) FROM videos").fetchone()[0],
        "pending": len(pending),
    }

    conn.close()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user": user,
        "pending": pending,
        "published": published,
        "stats": stats,
    })

# ── Routes: API ───────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def api_upload(
    request: Request,
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
):
    user = require_auth(request)

    # Validate extension
    ext = Path(video.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    # Generate unique filename
    file_hash = hashlib.md5(f"{user['id']}-{video.filename}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:12]
    safe_name = f"{file_hash}{ext}"
    file_path = UPLOAD_DIR / safe_name

    # Stream file to disk
    total_size = 0
    async with aiofiles.open(file_path, "wb") as f:
        while chunk := await video.read(1024 * 1024):  # 1MB chunks
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                await f.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large. Max {MAX_FILE_SIZE // (1024*1024)}MB")
            await f.write(chunk)

    # Probe metadata
    meta = probe_video(str(file_path))

    # Generate thumbnail
    thumb_name = f"{file_hash}.jpg"
    thumb_path = THUMB_DIR / thumb_name
    if not generate_thumbnail(str(file_path), str(thumb_path)):
        # Fallback SVG
        thumb_name = f"{file_hash}.svg"
        thumb_path = THUMB_DIR / thumb_name
        create_placeholder_thumbnail(str(thumb_path))

    # Determine mime type
    mime_map = {".mp4": "video/mp4", ".webm": "video/webm", ".ogg": "video/ogg",
                ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
    mime = mime_map.get(ext, "video/mp4")

    # Insert into DB
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO videos
           (user_id, title, description, filename, original_name, file_size,
            duration, width, height, mime_type, thumbnail, tags, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published')""",
        (
            user["id"], title.strip(), description.strip(), safe_name,
            video.filename, total_size, meta["duration"], meta["width"],
            meta["height"], mime, thumb_name, tags.strip(),
        ),
    )
    video_id = cur.lastrowid
    conn.commit()
    conn.close()

    await manager.broadcast({"type": "new_video", "id": video_id, "title": title})

    return JSONResponse({"status": "ok", "video_id": video_id, "message": "Video uploaded!"})

@app.get("/video/stream/{filename}")
async def stream_video(request: Request, filename: str):
    """Serve video with HTTP Range support for seeking."""
    file_path = UPLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Video not found")

    file_size = file_path.stat().st_size
    ext = Path(filename).suffix.lower()
    mime_map = {".mp4": "video/mp4", ".webm": "video/webm", ".ogg": "video/ogg",
                ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
    content_type = mime_map.get(ext, "video/mp4")

    range_header = request.headers.get("range")

    if range_header:
        # Parse range
        range_str = range_header.replace("bytes=", "")
        parts = range_str.split("-")
        start = int(parts[0])
        end = int(parts[1]) if parts[1] else min(start + 5 * 1024 * 1024, file_size - 1)
        end = min(end, file_size - 1)
        length = end - start + 1

        async def ranged_file():
            async with aiofiles.open(file_path, "rb") as f:
                await f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    data = await f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            ranged_file(),
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
                "Content-Type": content_type,
                "Cache-Control": "public, max-age=86400",
            },
        )
    else:
        return FileResponse(
            file_path,
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Cache-Control": "public, max-age=86400",
            },
        )

@app.get("/thumbnail/{filename}")
async def serve_thumbnail(filename: str):
    thumb_path = THUMB_DIR / filename
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    ct = "image/svg+xml" if filename.endswith(".svg") else "image/jpeg"
    return FileResponse(thumb_path, media_type=ct, headers={"Cache-Control": "public, max-age=604800"})

@app.post("/api/like/{video_id}")
async def toggle_like(request: Request, video_id: int):
    user = require_auth(request)
    conn = get_db()

    existing = conn.execute(
        "SELECT 1 FROM likes WHERE user_id = ? AND video_id = ?",
        (user["id"], video_id),
    ).fetchone()

    if existing:
        conn.execute("DELETE FROM likes WHERE user_id = ? AND video_id = ?", (user["id"], video_id))
        conn.execute("UPDATE videos SET likes_count = MAX(0, likes_count - 1) WHERE id = ?", (video_id,))
        action = "unliked"
    else:
        conn.execute("INSERT INTO likes (user_id, video_id) VALUES (?, ?)", (user["id"], video_id))
        conn.execute("UPDATE videos SET likes_count = likes_count + 1 WHERE id = ?", (video_id,))
        action = "liked"

    likes = conn.execute("SELECT likes_count FROM videos WHERE id = ?", (video_id,)).fetchone()
    conn.commit()
    conn.close()

    return JSONResponse({"status": "ok", "action": action, "likes": likes[0] if likes else 0})

@app.post("/api/comment/{video_id}")
async def add_comment(request: Request, video_id: int, text: str = Form(...)):
    user = require_auth(request)
    conn = get_db()
    conn.execute(
        "INSERT INTO comments (user_id, video_id, text) VALUES (?, ?, ?)",
        (user["id"], video_id, text.strip()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/watch/{video_id}", status_code=303)

@app.post("/api/delete/{video_id}")
async def delete_video(request: Request, video_id: int):
    user = require_auth(request)
    conn = get_db()
    video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not video:
        conn.close()
        raise HTTPException(404, "Video not found")

    video = dict(video)
    if video["user_id"] != user["id"] and not user.get("is_admin"):
        conn.close()
        raise HTTPException(403, "Not authorized")

    # Delete files
    vpath = UPLOAD_DIR / video["filename"]
    vpath.unlink(missing_ok=True)
    tpath = THUMB_DIR / video["thumbnail"]
    tpath.unlink(missing_ok=True)

    conn.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM likes WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()
    conn.close()

    return JSONResponse({"status": "ok", "message": "Video deleted"})

@app.post("/admin/publish/{video_id}")
async def admin_publish(request: Request, video_id: int):
    require_admin(request)
    conn = get_db()
    conn.execute("UPDATE videos SET status = 'published' WHERE id = ?", (video_id,))
    conn.commit()
    conn.close()
    await manager.broadcast({"type": "video_published", "id": video_id})
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/feature/{video_id}")
async def admin_feature(request: Request, video_id: int):
    require_admin(request)
    conn = get_db()
    conn.execute("UPDATE videos SET is_featured = 0")
    conn.execute("UPDATE videos SET is_featured = 1 WHERE id = ?", (video_id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/remove/{video_id}")
async def admin_remove(request: Request, video_id: int):
    require_admin(request)
    conn = get_db()
    video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if video:
        v = dict(video)
        (UPLOAD_DIR / v["filename"]).unlink(missing_ok=True)
        (THUMB_DIR / v["thumbnail"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM likes WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
    conn.close()
    return RedirectResponse("/admin", status_code=303)

@app.get("/api/stats")
async def api_stats():
    conn = get_db()
    stats = {
        "videos": conn.execute("SELECT COUNT(*) FROM videos WHERE status='published'").fetchone()[0],
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "views": conn.execute("SELECT COALESCE(SUM(views),0) FROM videos").fetchone()[0],
        "active": len(manager.active_connections),
    }
    conn.close()
    return stats

# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.get("/auth/{provider}/login")
async def oauth_login(request: Request, provider: str):
    redirect_uri = request.url_for("oauth_callback", provider=provider)
    return await oauth.create_client(provider).authorize_redirect(request, redirect_uri)

@app.get("/auth/{provider}/callback")
async def oauth_callback(request: Request, provider: str):
    try:
        token = await oauth.create_client(provider).authorize_access_token(request)
        if provider == "google":
            info = token.get("userinfo")
            email, name = info["email"], info.get("name", info["email"].split("@")[0])
            avatar, pid = info.get("picture", ""), info["sub"]
        elif provider == "github":
            resp = await oauth.create_client(provider).get("user", token=token)
            info = resp.json()
            email = info.get("email") or f"{info['login']}@github.local"
            name = info.get("name") or info["login"]
            avatar, pid = info.get("avatar_url", ""), str(info["id"])
        else:
            return RedirectResponse("/login?error=unknown_provider")

        uid = get_or_create_user(email, name, avatar, provider, pid)
        request.session["user_id"] = uid
        return RedirectResponse("/")
    except Exception as e:
        print(f"OAuth error: {e}")
        return RedirectResponse("/login?error=auth_failed")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "active": len(manager.active_connections)})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/health")
async def health():
    return {"status": "healthy", "app": "hellsy.tube", "version": "1.0.0"}

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
