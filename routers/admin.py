import asyncio
import contextlib
import html
import os

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db.database import db
from routers import learn as learn_module

router = Router()

# The admin panel is ONE message that is edited in place. While a text dialog is running (add/remove admin,
# broadcast) the panel's chat/message ids are kept in the FSM data so that the user's reply can update it.
PANEL_CHAT_KEY = "panel_chat_id"
PANEL_MESSAGE_KEY = "panel_message_id"


class AdminState(StatesGroup):
    waiting_add_id = State()
    waiting_add_name = State()
    waiting_remove_id = State()
    waiting_broadcast = State()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_env_admin_ids() -> set[int]:
    """Parse admin IDs from the ADMIN_ID env var (comma/semicolon/space separated)."""
    raw = os.getenv("ADMIN_ID", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def is_admin(user_id: int) -> bool:
    """Return True if the user is listed in the admins DB table or the ADMIN_ID env var."""
    return user_id in _get_env_admin_ids() or db.is_admin(user_id)


def _build_admin_list_lines() -> list[str]:
    """Build display lines for all admins, combining DB admins and env-configured ones."""
    lines: list[str] = ["👥 <b>Список администраторов:</b>\n"]

    db_admins = db.get_all_admins()
    db_admin_ids = {admin["user_id"] for admin in db_admins}

    if db_admins:
        for admin in db_admins:
            name = html.escape(admin["full_name"] or "—", quote=False)
            added = admin["added_at"]
            uid = admin["user_id"]
            lines.append(f"  • <code>{uid}</code> — {name} (добавлен: {added})")

    env_only_ids = _get_env_admin_ids() - db_admin_ids
    for uid in sorted(env_only_ids):
        lines.append(f"  • <code>{uid}</code> — из .env (ADMIN_ID)")

    if not db_admins and not env_only_ids:
        lines.append("Администраторов нет.")

    return lines


def _build_admin_keyboard() -> types.InlineKeyboardMarkup:
    """Build inline keyboard for the main admin panel."""
    builder = InlineKeyboardBuilder()

    builder.row(InlineKeyboardButton(text="🔄 Обновить данные с API", callback_data="adm:reload"))
    builder.row(InlineKeyboardButton(text="👥 Список администраторов", callback_data="adm:list"))
    builder.row(
        InlineKeyboardButton(text="➕ Добавить администратора", callback_data="adm:add"),
        InlineKeyboardButton(text="➖ Удалить администратора", callback_data="adm:remove"),
    )
    builder.row(InlineKeyboardButton(text="📋 Посмотреть фидбек", callback_data="adm:feedback"))
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
        InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:broadcast"),
    )

    return builder.as_markup()


def _back_keyboard(*extra_rows: list[InlineKeyboardButton]) -> types.InlineKeyboardMarkup:
    """Keyboard with a «Назад» button (to the main panel), optionally preceded by extra rows."""
    builder = InlineKeyboardBuilder()
    for row in extra_rows:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))
    return builder.as_markup()


def _cancel_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel"))
    return builder.as_markup()


ADMIN_PANEL_TEXT = "🛠 <b>Панель администратора</b>\n\nВыберите действие:"

# Telegram message length limit is 4096; keep a safety margin.
_MESSAGE_CHUNK_LIMIT = 4000

# Telegram allows ~30 messages per second to different chats; stay below that.
_BROADCAST_DELAY = 0.05

_NO_ACCESS_TEXT = "⛔ У вас нет доступа к панели администратора."


async def _replace_message(
    message: types.Message,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None,
) -> None:
    """Replace the given message's content, editing it in place when possible."""
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)


def _chunk_lines(lines: list[str], limit: int = _MESSAGE_CHUNK_LIMIT) -> list[str]:
    """Group lines into chunks that each stay under the given character limit."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # account for the joining newline
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks or [""]


async def _guard(callback: types.CallbackQuery) -> bool:
    """Common callback check. Answers the callback and returns False if the handler must stop."""
    if not callback.from_user or not callback.message:
        await callback.answer()
        return False

    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return False

    return True


async def _remember_panel(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Remember which message is the panel, so a text reply can edit it."""
    await state.update_data(
        **{PANEL_CHAT_KEY: callback.message.chat.id, PANEL_MESSAGE_KEY: callback.message.message_id}
    )


