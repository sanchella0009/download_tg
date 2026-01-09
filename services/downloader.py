from pathlib import Path
import requests
from selenium.webdriver.common.by import By
import yt_dlp
import asyncio
import os
import time
import re
import logging
from typing import Callable, Optional
from config import DOWNLOAD_DIR, MAX_FILE_SIZE, PLATFORMS, YTDLP_REMOTE_COMPONENTS
from services.utils import compress_video
from yt_dlp import YoutubeDL
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)

def _apply_js_runtimes(ydl_opts: dict) -> dict:
    js_runtimes = os.getenv('YTDLP_JS_RUNTIMES', '').strip()
    if js_runtimes:
        runtimes = {}
        for runtime in (r.strip() for r in js_runtimes.split(',') if r.strip()):
            name, _, path = runtime.partition(':')
            name = name.strip().lower()
            if name == 'nodejs':
                name = 'node'
            if not name:
                continue
            if path:
                runtimes[name] = {'path': path}
            else:
                runtimes[name] = {}
        ydl_opts['js_runtimes'] = runtimes
    return ydl_opts

def _apply_remote_components(ydl_opts: dict) -> dict:
    if YTDLP_REMOTE_COMPONENTS:
        ydl_opts['remote_components'] = YTDLP_REMOTE_COMPONENTS
    return ydl_opts

def _youtube_format(target_height: Optional[int]) -> str:
    if target_height:
        return (
            f"bv*[height={target_height}][vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a]"
            f"/bv*[height={target_height}][ext=mp4]+ba"
            f"/bv*[height={target_height}]+ba"
            f"/b[height={target_height}]/b"
        )
    return (
        "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a]"
        "/bv*[ext=mp4]+ba"
        "/bv*+ba"
        "/b"
    )

def get_ydl_opts(
    url: str,
    youtube_target_height: Optional[int] = None,
    youtube_audio_only: bool = False
) -> dict:
    """Возвращает параметры скачивания для разных платформ"""
    base_opts = {
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': False,
        'no_warnings': False,
        'retries': 3,
        'merge_output_format': 'mp4',
    }

    # Проверяем URL без использования скомпилированного паттерна
    if re.search(r"(youtube\.com|youtu\.be)", url, re.IGNORECASE):
        if youtube_audio_only:
            audio_opts = {
                **base_opts,
                'format': 'bestaudio[ext=m4a]/bestaudio',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
            audio_opts.pop('merge_output_format', None)
        return _apply_remote_components(_apply_js_runtimes(audio_opts))
    return _apply_remote_components(_apply_js_runtimes({
        **base_opts,
        'format': _youtube_format(youtube_target_height)
    }))
    
    if re.search(r"instagram\.com", url, re.IGNORECASE):
    return _apply_remote_components(_apply_js_runtimes({**base_opts, 'format': 'bv*+ba/b'}))
    
    if re.search(r"(x\.com|twitter\.com)", url, re.IGNORECASE):
    return _apply_remote_components(_apply_js_runtimes({
        **base_opts,
        'format': 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b',
        'extractor_args': {'twitter': {'username': None, 'password': None}}
    }))
    
    # Для всех остальных платформ
    return _apply_remote_components(_apply_js_runtimes(base_opts))

def get_vk_ydl_opts():
    """Оптимальные настройки для VK"""
    return _apply_remote_components(_apply_js_runtimes({
        'outtmpl': os.path.join(DOWNLOAD_DIR, 'vk_%(id)s.%(ext)s'),
        'quiet': False,
        'no_warnings': False,
        'retries': 3,
        'socket_timeout': 30,
        'extract_flat': False,
        'referer': 'https://vk.com/',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
        },
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'cookiefile': None,  # Явно отключаем сохранение cookies
        'no_cookies': True,   # Запрещаем yt-dlp использовать cookies, если не указан файл
        'merge_output_format': 'mp4',
        'windows_filenames': True,
        'restrictfilenames': True
    }))

def get_youtube_resolutions(url: str) -> list[tuple[int, int]]:
    """Возвращает список доступных разрешений видео для YouTube"""
    ydl_opts = _apply_remote_components(_apply_js_runtimes({
        'quiet': True,
        'skip_download': True,
    }))
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get('formats', [])
    resolutions = set()
    for fmt in formats:
        height = fmt.get('height')
        width = fmt.get('width')
        vcodec = str(fmt.get('vcodec', ''))
        if not height or not width:
            continue
        if vcodec == 'none':
            continue
        pair = (int(width), int(height))
        resolutions.add(pair)
    return sorted(resolutions, key=lambda r: (r[1], r[0]))

