import os
from dotenv import load_dotenv

load_dotenv()  # reads variables from your .env file

ENV = os.getenv("ENV", "local")  # defaults to "local" if not set

# --- Microsoft 365 login settings (from your Azure app registration) ---
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
TENANT_ID = os.getenv("TENANT_ID", "")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else ""
SCOPE = ["User.Read"]

# Check if Microsoft 365 OAuth is fully configured
HAS_AZURE_AUTH = bool(CLIENT_ID and CLIENT_SECRET and TENANT_ID)

# --- Redirect URI switches based on ENV ---
if ENV == "production":
    REDIRECT_URI = os.getenv("PROD_REDIRECT_URI", "https://ats.adroit-ai.com/auth/callback")
else:
    REDIRECT_URI = os.getenv("LOCAL_REDIRECT_URI", "http://localhost:5000/auth/callback")

# --- Flask session secret ---
# Sessions are signed with this key, so it MUST be a private random value in
# production. Both SECRET_KEY and FLASK_SECRET_KEY are accepted. The fallback
# below is published in this public repo, so anyone could forge a logged-in
# admin session with it - it exists only so local development still works.
_DEFAULT_DEV_SECRET_KEY = "adroit-ats-secret-key-2026-local-dev"
SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or _DEFAULT_DEV_SECRET_KEY
USING_DEFAULT_SECRET_KEY = SECRET_KEY == _DEFAULT_DEV_SECRET_KEY

# --- Login hardening ---
# /dev-login logs in as the admin WITHOUT a password. It is disabled unless
# ALLOW_DEV_LOGIN=1 is set on the server (intended for local development only).
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "").strip().lower() in ("1", "true", "yes")
# Set ADMIN_PASSWORD on the server to (re)set the admin account's password at
# startup instead of relying on the default that is visible in the source.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# --- Apify (job scraping) ---
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")

# --- People Data Labs (structured people search; free tier = 100 records/month) ---
PDL_API_KEY = os.getenv("PDL_API_KEY", "")

# --- Database ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "ats.db"))

# --- SerpAPI (Google SERP scraper for LinkedIn) ---
# NOTE: previously had a hardcoded key here as a fallback default - that key
# was exposed in a public commit and should be treated as compromised. Set
# SERPAPI_KEY as an environment variable instead; this has no fallback value.
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
