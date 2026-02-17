import logging
import aiosqlite
from datetime import datetime
from bot.config import DB_PATH

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None


async def init_db() -> None:
    """Initialize database and create tables."""
    global _db
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            format TEXT DEFAULT 'pdf',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            details TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (tg_id) REFERENCES users(tg_id)
        );
    """)
    await _db.commit()
    logger.info("Database initialized: %s", DB_PATH)


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


async def get_or_create_user(tg_id: int, username: str = "") -> dict:
    """Get existing user or create new one. Returns user dict."""
    db = _conn()
    cursor = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = await cursor.fetchone()
    if row:
        if username and row["username"] != username:
            await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (username, tg_id))
            await db.commit()
        return dict(row)
    await db.execute(
        "INSERT INTO users (tg_id, username) VALUES (?, ?)",
        (tg_id, username),
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = await cursor.fetchone()
    return dict(row)


async def get_balance(tg_id: int) -> int:
    db = _conn()
    cursor = await db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))
    row = await cursor.fetchone()
    return row["balance"] if row else 0


async def add_stars(tg_id: int, amount: int, details: str = "") -> int:
    """Add stars to user balance. Returns new balance."""
    db = _conn()
    await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id))
    await db.execute(
        "INSERT INTO transactions (tg_id, type, amount, details) VALUES (?, 'buy', ?, ?)",
        (tg_id, amount, details),
    )
    await db.commit()
    return await get_balance(tg_id)


async def gift_stars(tg_id: int, amount: int, gifted_by: str = "") -> int:
    """Gift stars to user (admin action). Returns new balance."""
    db = _conn()
    await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id))
    await db.execute(
        "INSERT INTO transactions (tg_id, type, amount, details) VALUES (?, 'gift', ?, ?)",
        (tg_id, amount, f"gifted by {gifted_by}"),
    )
    await db.commit()
    return await get_balance(tg_id)


async def spend_stars(tg_id: int, amount: int, details: str = "") -> int:
    """Deduct stars from user balance. Returns new balance."""
    db = _conn()
    await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id = ?", (amount, tg_id))
    await db.execute(
        "INSERT INTO transactions (tg_id, type, amount, details) VALUES (?, 'spend', ?, ?)",
        (tg_id, amount, details),
    )
    await db.commit()
    return await get_balance(tg_id)


async def set_format(tg_id: int, fmt: str) -> None:
    db = _conn()
    await db.execute("UPDATE users SET format = ? WHERE tg_id = ?", (fmt, tg_id))
    await db.commit()


async def get_format(tg_id: int) -> str:
    db = _conn()
    cursor = await db.execute("SELECT format FROM users WHERE tg_id = ?", (tg_id,))
    row = await cursor.fetchone()
    return row["format"] if row else "pdf"


async def get_all_users() -> list[dict]:
    db = _conn()
    cursor = await db.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_user_by_username(username: str) -> dict | None:
    db = _conn()
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_stats() -> dict:
    """Get bot statistics."""
    db = _conn()
    cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
    users_count = (await cursor.fetchone())["cnt"]

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM transactions WHERE type = 'spend'")
    translations_count = (await cursor.fetchone())["cnt"]

    cursor = await db.execute("SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = 'buy'")
    stars_bought = (await cursor.fetchone())["total"]

    cursor = await db.execute("SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = 'spend'")
    stars_spent = (await cursor.fetchone())["total"]

    cursor = await db.execute("SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = 'gift'")
    stars_gifted = (await cursor.fetchone())["total"]

    return {
        "users": users_count,
        "translations": translations_count,
        "stars_bought": stars_bought,
        "stars_spent": stars_spent,
        "stars_gifted": stars_gifted,
    }
