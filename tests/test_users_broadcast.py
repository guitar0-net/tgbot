from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from db.database import db
from routers import admin
from routers.middleware import UserTrackingMiddleware
from tests.conftest import make_user


class TestUsersDb:
    def test_upsert_registers_user_once(self):
        db.upsert_user(1, "Ivan", "ivan")
        db.upsert_user(1, "Ivan Renamed", "ivan2")

        assert db.get_all_user_ids() == [1]
        assert db.get_user_stats()["total"] == 1

    def test_stats_count_new_and_active(self):
        db.upsert_user(1, "A", None)
        db.upsert_user(2, "B", None)
        db.conn.execute(
            "UPDATE users SET first_seen = '2000-01-01 00:00:00', last_seen = '2000-01-01 00:00:00' WHERE user_id = 2"
        )

        stats = db.get_user_stats()

        assert stats == {"total": 2, "new_day": 1, "new_week": 1, "active_day": 1, "active_week": 1}

    def test_remove_user(self):
        db.upsert_user(1, "A", None)
        db.remove_user(1)
        assert db.get_all_user_ids() == []

    def test_feedback_count(self):
        db.add_feedback(1, "A", "hi")
        assert db.get_feedback_count() == 1


class TestUserTrackingMiddleware:
    async def test_registers_user_and_calls_handler(self):
        handler = AsyncMock(return_value="ok")
        user = make_user(user_id=7, full_name="Ivan")
        user.is_bot = False
        user.username = "ivan"

        result = await UserTrackingMiddleware()(handler, MagicMock(), {"event_from_user": user})

        assert result == "ok"
        assert db.get_all_user_ids() == [7]

    async def test_ignores_bots_and_missing_user(self):
        handler = AsyncMock()
        bot_user = make_user(user_id=8)
        bot_user.is_bot = True

        await UserTrackingMiddleware()(handler, MagicMock(), {"event_from_user": bot_user})
        await UserTrackingMiddleware()(handler, MagicMock(), {})

        assert db.get_all_user_ids() == []
        assert handler.await_count == 2


class TestStats:
    async def test_no_access_shows_alert(self, make_callback, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_stats(callback)

        callback.answer.assert_awaited_once_with("⛔ Нет доступа.", show_alert=True)

    async def test_shows_user_count(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.upsert_user(1, "A", None)
        db.upsert_user(2, "B", None)
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_stats(callback)

        text = callback.message.edit_text.call_args.args[0]
        assert "Всего пользователей: <b>2</b>" in text


class TestBroadcast:
    async def test_start_sets_state(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_broadcast_start(callback, fsm_context)

        assert await fsm_context.get_state() == admin.AdminState.waiting_broadcast.state

    async def test_message_is_stored_and_confirmation_asked(self, make_message, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(text="Hello", user=make_user(user_id=42))
        message.chat.id = 100
        message.message_id = 5
        await fsm_context.update_data(panel_chat_id=100, panel_message_id=9)

        await admin.fsm_broadcast_text(message, fsm_context)

        data = await fsm_context.get_data()
        assert (data["bc_chat_id"], data["bc_message_id"]) == (100, 5)
        # the panel message is edited in place; the admin's message stays as a preview
        message.bot.edit_message_text.assert_awaited_once()
        message.delete.assert_not_awaited()

    async def test_send_counts_delivered_blocked_and_failed(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        monkeypatch.setattr(admin, "_BROADCAST_DELAY", 0)
        for uid in (1, 2, 3, 4):
            db.upsert_user(uid, f"U{uid}", None)
        await fsm_context.update_data(bc_chat_id=100, bc_message_id=5)

        async def fake_copy(chat_id, from_chat_id, message_id):
            if chat_id == 2:
                raise TelegramForbiddenError(method=MagicMock(), message="blocked")
            if chat_id == 3:
                raise TelegramBadRequest(method=MagicMock(), message="bad")

        callback = make_callback(user=make_user(user_id=42))
        callback.message.bot.copy_message = AsyncMock(side_effect=fake_copy)

        await admin.cb_broadcast_send(callback, fsm_context)

        summary = callback.message.edit_text.call_args.args[0]
        assert "Доставлено: <b>2</b>" in summary
        assert "(удалены из базы): <b>1</b>" in summary
        assert "Ошибок: <b>1</b>" in summary
        assert db.get_all_user_ids() == [1, 3, 4]
        assert await fsm_context.get_state() is None

    async def test_retry_after_is_retried_once(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        monkeypatch.setattr(admin, "_BROADCAST_DELAY", 0)
        db.upsert_user(1, "U1", None)
        await fsm_context.update_data(bc_chat_id=100, bc_message_id=5)
        callback = make_callback(user=make_user(user_id=42))
        callback.message.bot.copy_message = AsyncMock(
            side_effect=[TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=0), None]
        )

        await admin.cb_broadcast_send(callback, fsm_context)

        assert callback.message.bot.copy_message.await_count == 2
        assert "Доставлено: <b>1</b>" in callback.message.edit_text.call_args.args[0]

    async def test_send_without_stored_message_alerts(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_broadcast_send(callback, fsm_context)

        callback.answer.assert_awaited_once_with("Нет сообщения для рассылки.", show_alert=True)

    @pytest.mark.parametrize("handler", ["cb_broadcast_start", "cb_broadcast_send"])
    async def test_no_access(self, handler, make_callback, fsm_context, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await getattr(admin, handler)(callback, fsm_context)

        callback.answer.assert_awaited_once_with("⛔ Нет доступа.", show_alert=True)
