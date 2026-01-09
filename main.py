import asyncio
import io
import locale
import logging
import sys
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command
from aiogram.enums import ParseMode
from config import BOT_TOKEN, TELEGRAM_API_URL
from handlers.base import handle_links, start
from handlers.inline import handle_inline_query, handle_chosen_inline_result
from handlers.youtube import handle_youtube_choice
from services.selenium import twitter_parser

# Настройка кодировки UTF-8 для всей системы
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ],
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

async def on_startup():
    """Действия при запуске бота"""
    logger.info("Starting bot...")

async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("Shutting down...")
    await twitter_parser._close_driver()
    logger.info("Bot stopped")

async def main():
    # Инициализация бота с настройками по умолчанию
    session = None
    if TELEGRAM_API_URL:
        api_url = TELEGRAM_API_URL
        if not api_url.startswith(("http://", "https://")):
            api_url = f"http://{api_url}"
        session = AiohttpSession(api=TelegramAPIServer.from_base(api_url))
        logger.info("Using Telegram Bot API server: %s", api_url)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session
    )
    dp = Dispatcher()

   
    # Регистрация обработчиков
    dp.message.register(start, Command("start"))
    dp.message.register(handle_links)
    dp.callback_query.register(handle_youtube_choice, F.data.startswith("ytq:"))
    dp.inline_query.register(handle_inline_query)
    dp.chosen_inline_result.register(handle_chosen_inline_result)

    # События запуска/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        logger.info("Bot is running...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}", exc_info=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Unexpected error: {str(e)}", exc_info=True)
