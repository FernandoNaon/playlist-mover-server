# Migrate Beats - Backend API

Flask API server for migrating playlists from Spotify to Tidal.

## Local Development with Docker

### Prerequisites
- Docker & Docker Compose installed
- Spotify Developer credentials

### Quick Start

1. **Copy environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Add your Spotify credentials to `.env`:**
   ```
   SPOTIPY_CLIENT_ID=your_spotify_client_id
   SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
   ```

3. **Start the services:**
   ```bash
   docker-compose up -d
   ```

   This starts:
   - PostgreSQL database on port 5432
   - Flask API server on port 5000 (with hot reload)

4. **Check logs:**
   ```bash
   docker-compose logs -f api
   ```

5. **Stop services:**
   ```bash
   docker-compose down
   ```

### Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/db/health` | GET | Check database health |
| `/db/stats` | GET | Get user/migration stats |
| `/user/me` | POST | Get current user info |
| `/user/history` | POST | Get migration history |
| `/login` | GET | Start Spotify OAuth |
| `/fetch_playlists` | POST | Get Spotify playlists |
| `/liked_songs` | POST | Get liked songs |
| `/migrate_tracks` | POST | Migrate tracks to Tidal |
| `/migrate_playlist` | POST | Migrate playlist to Tidal |

### Database Commands

```bash
# Initialize database (auto-runs on startup)
docker-compose exec api flask init-db

# Create seed data
docker-compose exec api flask seed-db

# Access PostgreSQL directly
docker-compose exec db psql -U migrate_beats
```

### Development without Docker

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up PostgreSQL or use SQLite (default fallback):
   ```bash
   # For SQLite (no setup needed)
   unset DATABASE_URL

   # For local PostgreSQL
   export DATABASE_URL=postgresql://user:pass@localhost:5432/migrate_beats
   ```

4. Run the server:
   ```bash
   flask run --reload
   ```

## Railway Deployment

1. Push to GitHub
2. Connect repo to Railway
3. Add PostgreSQL addon (Railway → New → Database → PostgreSQL)
4. Add environment variables in Railway dashboard
5. Railway auto-deploys on push

### Required Environment Variables for Railway:
- `SPOTIPY_CLIENT_ID`
- `SPOTIPY_CLIENT_SECRET`
- `SPOTIPY_REDIRECT_URI` (your Railway URL + `/callback`)
- `FRONTEND_URL` (your frontend Railway URL)
- `FRONTEND_REDIRECT` (your frontend URL + `/callback`)
- `FLASK_SECRET_KEY`
- `DATABASE_URL` (auto-set by Railway PostgreSQL addon)

## Database Schema

```
users                  # Core user data
├── user_identities    # Spotify/Tidal provider links
├── auth_tokens        # OAuth tokens (encrypted)
├── user_sessions      # Login sessions
├── user_activities    # Activity log
├── spotify_cache      # JSON cache for Spotify data
├── migrations         # Migration history
└── api_usage          # Rate limiting counters
```

## Rate Limits

| Action | Free Tier | Premium |
|--------|-----------|---------|
| Migrations/day | 50 | Unlimited |
| Tracks/migration | 500 | Unlimited |

Configure via environment variables:
```
RATE_LIMIT_MIGRATIONS=50
```