async def _update_panel(
    message: types.Message,
    panel: dict,
    text: str,
    reply_markup: types.InlineKeyboardMarkup | None,
    delete_input: bool = True,
) -> None:
    """Edit the panel message from a text handler (and remove the user's reply to keep the chat clean)."""
    if delete_input:
        with contextlib.suppress(TelegramAPIError):
            await message.delete()

    chat_id = panel.get(PANEL_CHAT_KEY)
    message_id = panel.get(PANEL_MESSAGE_KEY)
    if chat_id is not None and message_id is not None:
        try:
            await message.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=reply_markup
            )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return

    await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# /admin — open the panel
# ---------------------------------------------------------------------------


@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return await message.answer(_NO_ACCESS_TEXT)

    await state.clear()

    await message.answer(ADMIN_PANEL_TEXT, parse_mode="HTML", reply_markup=_build_admin_keyboard())


# ---------------------------------------------------------------------------
# ◀️ Назад / ❌ Отмена — return to the main panel
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "adm:back")
async def cb_back(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    await callback.answer()
    await state.clear()

    await _replace_message(callback.message, ADMIN_PANEL_TEXT, _build_admin_keyboard())


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    await callback.answer("Отменено")

    # A broadcast waiting for confirmation leaves the admin's message in the chat — remove it.
    data = await state.get_data()
    if data.get("bc_message_id") is not None:
        with contextlib.suppress(TelegramAPIError):
            await callback.message.bot.delete_message(data["bc_chat_id"], data["bc_message_id"])

    await state.clear()

    await _replace_message(callback.message, ADMIN_PANEL_TEXT, _build_admin_keyboard())


# ---------------------------------------------------------------------------
# 🔄 Обновить данные с API
# ---------------------------------------------------------------------------


async def _reload_result_text() -> str:
    """Reload the data from the API and describe the outcome."""
    try:
        await learn_module.load_data(strict=True)
    except Exception as e:
        return f"❌ Ошибка при загрузке данных: {html.escape(str(e), quote=False)}"

    lessons_count = len(learn_module.data_lessons.get("results", []))
    chords_count = len(learn_module.data_chords.get("results", []))
    return (
        f"✅ <b>Данные успешно обновлены!</b>\n\n📚 Уроков: <b>{lessons_count}</b>\n🎸 Аккордов: <b>{chords_count}</b>"
    )


@router.callback_query(F.data == "adm:reload")
async def cb_reload(callback: types.CallbackQuery):
    if not await _guard(callback):
        return

    await callback.answer()

    await _replace_message(callback.message, "⏳ Загружаю данные с API...", _back_keyboard())
    await _replace_message(callback.message, await _reload_result_text(), _back_keyboard())


# ---------------------------------------------------------------------------
# 👥 Список администраторов
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "adm:list")
async def cb_list(callback: types.CallbackQuery):
    if not await _guard(callback):
        return

    await callback.answer()

    await _replace_message(callback.message, "\n".join(_build_admin_list_lines()), _back_keyboard())


# ---------------------------------------------------------------------------
# 📋 Посмотреть фидбек (paginated, still a single message)
# ---------------------------------------------------------------------------


