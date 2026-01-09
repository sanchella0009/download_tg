from aiogram.types import Message, FSInputFile
from services.utils import download_image
import time
import os
import logging

logger = logging.getLogger(__name__)



async def download_and_send_image(
    message: Message,
    url: str,
    caption: str = "",
    filename: str = None
) -> bool:
    """
    Скачивает и отправляет одно изображение
    :param message: Объект сообщения aiogram
    :param url: URL изображения
    :param caption: Подпись к изображению
    :param filename: Имя файла (необязательно)
    :return: Статус отправки
    """
    try:
        if not filename:
            filename = f"image_{int(time.time())}.jpg"
        
        filepath = await download_image(url, filename)
        
        await message.answer_photo(
            photo=FSInputFile(filepath),
            caption=caption
        )
        os.remove(filepath)
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки изображения: {str(e)}")
        return False
