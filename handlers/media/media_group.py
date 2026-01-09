import os
from typing import List
from aiogram.types import Message, InputMediaPhoto, FSInputFile
from services.utils import download_image
import logging
import time

logger = logging.getLogger(__name__)


async def send_media_group(
    message: Message,
    image_urls: List[str],
    video_preview_urls: List[str] = None,
    max_items: int = 10
) -> bool:
    """
    Отправляет группу медиафайлов в одном сообщении
    :param message: Объект сообщения aiogram
    :param image_urls: Список URL изображений
    :param video_preview_urls: Список URL превью видео
    :param max_items: Максимальное количество медиафайлов
    :return: Статус отправки (True/False)
    """
    if not video_preview_urls:
        video_preview_urls = []

    media = []
    temp_files = []
    total_items = min(len(image_urls) + len(video_preview_urls), max_items)
    
    try:
        # Обработка изображений
        for i, url in enumerate(image_urls[:total_items]):
            try:
                filename = f"media_{i}_{int(time.time())}.jpg"
                filepath = await download_image(url, filename)
                
                media.append(InputMediaPhoto(
                    media=FSInputFile(filepath)
                ))
                temp_files.append(filepath)
            except Exception as e:
                continue

        # Обработка видео-превью (если осталось место)
        if len(media) < total_items:
            for url in video_preview_urls[:total_items - len(media)]:
                try:
                    filename = f"video_preview_{int(time.time())}.jpg"
                    filepath = await download_image(url, filename)
                    
                    media.append(InputMediaPhoto(
                        media=FSInputFile(filepath)
                    ))
                    temp_files.append(filepath)
                except Exception:
                    continue

        if media:
            await message.bot.send_media_group(
                chat_id=message.chat.id,
                media=media
            )
            return True
        return False
        
    except Exception as e:
        logger.error(f"Ошибка отправки медиагруппы: {str(e)}")
        return False
    finally:
        for filepath in temp_files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                continue
