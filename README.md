# Catio.cam 🐱

Full-featured livestreaming hub for catio cameras - OAuth, admin panel, favorites, tags, search, and real-time updates!

## Features

✅ **Auth**: Google & GitHub OAuth login
✅ **Streams**: Add/manage Twitch/YouTube catio streams  
✅ **Admin**: Approve/reject streams, set featured stream  
✅ **Discovery**: Search, tag filtering, featured showcase  
✅ **Community**: Favorites, comments, viewer counts  
✅ **Real-time**: WebSocket live updates  
✅ **Responsive**: Mobile-friendly design  

## Quick Start

1. **Install**:
```bash
pip install -r requirements.txt
```

2. **Configure OAuth** (see below)

3. **Run**:
```bash
python main.py
```

4. Open: **http://localhost:8000**

## OAuth Setup

### Google
1. [Google Cloud Console](https://console.cloud.google.com/) → Create Project
2. Enable Google+ API
3. Credentials → OAuth 2.0 Client ID
4. Redirect URI: `http://localhost:8000/auth/google/callback`

### GitHub  
1. [GitHub Settings](https://github.com/settings/developers) → New OAuth App
2. Callback URL: `http://localhost:8000/auth/github/callback`

### Configure
Copy `.env.example` to `.env` and add credentials:
```bash
cp .env.example .env
# Edit .env with your OAuth credentials
```

## Make Yourself Admin

```bash
sqlite3 catio.db
UPDATE users SET is_admin = 1 WHERE email = 'your@email.com';
.quit
```

## Tech Stack

- FastAPI + Uvicorn
- SQLite database
- Authlib OAuth2
- Jinja2 templates
- WebSockets
- Vanilla JS + Custom CSS

## Project Structure

```
├── main.py              # Backend (OAuth, admin, WebSocket)
├── templates/           # HTML templates
├── static/             # CSS & JavaScript
├── catio.db            # SQLite (auto-created)
└── requirements.txt    # Dependencies
```

## License

MIT - Made with ❤️ for cats everywhere 🐱
