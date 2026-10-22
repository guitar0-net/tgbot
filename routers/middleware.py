import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from db.database import db


class UserTrackingMiddleware(BaseMiddleware):
    """Remember every user who interacts with the bot (needed for stats and broadcasts)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user and not user.is_bot:
            with contextlib.suppress(Exception):
                db.upsert_user(user.id, user.full_name, user.username)
        return await handler(event, data)
