# hellsy.tube ▶

Fast, tight HTML5 video upload & streaming platform — YouTube-lite.

## Features

- **Video Upload**: Drag-and-drop with progress bar, MP4/WebM/OGG/MOV support (500MB max)
- **HTML5 Streaming**: Native `<video>` with HTTP Range support for instant seeking
- **Video Feed**: Grid layout with thumbnails, view counts, duration badges
- **Watch Page**: Full player, likes, comments, related videos sidebar
- **Channels**: User channel pages with all their uploads
- **Search**: Full-text search across titles, descriptions, tags
- **Tags**: Filter videos by tags
- **Admin Panel**: Publish/remove videos, feature videos, stats dashboard
- **OAuth Login**: Google & GitHub authentication
- **Real-time**: WebSocket live updates
- **Dark Theme**: Sleek dark UI with red accent
- **Responsive**: Mobile-first design

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8000**

## OAuth Setup

### Google
1. [Google Cloud Console](https://console.cloud.google.com/) → Create Project
2. Enable Google+ API → Credentials → OAuth 2.0 Client ID
3. Redirect URI: `http://localhost:8000/auth/google/callback`

### GitHub
1. [GitHub Settings](https://github.com/settings/developers) → New OAuth App
2. Callback URL: `http://localhost:8000/auth/github/callback`

### Configure
```bash
cp .env.example .env
# Edit .env with your OAuth credentials
```

## Tech Stack

- **Backend**: FastAPI + Uvicorn + SQLite
- **Frontend**: Vanilla JS + Custom CSS (dark theme)
- **Auth**: Authlib OAuth2 (Google, GitHub)
- **Templates**: Jinja2
- **Video**: HTML5 `<video>` with Range streaming
- **Thumbnails**: ffmpeg auto-generation (with SVG fallback)

## Project Structure

```
├── main.py              # FastAPI backend
├── templates/           # Jinja2 HTML templates
├── static/              # CSS & JavaScript
├── uploads/             # Video files (auto-created)
│   └── thumbnails/      # Video thumbnails
├── hellsy.db            # SQLite database (auto-created)
└── requirements.txt     # Python dependencies
```

## License

MIT
