"""
Catio.cam - Full-featured livestreaming hub
OAuth login, admin panel, WebSocket updates, SQLite database
"""
from fastapi import FastAPI, Request, Form, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from typing import Optional, List
import sqlite3
import secrets
import os

app = FastAPI(title="Catio.cam")

# Session middleware
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)))

# OAuth setup
oauth = OAuth()

oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID', 'your-google-client-id'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET', 'your-google-client-secret'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

oauth.register(
    name='github',
    client_id=os.getenv('GITHUB_CLIENT_ID', 'your-github-client-id'),
    client_secret=os.getenv('GITHUB_CLIENT_SECRET', 'your-github-client-secret'),
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'}
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_PATH = "catio.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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
        platform TEXT NOT NULL,
        channel_url TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        is_featured INTEGER DEFAULT 0,
        viewers INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )""")
    
    conn.commit()
    
    # Seed admin user and featured stream
    c.execute('SELECT COUNT(*) FROM users')
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (email, name, provider, provider_id, is_admin) VALUES (?, ?, ?, ?, ?)",
                  ('admin@catio.cam', 'Admin', 'system', 'admin', 1))
        admin_id = c.lastrowid
        
        c.execute("INSERT INTO streams (user_id, title, platform, channel_url, status, is_featured, viewers) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (admin_id, 'Featured Catio Stream', 'twitch', 'https://www.twitch.tv/twitchdev', 'approved', 1, 42))
        conn.commit()
    
    conn.close()

init_db()

# WebSocket connection manager
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
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

def get_current_user(request: Request) -> Optional[dict]:
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    return dict(user) if user else None

def get_or_create_user(email: str, name: str, avatar_url: str, provider: str, provider_id: str) -> int:
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT id FROM users WHERE email = ? AND provider = ?', (email, provider))
    user = c.fetchone()
    
    if user:
        user_id = user[0]
    else:
        c.execute("INSERT INTO users (email, name, avatar_url, provider, provider_id) VALUES (?, ?, ?, ?, ?)",
                  (email, name, avatar_url, provider, provider_id))
        user_id = c.lastrowid
        conn.commit()
    
    conn.close()
    return user_id

def is_admin(user: Optional[dict]) -> bool:
    return user and user.get('is_admin') == 1

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT s.*, u.name as owner_name FROM streams s JOIN users u ON s.user_id = u.id WHERE s.is_featured = 1 AND s.status = 'approved' LIMIT 1")
    featured = c.fetchone()
    
    c.execute("SELECT s.*, u.name as owner_name FROM streams s JOIN users u ON s.user_id = u.id WHERE s.is_featured = 0 AND s.status = 'approved' ORDER BY s.created_at DESC")
    community = c.fetchall()
    
    conn.close()
    
    user = get_current_user(request)
    
    return templates.TemplateResponse("home.html", {
        "request": request,
        "user": user,
        "featured_stream": dict(featured) if featured else None,
        "community_streams": [dict(s) for s in community]
    })

@app.get("/add", response_class=HTMLResponse)
async def add_stream_form(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    
    return templates.TemplateResponse("add_stream.html", {
        "request": request,
        "user": user
    })

@app.post("/add")
async def add_stream(request: Request, title: str = Form(...), platform: str = Form(...), channel_url: str = Form(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO streams (user_id, title, platform, channel_url, status) VALUES (?, ?, ?, ?, ?)",
              (user['id'], title, platform, channel_url, 'pending'))
    stream_id = c.lastrowid
    conn.commit()
    conn.close()
    
    await manager.broadcast({"type": "new_stream", "stream_id": stream_id})
    
    return RedirectResponse("/my-streams", status_code=303)

@app.get("/my-streams", response_class=HTMLResponse)
async def my_streams(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM streams WHERE user_id = ? ORDER BY created_at DESC", (user['id'],))
    streams = c.fetchall()
    conn.close()
    
    return templates.TemplateResponse("my_streams.html", {
        "request": request,
        "user": user,
        "streams": [dict(s) for s in streams]
    })

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    user = get_current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT s.*, u.name as owner_name, u.email as owner_email FROM streams s JOIN users u ON s.user_id = u.id WHERE s.status = 'pending' ORDER BY s.created_at DESC")
    pending = c.fetchall()
    
    c.execute("SELECT s.*, u.name as owner_name FROM streams s JOIN users u ON s.user_id = u.id WHERE s.status = 'approved' ORDER BY s.is_featured DESC, s.created_at DESC")
    approved = c.fetchall()
    
    conn.close()
    
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user": user,
        "pending_streams": [dict(s) for s in pending],
        "approved_streams": [dict(s) for s in approved]
    })

@app.post("/admin/approve/{stream_id}")
async def approve_stream(request: Request, stream_id: int):
    user = get_current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE streams SET status = ? WHERE id = ?', ('approved', stream_id))
    conn.commit()
    conn.close()
    
    await manager.broadcast({"type": "stream_approved", "stream_id": stream_id})
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/reject/{stream_id}")
async def reject_stream(request: Request, stream_id: int):
    user = get_current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE streams SET status = ? WHERE id = ?', ('rejected', stream_id))
    conn.commit()
    conn.close()
    
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/feature/{stream_id}")
async def feature_stream(request: Request, stream_id: int):
    user = get_current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=403)
    
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE streams SET is_featured = 0')
    c.execute('UPDATE streams SET is_featured = 1 WHERE id = ?', (stream_id,))
    conn.commit()
    conn.close()
    
    await manager.broadcast({"type": "featured_changed", "stream_id": stream_id})
    return RedirectResponse("/admin", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/auth/{provider}/login")
async def oauth_login(request: Request, provider: str):
    redirect_uri = request.url_for('oauth_callback', provider=provider)
    return await oauth.create_client(provider).authorize_redirect(request, redirect_uri)

@app.get("/auth/{provider}/callback")
async def oauth_callback(request: Request, provider: str):
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
            await websocket.send_json({"type": "pong", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/stats")
async def get_stats():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM streams WHERE status = ?', ('approved',))
    approved_count = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM streams WHERE status = ?', ('pending',))
    pending_count = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM users')
    user_count = c.fetchone()[0]
    
    c.execute('SELECT SUM(viewers) FROM streams WHERE status = ?', ('approved',))
    total_viewers = c.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "approved_streams": approved_count,
        "pending_streams": pending_count,
        "users": user_count,
        "viewers": total_viewers,
        "active_connections": len(manager.active_connections)
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "2.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
