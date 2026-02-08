from flask import Flask, request, redirect, session, jsonify
from flask_cors import CORS
from flask_migrate import Migrate
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import os
import tidalapi
from datetime import datetime

load_dotenv()

# Import database and config
from config import get_config
from models import (
    db, User, UserIdentity, AuthToken, UserSession, UserActivity,
    SpotifyCache, Migration, ApiUsage,
    get_or_create_user, log_activity, check_rate_limit, increment_usage
)

app = Flask(__name__)

# Load configuration
app.config.from_object(get_config())

# Initialize database
db.init_app(app)
migrate = Migrate(app, db)

# CORS Configuration - support both local and production
FRONTEND_URL = app.config.get('FRONTEND_URL', 'http://localhost:5173')
CORS(app,
     supports_credentials=True,
     origins=[FRONTEND_URL, "http://localhost:5173", "http://127.0.0.1:5173"],
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "OPTIONS"])

# Spotify Configuration
SPOTIPY_CLIENT_ID = app.config.get('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = app.config.get('SPOTIPY_CLIENT_SECRET')
SPOTIPY_REDIRECT_URI = app.config.get('SPOTIPY_REDIRECT_URI', 'http://127.0.0.1:5000/callback')

FRONTEND_REDIRECT = app.config.get('FRONTEND_REDIRECT', 'http://localhost:5173/callback')

# Extended scopes for dashboard insights
SCOPE = "playlist-read-private playlist-read-collaborative user-top-read user-read-recently-played user-library-read user-read-private user-follow-read"

# Store Tidal sessions in memory (in production, use Redis or database)
tidal_sessions = {}

# Store user contexts (spotify_id -> user_id mapping for current session)
user_contexts = {}


def get_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE
    )


def get_spotify_client(code):
    """Helper to get authenticated Spotify client from auth code."""
    sp_oauth = get_spotify_oauth()
    token_info = sp_oauth.get_access_token(code, as_dict=True)
    return spotipy.Spotify(auth=token_info['access_token']), token_info


def get_user_from_code(code):
    """Get or create user from Spotify auth code. Returns (user, sp_client)."""
    try:
        sp, token_info = get_spotify_client(code)
        spotify_user = sp.current_user()

        user, is_new = get_or_create_user(
            spotify_user_id=spotify_user['id'],
            email=spotify_user.get('email'),
            display_name=spotify_user.get('display_name', spotify_user['id']),
            avatar_url=spotify_user['images'][0]['url'] if spotify_user.get('images') else None
        )

        # Cache user context
        user_contexts[spotify_user['id']] = user.id

        if is_new:
            log_activity(user.id, 'signup', {'provider': 'spotify'})
        else:
            log_activity(user.id, 'login', {'provider': 'spotify'})

        return user, sp
    except Exception as e:
        print(f"Error getting user from code: {e}")
        return None, None


# ==================== DATABASE ENDPOINTS ====================

