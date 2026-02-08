"""
Configuration for Migrate Beats API.
Supports local development and Railway deployment.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration."""
    # Flask
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'supersecretkey')

    # Database - Railway provides DATABASE_URL automatically
    # Local: postgresql://migrate_beats:localdevpassword@localhost:5432/migrate_beats
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///migrate_beats.db')

    # Fix for Railway's postgres:// vs postgresql://
    if SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # CORS
    FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')
    FRONTEND_REDIRECT = os.environ.get('FRONTEND_REDIRECT', 'http://localhost:5173/callback')

    # Spotify
    SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
    SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
    SPOTIPY_REDIRECT_URI = os.environ.get('SPOTIPY_REDIRECT_URI', 'http://127.0.0.1:5000/callback')

    # Tidal (optional - tidalapi has built-in credentials)
    TIDAL_CLIENT_ID = os.environ.get('TIDAL_CLIENT_ID')
    TIDAL_CLIENT_SECRET = os.environ.get('TIDAL_CLIENT_SECRET')

    # Rate Limits (per day)
    RATE_LIMIT_MIGRATIONS = int(os.environ.get('RATE_LIMIT_MIGRATIONS', 50))
    RATE_LIMIT_FETCH_LIKED = int(os.environ.get('RATE_LIMIT_FETCH_LIKED', 100))

    # Cache TTL (hours)
    CACHE_TTL_LIBRARY_STATS = int(os.environ.get('CACHE_TTL_LIBRARY_STATS', 6))
    CACHE_TTL_TOP_TRACKS = int(os.environ.get('CACHE_TTL_TOP_TRACKS', 24))
    CACHE_TTL_PLAYLISTS = int(os.environ.get('CACHE_TTL_PLAYLISTS', 1))


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    SQLALCHEMY_ECHO = False  # Set to True to see SQL queries


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    SQLALCHEMY_ECHO = False


# Select config based on environment
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}


def get_config():
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
