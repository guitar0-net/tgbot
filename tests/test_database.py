import time

import pytest
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from db.database import Database, SQLiteStorage


@pytest.fixture
def fresh_db():
    database = Database(":memory:")
    yield database
    database.conn.close()


class TestFeedback:
    def test_add_and_get_recent_feedback(self, fresh_db):
        fresh_db.add_feedback(1, "Alice", "Great bot!")
        fresh_db.add_feedback(2, "Bob", "Needs more chords")

        result = fresh_db.get_recent_feedback(days=30)

        assert len(result) == 2
        assert result[0]["text"] in {"Great bot!", "Needs more chords"}
        assert {r["user_name"] for r in result} == {"Alice", "Bob"}

    def test_get_recent_feedback_excludes_old_entries(self, fresh_db):
        fresh_db.add_feedback(1, "Alice", "Old feedback")
        old_date = "2000-01-01 00:00:00"
        with fresh_db.conn:
            fresh_db.conn.execute("UPDATE feedback SET date = ?", (old_date,))

        result = fresh_db.get_recent_feedback(days=30)

        assert result == []

    def test_get_recent_feedback_empty(self, fresh_db):
        assert fresh_db.get_recent_feedback() == []


class TestAdmins:
    def test_add_admin_returns_true_when_new(self, fresh_db):
        assert fresh_db.add_admin(111, "Alice") is True
        assert fresh_db.is_admin(111) is True

    def test_add_admin_returns_false_when_duplicate(self, fresh_db):
        fresh_db.add_admin(111, "Alice")
        assert fresh_db.add_admin(111, "Alice again") is False

    def test_is_admin_false_for_unknown_user(self, fresh_db):
        assert fresh_db.is_admin(999) is False

    def test_remove_admin_returns_true_when_removed(self, fresh_db):
        fresh_db.add_admin(111, "Alice")
        assert fresh_db.remove_admin(111) is True
        assert fresh_db.is_admin(111) is False

    def test_remove_admin_returns_false_when_not_found(self, fresh_db):
        assert fresh_db.remove_admin(404) is False

    def test_get_all_admins_ordered_by_added_at(self, fresh_db):
        fresh_db.add_admin(1, "Alice")
        fresh_db.add_admin(2, "Bob")

        admins = fresh_db.get_all_admins()

        assert [a["user_id"] for a in admins] == [1, 2]
        assert admins[0]["full_name"] == "Alice"


