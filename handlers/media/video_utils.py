import os
from aiogram.types import Message, FSInputFile
from services.utils import get_video_dimensions
import logging

logger = logging.getLogger(__name__)


async def send_video_file(
    message: Message,
    filepath: str,
    caption: str = "",
    remove_after: bool = True
) -> bool:
    """
    Отправляет видеофайл с обработкой размера
    :param message: Объект сообщения aiogram
    :param filepath: Путь к файлу
    :param caption: Подпись к видео
    :param remove_after: Удалять ли файл после отправки
    :return: Статус отправки
    """
    try:
        video_size = await get_video_dimensions(filepath)
        await message.answer_video(
            video=FSInputFile(filepath),
            caption=caption,
            supports_streaming=True,
            width=video_size[0] if video_size else None,
            height=video_size[1] if video_size else None
        )
        
        if remove_after:
            os.remove(filepath)
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отправки видео: {str(e)}")
        if 'filepath' in locals() and os.path.exists(filepath) and remove_after:
            os.remove(filepath)
        return False
