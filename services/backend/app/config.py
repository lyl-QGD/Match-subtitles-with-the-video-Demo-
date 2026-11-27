import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = ("DATABASE_URL", "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ORIGINALS_BUCKET", "S3_DERIVED_BUCKET")

def missing_config():
    load_dotenv(ROOT / ".env", override=False)
    return [key for key in REQUIRED if not os.getenv(key, "").strip()]
