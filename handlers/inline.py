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
    ChosenInlineResult,
    InputMediaVideo,
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
        results = []
        for _, height in available:
            results.append(
                InlineQueryResultArticle(
                    id=f"yt:{token}:{height}",
                    title=f"YouTube {height}p",
                    description=f"Отправить видео в {height}p",
                    input_message_content=InputTextMessageContent(
                        message_text=f"⏳ Скачиваю YouTube {height}p..."
                    ),
                )
            )
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
            id=f"send:{token}",
            title="Отправить видео",
            description=url,
            input_message_content=InputTextMessageContent(
                message_text="⏳ Скачиваю видео..."
            ),
        )
    ]
    await inline_query.answer(results=results, cache_time=1, is_personal=True)


async def handle_chosen_inline_result(chosen: ChosenInlineResult) -> None:
    if not chosen.result_id or not chosen.inline_message_id:
        return

    parts = chosen.result_id.split(":")
    if len(parts) < 2:
        return

    action = parts[0]
    token = parts[1]
    selected_height: Optional[int] = None
    if action == "yt":
        if len(parts) < 3:
            return
        try:
            selected_height = int(parts[2])
        except ValueError:
            return

    url = _pop_inline_request(token, chosen.from_user.id)
    if not url:
        return

    await _send_downloaded_video_inline(
        chosen,
        url,
        youtube_target_height=selected_height
    )


async def _send_downloaded_video_inline(
    chosen: ChosenInlineResult,
    url: str,
    youtube_target_height: Optional[int] = None
) -> None:
    bot = chosen.bot
    inline_message_id = chosen.inline_message_id
    if not inline_message_id:
        return

    file_path = None
    try:
        if re.search(PLATFORMS["twitter"], url, re.IGNORECASE):
            file_path = await download_twitter_video(url)
        elif ("vk.com" in url or "vkvideo.ru" in url) and any(p in url for p in VK_PATTERNS):
            file_path = await download_vk_video(url)
        else:
            file_path = await download_video(url, youtube_target_height=youtube_target_height)

        video_size = await get_video_dimensions(file_path)
        file_id = await _upload_for_inline(
            bot,
            chosen.from_user.id,
            file_path,
            video_size
        )
        if not file_id:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text="❌ Не удалось отправить видео. Откройте бота и попробуйте снова."
            )
            return

        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaVideo(
                media=file_id,
                caption="Ваше видео готово!"
            )
        )
    except Exception as e:
        logger.error(f"Inline download failed: {str(e)}", exc_info=True)
        await bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=f"❌ Ошибка: {str(e)}"
        )
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


async def _upload_for_inline(
    bot,
    user_id: int,
    file_path: str,
    video_size: Optional[tuple[int, int]]
) -> Optional[str]:
    width = video_size[0] if video_size else None
    height = video_size[1] if video_size else None
    try:
        msg = await bot.send_video(
            chat_id=user_id,
            video=FSInputFile(file_path),
            caption="Ваше видео готово!",
            supports_streaming=True,
            width=width,
            height=height
        )
        file_id = msg.video.file_id if msg.video else None
        await bot.delete_message(chat_id=user_id, message_id=msg.message_id)
        return file_id
    except Exception as e:
        logger.error(f"Inline upload failed: {str(e)}", exc_info=True)
        return None
