import asyncio
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from db.database import SQLiteStorage, db
from routers import admin, base, learn
from routers.middleware import UserTrackingMiddleware

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

async def main():
    if not TOKEN:
        print("Ошибка: BOT_TOKEN не найден в .env файле!")
        sys.exit(1)

    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=SQLiteStorage(db))

    dp.update.outer_middleware(UserTrackingMiddleware())

    dp.include_router(base.router)
    dp.include_router(learn.router)
    dp.include_router(admin.router)

    print("Загрузка данных уроков и аккордов...")
    await learn.load_data()

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="help", description="Показать справку по командам"),
            BotCommand(command="fb", description="Отправить обратную связь"),
            BotCommand(command="lessons", description="Информация об уроке"),
            BotCommand(command="chords", description="Аппликатура аккорда"),
            BotCommand(command="admin", description="Панель администратора"),
        ]
    )

    print("Бот успешно запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")
