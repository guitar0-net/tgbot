from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from db.database import db

router = Router()

# Menu buttons must keep working while the user is in an input state (not be saved as feedback).
MENU_BUTTONS = {"📚 Уроки", "🎸 Аккорды", "✍️ Обратная связь", "ℹ️ Команды"}

MAX_FEEDBACK_LENGTH = 2000
FEEDBACK_TOO_LONG = f"❌ Слишком длинное сообщение (максимум {MAX_FEEDBACK_LENGTH} символов). Сократите его."


class FeedbackState(StatesGroup):
    waiting_for_feedback = State()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📚 Уроки"), KeyboardButton(text="🎸 Аккорды"))
    builder.row(KeyboardButton(text="✍️ Обратная связь"), KeyboardButton(text="ℹ️ Команды"))
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user:
        first_name = message.from_user.first_name or "пользователь"
        await message.answer(
            f"Здравствуйте, {first_name}! Воспользуйтесь меню ниже или введите /help для получения справки.",
            reply_markup=get_main_keyboard(),
        )


@router.message(F.text == "❌ Отмена")
@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return await message.answer("Нет активных действий для отмены.", reply_markup=get_main_keyboard())
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=get_main_keyboard())


@router.message(F.text == "✍️ Обратная связь")
@router.message(Command("fb"))
async def cmd_feedback(message: types.Message, command: CommandObject = None, state: FSMContext = None):
    if command and command.args:
        if len(command.args) > MAX_FEEDBACK_LENGTH:
            return await message.answer(FEEDBACK_TOO_LONG)
        if state:
            await state.clear()
        if message.from_user:
            db.add_feedback(message.from_user.id, message.from_user.full_name, command.args)
        return await message.answer("✅ Спасибо за обратную связь!", reply_markup=get_main_keyboard())

    if state:
        await state.set_state(FeedbackState.waiting_for_feedback)
    await message.answer(
        "✍️ Пожалуйста, напишите ваш отзыв или предложение "
        "(для отмены нажмите кнопку «❌ Отмена» или отправьте /cancel):",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(FeedbackState.waiting_for_feedback, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith("/"))
async def process_feedback(message: types.Message, state: FSMContext):
    if not message.text:
        return await message.answer("Пожалуйста, отправьте текстовое сообщение.")

    if message.text.strip() == "/cancel":
        await state.clear()
        return await message.answer("❌ Действие отменено.", reply_markup=get_main_keyboard())

    if len(message.text) > MAX_FEEDBACK_LENGTH:
        return await message.answer(FEEDBACK_TOO_LONG)

    if message.from_user:
        db.add_feedback(message.from_user.id, message.from_user.full_name, message.text)

    await state.clear()
    await message.answer("✅ Спасибо за обратную связь!", reply_markup=get_main_keyboard())


@router.message(F.text.in_({"ℹ️ Команды", "Команды", "❓ Справка"}))
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    return await message.answer(
        "Команды бота Guitar 0:\n\n"
        "/start - перезапустить бота\n"
        "/help - показать это сообщение\n"
        "/fb [сообщение] - отправить обратную связь\n"
        "/lessons [номер] - информация об уроке\n"
        "/chords [аккорд] - аппликатура аккорда\n",
        reply_markup=get_main_keyboard(),
    )
