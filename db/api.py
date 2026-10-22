import json
import os

import aiofiles
import aiohttp

API_DIR: str = os.path.join(os.path.dirname(__file__), "api")
LESSONS_API_URL: str = "https://api.guitar0.net/api/v1/lessons/?limit=100"
CHORDS_API_URL: str = "https://api.guitar0.net/api/v1/chords/?limit=100"


async def fetch_or_load_cache(session: aiohttp.ClientSession, url: str, filename: str, strict: bool = False) -> dict:
    """Fetch JSON from the API and cache it; fall back to the cached copy if the API is unreachable.

    With ``strict=True`` an API failure is raised instead of silently served from the cache.
    """
    os.makedirs(API_DIR, exist_ok=True)
    file_path = os.path.join(API_DIR, filename)

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            data = await response.json()
    except Exception:
        if strict:
            raise
    else:
        # A cache write failure must not discard data we have just fetched.
        try:
            async with aiofiles.open(file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(data, ensure_ascii=False, indent=4))
        except Exception:
            pass
        return data

    if os.path.exists(file_path):
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as file:
                content = await file.read()
                return json.loads(content)
        except Exception:
            pass

    return {"results": []}


async def get_api_data(strict: bool = False) -> list:
    async with aiohttp.ClientSession() as session:
        data_lessons = await fetch_or_load_cache(session, LESSONS_API_URL, "lessons.json", strict)
        data_chords = await fetch_or_load_cache(session, CHORDS_API_URL, "chords.json", strict)
    return [data_lessons, data_chords]


async def get_lesson_detail(lesson_uuid: str) -> dict:
    url = f"https://api.guitar0.net/api/v1/lessons/{lesson_uuid}/"
    filename = f"lesson_{lesson_uuid}.json"
    async with aiohttp.ClientSession() as session:
        return await fetch_or_load_cache(session, url, filename)
