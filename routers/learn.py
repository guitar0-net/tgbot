import html
import re

import resvg_py
from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from db.api import get_api_data, get_lesson_detail
from routers.base import get_main_keyboard

router = Router()

data_lessons = {"results": []}
data_chords = {"results": []}


class LearnState(StatesGroup):
    waiting_for_lesson = State()
    waiting_for_chord = State()


def get_watch_url(embed_or_watch_url: str) -> str:
    if "/embed/" in embed_or_watch_url:
        video_id = embed_or_watch_url.split("/embed/")[-1].split("?")[0]
        return f"https://www.youtube.com/watch?v={video_id}"
    return embed_or_watch_url


def format_song_text(raw_text: str) -> str:
    """Convert the song's Markdown text (from the API) to Telegram HTML markup."""
    if not raw_text:
        return ""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("&nbsp;", " ").replace("\u3000", " ")
    text = html.escape(text, quote=False)

    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        header_match = re.match(r"^#{1,6}\s*(.*)$", line)
        if header_match:
            content = header_match.group(1).strip()
            line = f"<b>{content}</b>" if content else ""
        lines.append(line)
    text = "\n".join(lines)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def load_data():
    global data_lessons, data_chords
    try:
        api_data = await get_api_data()
        data_lessons = api_data[0]
        data_chords = api_data[1]
    except Exception:
        pass


def render_chord_svg(svg_code: str) -> bytes:
    svg = svg_code.replace("currentColor", "#1e1e1e")

    # Add crisp white background
    if "<rect" not in svg:
        insert_pos = svg.find(">") + 1
        bg_rect = '<rect width="100%" height="100%" fill="#ffffff"/>'
        svg = svg[:insert_pos] + bg_rect + svg[insert_pos:]

    # Scale width and height based on viewBox for high-res crisp rendering
    vb_match = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    if vb_match:
        w = int(vb_match.group(1)) * 4
        h = int(vb_match.group(2)) * 4
        svg = re.sub(r"<svg\s+", f'<svg width="{w}" height="{h}" ', svg, count=1)

    return resvg_py.svg_to_bytes(svg)


def extract_svg_aria_label(svg_code: str) -> str:
    if not svg_code:
        return ""
    match = re.search(r'aria-label="([^"]*)"', svg_code)
    return match.group(1) if match else ""


def format_chord(chord: dict, idx: int = 1, total: int = 1, aria_label: str = "") -> str:
    title = chord.get("title", "")
    musical_title = chord.get("musical_title", "")

    header = f"🎸 Аккорд {title}"
    if musical_title:
        header += f" ({musical_title})"
    if total > 1:
        header += f" [Вариант {idx}/{total}]"

    if aria_label:
        return f"{header}\n\n{aria_label}"

    return header


def get_lessons_keyboard() -> ReplyKeyboardMarkup:
    global data_lessons
    results = data_lessons.get("results", [])

    builder = ReplyKeyboardBuilder()
    for i in range(len(results)):
        builder.button(text=str(i))
    builder.adjust(10)

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")], *builder.export()],
        resize_keyboard=True,
    )


async def show_lesson(message: types.Message, query: str) -> bool:
    global data_lessons
    if not data_lessons.get("results"):
        await load_data()

    results = data_lessons.get("results", [])

    arg = query.strip()
    if not arg.isdigit():
        await message.answer(
            "Пожалуйста, укажите корректный номер урока (число). Наример: 1", reply_markup=get_main_keyboard()
        )
        return False

    lesson_idx = int(arg)
    if lesson_idx < 0 or lesson_idx >= len(results):
        max_idx = len(results) - 1 if results else 0
        await message.answer(
            f"Урок с номером {lesson_idx} не найден. Доступные номера: от 0 до {max_idx}.",
            reply_markup=get_main_keyboard(),
        )
        return False

    lesson = results[lesson_idx]
    title = lesson.get("title", f"Урок № {lesson_idx}")
    video_url = lesson.get("video_url", "")
    songs = lesson.get("songs", [])

    lessons_message = f"📚 {title}\n"
    if video_url:
        watch_url = get_watch_url(video_url)
        lessons_message += f"\n▶️ Ссылка на видео: {watch_url}\n"

    if songs:
        lessons_message += "\n🎵 Песни в уроке:\n"
        for song in songs:
            song_title = song.get("title", "")
            if song_title:
                lessons_message += f"• {song_title}\n"

    builder = InlineKeyboardBuilder()

    top_buttons = []

    for i, song in enumerate(songs):
        song_title = song.get("title", "").strip()
        if not song_title:
            continue
        btn_text = f"🎵 {song_title}" if len(song_title) <= 22 else f"🎵 {song_title[:19]}..."
        top_buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"song:{lesson_idx}:{i}"))

    for j in range(0, len(top_buttons), 2):
        builder.row(*top_buttons[j : j + 2])

    if top_buttons:
        await message.answer(lessons_message, reply_markup=builder.as_markup())
        await message.answer("📋 Главное меню:", reply_markup=get_main_keyboard())
    else:
        await message.answer(lessons_message, reply_markup=get_main_keyboard())

    return True


