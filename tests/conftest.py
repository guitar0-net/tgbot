from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from db.database import Database
from db.database import db as global_db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Point the global DB singleton at a fresh in-memory SQLite connection for every test."""
    test_db = Database(":memory:")
    monkeypatch.setattr(global_db, "conn", test_db.conn)
    yield global_db
    test_db.conn.close()


@pytest.fixture
def storage_key():
    return StorageKey(bot_id=1, chat_id=100, user_id=200, destiny="default")


@pytest.fixture
def fsm_context(storage_key):
    return FSMContext(storage=MemoryStorage(), key=storage_key)


def make_user(user_id=200, first_name="Ivan", full_name="Ivan Ivann"):
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name
    user.full_name = full_name
    return user


def make_bot_message(text=None, user=None):
    msg = MagicMock()
    msg.text = text
    msg.from_user = user if user is not None else make_user()
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    msg.reply = AsyncMock()
    msg.bot = MagicMock(edit_message_text=AsyncMock(), delete_message=AsyncMock(), copy_message=AsyncMock())
    return msg


@pytest.fixture
def make_message():
    return make_bot_message


@pytest.fixture
def make_callback():
    def _make(data=None, user=None, message=None):
        cb = MagicMock()
        cb.data = data
        cb.from_user = user if user is not None else make_user()
        cb.message = message if message is not None else make_bot_message()
        cb.answer = AsyncMock()
        return cb

    return _make
