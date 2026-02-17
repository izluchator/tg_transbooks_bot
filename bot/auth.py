import json
import logging
from bot.config import WHITELIST_FILE, BOT_SECRET_WORD

logger = logging.getLogger(__name__)


def _load_whitelist() -> dict[str, str]:
    if WHITELIST_FILE.exists():
        with open(WHITELIST_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_whitelist(wl: dict[str, str]) -> None:
    with open(WHITELIST_FILE, "w") as f:
        json.dump(wl, f, indent=2)


def authenticate(user_id: int, username: str, secret: str) -> bool:
    if secret != BOT_SECRET_WORD:
        return False
    wl = _load_whitelist()
    wl[str(user_id)] = username or "unknown"
    _save_whitelist(wl)
    logger.info("User %s (%s) authorized", user_id, username)
    return True


def is_authorized(user_id: int) -> bool:
    wl = _load_whitelist()
    return str(user_id) in wl
