from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from routers import learn

SAMPLE_SVG = (
    '<svg viewBox="0 0 100 150" aria-label="Открытый аккорд Am">'
    '<path fill="currentColor" d="M0 0"/></svg>'
)


@pytest.fixture(autouse=True)
def reset_learn_data():
    original_lessons = learn.data_lessons
    original_chords = learn.data_chords
    learn.data_lessons = {"results": []}
    learn.data_chords = {"results": []}
    yield
    learn.data_lessons = original_lessons
    learn.data_chords = original_chords


class TestGetWatchUrl:
    def test_converts_embed_url(self):
        url = "https://www.youtube.com/embed/abc123?rel=0"
        assert learn.get_watch_url(url) == "https://www.youtube.com/watch?v=abc123"

    def test_leaves_watch_url_unchanged(self):
        url = "https://www.youtube.com/watch?v=abc123"
        assert learn.get_watch_url(url) == url


class TestFormatSongText:
    def test_empty_text_returns_empty_string(self):
        assert learn.format_song_text("") == ""
        assert learn.format_song_text(None) == ""

    def test_normalizes_line_endings(self):
        text = "line1\r\nline2\rline3"
        assert learn.format_song_text(text) == "line1\nline2\nline3"

    def test_converts_markdown_headers_to_bold_html(self):
        text = "### Chorus\nSome lyrics"
        assert learn.format_song_text(text) == "<b>Chorus</b>\nSome lyrics"

    def test_converts_markdown_bold_to_html(self):
        text = "**Em C G D**\nLyrics"
        assert learn.format_song_text(text) == "<b>Em C G D</b>\nLyrics"

    def test_escapes_html_special_characters(self):
        text = "5 < 10 & 10 > 5"
        assert learn.format_song_text(text) == "5 &lt; 10 &amp; 10 &gt; 5"

    def test_replaces_nbsp_and_ideographic_space(self):
        text = "hello&nbsp;world　!"
        assert learn.format_song_text(text) == "hello world !"

    def test_collapses_extra_blank_lines(self):
        text = "line1\n\n\n\nline2"
        assert learn.format_song_text(text) == "line1\n\nline2"

    def test_strips_leading_trailing_whitespace(self):
        assert learn.format_song_text("  \n hello \n  ") == "hello"


class TestLoadData:
    async def test_populates_module_globals(self, monkeypatch):
        async def fake_get_api_data():
            return [{"results": ["lesson1"]}, {"results": ["chord1"]}]

        monkeypatch.setattr(learn, "get_api_data", fake_get_api_data)

        await learn.load_data()

        assert learn.data_lessons == {"results": ["lesson1"]}
        assert learn.data_chords == {"results": ["chord1"]}

    async def test_swallows_exceptions(self, monkeypatch):
        async def failing_get_api_data():
            raise RuntimeError("network down")

        monkeypatch.setattr(learn, "get_api_data", failing_get_api_data)

        await learn.load_data()  # should not raise


class TestRenderChordSvg:
    def test_adds_background_rect_and_scales(self):
        png_bytes = learn.render_chord_svg(SAMPLE_SVG)
        assert isinstance(png_bytes, (bytes, bytearray))
        assert len(png_bytes) > 0

    def test_keeps_existing_rect(self):
        svg_with_rect = '<svg viewBox="0 0 10 10"><rect width="1" height="1"/></svg>'
        png_bytes = learn.render_chord_svg(svg_with_rect)
        assert isinstance(png_bytes, (bytes, bytearray))


class TestExtractSvgAriaLabel:
    def test_extracts_label(self):
        assert learn.extract_svg_aria_label(SAMPLE_SVG) == "Открытый аккорд Am"

    def test_returns_empty_when_missing(self):
        assert learn.extract_svg_aria_label("<svg></svg>") == ""

    def test_returns_empty_for_falsy_input(self):
        assert learn.extract_svg_aria_label("") == ""
        assert learn.extract_svg_aria_label(None) == ""


