"""
Central configuration for every process (web app, analyzer, parser, receiver, supervisor).

Values come from environment variables; a `.env` file in the project root is loaded
automatically if python-dotenv is installed. See `.env.example` for the full list.
"""
import os
from datetime import timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def sqlalchemy_url(url):
    """SQLAlchemy 2.x maps bare postgresql:// to psycopg 3; pin the psycopg2 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def psycopg2_dsn(url):
    """psycopg2 accepts postgresql:// URIs but not SQLAlchemy driver suffixes."""
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


# Every connection works in UTC so timestamptz values come back as UTC datetimes.
PG_OPTIONS = "-c timezone=UTC"
ENGINE_OPTIONS = {"pool_pre_ping": True, "connect_args": {"options": PG_OPTIONS}}

# Main application role (web app, analyzer, supervisor, simulator).
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://sonde_user:change-me@localhost/weather_sonde")

# Optional least-privilege role for the receiver and parser; falls back to DATABASE_URL.
INGEST_DATABASE_URL = os.getenv("INGEST_DATABASE_URL", DATABASE_URL)


class FlaskConfig:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-key")
    SQLALCHEMY_DATABASE_URI = sqlalchemy_url(DATABASE_URL)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = ENGINE_OPTIONS

    # Operators are logged out after this much inactivity (dashboard polling keeps it alive).
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_MINUTES", "30")))
    REMEMBER_COOKIE_DURATION = PERMANENT_SESSION_LIFETIME

    # Disable once your operator accounts exist so nobody else on the network can sign up.
    ALLOW_SIGNUP = _bool("ALLOW_SIGNUP", True)
