import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TMP_DIR = DATA_DIR / "tmp"
TEMPLATES_DIR = BASE_DIR / "templates"
DB_PATH = DATA_DIR / "bot.db"

TMP_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "3000"))

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "apustota")
STARS_PER_50_PAGES = int(os.getenv("STARS_PER_50_PAGES", "20"))