class TestFormatChord:
    def test_basic_header(self):
        chord = {"title": "Am"}
        assert learn.format_chord(chord) == "🎸 Аккорд Am"

    def test_includes_musical_title(self):
        chord = {"title": "Am", "musical_title": "A minor"}
        assert learn.format_chord(chord) == "🎸 Аккорд Am (A minor)"

    def test_includes_variant_counter_when_multiple(self):
        chord = {"title": "Am"}
        result = learn.format_chord(chord, idx=2, total=3)
        assert "[Вариант 2/3]" in result

    def test_appends_aria_label(self):
        chord = {"title": "Am"}
        result = learn.format_chord(chord, aria_label="fingering info")
        assert result == "🎸 Аккорд Am\n\nfingering info"


class TestGetLessonsKeyboard:
    def test_builds_numbered_buttons(self):
        learn.data_lessons = {"results": [{}, {}, {}]}
        markup = learn.get_lessons_keyboard()
        texts = [btn.text for row in markup.keyboard for btn in row]
        assert "❌ Отмена" in texts
        assert {"0", "1", "2"}.issubset(set(texts))

    def test_empty_results_still_has_cancel(self):
        learn.data_lessons = {"results": []}
        markup = learn.get_lessons_keyboard()
        texts = [btn.text for row in markup.keyboard for btn in row]
        assert texts == ["❌ Отмена"]


class TestShowLesson:
    async def test_invalid_query_reports_error(self, make_message):
        learn.data_lessons = {"results": [{"title": "Lesson 1"}]}
        message = make_message()

        result = await learn.show_lesson(message, "abc")

        assert result is False
        assert "корректный номер" in message.answer.call_args.args[0]

    async def test_out_of_range_reports_error(self, make_message):
        learn.data_lessons = {"results": [{"title": "Lesson 1"}]}
        message = make_message()

        result = await learn.show_lesson(message, "5")

        assert result is False
        assert "не найден" in message.answer.call_args.args[0]

    async def test_loads_data_when_empty(self, make_message, monkeypatch):
        learn.data_lessons = {"results": []}
        called = {"loaded": False}

        async def fake_load_data():
            called["loaded"] = True
            learn.data_lessons = {"results": [{"title": "Lesson 1"}]}

        monkeypatch.setattr(learn, "load_data", fake_load_data)
        message = make_message()

        await learn.show_lesson(message, "0")

        assert called["loaded"] is True

    async def test_valid_lesson_without_songs_sends_single_message(self, make_message):
        learn.data_lessons = {
            "results": [{"title": "Lesson 1", "video_url": "https://youtu.be/embed/xyz", "songs": []}]
        }
        message = make_message()

        result = await learn.show_lesson(message, "0")

        assert result is True
        message.answer.assert_awaited_once()
        text = message.answer.call_args.args[0]
        assert "Lesson 1" in text

    async def test_valid_lesson_with_songs_sends_two_messages(self, make_message):
        learn.data_lessons = {
            "results": [
                {
                    "title": "Lesson 1",
                    "video_url": "",
                    "songs": [{"title": "Song A"}, {"title": "Song B"}],
                }
            ]
        }
        message = make_message()

        result = await learn.show_lesson(message, "0")

        assert result is True
        assert message.answer.await_count == 2

    async def test_song_title_truncated_when_long(self, make_message):
        long_title = "S" * 30
        learn.data_lessons = {
            "results": [{"title": "Lesson 1", "video_url": "", "songs": [{"title": long_title}]}]
        }
        message = make_message()

        await learn.show_lesson(message, "0")

        markup = message.answer.call_args_list[0].kwargs["reply_markup"]
        btn_text = markup.inline_keyboard[0][0].text
        assert btn_text.endswith("...")

    async def test_song_without_title_is_skipped(self, make_message):
        learn.data_lessons = {
            "results": [{"title": "Lesson 1", "video_url": "", "songs": [{"title": ""}]}]
        }
        message = make_message()

        result = await learn.show_lesson(message, "0")

        assert result is True
        message.answer.assert_awaited_once()  # no inline buttons -> single message


