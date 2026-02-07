from flask import Flask, request, redirect, session, jsonify
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import os
import tidalapi

load_dotenv()

app = Flask(__name__)

# CORS Configuration - support both local and production
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")
CORS(app,
     supports_credentials=True,
     origins=[FRONTEND_URL, "http://localhost:5173", "http://127.0.0.1:5173"],
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "OPTIONS"])
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecretkey")

# Spotify Configuration
SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:5000/callback")

# Tidal Configuration
TIDAL_CLIENT_ID = os.environ.get("TIDAL_CLIENT_ID")
TIDAL_CLIENT_SECRET = os.environ.get("TIDAL_CLIENT_SECRET")

FRONTEND_REDIRECT = os.environ.get("FRONTEND_REDIRECT", "http://localhost:5173/callback")

# Extended scopes for dashboard insights
SCOPE = "playlist-read-private playlist-read-collaborative user-top-read user-read-recently-played user-library-read user-read-private user-follow-read"


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

# Store Tidal sessions in memory (in production, use Redis or database)
tidal_sessions = {}


@app.route("/tidal/login", methods=["POST"])
def tidal_login():
    """Start Tidal OAuth login flow using device authorization."""
    try:
        # Use default tidalapi session (library's built-in credentials)
        # Custom credentials cause 400 errors with Tidal's device auth flow
        # The tidalapi library has its own registered client ID
        tidal_session = tidalapi.Session()

        print(f"[Tidal] Starting OAuth login...")

        # Use login_oauth - this uses tidalapi's internal credentials
        login, future = tidal_session.login_oauth()

        # Generate a unique session ID
        import uuid
        session_id = str(uuid.uuid4())

        tidal_sessions[session_id] = {
            "session": tidal_session,
            "future": future,
            "login": login
        }

        # Fix URL - tidalapi returns URL without https:// prefix
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

        # Check if the future is done (user completed login)
        if future.done():
            try:
                future.result()  # This will raise if there was an error
                # Authorization successful
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
            # Get image URL - image() is a method in tidalapi
            image_url = None
            try:
                if hasattr(p, 'image') and callable(p.image):
                    image_url = p.image(320)  # Get 320x320 image
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
            # Get album image URL - image() is a method in tidalapi
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

        # Delete the playlist
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
    source_playlist_id = data.get("source_playlist_id")  # Playlist to merge FROM (will be deleted)
    target_playlist_id = data.get("target_playlist_id")  # Playlist to merge INTO (will keep)

    if not session_id or session_id not in tidal_sessions:
        return jsonify({"error": "Invalid session"}), 400
    if not source_playlist_id or not target_playlist_id:
        return jsonify({"error": "Both source and target playlist IDs required"}), 400
    if source_playlist_id == target_playlist_id:
        return jsonify({"error": "Cannot merge a playlist with itself"}), 400

    try:
        tidal_session = tidal_sessions[session_id]["session"]

        # Get both playlists
        source_playlist = tidal_session.playlist(source_playlist_id)
        target_playlist = tidal_session.playlist(target_playlist_id)

        # Get tracks from source playlist
        source_tracks = source_playlist.tracks()

        # Get existing track IDs in target to avoid duplicates
        target_tracks = target_playlist.tracks()
        existing_track_ids = {str(t.id) for t in target_tracks}

        # Filter out tracks that already exist in target
        tracks_to_add = [t for t in source_tracks if str(t.id) not in existing_track_ids]

        # Add tracks to target playlist
        added_count = 0
        for track in tracks_to_add:
            try:
                target_playlist.add([track.id])
                added_count += 1
            except Exception as e:
                print(f"Failed to add track {track.id}: {e}")

        # Delete the source playlist
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

        # Create playlist
        playlist = tidal_session.user.create_playlist(name, description)

        # Add tracks if provided
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
    tracks = data.get("tracks", [])  # List of {name, artist, album} objects
    playlist_name = data.get("playlist_name", "Migrated Songs")
    target_playlist_id = data.get("target_playlist_id")  # Existing playlist ID (optional)
    add_to_favorites = data.get("add_to_favorites", False)  # Add to Tidal favorites

    if not spotify_code:
        return jsonify({"error": "Spotify authorization required"}), 400
    if not tidal_session_id or tidal_session_id not in tidal_sessions:
        return jsonify({"error": "Tidal authorization required"}), 400
    if not tracks:
        return jsonify({"error": "No tracks provided"}), 400

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

        if add_to_favorites:
            # Add tracks to Tidal favorites/collection
            for track_id in tidal_track_ids:
                try:
                    tidal_session.user.favorites.add_track(track_id)
                except Exception as e:
                    print(f"Failed to add track {track_id} to favorites: {e}")
            result_playlist_name = "Favorites"
        elif target_playlist_id:
            # Add to existing playlist
            playlist = tidal_session.playlist(target_playlist_id)
            if tidal_track_ids:
                playlist.add(tidal_track_ids)
            result_playlist_name = playlist.name
            result_playlist_id = playlist.id
        else:
            # Create new playlist on Tidal
            description = f"Migrated from Spotify"
            playlist = tidal_session.user.create_playlist(playlist_name, description)
            if tidal_track_ids:
                playlist.add(tidal_track_ids)
            result_playlist_name = playlist.name
            result_playlist_id = playlist.id

        return jsonify({
            "success": True,
            "playlist_id": result_playlist_id,
            "playlist_name": result_playlist_name,
            "total_tracks": len(tracks),
            "migrated": len(tidal_track_ids),
            "not_found": len(not_found),
            "not_found_tracks": not_found[:10]  # Return first 10 not found for reference
        })
    except Exception as e:
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

    try:
        # Get Spotify tracks
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
                tracks = results.get("tracks", [])
                if tracks:
                    tidal_track_ids.append(tracks[0].id)
                else:
                    not_found.append(track)
            except:
                not_found.append(track)

        # Create playlist on Tidal
        description = f"Migrated from Spotify"
        playlist = tidal_session.user.create_playlist(playlist_name or "Migrated Playlist", description)

        # Add tracks to playlist
        if tidal_track_ids:
            playlist.add(tidal_track_ids)

        return jsonify({
            "success": True,
            "playlist_id": playlist.id,
            "playlist_name": playlist.name,
            "total_tracks": len(spotify_tracks),
            "migrated": len(tidal_track_ids),
            "not_found": len(not_found),
            "not_found_tracks": not_found[:10]  # Return first 10 not found for reference
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
