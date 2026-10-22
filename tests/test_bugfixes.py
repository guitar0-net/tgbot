import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from db import api as db_api
from db.database import db
from routers import base, learn
from tests.conftest import make_user


@pytest.fixture(autouse=True)
def reset_learn_data():
    learn.data_lessons = {"results": []}
    learn.data_chords = {"results": []}
    yield
    learn.data_lessons = {"results": []}
    learn.data_chords = {"results": []}


# --- API client -----------------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self._json_data = json_data

    async def json(self):
        return self._json_data


class FakeGet:
    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception

    async def __aenter__(self):
        if self._exception is not None:
            raise self._exception
        return self._response

    async def __aexit__(self, *args):
        return False


def make_session(response=None, exception=None):
    session = MagicMock()
    session.get = MagicMock(return_value=FakeGet(response=response, exception=exception))
    return session


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db_api, "API_DIR", str(tmp_path))
    return tmp_path


class TestStrictAndCacheWrite:
    async def test_strict_raises_instead_of_using_cache(self, cache_dir):
        with open(os.path.join(str(cache_dir), "data.json"), "w", encoding="utf-8") as f:
            json.dump({"results": ["cached"]}, f)
        session = make_session(response=FakeResponse(500))

        with pytest.raises(RuntimeError):
            await db_api.fetch_or_load_cache(session, "https://example.com/data", "data.json", strict=True)

    async def test_strict_raises_on_network_error(self, cache_dir):
        session = make_session(exception=ConnectionError("down"))

        with pytest.raises(ConnectionError):
            await db_api.fetch_or_load_cache(session, "https://example.com/data", "data.json", strict=True)

    async def test_fresh_data_returned_when_cache_write_fails(self, cache_dir, monkeypatch):
        with open(os.path.join(str(cache_dir), "data.json"), "w", encoding="utf-8") as f:
            json.dump({"results": ["stale"]}, f)

        def broken_open(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(db_api.aiofiles, "open", broken_open)
        session = make_session(response=FakeResponse(200, {"results": ["fresh"]}))

        result = await db_api.fetch_or_load_cache(session, "https://example.com/data", "data.json")

        assert result == {"results": ["fresh"]}


# --- Song buttons ---------------------------------------------------------------------------------------------------


class TestSongButtonsByUuid:
    async def test_show_lesson_uses_uuid_in_callback_data(self, make_message):
        learn.data_lessons = {"results": [{"uuid": "abc-1", "title": "L", "songs": [{"title": "S"}]}]}
        message = make_message()

        await learn.show_lesson(message, "0")

        markup = message.answer.call_args_list[0].kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "song:abc-1:0"

    async def test_song_found_by_uuid_after_lessons_reordered(self, make_callback, monkeypatch):
        learn.data_lessons = {
            "results": [
                {"uuid": "new-first", "title": "New", "songs": [{"title": "Other"}]},
                {"uuid": "abc-1", "title": "L", "songs": [{"title": "Mine"}]},
            ]
        }
        seen = {}

        async def fake_detail(lesson_uuid):
            seen["uuid"] = lesson_uuid
            return {"songs": [{"title": "Mine", "text": "hi"}]}

        monkeypatch.setattr(learn, "get_lesson_detail", fake_detail)
        callback = make_callback(data="song:abc-1:0")

        await learn.callback_song(callback)

        assert seen["uuid"] == "abc-1"

    async def test_unknown_uuid_reports_not_found(self, make_callback):
        learn.data_lessons = {"results": [{"uuid": "abc-1", "title": "L"}]}
        callback = make_callback(data="song:gone:0")

        await learn.callback_song(callback)

        assert "Урок не найден" in callback.message.answer.call_args.args[0]


# --- Keyboards / load_data ------------------------------------------------------------------------------------------


class TestKeyboardLimits:
    def test_lessons_keyboard_stays_within_telegram_limit(self):
        learn.data_lessons = {"results": [{} for _ in range(150)]}

        keyboard = learn.get_lessons_keyboard()

        assert sum(len(row) for row in keyboard.keyboard) <= 100

    def test_chords_keyboard_stays_within_telegram_limit(self):
        learn.data_chords = {"results": [{"title": f"C{i}x"} for i in range(150)]}

        keyboard = learn.get_chords_keyboard()

        assert sum(len(row) for row in keyboard.keyboard) <= 100


class TestLoadDataStrict:
    async def test_strict_reraises(self, monkeypatch):
        async def failing(strict=False):
            raise RuntimeError("down")

        monkeypatch.setattr(learn, "get_api_data", failing)

        with pytest.raises(RuntimeError):
            await learn.load_data(strict=True)

    async def test_strict_keeps_previous_data_on_failure(self, monkeypatch):
        learn.data_lessons = {"results": ["old"]}

        async def failing(strict=False):
            raise RuntimeError("down")

        monkeypatch.setattr(learn, "get_api_data", failing)

        with pytest.raises(RuntimeError):
            await learn.load_data(strict=True)

        assert learn.data_lessons == {"results": ["old"]}

    async def test_chord_without_title_does_not_crash_search(self, make_message):
        learn.data_chords = {"results": [{"title": None, "musical_title": None}, {"title": "Am"}]}
        message = make_message()

        assert await learn.show_chord(message, "x") is False


# --- Feedback length ------------------------------------------------------------------------------------------------


class TestFeedbackLength:
    async def test_too_long_inline_feedback_rejected(self, make_message):
        message = make_message(user=make_user())
        command = CommandObject(command="fb", args="x" * (base.MAX_FEEDBACK_LENGTH + 1))

        await base.cmd_feedback(message, command)

        assert "Слишком длинное" in message.answer.call_args.args[0]
        assert db.get_recent_feedback() == []

    async def test_too_long_dialog_feedback_keeps_state(self, make_message, fsm_context):
        await fsm_context.set_state(base.FeedbackState.waiting_for_feedback)
        message = make_message(text="x" * (base.MAX_FEEDBACK_LENGTH + 1))

        await base.process_feedback(message, fsm_context)

        assert await fsm_context.get_state() == base.FeedbackState.waiting_for_feedback.state
        assert db.get_recent_feedback() == []


# --- FSM input handlers must not swallow menu buttons / commands (end-to-end through the dispatcher) ----------------


_dispatcher = None


def get_dispatcher():
    """Routers can be attached to a single dispatcher only, so share one between the tests."""
    global _dispatcher
    if _dispatcher is None:
        from aiogram import Dispatcher
        from aiogram.fsm.storage.memory import MemoryStorage

        _dispatcher = Dispatcher(storage=MemoryStorage())
        _dispatcher.include_router(base.router)
        _dispatcher.include_router(learn.router)
    return _dispatcher


class TestMenuNotSwallowedByInputStates:
    async def _feed(self, text, state):
        from datetime import datetime

        from aiogram.types import Chat, Message, Update, User

        dp = get_dispatcher()
        bot = MagicMock()
        bot.id = 1
        context = dp.fsm.get_context(bot=bot, chat_id=5, user_id=5)
        await context.set_state(state)
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=5, type="private"),
            from_user=User(id=5, is_bot=False, first_name="x"),
            text=text,
        ).as_(bot)
        await dp.feed_update(bot, Update(update_id=1, message=message))
        return context

    @pytest.mark.parametrize("text", ["📚 Уроки", "🎸 Аккорды", "/lessons", "/chords", "/help"])
    async def test_not_saved_as_feedback(self, text, monkeypatch):
        monkeypatch.setattr("aiogram.types.Message.answer", AsyncMock())

        await self._feed(text, base.FeedbackState.waiting_for_feedback)

        assert db.get_recent_feedback() == []

    async def test_plain_text_is_saved_as_feedback(self, monkeypatch):
        monkeypatch.setattr("aiogram.types.Message.answer", AsyncMock())

        await self._feed("great bot", base.FeedbackState.waiting_for_feedback)

        assert [fb["text"] for fb in db.get_recent_feedback()] == ["great bot"]

    async def test_chords_button_during_lesson_input_switches_dialog(self, monkeypatch):
        monkeypatch.setattr("aiogram.types.Message.answer", AsyncMock())

        context = await self._feed("🎸 Аккорды", learn.LearnState.waiting_for_lesson)

        assert await context.get_state() == learn.LearnState.waiting_for_chord.state