class TestCallbackSong:
    async def test_no_data_returns_early(self, make_callback):
        callback = make_callback(data=None)

        await learn.callback_song(callback)

        callback.answer.assert_awaited_once()

    async def test_malformed_data_returns_early(self, make_callback):
        callback = make_callback(data="song:only-two")

        await learn.callback_song(callback)

        callback.message.answer.assert_not_awaited()

    async def test_lesson_not_found(self, make_callback, monkeypatch):
        learn.data_lessons = {"results": []}
        monkeypatch.setattr(learn, "load_data", AsyncMock())
        callback = make_callback(data="song:0:0")

        await learn.callback_song(callback)

        callback.message.answer.assert_awaited_once()
        assert "Урок не найден" in callback.message.answer.call_args.args[0]

    async def test_missing_lesson_uuid(self, make_callback):
        learn.data_lessons = {"results": [{"title": "L1"}]}
        callback = make_callback(data="song:0:0")

        await learn.callback_song(callback)

        assert "Данные урока недоступны" in callback.message.answer.call_args.args[0]

    async def test_loads_data_when_empty(self, make_callback, monkeypatch):
        learn.data_lessons = {"results": []}
        called = {"loaded": False}

        async def fake_load_data():
            called["loaded"] = True

        monkeypatch.setattr(learn, "load_data", fake_load_data)
        callback = make_callback(data="song:0:0")

        await learn.callback_song(callback)

        assert called["loaded"] is True

    async def test_song_detail_missing_falls_back_to_summary(self, make_callback, monkeypatch):
        learn.data_lessons = {
            "results": [{"title": "L1", "uuid": "u1", "songs": [{"title": "Summary Song"}]}]
        }

        async def fake_get_lesson_detail(uuid):
            return {"songs": []}

        monkeypatch.setattr(learn, "get_lesson_detail", fake_get_lesson_detail)
        callback = make_callback(data="song:0:0")

        await learn.callback_song(callback)

        text = callback.message.answer.call_args.args[0]
        assert "Summary Song" in text
        assert "недоступен" in text

    async def test_song_not_found_at_all(self, make_callback, monkeypatch):
        learn.data_lessons = {"results": [{"title": "L1", "uuid": "u1", "songs": []}]}

        async def fake_get_lesson_detail(uuid):
            return {"songs": []}

        monkeypatch.setattr(learn, "get_lesson_detail", fake_get_lesson_detail)
        callback = make_callback(data="song:0:5")

        await learn.callback_song(callback)

        assert "Песня не найдена" in callback.message.answer.call_args.args[0]

    async def test_full_song_detail_rendered(self, make_callback, monkeypatch):
        learn.data_lessons = {"results": [{"title": "L1", "uuid": "u1", "songs": [{"title": "Song A"}]}]}

        async def fake_get_lesson_detail(uuid):
            return {
                "songs": [
                    {
                        "title": "Song A",
                        "metronome": 90,
                        "schemes": [{"inscription": "Down-up"}],
                        "chords": [{"title": "Am"}, {"title": "C"}],
                        "text": "Verse one\nVerse two",
                    }
                ]
            }

        monkeypatch.setattr(learn, "get_lesson_detail", fake_get_lesson_detail)
        callback = make_callback(data="song:0:0")

        await learn.callback_song(callback)

        text = callback.message.answer.call_args.args[0]
        assert "90 BPM" in text
        assert "Down-up" in text
        assert "Am, C" in text
        assert "Verse one" in text

    async def test_long_song_text_is_chunked(self, make_callback, monkeypatch):
        learn.data_lessons = {"results": [{"title": "L1", "uuid": "u1", "songs": [{"title": "Song A"}]}]}

        async def fake_get_lesson_detail(uuid):
            return {"songs": [{"title": "Song A", "text": "x" * 5000}]}

        monkeypatch.setattr(learn, "get_lesson_detail", fake_get_lesson_detail)
        callback = make_callback(data="song:0:0")

        await learn.callback_song(callback)

        assert callback.message.answer.await_count > 1


class TestChunkHtmlMessage:
    def test_short_text_is_single_chunk(self):
        assert learn._chunk_html_message("a\nb") == ["a\nb"]

    def test_empty_text_returns_single_empty_chunk(self):
        assert learn._chunk_html_message("") == [""]

    def test_splits_between_lines_without_breaking_them(self):
        lines = ["<b>line</b>"] * 10

        chunks = learn._chunk_html_message("\n".join(lines), limit=50)

        assert len(chunks) > 1
        assert all(len(chunk) <= 50 for chunk in chunks)
        assert "\n".join(chunks) == "\n".join(lines)

    def test_splits_overlong_line_into_pieces(self):
        chunks = learn._chunk_html_message("ab\n" + "x" * 25 + "\ncd", limit=10)

        assert chunks == ["ab", "x" * 10, "x" * 10, "x" * 5, "cd"]


