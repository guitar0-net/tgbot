from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.methods import EditMessageText

from db.database import db
from routers import admin
from routers import learn as learn_module
from tests.conftest import make_user


class TestEnvAdminIds:
    def test_parses_comma_semicolon_and_space(self, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "1, 2;3 4")
        assert admin._get_env_admin_ids() == {1, 2, 3, 4}

    def test_empty_env_returns_empty_set(self, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        assert admin._get_env_admin_ids() == set()

    def test_ignores_non_digit_parts(self, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "1,abc,2")
        assert admin._get_env_admin_ids() == {1, 2}


class TestIsAdmin:
    def test_true_for_env_admin(self, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "555")
        assert admin.is_admin(555) is True

    def test_true_for_db_admin(self, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        db.add_admin(777, "Db Admin")
        assert admin.is_admin(777) is True

    def test_false_for_unknown_user(self, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        assert admin.is_admin(999) is False


class TestBuildAdminListLines:
    def test_combines_db_and_env_admins(self, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "111,222")
        db.add_admin(222, "Overlap")  # also present in env -> should not duplicate
        db.add_admin(333, "DB Only")

        lines = admin._build_admin_list_lines()
        text = "\n".join(lines)

        assert text.count("222") == 1
        assert "DB Only" in text
        assert "111" in text
        assert "из .env" in text

    def test_no_admins_reports_none(self, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)

        lines = admin._build_admin_list_lines()

        assert "Администраторов нет." in lines


class TestChunkLines:
    def test_single_chunk_when_short(self):
        chunks = admin._chunk_lines(["a", "b", "c"], limit=100)
        assert chunks == ["a\nb\nc"]

    def test_splits_into_multiple_chunks(self):
        lines = ["x" * 10] * 5
        chunks = admin._chunk_lines(lines, limit=25)
        assert len(chunks) > 1
        assert all(len(chunk) <= 25 + 11 for chunk in chunks)  # allow last line slack

    def test_empty_lines_returns_single_empty_chunk(self):
        assert admin._chunk_lines([]) == [""]


class TestReplaceMessage:
    async def test_edits_when_possible(self, make_message):
        message = make_message()
        markup = admin._build_admin_keyboard()

        await admin._replace_message(message, "hello", markup)

        message.edit_text.assert_awaited_once_with("hello", parse_mode="HTML", reply_markup=markup)
        message.answer.assert_not_awaited()

    async def test_falls_back_to_answer_on_bad_request(self, make_message):
        message = make_message()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=EditMessageText(text="x"), message="message to edit not found")
        )
        markup = admin._build_admin_keyboard()

        await admin._replace_message(message, "hello", markup)

        message.answer.assert_awaited_once_with("hello", parse_mode="HTML", reply_markup=markup)

    async def test_not_modified_is_ignored(self, make_message):
        message = make_message()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=EditMessageText(text="x"), message="message is not modified")
        )

        await admin._replace_message(message, "hello", admin._build_admin_keyboard())

        message.answer.assert_not_awaited()