async def download_video(
    url: str,
    youtube_target_height: Optional[int] = None,
    youtube_audio_only: bool = False,
    progress_hook: Optional[Callable[[dict], None]] = None
) -> str:
    """Скачивание видео с обработкой ошибок"""
    try:
        ydl_opts = get_ydl_opts(
            url,
            youtube_target_height=youtube_target_height,
            youtube_audio_only=youtube_audio_only
        )
        if progress_hook:
            ydl_opts['progress_hooks'] = [progress_hook]

        return await asyncio.to_thread(
            _download_video_sync,
            url,
            ydl_opts,
            youtube_audio_only
        )
            
    except yt_dlp.DownloadError as e:
        logger.error(f"Ошибка скачивания: {str(e)}")
        raise ValueError(f"Не удалось скачать видео: {str(e)}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {str(e)}")
        raise

def _download_video_sync(url: str, ydl_opts: dict, youtube_audio_only: bool) -> str:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

        if youtube_audio_only:
            mp3_filename = f"{os.path.splitext(filename)[0]}.mp3"
            if os.path.exists(mp3_filename):
                return mp3_filename

        if not os.path.exists(filename):
            exts = ('.mp4', '.mkv', '.webm')
            if youtube_audio_only:
                exts = ('.mp3',)
            files = [f for f in os.listdir('downloads') if f.endswith(exts)]
            if files:
                filename = os.path.join('downloads', files[0])
            else:
                raise FileNotFoundError("Не удалось найти скачанный файл")

        return filename

async def download_twitter_video(url: str) -> str:
    """Улучшенное скачивание Twitter видео"""
    ydl_opts = _apply_remote_components(_apply_js_runtimes({
        'outtmpl': 'downloads/twitter_%(id)s.%(ext)s',
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'retries': 5,
        'socket_timeout': 60,
        'extractor_args': {
            'twitter': {
                'username': os.getenv('TWITTER_USERNAME'),
                'password': os.getenv('TWITTER_PASSWORD')
            }
        },
        'logger': logging.getLogger('yt-dlp'),
    }))
    
    try:
        # Сначала пробуем стандартный метод
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if not os.path.exists(filename):
                # Если файл не найден, ищем любой видеофайл в папке загрузок
                files = [f for f in os.listdir('downloads') 
                        if f.startswith('twitter_') and f.endswith('.mp4')]
                if files:
                    filename = os.path.join('downloads', files[0])
                else:
                    raise FileNotFoundError("Видеофайл не найден после скачивания")
            
            return filename
            
    except Exception as e:
        logger.error(f"Twitter video download failed: {str(e)}")
        raise ValueError(f"Не удалось скачать видео: {str(e)}")

async def download_vk_video(url: str) -> str:
    """Улучшенная загрузка видео из VK"""
    try:
        # Создаем директорию, если не существует
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        ydl_opts = get_vk_ydl_opts()
        filename = None

        with YoutubeDL(ydl_opts) as ydl:
            # Сначала получаем информацию о видео
            info_dict = ydl.extract_info(url, download=False)
            video_id = info_dict.get('id', 'video')
            ext = info_dict.get('ext', 'mp4')
            filename = os.path.join(DOWNLOAD_DIR, f'vk_{video_id}.{ext}')
            
            # Удаляем старый файл, если существует
            if os.path.exists(filename):
                os.remove(filename)
            
            # Скачиваем видео
            ydl.download([url])
            
            # Проверяем, что файл создан
            if not os.path.exists(filename):
                # Ищем файл по шаблону, если не найден
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.startswith(f'vk_{video_id}') and f.endswith(('.mp4', '.mkv', '.webm')):
                        filename = os.path.join(DOWNLOAD_DIR, f)
                        break
                else:
                    raise FileNotFoundError("Файл видео не найден после загрузки")
            
            return filename

    except Exception as e:
        logger.error(f"Ошибка загрузки VK видео: {str(e)}", exc_info=True)
        if filename and os.path.exists(filename):
            os.remove(filename)
        raise ValueError(f"Не удалось скачать видео: {str(e)}")


    