@app.route("/db/health", methods=["GET"])
def db_health():
    """Check database health."""
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify({"status": "healthy", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.route("/db/stats", methods=["GET"])
def db_stats():
    """Get database statistics (admin only in production)."""
    try:
        stats = {
            "users": User.query.count(),
            "migrations": Migration.query.count(),
            "activities": UserActivity.query.count(),
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/user/me", methods=["POST"])
def get_current_user():
    """Get current user info with usage stats."""
    data = request.get_json()
    code = data.get("code")

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        user, sp = get_user_from_code(code)
        if not user:
            return jsonify({"error": "Could not authenticate user"}), 401

        # Get usage stats
        from datetime import date
        today = date.today()

        migrations_today = ApiUsage.query.filter_by(
            user_id=user.id,
            action='migration',
            window_start=today
        ).first()

        return jsonify({
            **user.to_dict(),
            "usage": {
                "migrations_today": migrations_today.count if migrations_today else 0,
                "migrations_limit": app.config.get('RATE_LIMIT_MIGRATIONS', 50),
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/user/history", methods=["POST"])
def get_user_history():
    """Get user's migration history."""
    data = request.get_json()
    code = data.get("code")
    limit = data.get("limit", 20)

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        user, _ = get_user_from_code(code)
        if not user:
            return jsonify({"error": "Could not authenticate user"}), 401

        migrations = Migration.query.filter_by(user_id=user.id)\
            .order_by(Migration.created_at.desc())\
            .limit(limit)\
            .all()

        return jsonify([m.to_dict() for m in migrations])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== SPOTIFY AUTH ====================

@app.route("/login", methods=["GET"])
def login():
    auth_url = get_spotify_oauth().get_authorize_url()
    return jsonify({"auth_url": auth_url})


@app.route("/callback")
def spotify_callback_redirect():
    code = request.args.get("code")
    return redirect(f"{FRONTEND_REDIRECT}?code={code}")


# ==================== SPOTIFY PLAYLISTS ====================

@app.route("/fetch_playlists", methods=["POST"])
def fetch_playlists():
    data = request.get_json()
    code = data.get("code")

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)

        playlists = []
        limit = 50
        offset = 0

        while True:
            response = sp.current_user_playlists(limit=limit, offset=offset)
            items = response.get("items", [])
            playlists.extend([
                {
                    "id": p["id"],
                    "name": p["name"],
                    "tracks_total": p["tracks"]["total"],
                    "image": p["images"][0]["url"] if p.get("images") else None,
                    "owner": p["owner"]["display_name"]
                } for p in items
            ])
            if response.get("next"):
                offset += limit
            else:
                break

        return jsonify(playlists)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/playlist_tracks", methods=["POST"])
def playlist_tracks():
    data = request.get_json()
    playlist_id = data.get("playlist_id")
    code = data.get("code")

    if not playlist_id:
        return jsonify({"error": "playlist_id is required"}), 400
    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)

        tracks = []
        offset = 0
        limit = 100

        while True:
            results = sp.playlist_tracks(playlist_id, offset=offset, limit=limit)
            for item in results["items"]:
                track = item.get("track")
                if track:
                    tracks.append({
                        "id": track.get("id"),
                        "name": track["name"],
                        "artist": ", ".join([artist["name"] for artist in track["artists"]]),
                        "artists": [artist["name"] for artist in track["artists"]],
                        "album": track["album"]["name"],
                        "duration_ms": track["duration_ms"],
                        "image": track["album"]["images"][0]["url"] if track["album"].get("images") else None
                    })

            if results.get("next"):
                offset += limit
            else:
                break

        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== SPOTIFY INSIGHTS/DASHBOARD ====================

@app.route("/user_profile", methods=["POST"])
def user_profile():
    data = request.get_json()
    code = data.get("code")

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)
        user = sp.current_user()

        return jsonify({
            "id": user["id"],
            "display_name": user.get("display_name", user["id"]),
            "email": user.get("email"),
            "image": user["images"][0]["url"] if user.get("images") else None,
            "country": user.get("country"),
            "product": user.get("product"),  # premium, free, etc.
            "followers": user.get("followers", {}).get("total", 0)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/top_tracks", methods=["POST"])
def top_tracks():
    data = request.get_json()
    code = data.get("code")
    time_range = data.get("time_range", "medium_term")  # short_term, medium_term, long_term
    limit = data.get("limit", 20)

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)
        results = sp.current_user_top_tracks(limit=limit, time_range=time_range)

        tracks = []
        for track in results["items"]:
            tracks.append({
                "id": track["id"],
                "name": track["name"],
                "artist": ", ".join([artist["name"] for artist in track["artists"]]),
                "album": track["album"]["name"],
                "image": track["album"]["images"][0]["url"] if track["album"].get("images") else None,
                "popularity": track.get("popularity", 0)
            })

        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/top_artists", methods=["POST"])
def top_artists():
    data = request.get_json()
    code = data.get("code")
    time_range = data.get("time_range", "medium_term")
    limit = data.get("limit", 20)

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)
        results = sp.current_user_top_artists(limit=limit, time_range=time_range)

        artists = []
        for artist in results["items"]:
            artists.append({
                "id": artist["id"],
                "name": artist["name"],
                "genres": artist.get("genres", []),
                "image": artist["images"][0]["url"] if artist.get("images") else None,
                "popularity": artist.get("popularity", 0),
                "followers": artist.get("followers", {}).get("total", 0)
            })

        return jsonify(artists)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/recently_played", methods=["POST"])
def recently_played():
    data = request.get_json()
    code = data.get("code")
    limit = data.get("limit", 20)

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)
        results = sp.current_user_recently_played(limit=limit)

        tracks = []
        for item in results["items"]:
            track = item["track"]
            tracks.append({
                "id": track["id"],
                "name": track["name"],
                "artist": ", ".join([artist["name"] for artist in track["artists"]]),
                "album": track["album"]["name"],
                "image": track["album"]["images"][0]["url"] if track["album"].get("images") else None,
                "played_at": item["played_at"]
            })

        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/liked_songs", methods=["POST", "OPTIONS"])
def liked_songs():
    if request.method == "OPTIONS":
        return "", 200
    """Get user's liked/saved songs from Spotify."""
    data = request.get_json()
    code = data.get("code")
    limit = data.get("limit", 50)
    offset = data.get("offset", 0)

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)
        results = sp.current_user_saved_tracks(limit=limit, offset=offset)

        tracks = []
        for item in results["items"]:
            track = item["track"]
            tracks.append({
                "id": track["id"],
                "name": track["name"],
                "artist": ", ".join([artist["name"] for artist in track["artists"]]),
                "artists": [artist["name"] for artist in track["artists"]],
                "album": track["album"]["name"],
                "duration_ms": track["duration_ms"],
                "image": track["album"]["images"][0]["url"] if track["album"].get("images") else None,
                "added_at": item["added_at"]
            })

        return jsonify({
            "tracks": tracks,
            "total": results.get("total", 0),
            "limit": limit,
            "offset": offset,
            "has_more": results.get("next") is not None
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/library_stats", methods=["POST"])
def library_stats():
    data = request.get_json()
    code = data.get("code")

    if not code:
        return jsonify({"error": "Authorization code required"}), 400

    try:
        sp, _ = get_spotify_client(code)

        # Get saved tracks count
        try:
            saved_tracks = sp.current_user_saved_tracks(limit=1)
            total_saved_tracks = saved_tracks.get("total", 0)
        except:
            total_saved_tracks = 0

        # Get playlists count
        try:
            playlists = sp.current_user_playlists(limit=1)
            total_playlists = playlists.get("total", 0)
        except:
            total_playlists = 0

        # Get saved albums count
        try:
            saved_albums = sp.current_user_saved_albums(limit=1)
            total_saved_albums = saved_albums.get("total", 0)
        except:
            total_saved_albums = 0

        # Get followed artists count
        try:
            followed = sp.current_user_followed_artists(limit=1)
            total_followed_artists = followed.get("artists", {}).get("total", 0)
        except:
            total_followed_artists = 0

        return jsonify({
            "saved_tracks": total_saved_tracks,
            "playlists": total_playlists,
            "saved_albums": total_saved_albums,
            "followed_artists": total_followed_artists
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== TIDAL INTEGRATION ====================

@app.route("/tidal/login", methods=["POST"])
def tidal_login():
    """Start Tidal OAuth login flow using device authorization."""
    try:
        tidal_session = tidalapi.Session()

        print(f"[Tidal] Starting OAuth login...")

        login, future = tidal_session.login_oauth()

        import uuid
        session_id = str(uuid.uuid4())

        tidal_sessions[session_id] = {
            "session": tidal_session,
            "future": future,
            "login": login
        }

        verification_url = login.verification_uri_complete
        if not verification_url.startswith("http"):
            verification_url = f"https://{verification_url}"

        print(f"[Tidal] Login URL: {verification_url}")
        print(f"[Tidal] User code: {login.user_code}")

        return jsonify({
            "verification_uri": verification_url,
            "user_code": login.user_code,
            "session_id": session_id,
            "expires_in": login.expires_in
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/tidal/check_auth", methods=["POST"])
def tidal_check_auth():
    """Check if Tidal authorization has been completed."""
    data = request.get_json()
    session_id = data.get("session_id")

    if not session_id or session_id not in tidal_sessions:
        print(f"[Tidal] Invalid session: {session_id}")
        return jsonify({"authenticated": False, "error": "Invalid session"}), 200

    try:
        tidal_data = tidal_sessions[session_id]
        tidal_session = tidal_data["session"]
        future = tidal_data["future"]

        print(f"[Tidal] Checking auth - future.done(): {future.done()}")

        if future.done():
            try:
                future.result()
                user = tidal_session.user
                print(f"[Tidal] Auth successful! User: {user}")
                return jsonify({
                    "authenticated": True,
                    "user": {
                        "id": str(user.id) if user else "unknown",
                        "name": getattr(user, 'name', None) or getattr(user, 'first_name', None) or str(user.id) if user else "Tidal User"
                    }
                })
            except Exception as e:
                print(f"[Tidal] Future result error: {e}")
                return jsonify({"authenticated": False, "error": str(e)})
        else:
            print(f"[Tidal] Still waiting for user to authorize...")
            return jsonify({"authenticated": False})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "authenticated": False}), 500


@app.route("/tidal/playlists", methods=["POST"])
def tidal_playlists():
    """Get user's Tidal playlists."""
    data = request.get_json()
    session_id = data.get("session_id")

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]
        user_playlists = tidal_session.user.playlists()

        playlists = []
        for p in user_playlists:
            image_url = None
            try:
                if hasattr(p, 'image') and callable(p.image):
                    image_url = p.image(320)
                elif hasattr(p, 'picture') and p.picture:
                    image_url = f"https://resources.tidal.com/images/{p.picture.replace('-', '/')}/320x320.jpg"
                elif hasattr(p, 'square_picture') and p.square_picture:
                    image_url = f"https://resources.tidal.com/images/{p.square_picture.replace('-', '/')}/320x320.jpg"
            except:
                pass

            playlists.append({
                "id": str(p.id),
                "name": p.name,
                "tracks_total": p.num_tracks if hasattr(p, 'num_tracks') else 0,
                "image": image_url,
                "description": p.description if hasattr(p, 'description') else ""
            })

        return jsonify(playlists)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/tidal/playlist_tracks", methods=["POST"])
def tidal_playlist_tracks():
    """Get tracks from a Tidal playlist."""
    data = request.get_json()
    session_id = data.get("session_id")
    playlist_id = data.get("playlist_id")

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400
    if not playlist_id:
        return jsonify({"error": "Playlist ID required"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]
        playlist = tidal_session.playlist(playlist_id)
        playlist_tracks = playlist.tracks()

        tracks = []
        for track in playlist_tracks:
            image_url = None
            try:
                if track.album:
                    if hasattr(track.album, 'image') and callable(track.album.image):
                        image_url = track.album.image(320)
                    elif hasattr(track.album, 'cover') and track.album.cover:
                        image_url = f"https://resources.tidal.com/images/{track.album.cover.replace('-', '/')}/320x320.jpg"
            except:
                pass

            tracks.append({
                "id": str(track.id),
                "name": track.name,
                "artist": track.artist.name if track.artist else "Unknown",
                "album": track.album.name if track.album else "Unknown",
                "duration_ms": track.duration * 1000 if hasattr(track, 'duration') else 0,
                "image": image_url
            })

        return jsonify(tracks)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/tidal/delete_playlist", methods=["POST"])
def tidal_delete_playlist():
    """Delete a Tidal playlist."""
    data = request.get_json()
    session_id = data.get("session_id")
    playlist_id = data.get("playlist_id")

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400
    if not playlist_id:
        return jsonify({"error": "Playlist ID required"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]
        playlist = tidal_session.playlist(playlist_id)
        playlist.delete()

        return jsonify({"success": True, "message": "Playlist deleted successfully"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/tidal/merge_playlists", methods=["POST"])
def tidal_merge_playlists():
    """Merge two Tidal playlists into one."""
    data = request.get_json()
    session_id = data.get("session_id")
    source_playlist_id = data.get("source_playlist_id")
    target_playlist_id = data.get("target_playlist_id")

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400
    if not source_playlist_id or not target_playlist_id:
        return jsonify({"error": "Both source and target playlist IDs required"}), 400
    if source_playlist_id == target_playlist_id:
        return jsonify({"error": "Cannot merge a playlist with itself"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]

        source_playlist = tidal_session.playlist(source_playlist_id)
        target_playlist = tidal_session.playlist(target_playlist_id)

        source_tracks = source_playlist.tracks()
        target_tracks = target_playlist.tracks()
        existing_track_ids = {str(t.id) for t in target_tracks}

        tracks_to_add = [t for t in source_tracks if str(t.id) not in existing_track_ids]

        added_count = 0
        for track in tracks_to_add:
            try:
                target_playlist.add([track.id])
                added_count += 1
            except Exception as e:
                print(f"Failed to add track {track.id}: {e}")

        source_playlist.delete()

        return jsonify({
            "success": True,
            "message": f"Merged {added_count} tracks into target playlist",
            "tracks_added": added_count,
            "tracks_skipped": len(source_tracks) - added_count,
            "source_deleted": True
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/tidal/search", methods=["POST"])
def tidal_search():
    """Search for tracks on Tidal."""
    data = request.get_json()
    session_id = data.get("session_id")
    query = data.get("query")

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400
    if not query:
        return jsonify({"error": "Query required"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]
        results = tidal_session.search(query, models=[tidalapi.media.Track], limit=5)

        tracks = []
        for track in results.get("tracks", []):
            tracks.append({
                "id": track.id,
                "name": track.name,
                "artist": track.artist.name if track.artist else "Unknown",
                "album": track.album.name if track.album else "Unknown"
            })

        return jsonify(tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tidal/create_playlist", methods=["POST"])
def tidal_create_playlist():
    """Create a new playlist on Tidal and add tracks."""
    data = request.get_json()
    session_id = data.get("session_id")
    name = data.get("name")
    description = data.get("description", "")
    track_ids = data.get("track_ids", [])

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400
    if not name:
        return jsonify({"error": "Playlist name required"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]
        playlist = tidal_session.user.create_playlist(name, description)

        if track_ids:
            playlist.add(track_ids)

        return jsonify({
            "success": True,
            "playlist_id": playlist.id,
            "name": playlist.name,
            "tracks_added": len(track_ids)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/migrate_tracks", methods=["POST", "OPTIONS"])
def migrate_tracks():
    if request.method == "OPTIONS":
        return "", 200
    """Migrate selected tracks from Spotify to Tidal."""
    data = request.get_json()
    spotify_code = data.get("spotify_code")
    tidal_session_id = data.get("tidal_session_id")
    tracks = data.get("tracks", [])
    playlist_name = data.get("playlist_name", "Migrated Songs")
    target_playlist_id = data.get("target_playlist_id")
    add_to_favorites = data.get("add_to_favorites", False)

    if not spotify_code:
        return jsonify({"error": "Spotify authorization required"}), 400
    if not tidal_session_id or tidal_session_id not in tidal_sessions:
        return jsonify({"error": "Tidal authorization required"}), 400
    if not tracks:
        return jsonify({"error": "No tracks provided"}), 400

    # Get user for tracking
    user = None
    try:
        user, _ = get_user_from_code(spotify_code)
        if user:
            # Check rate limit
            allowed, remaining = check_rate_limit(
                user.id, 'migration',
                app.config.get('RATE_LIMIT_MIGRATIONS', 50)
            )
            if not allowed:
                return jsonify({
                    "error": "Daily migration limit reached",
                    "limit": app.config.get('RATE_LIMIT_MIGRATIONS', 50)
                }), 429
    except Exception as e:
        print(f"Error getting user for migration tracking: {e}")

    try:
        tidal_session = tidal_sessions[tidal_session_id]["session"]

        # Search for tracks on Tidal and collect IDs
        tidal_track_ids = []
        not_found = []

        for track in tracks:
            query = f"{track['name']} {track['artist']}"
            try:
                results = tidal_session.search(query, models=[tidalapi.media.Track], limit=1)
                found_tracks = results.get("tracks", [])
                if found_tracks:
                    tidal_track_ids.append(found_tracks[0].id)
                else:
                    not_found.append(track)
            except:
                not_found.append(track)

        result_playlist_name = ""
        result_playlist_id = None
        migration_type = "custom"

        if add_to_favorites:
            for track_id in tidal_track_ids:
                try:
                    tidal_session.user.favorites.add_track(track_id)
                except Exception as e:
                    print(f"Failed to add track {track_id} to favorites: {e}")
            result_playlist_name = "Favorites"
            migration_type = "favorites"
        elif target_playlist_id:
            playlist = tidal_session.playlist(target_playlist_id)
            if tidal_track_ids:
                playlist.add(tidal_track_ids)
            result_playlist_name = playlist.name
            result_playlist_id = playlist.id
            migration_type = "existing_playlist"
        else:
            description = f"Migrated from Spotify"
            playlist = tidal_session.user.create_playlist(playlist_name, description)
            if tidal_track_ids:
                playlist.add(tidal_track_ids)
            result_playlist_name = playlist.name
            result_playlist_id = playlist.id
            migration_type = "new_playlist"

        # Log migration to database
        if user:
            try:
                migration = Migration(
                    user_id=user.id,
                    source_provider='spotify',
                    target_provider='tidal',
                    target_playlist_id=result_playlist_id,
                    target_playlist_name=result_playlist_name,
                    migration_type=migration_type,
                    total_tracks=len(tracks),
                    migrated_tracks=len(tidal_track_ids),
                    skipped_tracks=len(not_found),
                    not_found_tracks=not_found[:10],
                    status='completed',
                    completed_at=datetime.utcnow()
                )
                db.session.add(migration)
                db.session.commit()

                # Update usage counter
                increment_usage(user.id, 'migration', len(tracks))

                log_activity(user.id, 'migration', {
                    'type': migration_type,
                    'total': len(tracks),
                    'migrated': len(tidal_track_ids),
                    'not_found': len(not_found)
                })
            except Exception as e:
                print(f"Error logging migration: {e}")
                db.session.rollback()

        return jsonify({
            "success": True,
            "playlist_id": result_playlist_id,
            "playlist_name": result_playlist_name,
            "total_tracks": len(tracks),
            "migrated": len(tidal_track_ids),
            "not_found": len(not_found),
            "not_found_tracks": not_found[:10]
        })
    except Exception as e:
        # Log failed migration
        if user:
            try:
                migration = Migration(
                    user_id=user.id,
                    source_provider='spotify',
                    target_provider='tidal',
                    total_tracks=len(tracks),
                    status='failed',
                    error_message=str(e)
                )
                db.session.add(migration)
                db.session.commit()
            except:
                db.session.rollback()

        return jsonify({"error": str(e)}), 500


@app.route("/migrate_playlist", methods=["POST"])
def migrate_playlist():
    """Migrate a playlist from Spotify to Tidal."""
    data = request.get_json()
    spotify_code = data.get("spotify_code")
    tidal_session_id = data.get("tidal_session_id")
    playlist_id = data.get("playlist_id")
    playlist_name = data.get("playlist_name")

    if not spotify_code:
        return jsonify({"error": "Spotify authorization required"}), 400
    if not tidal_session_id or tidal_session_id not in tidal_sessions:
        return jsonify({"error": "Tidal authorization required"}), 400
    if not playlist_id:
        return jsonify({"error": "Playlist ID required"}), 400

    # Get user for tracking
    user = None
    try:
        user, _ = get_user_from_code(spotify_code)
        if user:
            allowed, remaining = check_rate_limit(
                user.id, 'migration',
                app.config.get('RATE_LIMIT_MIGRATIONS', 50)
            )
            if not allowed:
                return jsonify({
                    "error": "Daily migration limit reached",
                    "limit": app.config.get('RATE_LIMIT_MIGRATIONS', 50)
                }), 429
    except Exception as e:
        print(f"Error getting user for migration tracking: {e}")

    try:
        sp, _ = get_spotify_client(spotify_code)
        tidal_session = tidal_sessions[tidal_session_id]["session"]

        # Fetch all tracks from Spotify playlist
        spotify_tracks = []
        offset = 0
        while True:
            results = sp.playlist_tracks(playlist_id, offset=offset, limit=100)
            for item in results["items"]:
                track = item.get("track")
                if track:
                    spotify_tracks.append({
                        "name": track["name"],
                        "artist": track["artists"][0]["name"] if track["artists"] else "",
                        "album": track["album"]["name"]
                    })
            if results.get("next"):
                offset += 100
            else:
                break

        # Search for tracks on Tidal and collect IDs
        tidal_track_ids = []
        not_found = []

        for track in spotify_tracks:
            query = f"{track['name']} {track['artist']}"
            try:
                results = tidal_session.search(query, models=[tidalapi.media.Track], limit=1)
                found = results.get("tracks", [])
                if found:
                    tidal_track_ids.append(found[0].id)
                else:
                    not_found.append(track)
            except:
                not_found.append(track)

        # Create playlist on Tidal
        description = f"Migrated from Spotify"
        playlist = tidal_session.user.create_playlist(playlist_name or "Migrated Playlist", description)

        if tidal_track_ids:
            playlist.add(tidal_track_ids)

        # Log migration to database
        if user:
            try:
                migration = Migration(
                    user_id=user.id,
                    source_provider='spotify',
                    target_provider='tidal',
                    source_playlist_id=playlist_id,
                    source_playlist_name=playlist_name,
                    target_playlist_id=str(playlist.id),
                    target_playlist_name=playlist.name,
                    migration_type='playlist',
                    total_tracks=len(spotify_tracks),
                    migrated_tracks=len(tidal_track_ids),
                    skipped_tracks=len(not_found),
                    not_found_tracks=not_found[:10],
                    status='completed',
                    completed_at=datetime.utcnow()
                )
                db.session.add(migration)
                db.session.commit()

                increment_usage(user.id, 'migration', len(spotify_tracks))

                log_activity(user.id, 'migration', {
                    'type': 'playlist',
                    'playlist_name': playlist_name,
                    'total': len(spotify_tracks),
                    'migrated': len(tidal_track_ids),
                    'not_found': len(not_found)
                })
            except Exception as e:
                print(f"Error logging migration: {e}")
                db.session.rollback()

        return jsonify({
            "success": True,
            "playlist_id": playlist.id,
            "playlist_name": playlist.name,
            "total_tracks": len(spotify_tracks),
            "migrated": len(tidal_track_ids),
            "not_found": len(not_found),
            "not_found_tracks": not_found[:10]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== DATABASE INITIALIZATION ====================

@app.cli.command("init-db")
def init_db():
    """Initialize the database."""
    db.create_all()
    print("Database tables created.")


@app.cli.command("seed-db")
def seed_db():
    """Seed the database with test data."""
    # Create a test user
    test_user = User(
        email="test@example.com",
        display_name="Test User",
        tier="free"
    )
    db.session.add(test_user)
    db.session.commit()
    print(f"Created test user: {test_user.id}")


# Create tables on startup if they don't exist
with app.app_context():
    try:
        db.create_all()
        print("[DB] Database tables ready")
    except Exception as e:
        print(f"[DB] Warning: Could not create tables: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