class TestSQLiteStorage:
    @pytest.fixture
    def storage(self, fresh_db):
        return SQLiteStorage(fresh_db, ttl_seconds=3600)

    @pytest.fixture
    def key(self):
        return StorageKey(bot_id=1, chat_id=10, user_id=20, destiny="default")

    def test_key_to_str_uses_zero_for_missing_thread(self, storage, key):
        assert storage._key_to_str(key) == "1:10:20:0:default"

    async def test_set_and_get_state_with_state_object(self, storage, key):
        class SampleGroup(StatesGroup):
            bar = State()

        await storage.set_state(key, SampleGroup.bar)

        result = await storage.get_state(key)

        assert result == "SampleGroup:bar"

    async def test_set_and_get_state_with_string(self, storage, key):
        await storage.set_state(key, "custom_state")
        assert await storage.get_state(key) == "custom_state"

    async def test_get_state_returns_none_when_absent(self, storage, key):
        assert await storage.get_state(key) is None

    async def test_set_state_none_deletes_row_without_data(self, storage, key):
        await storage.set_state(key, "some_state")
        await storage.set_state(key, None)

        assert await storage.get_state(key) is None

    async def test_set_state_none_keeps_row_when_data_present(self, storage, key):
        await storage.set_state(key, "some_state")
        await storage.set_data(key, {"foo": "bar"})

        await storage.set_state(key, None)

        assert await storage.get_state(key) is None
        assert await storage.get_data(key) == {"foo": "bar"}

    async def test_set_and_get_data(self, storage, key):
        await storage.set_data(key, {"a": 1, "b": "two"})

        result = await storage.get_data(key)

        assert result == {"a": 1, "b": "two"}

    async def test_get_data_returns_empty_dict_when_absent(self, storage, key):
        assert await storage.get_data(key) == {}

    async def test_set_data_empty_deletes_row_without_state(self, storage, key):
        await storage.set_data(key, {"a": 1})
        await storage.set_data(key, {})

        assert await storage.get_data(key) == {}

    async def test_set_data_empty_keeps_row_when_state_present(self, storage, key):
        await storage.set_state(key, "some_state")
        await storage.set_data(key, {"a": 1})

        await storage.set_data(key, {})

        assert await storage.get_state(key) == "some_state"
        assert await storage.get_data(key) == {}

    async def test_data_survives_state_update(self, storage, key):
        await storage.set_data(key, {"a": 1})
        await storage.set_state(key, "some_state")

        assert await storage.get_data(key) == {"a": 1}
        assert await storage.get_state(key) == "some_state"

    async def test_expired_state_is_cleared(self, storage, key):
        await storage.set_state(key, "some_state")
        with storage.db.conn:
            storage.db.conn.execute(
                "UPDATE fsm_data SET updated_at = ? WHERE key = ?",
                (time.time() - 999999, storage._key_to_str(key)),
            )

        assert await storage.get_state(key) is None

    async def test_expired_data_is_cleared(self, storage, key):
        await storage.set_data(key, {"a": 1})
        with storage.db.conn:
            storage.db.conn.execute(
                "UPDATE fsm_data SET updated_at = ? WHERE key = ?",
                (time.time() - 999999, storage._key_to_str(key)),
            )

        assert await storage.get_data(key) == {}

    async def test_get_state_clears_row_expired_after_cleanup_window(self, storage, key, monkeypatch):
        """Covers the defensive expiry check in get_state for a row that slips past cleanup."""
        await storage.set_state(key, "some_state")
        key_str = storage._key_to_str(key)
        with storage.db.conn:
            storage.db.conn.execute(
                "UPDATE fsm_data SET updated_at = ? WHERE key = ?",
                (time.time() - storage.ttl_seconds - 1, key_str),
            )
        monkeypatch.setattr(storage, "_cleanup_expired", lambda: None)

        assert await storage.get_state(key) is None
        cursor = storage.db.conn.execute("SELECT * FROM fsm_data WHERE key = ?", (key_str,))
        assert cursor.fetchone() is None

    async def test_get_data_clears_row_expired_after_cleanup_window(self, storage, key, monkeypatch):
        """Covers the defensive expiry check in get_data for a row that slips past cleanup."""
        await storage.set_data(key, {"a": 1})
        key_str = storage._key_to_str(key)
        with storage.db.conn:
            storage.db.conn.execute(
                "UPDATE fsm_data SET updated_at = ? WHERE key = ?",
                (time.time() - storage.ttl_seconds - 1, key_str),
            )
        monkeypatch.setattr(storage, "_cleanup_expired", lambda: None)

        assert await storage.get_data(key) == {}
        cursor = storage.db.conn.execute("SELECT * FROM fsm_data WHERE key = ?", (key_str,))
        assert cursor.fetchone() is None

    async def test_get_data_handles_corrupt_json(self, storage, key):
        await storage.set_state(key, "some_state")
        key_str = storage._key_to_str(key)
        with storage.db.conn:
            storage.db.conn.execute(
                "UPDATE fsm_data SET data = ? WHERE key = ?",
                ("not-json", key_str),
            )

        assert await storage.get_data(key) == {}

    async def test_cleanup_expired_removes_stale_rows(self, storage, key):
        await storage.set_state(key, "some_state")
        key_str = storage._key_to_str(key)
        with storage.db.conn:
            storage.db.conn.execute(
                "UPDATE fsm_data SET updated_at = ? WHERE key = ?",
                (time.time() - 999999, key_str),
            )

        storage._cleanup_expired()

        cursor = storage.db.conn.execute("SELECT * FROM fsm_data WHERE key = ?", (key_str,))
        assert cursor.fetchone() is None

    async def test_close_does_not_raise(self, storage):
        await storage.close()


class TestDatabasePath:
    def test_custom_db_path_creates_directory(self, tmp_path):
        target_dir = tmp_path / "custom_dir"
        db_file = target_dir / "test.db"
        assert not target_dir.exists()

        db_instance = Database(str(db_file))
        try:
            assert target_dir.exists()
            assert db_file.exists()
        finally:
            db_instance.conn.close()

    def test_db_path_env_var(self, tmp_path, monkeypatch):
        db_file = tmp_path / "env_test.db"
        monkeypatch.setenv("DB_PATH", str(db_file))

        db_instance = Database()
        try:
            assert db_instance.db_name == str(db_file)
            assert db_file.exists()
        finally:
            db_instance.conn.close()
