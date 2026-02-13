"""
Catio.cam - Livestreaming hub for catio cameras
SQLite-backed test site
"""
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import sqlite3

app = FastAPI(title="Catio.cam")

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
    c.execute("""
        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            platform TEXT NOT NULL,
            channel_url TEXT NOT NULL,
            is_featured INTEGER DEFAULT 0,
            is_approved INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    
    # Seed featured stream if empty
    c.execute("SELECT COUNT(*) FROM streams")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO streams (title, platform, channel_url, is_featured, is_approved)
            VALUES (?, ?, ?, ?, ?)
        """, ("Featured Catio Stream", "twitch", "https://www.twitch.tv/twitchdev", 1, 1))
        conn.commit()
    
    conn.close()

init_db()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""SELECT * FROM streams 
                 WHERE is_featured = 1 AND is_approved = 1 
                 ORDER BY created_at DESC LIMIT 1""")
    featured = c.fetchone()
    
    c.execute("""SELECT * FROM streams 
                 WHERE is_featured = 0 AND is_approved = 1 
                 ORDER BY created_at DESC""")
    community = c.fetchall()
    conn.close()
    
    return templates.TemplateResponse("home.html", {
        "request": request,
        "featured_stream": dict(featured) if featured else None,
        "community_streams": [dict(s) for s in community]
    })

@app.get("/add", response_class=HTMLResponse)
async def add_stream_form(request: Request):
    return templates.TemplateResponse("add_stream.html", {
        "request": request
    })

@app.post("/add")
async def add_stream(
    title: str = Form(...),
    platform: str = Form(...),
    channel_url: str = Form(...)
):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO streams (title, platform, channel_url, is_featured, is_approved)
        VALUES (?, ?, ?, 0, 1)
    """, (title, platform, channel_url))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.get("/health")
async def health():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM streams")
    stream_count = c.fetchone()[0]
    conn.close()
    return {"status": "healthy", "streams": stream_count, "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