def _build_feedback_page(page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    feedback_list = db.get_recent_feedback(days=30)

    lines: list[str] = ["📋 <b>Фидбек за последний месяц:</b>"]
    if feedback_list:
        for fb in feedback_list:
            name = html.escape(fb["user_name"] or "—")
            text = html.escape(fb["text"] or "")
            lines.append(f"\n🗓 <b>{fb['date']}</b> — {name}\n{text}")
    else:
        lines.append("\nФидбека нет.")

    chunks = _chunk_lines(lines)
    page = max(0, min(page, len(chunks) - 1))

    nav: list[InlineKeyboardButton] = []
    if len(chunks) > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:fb:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{len(chunks)}", callback_data=f"adm:fb:{page}"))
        if page < len(chunks) - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:fb:{page + 1}"))

    return chunks[page], _back_keyboard(nav) if nav else _back_keyboard()


@router.callback_query(F.data.in_({"adm:feedback"}) | F.data.startswith("adm:fb:"))
async def cb_feedback(callback: types.CallbackQuery):
    if not await _guard(callback):
        return

    await callback.answer()

    page = 0
    data = callback.data or ""
    if data.startswith("adm:fb:") and data.rsplit(":", 1)[-1].isdigit():
        page = int(data.rsplit(":", 1)[-1])

    text, markup = _build_feedback_page(page)
    await _replace_message(callback.message, text, markup)


# ---------------------------------------------------------------------------
# 📊 Статистика
# ---------------------------------------------------------------------------


def _build_stats_text() -> str:
    stats = db.get_user_stats()
    return (
        "📊 <b>Статистика</b>\n\n"
        f"👤 Всего пользователей: <b>{stats['total']}</b>\n"
        f"🆕 Новых за 24 часа: <b>{stats['new_day']}</b>\n"
        f"🆕 Новых за 7 дней: <b>{stats['new_week']}</b>\n"
        f"🔥 Активных за 24 часа: <b>{stats['active_day']}</b>\n"
        f"🔥 Активных за 7 дней: <b>{stats['active_week']}</b>\n"
        f"✍️ Отзывов всего: <b>{db.get_feedback_count()}</b>\n"
        f"📚 Уроков загружено: <b>{len(learn_module.data_lessons.get('results', []))}</b>\n"
        f"🎸 Аккордов загружено: <b>{len(learn_module.data_chords.get('results', []))}</b>"
    )


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: types.CallbackQuery):
    if not await _guard(callback):
        return

    await callback.answer()

    refresh = [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:stats")]
    await _replace_message(callback.message, _build_stats_text(), _back_keyboard(refresh))


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return await message.answer(_NO_ACCESS_TEXT)

    await message.answer(_build_stats_text(), parse_mode="HTML")


# ---------------------------------------------------------------------------
# ➕ Добавить администратора (FSM)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "adm:add")
async def cb_add_start(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    await callback.answer()
    await state.set_state(AdminState.waiting_add_id)
    await _remember_panel(callback, state)

    await _replace_message(
        callback.message,
        "➕ <b>Добавление администратора</b>\n\nВведите <b>Telegram ID</b> нового администратора (только цифры):",
        _cancel_keyboard(),
    )


@router.message(AdminState.waiting_add_id)
async def fsm_add_id(message: types.Message, state: FSMContext):
    if not message.text or not message.from_user:
        return

    panel = await state.get_data()
    text = message.text.strip()
    if not text.isdigit():
        return await _update_panel(
            message,
            panel,
            "➕ <b>Добавление администратора</b>\n\n❌ Введите корректный Telegram ID (только цифры):",
            _cancel_keyboard(),
        )

    new_id = int(text)

    await state.update_data(new_admin_id=new_id)
    await state.set_state(AdminState.waiting_add_name)

    await _update_panel(
        message,
        panel,
        f"✏️ Теперь введите имя для <code>{new_id}</code> (или напишите <b>—</b> чтобы пропустить):",
        _cancel_keyboard(),
    )


@router.message(AdminState.waiting_add_name)
async def fsm_add_name(message: types.Message, state: FSMContext):
    if not message.text or not message.from_user:
        return

    data = await state.get_data()
    new_id: int = data["new_admin_id"]
    name = message.text.strip() if message.text.strip() != "—" else "Без имени"

    await state.clear()

    added = db.add_admin(new_id, name)
    if added:
        text = f"✅ Администратор <code>{new_id}</code> (<b>{html.escape(name, quote=False)}</b>) успешно добавлен."
    else:
        text = f"ℹ️ Пользователь <code>{new_id}</code> уже является администратором."

    await _update_panel(message, data, text, _back_keyboard())


# ---------------------------------------------------------------------------
# ➖ Удалить администратора (FSM)
# ---------------------------------------------------------------------------

_SELF_REMOVE_TEXT = "⛔ Нельзя удалить самого себя."


@router.callback_query(F.data == "adm:remove")
async def cb_remove_start(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    await callback.answer()

    db_admins = db.get_all_admins()
    if not db_admins:
        return await _replace_message(
            callback.message, "ℹ️ Нет администраторов для удаления (БД пуста).", _back_keyboard()
        )

    builder = InlineKeyboardBuilder()
    for admin in db_admins:
        name = admin["full_name"] or "Без имени"
        uid = admin["user_id"]
        builder.row(InlineKeyboardButton(text=f"🗑 {name} ({uid})", callback_data=f"adm:rm:{uid}"))

    builder.row(InlineKeyboardButton(text="✏️ Ввести ID вручную", callback_data="adm:remove_manual"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await _replace_message(callback.message, "➖ <b>Выберите администратора для удаления:</b>", builder.as_markup())


@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_remove_confirm(callback: types.CallbackQuery):
    if not await _guard(callback):
        return

    target_id = int(callback.data.split(":")[-1])

    if target_id == callback.from_user.id:
        return await callback.answer(_SELF_REMOVE_TEXT, show_alert=True)

    removed = db.remove_admin(target_id)
    await callback.answer()

    if removed:
        text = f"✅ Администратор <code>{target_id}</code> удалён."
    else:
        text = f"❌ Пользователь <code>{target_id}</code> не найден среди администраторов."

    await _replace_message(callback.message, text, _back_keyboard())


@router.callback_query(F.data == "adm:remove_manual")
async def cb_remove_manual(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    await callback.answer()
    await state.set_state(AdminState.waiting_remove_id)
    await _remember_panel(callback, state)

    await _replace_message(
        callback.message,
        "✏️ Введите <b>Telegram ID</b> администратора для удаления:",
        _cancel_keyboard(),
    )


@router.message(AdminState.waiting_remove_id)
async def fsm_remove_id(message: types.Message, state: FSMContext):
    if not message.text or not message.from_user:
        return

    panel = await state.get_data()
    text = message.text.strip()
    if not text.isdigit():
        return await _update_panel(
            message,
            panel,
            "✏️ <b>Удаление администратора</b>\n\n❌ Введите корректный Telegram ID (только цифры):",
            _cancel_keyboard(),
        )

    target_id = int(text)
    await state.clear()

    if target_id == message.from_user.id:
        result_text = _SELF_REMOVE_TEXT
    elif db.remove_admin(target_id):
        result_text = f"✅ Администратор <code>{target_id}</code> удалён."
    else:
        result_text = f"❌ Пользователь <code>{target_id}</code> не найден среди администраторов."

    await _update_panel(message, panel, result_text, _back_keyboard())


# ---------------------------------------------------------------------------
# 📢 Рассылка (FSM: сообщение -> подтверждение -> отправка)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    await callback.answer()
    await state.set_state(AdminState.waiting_broadcast)
    await _remember_panel(callback, state)

    await _replace_message(
        callback.message,
        "📢 <b>Рассылка</b>\n\n"
        f"Получателей: <b>{len(db.get_all_user_ids())}</b>\n"
        "Отправьте сообщение для рассылки (текст, фото, видео и т.д. — оно будет скопировано как есть):",
        _cancel_keyboard(),
    )


@router.message(AdminState.waiting_broadcast)
async def fsm_broadcast_text(message: types.Message, state: FSMContext):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    await state.update_data(bc_chat_id=message.chat.id, bc_message_id=message.message_id)
    panel = await state.get_data()

    confirm_builder = InlineKeyboardBuilder()
    confirm_builder.row(
        InlineKeyboardButton(text="✅ Отправить", callback_data="adm:bc_send"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel"),
    )

    # The admin's message stays in the chat as a preview until the broadcast is sent or cancelled.
    await _update_panel(
        message,
        panel,
        f"📢 Отправить сообщение выше <b>{len(db.get_all_user_ids())}</b> пользователям?",
        confirm_builder.as_markup(),
        delete_input=False,
    )


async def _copy_with_retry(bot, user_id: int, from_chat_id: int, message_id: int) -> None:
    try:
        await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)


@router.callback_query(F.data == "adm:bc_send")
async def cb_broadcast_send(callback: types.CallbackQuery, state: FSMContext):
    if not await _guard(callback):
        return

    data = await state.get_data()
    chat_id = data.get("bc_chat_id")
    message_id = data.get("bc_message_id")
    if chat_id is None or message_id is None:
        return await callback.answer("Нет сообщения для рассылки.", show_alert=True)

    await callback.answer()
    await state.clear()

    user_ids = db.get_all_user_ids()
    await _replace_message(callback.message, f"⏳ Рассылка запущена ({len(user_ids)} получателей)...", None)

    sent = blocked = failed = 0
    for user_id in user_ids:
        try:
            await _copy_with_retry(callback.message.bot, user_id, chat_id, message_id)
            sent += 1
        except TelegramForbiddenError:
            db.remove_user(user_id)  # user blocked the bot or deleted the chat
            blocked += 1
        except TelegramAPIError:
            failed += 1
        await asyncio.sleep(_BROADCAST_DELAY)

    with contextlib.suppress(TelegramAPIError):
        await callback.message.bot.delete_message(chat_id, message_id)

    await _replace_message(
        callback.message,
        "✅ <b>Рассылка завершена</b>\n\n"
        f"📨 Доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота (удалены из базы): <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>",
        _back_keyboard(),
    )


# ---------------------------------------------------------------------------
# /admin_add, /admin_remove, /admin_list, /reload_data still work as text commands
# ---------------------------------------------------------------------------


@router.message(Command("admin_add"))
async def cmd_admin_add(message: types.Message, command: CommandObject):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return await message.answer(_NO_ACCESS_TEXT)

    if not command.args:
        return await message.answer(
            "ℹ️ Использование: <code>/admin_add &lt;user_id&gt; [имя]</code>\n"
            "Пример: <code>/admin_add 123456789 Иван</code>",
            parse_mode="HTML",
        )

    parts = command.args.split(maxsplit=1)
    user_id_str = parts[0]
    name = parts[1] if len(parts) > 1 else "Без имени"

    if not user_id_str.isdigit():
        return await message.answer("❌ user_id должен быть числом.")

    new_admin_id = int(user_id_str)

    added = db.add_admin(new_admin_id, name)
    if added:
        await message.answer(
            f"✅ Администратор <code>{new_admin_id}</code> (<b>{html.escape(name, quote=False)}</b>) успешно добавлен.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"ℹ️ Пользователь <code>{new_admin_id}</code> уже является администратором.",
            parse_mode="HTML",
        )


@router.message(Command("admin_remove"))
async def cmd_admin_remove(message: types.Message, command: CommandObject):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return await message.answer(_NO_ACCESS_TEXT)

    if not command.args:
        return await message.answer(
            "ℹ️ Использование: <code>/admin_remove &lt;user_id&gt;</code>\nПример: <code>/admin_remove 123456789</code>",
            parse_mode="HTML",
        )

    user_id_str = command.args.strip()
    if not user_id_str.isdigit():
        return await message.answer("❌ user_id должен быть числом.")

    target_id = int(user_id_str)

    if target_id == message.from_user.id:
        return await message.answer(_SELF_REMOVE_TEXT)

    removed = db.remove_admin(target_id)
    if removed:
        await message.answer(f"✅ Администратор <code>{target_id}</code> удалён.", parse_mode="HTML")
    else:
        await message.answer(
            f"❌ Пользователь <code>{target_id}</code> не найден среди администраторов БД.",
            parse_mode="HTML",
        )


@router.message(Command("admin_list"))
async def cmd_admin_list(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return await message.answer(_NO_ACCESS_TEXT)

    await message.answer("\n".join(_build_admin_list_lines()), parse_mode="HTML")


@router.message(Command("reload_data"))
async def cmd_reload_data(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return await message.answer(_NO_ACCESS_TEXT)

    await message.answer("⏳ Загружаю данные с API...")
    await message.answer(await _reload_result_text(), parse_mode="HTML")
