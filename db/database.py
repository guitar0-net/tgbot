import json
import sqlite3
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from aiogram.fsm.storage.base import BaseStorage, State, StateType, StorageKey


class Database:
    def __init__(self, db_name="bot_database.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_name TEXT,
                    text TEXT,
                    date TEXT
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    added_at TEXT
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    username TEXT,
                    first_seen TEXT,
                    last_seen TEXT
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS fsm_data (
                    key TEXT PRIMARY KEY,
                    state TEXT,
                    data TEXT,
                    updated_at REAL
                )
            """)

    def add_feedback(self, user_id, user_name, text):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.conn:
            self.conn.execute(
                "INSERT INTO feedback (user_id, user_name, text, date) VALUES (?, ?, ?, ?)",
                (user_id, user_name, text, date),
            )

    def get_recent_feedback(self, days: int = 30) -> list[dict]:
        """Return feedback entries from the last `days` days, newest first."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.conn.execute(
            "SELECT user_id, user_name, text, date FROM feedback WHERE date >= ? ORDER BY date DESC",
            (cutoff,),
        )
        return [{"user_id": row[0], "user_name": row[1], "text": row[2], "date": row[3]} for row in cursor.fetchall()]

    def get_feedback_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

    # --- Users ---

    def upsert_user(self, user_id: int, full_name: str | None, username: str | None) -> None:
        """Register a user on first contact, or refresh their name and last activity."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO users (user_id, full_name, username, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (user_id, full_name, username, now, now),
            )

    def remove_user(self, user_id: int) -> None:
        """Forget a user (e.g. after they blocked the bot)."""
        with self.conn:
            self.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

    def get_all_user_ids(self) -> list[int]:
        return [row[0] for row in self.conn.execute("SELECT user_id FROM users ORDER BY user_id")]

    def get_user_stats(self) -> dict:
        """Return total users and the number of new/active users over the last day and week."""
        now = datetime.now()
        day = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        week = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

        row = self.conn.execute(
            "SELECT COUNT(*),"
            " COALESCE(SUM(first_seen >= ?), 0), COALESCE(SUM(first_seen >= ?), 0),"
            " COALESCE(SUM(last_seen >= ?), 0), COALESCE(SUM(last_seen >= ?), 0)"
            " FROM users",
            (day, week, day, week),
        ).fetchone()

        return {
            "total": row[0],
            "new_day": row[1],
            "new_week": row[2],
            "active_day": row[3],
            "active_week": row[4],
        }

    # --- Admin management ---

    def add_admin(self, user_id: int, full_name: str) -> bool:
        """Add an admin. Returns True if added, False if already exists."""
        added_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO admins (user_id, full_name, added_at) VALUES (?, ?, ?)", (user_id, full_name, added_at)
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_admin(self, user_id: int) -> bool:
        """Remove an admin. Returns True if removed, False if not found."""
        with self.conn:
            cursor = self.conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0

    def is_admin(self, user_id: int) -> bool:
        """Check if user_id is in the admins table."""
        cursor = self.conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

    def get_all_admins(self) -> list[dict]:
        """Return list of all admins from DB."""
        cursor = self.conn.execute("SELECT user_id, full_name, added_at FROM admins ORDER BY added_at")
        return [{"user_id": row[0], "full_name": row[1], "added_at": row[2]} for row in cursor.fetchall()]


db = Database()


DEFAULT_TTL_SECONDS = 48 * 3600  # 48 hours in seconds


class SQLiteStorage(BaseStorage):
    """
    Persistent FSM Storage implementation using SQLite.
    Stores state and state data with an expiration timestamp (TTL = 48 hours).
    """

    def __init__(self, db_instance: Database = db, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.db = db_instance
        self.ttl_seconds = ttl_seconds

    def _key_to_str(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id or 0}:{key.destiny}"

    def _cleanup_expired(self):
        cutoff = time.time() - self.ttl_seconds
        with self.db.conn:
            self.db.conn.execute("DELETE FROM fsm_data WHERE updated_at < ?", (cutoff,))

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        self._cleanup_expired()
        key_str = self._key_to_str(key)
        state_str = state.state if isinstance(state, State) else (str(state) if state is not None else None)

        now = time.time()
        with self.db.conn:
            cursor = self.db.conn.execute("SELECT data FROM fsm_data WHERE key = ?", (key_str,))
            row = cursor.fetchone()
            if row is None:
                if state_str is not None:
                    self.db.conn.execute(
                        "INSERT INTO fsm_data (key, state, data, updated_at) VALUES (?, ?, ?, ?)",
                        (key_str, state_str, "{}", now),
                    )
            else:
                data_str = row[0]
                if state_str is None and (not data_str or data_str == "{}"):
                    self.db.conn.execute("DELETE FROM fsm_data WHERE key = ?", (key_str,))
                else:
                    self.db.conn.execute(
                        "UPDATE fsm_data SET state = ?, updated_at = ? WHERE key = ?",
                        (state_str, now, key_str),
                    )

    async def get_state(self, key: StorageKey) -> str | None:
        self._cleanup_expired()
        key_str = self._key_to_str(key)
        cursor = self.db.conn.execute("SELECT state, updated_at FROM fsm_data WHERE key = ?", (key_str,))
        row = cursor.fetchone()
        if not row:
            return None

        state_str, updated_at = row
        if time.time() - updated_at > self.ttl_seconds:
            with self.db.conn:
                self.db.conn.execute("DELETE FROM fsm_data WHERE key = ?", (key_str,))
            return None

        return state_str

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        self._cleanup_expired()
        key_str = self._key_to_str(key)
        data_str = json.dumps(dict(data), ensure_ascii=False)
        now = time.time()

        with self.db.conn:
            cursor = self.db.conn.execute("SELECT state FROM fsm_data WHERE key = ?", (key_str,))
            row = cursor.fetchone()
            if row is None:
                if data:
                    self.db.conn.execute(
                        "INSERT INTO fsm_data (key, state, data, updated_at) VALUES (?, ?, ?, ?)",
                        (key_str, None, data_str, now),
                    )
            else:
                state_str = row[0]
                if state_str is None and not data:
                    self.db.conn.execute("DELETE FROM fsm_data WHERE key = ?", (key_str,))
                else:
                    self.db.conn.execute(
                        "UPDATE fsm_data SET data = ?, updated_at = ? WHERE key = ?",
                        (data_str, now, key_str),
                    )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        self._cleanup_expired()
        key_str = self._key_to_str(key)
        cursor = self.db.conn.execute("SELECT data, updated_at FROM fsm_data WHERE key = ?", (key_str,))
        row = cursor.fetchone()
        if not row:
            return {}

        data_str, updated_at = row
        if time.time() - updated_at > self.ttl_seconds:
            with self.db.conn:
                self.db.conn.execute("DELETE FROM fsm_data WHERE key = ?", (key_str,))
            return {}

        try:
            return json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            return {}

    async def close(self) -> None:
        pass
