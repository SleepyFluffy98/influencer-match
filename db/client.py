"""
Supabase client — initialised once at import time.
`supabase` is None when SUPABASE_URL / SUPABASE_ANON_KEY are not set,
which means the app falls back to local JSON files (local dev mode).
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

supabase = None

_url = os.getenv("SUPABASE_URL")
_key = os.getenv("SUPABASE_ANON_KEY")

if _url and _key:
    try:
        from supabase import create_client
        supabase = create_client(_url, _key)
        logger.info("Supabase client initialised ✓")
    except Exception as exc:
        logger.warning("Could not initialise Supabase client: %s — falling back to file storage", exc)
