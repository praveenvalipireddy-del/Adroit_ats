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
SECRET_KEY = os.getenv("SECRET_KEY", "adroit-ats-secret-key-2026-local-dev")

# --- Apify (job scraping) ---
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")

# --- Database ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "ats.db"))

# --- SerpAPI (Google SERP scraper for LinkedIn) ---
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "9d401009a7f2e0ce89b92baaf0b7613bd440e43a198ee34327cc8ef5a8775773")
