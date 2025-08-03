from flask import Flask, request, redirect, session, url_for, jsonify
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import os
load_dotenv()

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecretkey")

SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:5000/callback")

FRONTEND_REDIRECT = "http://localhost:5173/callback"  

SCOPE = "playlist-read-private playlist-read-collaborative"

def get_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE
    )

@app.route("/login")
def login():
    auth_url = get_spotify_oauth().get_authorize_url()
    return jsonify({"auth_url": auth_url})

@app.route("/callback")
def spotify_callback_redirect():
    code = request.args.get("code")
    return redirect(f"{FRONTEND_REDIRECT}?code={code}")

@app.route("/fetch_playlists", methods=["POST"])
def fetch_playlists():
    data = request.get_json()
    code = data.get("code")

    sp_oauth = get_spotify_oauth()
    session.permanent = True

    token_info = sp_oauth.get_access_token(code)

    sp = spotipy.Spotify(auth=token_info['access_token'])

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
                "tracks_total": p["tracks"]["total"]
            } for p in items
        ])
        if response.get("next"):
            offset += limit
        else:
            break

    return jsonify(playlists)

@app.route("/playlist_tracks", methods=["POST"])
def playlist_tracks():
    data = request.get_json()
    playlist_id = data.get("playlist_id")
    if not playlist_id:
        return jsonify({"error": "playlist_id is required"}), 400
    
    session.permanent = True
    data = request.get_json()
    code = data.get("code")

    sp_oauth = get_spotify_oauth()
    session.permanent = True

    token_info = sp_oauth.get_access_token(code)

    print(token_info)
    if not token_info:
        return jsonify({"error": "Not authenticated"}), 401

    sp = spotipy.Spotify(auth=token_info["access_token"])
    results = sp.playlist_tracks(playlist_id)
    
    tracks = []
    for item in results["items"]:
        track = item["track"]
        if track:
            tracks.append({
                "name": track["name"],
                "artist": ", ".join([artist["name"] for artist in track["artists"]]),
                "album": track["album"]["name"]
            })

    return jsonify(tracks)

if __name__ == "__main__":
    app.run(debug=True)
