# Catio.cam 🐱

Full-featured livestreaming hub for catio cameras with OAuth, admin panel, and real-time updates.

## Features

✅ **OAuth Login** - Sign in with Google or GitHub  
✅ **Admin Panel** - Approve/reject streams, set featured stream  
✅ **WebSocket Updates** - Real-time notifications and stats  
✅ **SQLite Database** - Persistent storage  
✅ **Responsive Design** - Mobile-friendly interface  
✅ **User Dashboard** - Manage your streams  

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure OAuth (Optional for Testing)

Copy `.env.example` to `.env` and add your OAuth credentials:

```bash
cp .env.example .env
```

**To get OAuth credentials:**

- **Google**: https://console.cloud.google.com/apis/credentials
  - Create OAuth 2.0 Client ID
  - Add redirect URI: `http://localhost:8000/auth/google/callback`
  
- **GitHub**: https://github.com/settings/developers
  - Create OAuth App
  - Add callback URL: `http://localhost:8000/auth/github/callback`

### 3. Run the Server

```bash
python main.py
```

### 4. Open Browser

http://localhost:8000

## Test Without OAuth

The site works without OAuth configuration - you just won't be able to log in. To test:

1. Visit homepage to see featured stream
2. Check `/api/stats` for site statistics
3. Try `/health` for health check

## Admin Access

The first user (`admin@catio.cam`) is automatically created with admin privileges. To make yourself admin:

```sql
sqlite3 catio.db
UPDATE users SET is_admin = 1 WHERE email = 'your-email@gmail.com';
```

## API Endpoints

- `GET /` - Homepage with streams
- `GET /login` - Login page
- `GET /add` - Add stream form (requires login)
- `GET /my-streams` - User's streams (requires login)
- `GET /admin` - Admin panel (requires admin)
- `GET /api/stats` - Site statistics (JSON)
- `GET /health` - Health check
- `WebSocket /ws` - Real-time updates

## Tech Stack

- **Backend**: FastAPI + Uvicorn
- **Auth**: Authlib (OAuth 2.0)
- **Database**: SQLite
- **Templates**: Jinja2
- **Real-time**: WebSocket
- **Frontend**: Custom CSS

## Project Structure

```
catio-cam/
├── main.py              # FastAPI app with all routes
├── requirements.txt     # Python dependencies
├── .env.example         # OAuth config template
├── templates/          # HTML templates
│   ├── base.html       # Base layout with WebSocket
│   ├── home.html       # Homepage
│   ├── login.html      # OAuth login
│   ├── add_stream.html # Add stream form
│   ├── my_streams.html # User dashboard
│   └── admin.html      # Admin panel
├── static/
│   └── style.css       # Full styling
└── catio.db            # SQLite database (auto-created)
```

## Deployment

### Apache + mod_wsgi

1. Install dependencies in virtualenv
2. Configure OAuth with production URLs
3. Point Apache to WSGI app
4. Set proper file permissions for `catio.db`

### Environment Variables

Set these in production:

```bash
export GOOGLE_CLIENT_ID=your-production-client-id
export GOOGLE_CLIENT_SECRET=your-production-secret
export GITHUB_CLIENT_ID=your-github-client-id
export GITHUB_CLIENT_SECRET=your-github-secret
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

## Next Steps

- Deploy to production server
- Add email notifications for stream approvals
- Add stream analytics/viewer tracking
- Add chat integration
- Add stream health monitoring

## License

MIT
