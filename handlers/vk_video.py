from config import MAX_FILE_SIZE
from services.vk_parser import vk_parser
from services.downloader import download_vk_video
from aiogram import types
from aiogram.types import FSInputFile
from services.utils import get_video_dimensions
import logging
import os

logger = logging.getLogger(__name__)

MAX_TELEGRAM_SIZE = MAX_FILE_SIZE

async def handle_vk_video_download(message: types.Message, url: str):
    try:
        progress = await message.answer("⏳ Начинаю загрузку...")
        
        # 1. Загрузка
        video_path = await download_vk_video(url)
        file_size = os.path.getsize(video_path)
        
        # 3. Отправка
        await progress.edit_text("📤 Отправляю видео...")
        video_size = await get_video_dimensions(video_path)
        await message.answer_video(
            video=FSInputFile(video_path),
            caption="Ваше видео готово!",
            supports_streaming=True,
            width=video_size[0] if video_size else None,
            height=video_size[1] if video_size else None
        )
            
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        
    finally:
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)
        if 'progress' in locals():
            await progress.delete()