def _chunk_html_message(text: str, limit: int = 3900) -> list[str]:
    """Split text into chunks without breaking lines, so HTML tags stay intact."""
    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush():
        if current:
            chunks.append("\n".join(current))
            current.clear()

    for line in lines:
        if len(line) > limit:
            flush()
            current_len = 0
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            flush()
            current_len = 0
        current.append(line)
        current_len += line_len

    flush()
    return chunks or [""]


@router.callback_query(F.data.startswith("song:"))
async def callback_song(callback: types.CallbackQuery):
    await callback.answer()
    if not callback.data:
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        return

    lesson_idx = int(parts[1])
    song_idx = int(parts[2])

    global data_lessons
    if not data_lessons.get("results"):
        await load_data()

    results = data_lessons.get("results", [])
    if lesson_idx < 0 or lesson_idx >= len(results):
        if callback.message:
            await callback.message.answer("❌ Урок не найден.")
        return

    lesson = results[lesson_idx]
    lesson_uuid = lesson.get("uuid")
    if not lesson_uuid:
        if callback.message:
            await callback.message.answer("❌ Данные урока недоступны.")
        return

    lesson_detail = await get_lesson_detail(lesson_uuid)
    songs_detail = lesson_detail.get("songs", [])

    if not songs_detail or song_idx < 0 or song_idx >= len(songs_detail):
        summary_songs = lesson.get("songs", [])
        if 0 <= song_idx < len(summary_songs) and callback.message:
            song_title = summary_songs[song_idx].get("title", "")
            return await callback.message.answer(f"🎵 {song_title}\n\n(Подробный текст песни пока недоступен)")
        if callback.message:
            await callback.message.answer("❌ Песня не найдена.")
        return

    song = songs_detail[song_idx]
    title = html.escape(song.get("title", "Песня"), quote=False)
    metronome = song.get("metronome")
    schemes = song.get("schemes", [])
    chords = song.get("chords", [])
    raw_text = song.get("text", "")

    msg_parts = [f"🎵 {title}\n"]

    if metronome:
        msg_parts.append(f"⏱️ Темп (метроном): {metronome} BPM")

    if schemes:
        scheme_names = [
            html.escape(s.get("inscription") or s.get("title"), quote=False)
            for s in schemes
            if (s.get("inscription") or s.get("title"))
        ]
        if scheme_names:
            msg_parts.append(f"🥁 Бой/перебор: {', '.join(scheme_names)}")

    if chords:
        chord_names = [html.escape(c.get("title"), quote=False) for c in chords if c.get("title")]
        if chord_names:
            msg_parts.append(f"🎸 Аккорды в песне: {', '.join(chord_names)}")

    cleaned_text = format_song_text(raw_text)
    if cleaned_text:
        msg_parts.append("\n📝 Текст и аккорды:\n" + cleaned_text)

    full_message = "\n".join(msg_parts)

    if callback.message:
        if len(full_message) <= 4000:
            await callback.message.answer(full_message, parse_mode="HTML")
        else:
            for chunk in _chunk_html_message(full_message):
                await callback.message.answer(chunk, parse_mode="HTML")


def get_chords_keyboard() -> ReplyKeyboardMarkup:
    global data_chords
    results = data_chords.get("results", [])

    seen: set[str] = set()
    titles: list[str] = []
    for chord in results:
        title = chord.get("title", "").strip()
        if title and not title.isdigit() and title not in seen:
            seen.add(title)
            titles.append(title)

    builder = ReplyKeyboardBuilder()
    for title in titles:
        builder.button(text=title)
    builder.adjust(10)

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")], *builder.export()],
        resize_keyboard=True,
    )


