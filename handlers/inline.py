import asyncio
import logging
import os
import re
import secrets
import time
from typing import Dict, Optional, List

from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile,
)

from config import PLATFORMS, VK_PATTERNS
from services.downloader import (
    download_video,
    download_vk_video,
    download_twitter_video,
    get_youtube_resolutions,
)
from services.utils import get_video_dimensions

logger = logging.getLogger(__name__)

_INLINE_REQUESTS: Dict[str, Dict[str, object]] = {}
_INLINE_REQUEST_TTL = 15 * 60


def _cleanup_inline_requests() -> None:
    now = time.time()
    expired = [key for key, value in _INLINE_REQUESTS.items() if now - value["ts"] > _INLINE_REQUEST_TTL]
    for key in expired:
        _INLINE_REQUESTS.pop(key, None)


def _store_inline_request(url: str, user_id: int) -> str:
    _cleanup_inline_requests()
    token = secrets.token_urlsafe(6)
    _INLINE_REQUESTS[token] = {"url": url, "user_id": user_id, "ts": time.time()}
    return token


def _pop_inline_request(token: str, user_id: int) -> Optional[str]:
    data = _INLINE_REQUESTS.pop(token, None)
    if not data or data.get("user_id") != user_id:
        return None
    return data.get("url")  # type: ignore[return-value]


def _extract_url(text: str) -> Optional[str]:
    match = re.search(r"(https?://\S+)", text)
    if not match:
        return None
    url = match.group(1).strip()
    return url.rstrip(").,!?\"'")


def _build_send_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить видео", callback_data=f"inl:send:{token}")]
        ]
    )


def _build_youtube_keyboard(token: str, resolutions: List[tuple[int, int]]) -> InlineKeyboardMarkup:
    buttons = []
    row: List[InlineKeyboardButton] = []
    for width, height in resolutions:
        row.append(
            InlineKeyboardButton(text=f"{height}p", callback_data=f"inl:yt:{token}:{height}")
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def handle_inline_query(inline_query: InlineQuery) -> None:
    query_text = (inline_query.query or "").strip()
    url = _extract_url(query_text)

    if not url:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="Пришлите ссылку на видео",
                input_message_content=InputTextMessageContent(
                    message_text="Пришлите ссылку на видео (YouTube/Instagram/TikTok/VK/Twitter)."
                ),
            )
        ]
        await inline_query.answer(results=results, cache_time=1, is_personal=True)
        return

    if re.search(PLATFORMS["youtube"], url, re.IGNORECASE):
        token = _store_inline_request(url, inline_query.from_user.id)
        resolutions: List[tuple[int, int]] = []
        try:
            resolutions = await asyncio.to_thread(get_youtube_resolutions, url)
        except Exception as e:
            logger.warning(f"Failed to fetch YouTube qualities: {str(e)}")

        if resolutions:
            available = [(w, h) for (w, h) in resolutions if h >= 360]
            if not available:
                available = resolutions
        else:
            available = [
                (640, 360),
                (854, 480),
                (1280, 720),
                (1920, 1080),
                (2560, 1440),
                (3840, 2160),
            ]

        available = sorted(set(available), key=lambda r: (r[1], r[0]))
        keyboard = _build_youtube_keyboard(token, available)
        results = [
            InlineQueryResultArticle(
                id=token,
                title="YouTube: выбрать качество",
                description="Нажмите и выберите качество",
                input_message_content=InputTextMessageContent(message_text="Выберите качество для YouTube:"),
                reply_markup=keyboard,
            )
        ]
        await inline_query.answer(results=results, cache_time=1, is_personal=True)
        return

    if not any(re.search(pattern, url, re.IGNORECASE) for pattern in PLATFORMS.values()):
        results = [
            InlineQueryResultArticle(
                id="unsupported",
                title="Ссылка не поддерживается",
                input_message_content=InputTextMessageContent(
                    message_text="Эта ссылка не поддерживается. Пришлите ссылку на видео."
                ),
            )
        ]
        await inline_query.answer(results=results, cache_time=1, is_personal=True)
        return

    token = _store_inline_request(url, inline_query.from_user.id)
    results = [
        InlineQueryResultArticle(
            id=token,
            title="Отправить видео",
            description=url,
            input_message_content=InputTextMessageContent(
                message_text="Нажмите кнопку ниже, чтобы отправить видео."
            ),
            reply_markup=_build_send_keyboard(token),
        )
    ]
    await inline_query.answer(results=results, cache_time=1, is_personal=True)


async def handle_inline_callback(callback: CallbackQuery) -> None:
    if not callback.data:
        return

    parts = callback.data.split(":")
    if len(parts) < 3 or parts[0] != "inl":
        return

    action = parts[1]
    token = parts[2]

    url = _pop_inline_request(token, callback.from_user.id)
    if not url:
        await callback.answer("Ссылка устарела, отправьте заново", show_alert=True)
        return

    await callback.answer()
    if not callback.message:
        return

    if action == "yt":
        if len(parts) < 4:
            await callback.message.edit_text("❌ Некорректный выбор качества")
            return
        try:
            selected_height = int(parts[3])
        except ValueError:
            await callback.message.edit_text("❌ Некорректный выбор качества")
            return
        await _send_downloaded_video(callback, url, youtube_target_height=selected_height)
        return

    if action == "send":
        await _send_downloaded_video(callback, url)
        return


async def _send_downloaded_video(
    callback: CallbackQuery,
    url: str,
    youtube_target_height: Optional[int] = None
) -> None:
    if not callback.message:
        return

    await callback.message.edit_text("⏳ Скачиваю видео...")
    file_path = None
    try:
        if re.search(PLATFORMS["twitter"], url, re.IGNORECASE):
            file_path = await download_twitter_video(url)
        elif ("vk.com" in url or "vkvideo.ru" in url) and any(p in url for p in VK_PATTERNS):
            file_path = await download_vk_video(url)
        else:
            file_path = await download_video(url, youtube_target_height=youtube_target_height)

        video_size = await get_video_dimensions(file_path)
        await callback.message.answer_video(
            video=FSInputFile(file_path),
            caption="Ваше видео готово!",
            supports_streaming=True,
            width=video_size[0] if video_size else None,
            height=video_size[1] if video_size else None
        )
        await callback.message.edit_text("✅ Готово")
    except Exception as e:
        logger.error(f"Inline download failed: {str(e)}", exc_info=True)
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