class TestGetChordsKeyboard:
    def test_deduplicates_and_skips_numeric_titles(self):
        learn.data_chords = {
            "results": [
                {"title": "Am"},
                {"title": "Am"},
                {"title": "7"},
                {"title": "C"},
            ]
        }

        markup = learn.get_chords_keyboard()

        texts = [btn.text for row in markup.keyboard for btn in row]
        assert texts.count("Am") == 1
        assert "7" not in texts
        assert "C" in texts


class TestShowChord:
    async def test_loads_data_when_empty(self, make_message, monkeypatch):
        learn.data_chords = {"results": []}
        called = {"loaded": False}

        async def fake_load_data():
            called["loaded"] = True

        monkeypatch.setattr(learn, "load_data", fake_load_data)
        message = make_message()

        await learn.show_chord(message, "Am")

        assert called["loaded"] is True

    async def test_exact_match_found(self, make_message):
        learn.data_chords = {"results": [{"id": 1, "title": "Am", "svg_vertical": ""}]}
        message = make_message()

        result = await learn.show_chord(message, "am")

        assert result is True
        message.answer.assert_awaited_once()

    async def test_numeric_query_matches_by_index(self, make_message):
        learn.data_chords = {
            "results": [
                {"id": 10, "title": "Am", "svg_vertical": ""},
                {"id": 20, "title": "C", "svg_vertical": ""},
            ]
        }
        message = make_message()

        result = await learn.show_chord(message, "1")

        assert result is True
        text = message.answer.call_args.args[0]
        assert "C" in text

    async def test_numeric_query_matches_by_id_when_out_of_index_range(self, make_message):
        learn.data_chords = {"results": [{"id": 99, "title": "Am", "svg_vertical": ""}]}
        message = make_message()

        result = await learn.show_chord(message, "99")

        assert result is True
        text = message.answer.call_args.args[0]
        assert "Am" in text

    async def test_partial_match_on_title(self, make_message):
        learn.data_chords = {"results": [{"id": 1, "title": "Amaj7", "svg_vertical": ""}]}
        message = make_message()

        result = await learn.show_chord(message, "maj")

        assert result is True

    async def test_no_match_reports_not_found(self, make_message):
        learn.data_chords = {"results": [{"id": 1, "title": "Am", "svg_vertical": ""}]}
        message = make_message()

        result = await learn.show_chord(message, "Zzz")

        assert result is False
        assert "не найден" in message.answer.call_args.args[0]

    async def test_renders_svg_as_photo(self, make_message):
        learn.data_chords = {"results": [{"id": 1, "title": "Am", "svg_vertical": SAMPLE_SVG}]}
        message = make_message()

        result = await learn.show_chord(message, "Am")

        assert result is True
        message.answer_photo.assert_awaited_once()
        message.answer.assert_not_awaited()

    async def test_falls_back_to_text_when_svg_render_fails(self, make_message, monkeypatch):
        learn.data_chords = {"results": [{"id": 1, "title": "Am", "svg_vertical": "<broken"}]}

        def fake_render(svg):
            raise ValueError("bad svg")

        monkeypatch.setattr(learn, "render_chord_svg", fake_render)
        message = make_message()

        result = await learn.show_chord(message, "Am")

        assert result is True
        message.answer.assert_awaited_once()
        message.answer_photo.assert_not_awaited()

    async def test_shows_at_most_three_variants(self, make_message):
        learn.data_chords = {
            "results": [
                {"id": i, "title": "Am", "svg_vertical": ""} for i in range(5)
            ]
        }
        message = make_message()

        await learn.show_chord(message, "Am")

        assert message.answer.await_count == 3


class TestCmdLessons:
    async def test_with_args_delegates_to_show_lesson(self, make_message, fsm_context, monkeypatch):
        mock_show = AsyncMock(return_value=True)
        monkeypatch.setattr(learn, "show_lesson", mock_show)
        message = make_message()
        command = CommandObject(command="lessons", args="1")

        await learn.cmd_lessons(message, command=command, state=fsm_context)

        mock_show.assert_awaited_once_with(message, "1")

    async def test_without_args_prompts_and_sets_state(self, make_message, fsm_context):
        learn.data_lessons = {"results": [{"title": "L1"}]}
        message = make_message()

        await learn.cmd_lessons(message, command=None, state=fsm_context)

        assert await fsm_context.get_state() == learn.LearnState.waiting_for_lesson.state
        message.answer.assert_awaited_once()

    async def test_loads_data_when_empty(self, make_message, fsm_context, monkeypatch):
        learn.data_lessons = {"results": []}
        called = {"loaded": False}

        async def fake_load_data():
            called["loaded"] = True

        monkeypatch.setattr(learn, "load_data", fake_load_data)
        message = make_message()

        await learn.cmd_lessons(message, command=None, state=fsm_context)

        assert called["loaded"] is True


