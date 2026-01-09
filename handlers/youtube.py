import logging
import os
import secrets
import time
import asyncio
from typing import Dict, Optional

from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.downloader import download_video, get_youtube_resolutions
from services.utils import get_video_dimensions

logger = logging.getLogger(__name__)

_YT_REQUESTS: Dict[str, Dict[str, object]] = {}
_YT_REQUEST_TTL = 15 * 60


def _cleanup_requests() -> None:
    now = time.time()
    expired = [key for key, value in _YT_REQUESTS.items() if now - value["ts"] > _YT_REQUEST_TTL]
    for key in expired:
        _YT_REQUESTS.pop(key, None)


def _store_request(url: str, chat_id: int) -> str:
    _cleanup_requests()
    token = secrets.token_urlsafe(6)
    _YT_REQUESTS[token] = {"url": url, "chat_id": chat_id, "ts": time.time()}
    return token


def _pop_request(token: str, chat_id: int) -> Optional[str]:
    data = _YT_REQUESTS.pop(token, None)
    if not data or data.get("chat_id") != chat_id:
        return None
    return data.get("url")  # type: ignore[return-value]


async def prompt_youtube_quality(message: Message, url: str) -> None:
    token = _store_request(url, message.chat.id)
    resolutions = []
    try:
        resolutions = await asyncio.to_thread(get_youtube_resolutions, url)
    except Exception as e:
        logger.warning(f"Failed to fetch YouTube qualities: {str(e)}")

    if resolutions:
        available = [(w, h) for (w, h) in resolutions if h >= 360]
        if not available:
            available = resolutions
    else:
        available = [(640, 360), (854, 480), (1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]

    available = sorted(set(available), key=lambda r: (r[1], r[0]))
    buttons = []
    row = []
    for width, height in available:
        row.append(
            InlineKeyboardButton(
                text=f"{height}p",
                callback_data=f"ytq:{token}:{width}x{height}"
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="Только аудио (mp3)", callback_data=f"ytq:{token}:mp3")
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите качество для YouTube:", reply_markup=keyboard)


async def handle_youtube_choice(callback: CallbackQuery) -> None:
    if not callback.data:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    _, token, choice = parts
    url = _pop_request(token, callback.message.chat.id) if callback.message else None
    if not url:
        await callback.answer("Ссылка устарела, отправьте заново", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text("⏳ Скачиваю YouTube...")

    file_path = None
    last_update = 0.0
    last_text = ""
    last_downloaded = 0
    loop = asyncio.get_running_loop()

    def _format_bytes(value: Optional[float]) -> str:
        if value is None:
            return "0 B"
        units = ["B", "KB", "MB", "GB"]
        size = float(value)
        for unit in units:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    async def _update_progress(text: str, downloaded: int) -> None:
        nonlocal last_update, last_text, last_downloaded
        now = time.time()
        if now - last_update < 0.3 or (text == last_text and downloaded == last_downloaded):
            return
        last_update = now
        last_text = text
        last_downloaded = downloaded
        try:
            await callback.message.edit_text(text)
        except Exception:
            pass

    def _progress_hook(progress: dict) -> None:
        if not callback.message:
            return
        status = progress.get("status")
        if status == "downloading":
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            downloaded = progress.get("downloaded_bytes")
            percent = int(downloaded * 100 / total) if downloaded and total else None
            parts = ["⏳ Скачиваю YouTube..."]
            if percent is not None:
                parts.append(f"{percent}%")
            if downloaded:
                parts.append(f"{_format_bytes(downloaded)}")
            if total:
                parts.append(f"/ {_format_bytes(total)}")
            text = " ".join(parts)
            loop.call_soon_threadsafe(
                asyncio.create_task,
                _update_progress(text, int(downloaded or 0))
            )
        elif status == "finished":
            loop.call_soon_threadsafe(
                asyncio.create_task,
                _update_progress("✅ Загружено. Отправляю...", 0)
            )

    try:
        if choice == "mp3":
            file_path = await download_video(
                url,
                youtube_audio_only=True,
                progress_hook=_progress_hook
            )
            await callback.message.answer_audio(
                audio=FSInputFile(file_path),
                caption="Ваше аудио готово!"
            )
        else:
            selected_width = None
            selected_height = None
            try:
                if "x" in choice:
                    width_str, height_str = choice.split("x", 1)
                    selected_width = int(width_str)
                    selected_height = int(height_str)
                else:
                    selected_height = int(choice)
            except ValueError:
                await callback.message.answer("❌ Некорректный выбор качества")
                return
            file_path = await download_video(
                url,
                youtube_target_height=selected_height,
                progress_hook=_progress_hook
            )
            video_size = await get_video_dimensions(file_path)
            width = video_size[0] if video_size else selected_width
            height = video_size[1] if video_size else selected_height
            await callback.message.answer_video(
                video=FSInputFile(file_path),
                caption="Ваше видео готово!",
                supports_streaming=True,
                width=width,
                height=height
            )
    except Exception as e:
        logger.error(f"YouTube download failed: {str(e)}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