async def show_chord(message: types.Message, query: str) -> bool:
    global data_chords
    if not data_chords.get("results"):
        await load_data()

    results = data_chords.get("results", [])
    query = query.strip()

    # Поиск по точному названию аккорда (например, "Am", "am", "C", "Em")
    matched_chords = [chord for chord in results if chord.get("title", "").strip().lower() == query.lower()]

    # Если точного совпадения нет и введено число, пробуем найти по индексу или ID
    if not matched_chords and query.isdigit():
        idx = int(query)
        if 0 <= idx < len(results):
            matched_chords = [results[idx]]
        else:
            matched_chords = [chord for chord in results if chord.get("id") == idx]

    # Если всё ещё не найдено, ищем частичное совпадение по названию или описанию
    if not matched_chords:
        matched_chords = [
            chord
            for chord in results
            if query.lower() in chord.get("title", "").lower()
            or query.lower() in chord.get("musical_title", "").lower()
        ]

    if not matched_chords:
        await message.answer(f"❌ Аккорд «{query}» не найден.\nПопробуйте, например: Am, C, Em, D")
        return False

    total = len(matched_chords)
    shown = matched_chords[:3]
    for i, chord in enumerate(shown, 1):
        svg = chord.get("svg_vertical") or chord.get("svg_horizontal")
        aria_label = extract_svg_aria_label(svg)
        caption = format_chord(chord, idx=i, total=total, aria_label=aria_label)
        is_last = i == len(shown)
        markup = get_main_keyboard() if is_last else None
        if svg:
            try:
                png_bytes = render_chord_svg(svg)
                photo = types.BufferedInputFile(png_bytes, filename=f"chord_{chord.get('id', i)}.png")
                await message.answer_photo(photo=photo, caption=caption, reply_markup=markup)
                continue
            except Exception:
                pass
        await message.answer(caption, reply_markup=markup)

    return True


@router.message(F.text == "📚 Уроки")
@router.message(Command("lessons"))
async def cmd_lessons(message: types.Message, command: CommandObject = None, state: FSMContext = None):
    if command and command.args:
        if state:
            await state.clear()
        return await show_lesson(message, command.args)

    if not data_lessons.get("results"):
        await load_data()

    if state:
        await state.set_state(LearnState.waiting_for_lesson)

    await message.answer(
        "📚 Введите номер урока, который вы хотите посмотреть (например: 1) "
        "(для отмены нажмите «❌ Отмена» или отправьте /cancel):",
        reply_markup=get_lessons_keyboard(),
    )


@router.message(LearnState.waiting_for_lesson)
async def process_lesson_input(message: types.Message, state: FSMContext):
    if not message.text:
        return await message.answer("Пожалуйста, введите номер урока текстом.")

    text = message.text.strip()
    if text in {"/cancel", "❌ Отмена"}:
        await state.clear()
        return await message.answer("❌ Действие отменено.", reply_markup=get_main_keyboard())

    success = await show_lesson(message, text)
    if success:
        await state.clear()


@router.message(F.text == "🎸 Аккорды")
@router.message(Command("chords"))
async def cmd_chords(message: types.Message, command: CommandObject = None, state: FSMContext = None):
    if command and command.args:
        if state:
            await state.clear()
        return await show_chord(message, command.args)

    if not data_chords.get("results"):
        await load_data()

    if state:
        await state.set_state(LearnState.waiting_for_chord)
    await message.answer(
        "🎸 Введите название или номер аккорда (например: Am, C, Em) "
        "или выберите его на клавиатуре ниже "
        "(для отмены нажмите «❌ Отмена» или отправьте /cancel):",
        reply_markup=get_chords_keyboard(),
    )


@router.message(LearnState.waiting_for_chord)
async def process_chord_input(message: types.Message, state: FSMContext):
    if not message.text:
        return await message.answer("Пожалуйста, введите название аккорда текстом.")

    text = message.text.strip()
    if text in {"/cancel", "❌ Отмена"}:
        await state.clear()
        return await message.answer("❌ Действие отменено.", reply_markup=get_main_keyboard())

    success = await show_chord(message, text)
    if success:
        await state.clear()
