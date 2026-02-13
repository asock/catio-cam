# Catio.cam 🐱

Livestreaming hub for catio cameras - watch catios live around the world!

## Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the server:
```bash
python main.py
```

3. Open browser: http://localhost:8000

## Test the Site

1. **Homepage** - Shows featured stream + community streams
2. **Add stream** - Click "Add Your Catio" and submit
3. **Persistence** - Restart server, streams remain (stored in catio.db)
4. **Health check** - Visit /health to see stream count

## Features

✅ Featured catio stream (large display)
✅ Community streams grid
✅ Add new streams (Twitch/YouTube)
✅ SQLite database persistence
✅ Responsive mobile design
✅ Health check endpoint

## Next Steps

- Add Google/GitHub OAuth login
- Add admin approval workflow
- Add WebSocket live updates
- Deploy to production

## Tech Stack

- FastAPI + Uvicorn
- Jinja2 templates
- SQLite database
- Custom CSS (no framework)

## Project Structure

```
catio-cam/
├── main.py              # FastAPI backend
├── requirements.txt     # Python dependencies
├── templates/          # HTML templates
│   ├── base.html
│   ├── home.html
│   └── add_stream.html
├── static/
│   └── style.css       # Custom CSS
└── catio.db            # SQLite database (auto-created)
```

## License

MIT
