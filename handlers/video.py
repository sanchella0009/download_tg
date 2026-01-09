from aiogram import types
from aiogram.types import FSInputFile
import os
from services.downloader import download_video
from services.utils import get_video_dimensions
import logging


logger = logging.getLogger(__name__)

async def handle_video_download(message: types.Message, url: str):
    """Обрабатывает запрос на скачивание видео"""
    try:
        await message.answer("⏳ Скачиваю видео...")
        filename = await download_video(url)
        
        video_size = await get_video_dimensions(filename)
        await message.answer_video(
            video=FSInputFile(filename),
            caption="Ваше видео готово!",
            supports_streaming=True,
            width=video_size[0] if video_size else None,
            height=video_size[1] if video_size else None
        )
        os.remove(filename)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        if 'filename' in locals() and os.path.exists(filename):
            os.remove(filename)