class TestProcessLessonInput:
    async def test_empty_text_prompts_again(self, make_message, fsm_context):
        message = make_message(text=None)

        await learn.process_lesson_input(message, fsm_context)

        message.answer.assert_awaited_once()

    async def test_cancel_clears_state(self, make_message, fsm_context):
        await fsm_context.set_state(learn.LearnState.waiting_for_lesson)
        message = make_message(text="❌ Отмена")

        await learn.process_lesson_input(message, fsm_context)

        assert await fsm_context.get_state() is None

    async def test_successful_lookup_clears_state(self, make_message, fsm_context):
        await fsm_context.set_state(learn.LearnState.waiting_for_lesson)
        learn.data_lessons = {"results": [{"title": "Lesson 1", "video_url": "", "songs": []}]}
        message = make_message(text="0")

        await learn.process_lesson_input(message, fsm_context)

        assert await fsm_context.get_state() is None

    async def test_failed_lookup_keeps_state(self, make_message, fsm_context):
        await fsm_context.set_state(learn.LearnState.waiting_for_lesson)
        learn.data_lessons = {"results": []}
        message = make_message(text="not-a-number")

        await learn.process_lesson_input(message, fsm_context)

        assert await fsm_context.get_state() == learn.LearnState.waiting_for_lesson.state


class TestCmdChords:
    async def test_with_args_delegates_to_show_chord(self, make_message, fsm_context, monkeypatch):
        mock_show = AsyncMock(return_value=True)
        monkeypatch.setattr(learn, "show_chord", mock_show)
        message = make_message()
        command = CommandObject(command="chords", args="Am")

        await learn.cmd_chords(message, command=command, state=fsm_context)

        mock_show.assert_awaited_once_with(message, "Am")

    async def test_loads_data_when_empty(self, make_message, fsm_context, monkeypatch):
        learn.data_chords = {"results": []}
        called = {"loaded": False}

        async def fake_load_data():
            called["loaded"] = True

        monkeypatch.setattr(learn, "load_data", fake_load_data)
        message = make_message()

        await learn.cmd_chords(message, command=None, state=fsm_context)

        assert called["loaded"] is True

    async def test_without_args_prompts_and_sets_state(self, make_message, fsm_context):
        learn.data_chords = {"results": [{"title": "Am"}]}
        message = make_message()

        await learn.cmd_chords(message, command=None, state=fsm_context)

        assert await fsm_context.get_state() == learn.LearnState.waiting_for_chord.state
        message.answer.assert_awaited_once()


class TestProcessChordInput:
    async def test_empty_text_prompts_again(self, make_message, fsm_context):
        message = make_message(text=None)

        await learn.process_chord_input(message, fsm_context)

        message.answer.assert_awaited_once()

    async def test_cancel_clears_state(self, make_message, fsm_context):
        await fsm_context.set_state(learn.LearnState.waiting_for_chord)
        message = make_message(text="/cancel")

        await learn.process_chord_input(message, fsm_context)

        assert await fsm_context.get_state() is None

    async def test_successful_lookup_clears_state(self, make_message, fsm_context):
        await fsm_context.set_state(learn.LearnState.waiting_for_chord)
        learn.data_chords = {"results": [{"id": 1, "title": "Am", "svg_vertical": ""}]}
        message = make_message(text="Am")

        await learn.process_chord_input(message, fsm_context)

        assert await fsm_context.get_state() is None

    async def test_failed_lookup_keeps_state(self, make_message, fsm_context):
        await fsm_context.set_state(learn.LearnState.waiting_for_chord)
        learn.data_chords = {"results": []}
        message = make_message(text="Zzz")

        await learn.process_chord_input(message, fsm_context)

        assert await fsm_context.get_state() == learn.LearnState.waiting_for_chord.state