class TestCmdAdmin:
    async def test_no_access_for_non_admin(self, make_message, fsm_context, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        message = make_message(user=make_user(user_id=1))

        await admin.cmd_admin(message, fsm_context)

        assert "нет доступа" in message.answer.call_args.args[0]

    async def test_shows_panel_for_admin(self, make_message, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))

        await admin.cmd_admin(message, fsm_context)

        text = message.answer.call_args.args[0]
        assert "Панель администратора" in text

    async def test_no_from_user_returns_silently(self, make_message, fsm_context):
        message = make_message()
        message.from_user = None

        await admin.cmd_admin(message, fsm_context)

        message.answer.assert_not_awaited()

    async def test_clears_existing_state(self, make_message, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        await fsm_context.set_state("SomeState")
        message = make_message(user=make_user(user_id=42))

        await admin.cmd_admin(message, fsm_context)

        assert await fsm_context.get_state() is None


class TestCbReload:
    async def test_no_access_shows_alert(self, make_callback, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_reload(callback)

        callback.answer.assert_awaited_once_with("⛔ Нет доступа.", show_alert=True)

    async def test_reloads_data_successfully(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")

        async def fake_load_data(strict=False):
            learn_module.data_lessons = {"results": [1, 2]}
            learn_module.data_chords = {"results": [1]}

        monkeypatch.setattr(learn_module, "load_data", fake_load_data)
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_reload(callback)

        final_call_text = callback.message.edit_text.call_args.args[0]
        assert "успешно обновлены" in final_call_text
        assert "2" in final_call_text

    async def test_reports_error_on_exception(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")

        async def failing_load_data(strict=False):
            raise RuntimeError("api down")

        monkeypatch.setattr(learn_module, "load_data", failing_load_data)
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_reload(callback)

        final_call_text = callback.message.edit_text.call_args.args[0]
        assert "Ошибка" in final_call_text

    async def test_missing_message_answers_only(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))
        callback.message = None

        await admin.cb_reload(callback)

        callback.answer.assert_awaited_once()


class TestCbList:
    async def test_lists_admins(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(42, "Boss")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_list(callback)

        text = callback.message.edit_text.call_args.args[0]
        assert "Boss" in text

    async def test_no_access(self, make_callback, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_list(callback)

        callback.message.answer.assert_not_awaited()


class TestCbFeedback:
    async def test_shows_feedback_entries(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_feedback(5, "Alice", "Great job")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_feedback(callback)

        text = callback.message.edit_text.call_args.args[0]
        assert "Alice" in text
        assert "Great job" in text

    async def test_no_feedback_reports_empty(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_feedback(callback)

        text = callback.message.edit_text.call_args.args[0]
        assert "Фидбека нет." in text

    async def test_escapes_html_in_feedback(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_feedback(5, "<b>Alice</b>", "<script>alert(1)</script>")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_feedback(callback)

        text = callback.message.edit_text.call_args.args[0]
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    async def test_no_access(self, make_callback, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_feedback(callback)

        callback.message.edit_text.assert_not_awaited()


class TestAddAdminFlow:
    async def test_cb_add_start_sets_state(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_add_start(callback, fsm_context)

        assert await fsm_context.get_state() == admin.AdminState.waiting_add_id.state

    async def test_cb_add_start_no_access(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_add_start(callback, fsm_context)

        assert await fsm_context.get_state() is None

    async def test_fsm_add_id_rejects_non_digit(self, make_message, fsm_context):
        message = make_message(text="not-a-number")

        await admin.fsm_add_id(message, fsm_context)

        assert "корректный Telegram ID" in message.answer.call_args.args[0]

    async def test_fsm_add_id_stores_id_and_advances_state(self, make_message, fsm_context):
        message = make_message(text="12345")

        await admin.fsm_add_id(message, fsm_context)

        assert await fsm_context.get_state() == admin.AdminState.waiting_add_name.state
        data = await fsm_context.get_data()
        assert data["new_admin_id"] == 12345

    async def test_fsm_add_id_ignores_missing_text_or_user(self, make_message, fsm_context):
        message = make_message(text=None)

        await admin.fsm_add_id(message, fsm_context)

        assert await fsm_context.get_state() is None

    async def test_fsm_add_name_adds_admin(self, make_message, fsm_context):
        await fsm_context.update_data(new_admin_id=555)
        message = make_message(text="New Admin")

        await admin.fsm_add_name(message, fsm_context)

        assert db.is_admin(555) is True
        assert await fsm_context.get_state() is None
        text = message.answer.call_args.args[0]
        assert "успешно добавлен" in text

    async def test_fsm_add_name_uses_placeholder_for_dash(self, make_message, fsm_context):
        await fsm_context.update_data(new_admin_id=556)
        message = make_message(text="—")

        await admin.fsm_add_name(message, fsm_context)

        admins = db.get_all_admins()
        assert any(a["user_id"] == 556 and a["full_name"] == "Без имени" for a in admins)

    async def test_fsm_add_name_reports_already_admin(self, make_message, fsm_context):
        db.add_admin(557, "Existing")
        await fsm_context.update_data(new_admin_id=557)
        message = make_message(text="Whatever")

        await admin.fsm_add_name(message, fsm_context)

        text = message.answer.call_args.args[0]
        assert "уже является" in text


class TestRemoveAdminFlow:
    async def test_cb_remove_start_lists_admins(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(100, "Target")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_remove_start(callback, fsm_context)

        markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
        button_texts = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("Target" in t for t in button_texts)

    async def test_cb_remove_start_empty_db(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_remove_start(callback, fsm_context)

        text = callback.message.edit_text.call_args.args[0]
        assert "Нет администраторов" in text

    async def test_cb_remove_confirm_removes_admin(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(200, "Removable")
        callback = make_callback(data="adm:rm:200", user=make_user(user_id=42))

        await admin.cb_remove_confirm(callback)

        assert db.is_admin(200) is False
        text = callback.message.edit_text.call_args.args[0]
        assert "удалён" in text

    async def test_cb_remove_confirm_not_found(self, make_callback, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(data="adm:rm:999999", user=make_user(user_id=42))

        await admin.cb_remove_confirm(callback)

        text = callback.message.edit_text.call_args.args[0]
        assert "не найден" in text

    async def test_cb_remove_manual_sets_state(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_remove_manual(callback, fsm_context)

        assert await fsm_context.get_state() == admin.AdminState.waiting_remove_id.state

    async def test_fsm_remove_id_rejects_non_digit(self, make_message, fsm_context):
        message = make_message(text="abc")

        await admin.fsm_remove_id(message, fsm_context)

        assert "корректный Telegram ID" in message.answer.call_args.args[0]

    async def test_fsm_remove_id_removes_admin(self, make_message, fsm_context):
        db.add_admin(321, "ToRemove")
        message = make_message(text="321")

        await admin.fsm_remove_id(message, fsm_context)

        assert db.is_admin(321) is False
        assert await fsm_context.get_state() is None
        text = message.answer.call_args.args[0]
        assert "удалён" in text

    async def test_fsm_remove_id_not_found(self, make_message, fsm_context):
        message = make_message(text="909090")

        await admin.fsm_remove_id(message, fsm_context)

        text = message.answer.call_args.args[0]
        assert "не найден" in text


class TestBackAndCancel:
    async def test_cb_back_restores_panel(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        await fsm_context.set_state("SomeState")
        callback = make_callback(user=make_user(user_id=42))

        await admin.cb_back(callback, fsm_context)

        assert await fsm_context.get_state() is None
        callback.message.edit_text.assert_awaited_once()

    async def test_cb_back_no_access(self, make_callback, fsm_context, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        callback = make_callback(user=make_user(user_id=1))

        await admin.cb_back(callback, fsm_context)

        callback.message.edit_text.assert_not_awaited()

    async def test_cb_cancel_clears_state(self, make_callback, fsm_context, monkeypatch):
        await fsm_context.set_state(admin.AdminState.waiting_add_id)
        callback = make_callback(user=make_user(user_id=42))
        monkeypatch.setenv("ADMIN_ID", "42")

        await admin.cb_cancel(callback, fsm_context)

        assert await fsm_context.get_state() is None
        callback.message.edit_text.assert_awaited_once()


class TestTextCommands:
    async def test_admin_add_no_access(self, make_message, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        message = make_message(user=make_user(user_id=1))
        command = CommandObject(command="admin_add", args="123 Name")

        await admin.cmd_admin_add(message, command)

        assert "нет доступа" in message.answer.call_args.args[0]

    async def test_admin_add_missing_args_shows_usage(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_add", args=None)

        await admin.cmd_admin_add(message, command)

        assert "Использование" in message.answer.call_args.args[0]

    async def test_admin_add_rejects_non_digit_id(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_add", args="abc Name")

        await admin.cmd_admin_add(message, command)

        assert "должен быть числом" in message.answer.call_args.args[0]

    async def test_admin_add_success_with_name(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_add", args="123 Ivan Petrov")

        await admin.cmd_admin_add(message, command)

        admins = db.get_all_admins()
        assert any(a["user_id"] == 123 and a["full_name"] == "Ivan Petrov" for a in admins)

    async def test_admin_add_default_name(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_add", args="124")

        await admin.cmd_admin_add(message, command)

        admins = db.get_all_admins()
        assert any(a["user_id"] == 124 and a["full_name"] == "Без имени" for a in admins)

    async def test_admin_add_already_exists(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(125, "Existing")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_add", args="125")

        await admin.cmd_admin_add(message, command)

        assert "уже является" in message.answer.call_args.args[0]

    async def test_admin_add_no_from_user(self, make_message):
        message = make_message()
        message.from_user = None
        command = CommandObject(command="admin_add", args="123")

        await admin.cmd_admin_add(message, command)

        message.answer.assert_not_awaited()

    async def test_admin_remove_no_access(self, make_message, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        message = make_message(user=make_user(user_id=1))
        command = CommandObject(command="admin_remove", args="123")

        await admin.cmd_admin_remove(message, command)

        assert "нет доступа" in message.answer.call_args.args[0]

    async def test_admin_remove_missing_args(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_remove", args=None)

        await admin.cmd_admin_remove(message, command)

        assert "Использование" in message.answer.call_args.args[0]

    async def test_admin_remove_rejects_non_digit(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_remove", args="abc")

        await admin.cmd_admin_remove(message, command)

        assert "должен быть числом" in message.answer.call_args.args[0]

    async def test_admin_remove_success(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(130, "ToRemove")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_remove", args="130")

        await admin.cmd_admin_remove(message, command)

        assert db.is_admin(130) is False
        assert "удалён" in message.answer.call_args.args[0]

    async def test_admin_remove_not_found(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        message = make_message(user=make_user(user_id=42))
        command = CommandObject(command="admin_remove", args="999999")

        await admin.cmd_admin_remove(message, command)

        assert "не найден" in message.answer.call_args.args[0]

    async def test_admin_remove_no_from_user(self, make_message):
        message = make_message()
        message.from_user = None
        command = CommandObject(command="admin_remove", args="123")

        await admin.cmd_admin_remove(message, command)

        message.answer.assert_not_awaited()

    async def test_admin_list_no_access(self, make_message, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        message = make_message(user=make_user(user_id=1))

        await admin.cmd_admin_list(message)

        assert "нет доступа" in message.answer.call_args.args[0]

    async def test_admin_list_shows_admins(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")
        db.add_admin(140, "Listed")
        message = make_message(user=make_user(user_id=42))

        await admin.cmd_admin_list(message)

        assert "Listed" in message.answer.call_args.args[0]

    async def test_admin_list_no_from_user(self, make_message):
        message = make_message()
        message.from_user = None

        await admin.cmd_admin_list(message)

        assert "нет доступа" in message.answer.call_args.args[0]

    async def test_reload_data_no_access(self, make_message, monkeypatch):
        monkeypatch.delenv("ADMIN_ID", raising=False)
        message = make_message(user=make_user(user_id=1))

        await admin.cmd_reload_data(message)

        assert "нет доступа" in message.answer.call_args.args[0]

    async def test_reload_data_success(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")

        async def fake_load_data(strict=False):
            learn_module.data_lessons = {"results": [1]}
            learn_module.data_chords = {"results": [1, 2]}

        monkeypatch.setattr(learn_module, "load_data", fake_load_data)
        message = make_message(user=make_user(user_id=42))

        await admin.cmd_reload_data(message)

        final_text = message.answer.call_args.args[0]
        assert "успешно обновлены" in final_text

    async def test_reload_data_error(self, make_message, monkeypatch):
        monkeypatch.setenv("ADMIN_ID", "42")

        async def failing_load_data(strict=False):
            raise RuntimeError("boom")

        monkeypatch.setattr(learn_module, "load_data", failing_load_data)
        message = make_message(user=make_user(user_id=42))

        await admin.cmd_reload_data(message)

        final_text = message.answer.call_args.args[0]
        assert "Ошибка" in final_text
