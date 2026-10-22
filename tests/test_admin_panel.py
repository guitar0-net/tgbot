from unittest.mock import AsyncMock

from aiogram.filters import CommandObject

from db.database import db
from routers import admin
from routers import learn as learn_module
from tests.conftest import make_user


def button_data(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


class TestSinglePanelMessage:
    async def test_add_id_edits_panel_and_removes_input(self, make_message, fsm_context):
        await fsm_context.set_state(admin.AdminState.waiting_add_id)
        await fsm_context.update_data(panel_chat_id=100, panel_message_id=9)
        message = make_message(text="555")

        await admin.fsm_add_id(message, fsm_context)

        message.bot.edit_message_text.assert_awaited_once()
        assert message.bot.edit_message_text.call_args.kwargs["message_id"] == 9
        message.delete.assert_awaited_once()
        message.answer.assert_not_awaited()
        assert await fsm_context.get_state() == admin.AdminState.waiting_add_name.state

    async def test_invalid_id_shows_error_in_panel(self, make_message, fsm_context):
        await fsm_context.set_state(admin.AdminState.waiting_add_id)
        await fsm_context.update_data(panel_chat_id=100, panel_message_id=9)
        message = make_message(text="abc")

        await admin.fsm_add_id(message, fsm_context)

        assert "корректный" in message.bot.edit_message_text.call_args.args[0]
        message.answer.assert_not_awaited()

    async def test_add_name_result_has_back_button(self, make_message, fsm_context):
        await fsm_context.update_data(panel_chat_id=100, panel_message_id=9, new_admin_id=777)
        message = make_message(text="Petya")

        await admin.fsm_add_name(message, fsm_context)

        kwargs = message.bot.edit_message_text.call_args.kwargs
        assert "adm:back" in button_data(kwargs["reply_markup"])
        assert db.is_admin(777)

    async def test_falls_back_to_new_message_without_panel_ids(self, make_message):
        message = make_message()

        await admin._update_panel(message, {}, "hello", admin._build_admin_keyboard())

        message.answer.assert_awaited_once()
        message.bot.edit_message_text.assert_not_awaited()

    async def test_dialog_start_remembers_panel(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))
        callback.message.chat.id = 100
        callback.message.message_id = 9

        await admin.cb_add_start(callback, fsm_context)

        data = await fsm_context.get_data()
        assert (data["panel_chat_id"], data["panel_message_id"]) == (100, 9)
        callback.message.answer.assert_not_awaited()
        callback.message.edit_text.assert_awaited_once()

    async def test_every_view_has_back_button(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(100, "Target")
        views = [
            (admin.cb_list, ()),
            (admin.cb_feedback, ()),
            (admin.cb_stats, ()),
            (admin.cb_remove_start, (fsm_context,)),
        ]
        for handler, extra in views:
            callback = make_callback(data="adm:feedback", user=make_user(user_id=42))
            await handler(callback, *extra)
            markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
            assert "adm:back" in button_data(markup), handler.__name__
            callback.message.answer.assert_not_awaited()


class TestFeedbackPagination:
    async def test_pages_split_and_navigate(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        for i in range(30):
            db.add_feedback(i, f"User{i}", "x" * 500)

        first = make_callback(data="adm:feedback", user=make_user(user_id=42))
        await admin.cb_feedback(first)
        markup = first.message.edit_text.call_args.kwargs["reply_markup"]
        assert "adm:fb:1" in button_data(markup)
        assert "adm:fb:-1" not in button_data(markup)

        second = make_callback(data="adm:fb:1", user=make_user(user_id=42))
        await admin.cb_feedback(second)
        markup = second.message.edit_text.call_args.kwargs["reply_markup"]
        assert "adm:fb:0" in button_data(markup)
        assert second.message.edit_text.call_args.args[0] != first.message.edit_text.call_args.args[0]

    async def test_out_of_range_page_is_clamped(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_feedback(1, "A", "hi")
        callback = make_callback(data="adm:fb:99", user=make_user(user_id=42))

        await admin.cb_feedback(callback)

        assert "hi" in callback.message.edit_text.call_args.args[0]


class TestSelfRemovalBlocked:
    async def test_callback(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(42, "Me")
        callback = make_callback(data="adm:rm:42", user=make_user(user_id=42))

        await admin.cb_remove_confirm(callback)

        assert db.is_admin(42)
        assert callback.answer.call_args.kwargs.get("show_alert") is True

    async def test_manual_input(self, make_message, fsm_context):
        db.add_admin(200, "Me")
        await fsm_context.update_data(panel_chat_id=100, panel_message_id=9)
        message = make_message(text="200", user=make_user(user_id=200))

        await admin.fsm_remove_id(message, fsm_context)

        assert db.is_admin(200)
        assert "самого себя" in message.bot.edit_message_text.call_args.args[0]

    async def test_text_command(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(42, "Me")
        message = make_message(user=make_user(user_id=42))

        await admin.cmd_admin_remove(message, CommandObject(command="admin_remove", args="42"))

        assert db.is_admin(42)
        assert "самого себя" in message.answer.call_args.args[0]


class TestCancelAndStats:
    async def test_cancel_requires_admin(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        await fsm_context.set_state(admin.AdminState.waiting_add_id)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_cancel(callback, fsm_context)

        assert await fsm_context.get_state() == admin.AdminState.waiting_add_id.state

    async def test_cancel_removes_pending_broadcast_message(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        await fsm_context.update_data(bc_chat_id=100, bc_message_id=5)
        callback = make_callback(user=make_user(user_id=42))
        callback.message.bot.delete_message = AsyncMock()

        await admin.cb_cancel(callback, fsm_context)

        callback.message.bot.delete_message.assert_awaited_once_with(100, 5)
        assert await fsm_context.get_data() == {}

    async def test_stats_has_refresh_button(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_stats(callback)

        assert "adm:stats" in button_data(callback.message.edit_text.call_args.kwargs["reply_markup"])


class TestReloadIsStrict:
    async def test_panel_reload_requests_strict_mode(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        load = AsyncMock()
        monkeypatch.setattr(learn_module, "load_data", load)
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_reload(callback)

        load.assert_awaited_once_with(strict=True)
