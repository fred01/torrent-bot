#!/usr/bin/env python3
"""
Telegram bot to download magnet links via Transmission RPC API
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from collections import defaultdict
from urllib.parse import urlparse
import asyncio

import requests as http_requests
from bs4 import BeautifulSoup
import transmission_rpc
from transmission_rpc.error import TransmissionError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from dotenv import load_dotenv
from aiohttp import web

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TRANSMISSION_URL = os.getenv('TRANSMISSION_URL', 'http://localhost:9091')
TRANSMISSION_USER = os.getenv('TRANSMISSION_USER')
TRANSMISSION_PASS = os.getenv('TRANSMISSION_PASS')

# RuTracker configuration
RUTRACKER_USERNAME = os.getenv('RUTRACKER_USERNAME')
RUTRACKER_PASSWORD = os.getenv('RUTRACKER_PASSWORD')

# LLM configuration (optional - enables smart intent parsing & result filtering)
AI_LLM_API_BASE_URL = os.getenv('AI_LLM_API_BASE_URL')
AI_LLM_API_KEY = os.getenv('AI_LLM_API_KEY')
AI_LLM_MODEL = os.getenv('AI_LLM_MODEL', 'gemma3:12b')
AI_LLM_KEEP_ALIVE = os.getenv('AI_LLM_KEEP_ALIVE', '30m')
AI_LLM_ENABLED = bool(AI_LLM_API_BASE_URL and AI_LLM_API_KEY)

# Webhook configuration
WEBHOOK_MODE = os.getenv('WEBHOOK_MODE', 'false').lower() == 'true'
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://torrent-bot.svc.fred.org.ru/update')
WEBHOOK_SECRET_TOKEN = os.getenv('WEBHOOK_SECRET_TOKEN')

# Default download directories if not available from Transmission
DEFAULT_DOWNLOAD_DIRS = {
    '🎬 Movies': '/downloads/complete/movies',
    '📺 TV Shows': '/downloads/complete/tvseries',
    '📚 Books': '/downloads/complete/books',
    '🎮 Games': '/downloads/complete/games',
    '📁 Other': '/downloads/complete/other',
    '💻 Soft': '/downloads/complete/soft',
    '🎵 Music': '/downloads/complete/music',
    '📖 Courses': '/downloads/complete/courses'
}

# Magnet link regex pattern
MAGNET_PATTERN = re.compile(r'magnet:\?[^\s]+')

TORRENT_JOBS_KEY = 'tracked_torrent_jobs'
TORRENT_POLL_INTERVAL = 30  # seconds
HEALTHZ_PATH = '/healthz'  # Health check endpoint path

# Global in-memory store for torrent monitoring tasks
_torrent_monitor_tasks: Dict[int, asyncio.Task] = {}


async def healthz_handler(request):
    """Health check endpoint handler"""
    return web.Response(text='OK', status=200)


async def status_handler(request):
    """Status page endpoint handler"""
    # Get Transmission status
    transmission_status = get_transmission_status()
    
    # Generate HTML status page
    html_content = generate_status_page(transmission_status)
    
    return web.Response(text=html_content, content_type='text/html', status=200)


def get_transmission_status():
    """Get Transmission connection status and details"""
    status = {
        'connected': False,
        'error': None,
        'version': None,
        'download_dir': None,
        'active_torrents': 0
    }
    
    try:
        if transmission_client.client:
            session = transmission_client.client.get_session()
            torrents = transmission_client.client.get_torrents()
            status['connected'] = True
            status['version'] = session.version
            status['download_dir'] = session.download_dir
            status['active_torrents'] = len(torrents)
        else:
            status['error'] = 'Transmission client not initialized'
    except Exception as e:
        status['error'] = str(e)
    
    return status


def generate_status_page(transmission_status):
    """Generate HTML status page"""
    # Load HTML template
    template_path = os.path.join(os.path.dirname(__file__), 'status_page.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()
    
    # Prepare values for substitution
    app_status = "✅ Running" if transmission_status['connected'] else "⚠️ Running (Transmission not connected)"
    transmission_icon = "✅" if transmission_status['connected'] else "❌"
    transmission_text = "Connected" if transmission_status['connected'] else "Disconnected"
    webhook_mode = 'Enabled' if WEBHOOK_MODE else 'Disabled (Polling)'
    
    # Build transmission details section
    transmission_details = ""
    if transmission_status['connected']:
        transmission_details = """
        <div class="status-row">
            <div class="status-label">Version:</div>
            <div class="status-value">{{VERSION}}</div>
        </div>
        <div class="status-row">
            <div class="status-label">Download Directory:</div>
            <div class="status-value">{{DOWNLOAD_DIR}}</div>
        </div>
        <div class="status-row">
            <div class="status-label">Active Torrents:</div>
            <div class="status-value">{{ACTIVE_TORRENTS}}</div>
        </div>"""
        transmission_details = transmission_details.replace('{{VERSION}}', str(transmission_status['version']))
        transmission_details = transmission_details.replace('{{DOWNLOAD_DIR}}', str(transmission_status['download_dir']))
        transmission_details = transmission_details.replace('{{ACTIVE_TORRENTS}}', str(transmission_status['active_torrents']))
    
    # Build error section
    error_section = ""
    if transmission_status['error']:
        error_section = """
        <div class="error-box">
            <strong>Connection Error:</strong><br>
            {{ERROR_MESSAGE}}
        </div>"""
        error_section = error_section.replace('{{ERROR_MESSAGE}}', str(transmission_status['error']))
    
    # Substitute values in template
    html = html.replace('{{APP_STATUS}}', app_status)
    html = html.replace('{{WEBHOOK_MODE}}', webhook_mode)
    html = html.replace('{{TRANSMISSION_ICON}}', transmission_icon)
    html = html.replace('{{TRANSMISSION_TEXT}}', transmission_text)
    html = html.replace('{{TRANSMISSION_DETAILS}}', transmission_details)
    html = html.replace('{{ERROR_SECTION}}', error_section)
    
    return html



async def setup_webhook(application):
    """Set up webhook for the bot"""
    try:
        await application.bot.set_webhook(
            url=WEBHOOK_URL,
            allowed_updates=["message", "callback_query"],
            secret_token=WEBHOOK_SECRET_TOKEN
        )
        logger.info(f"Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        raise


async def remove_webhook(application):
    """Remove webhook when shutting down"""
    try:
        await application.bot.delete_webhook()
        logger.info("Webhook removed")
    except Exception as e:
        logger.error(f"Failed to remove webhook: {e}")


class TransmissionClient:
    """Transmission RPC client wrapper"""
    
    def __init__(self):
        self.client = None
        self._connect()
    
    def _connect(self):
        """Connect to Transmission daemon"""
        try:
            parsed_url = urlparse(TRANSMISSION_URL)
            host = parsed_url.hostname or 'localhost'
            port = parsed_url.port or 9091
            
            self.client = transmission_rpc.Client(
                host=host,
                port=port,
                username=TRANSMISSION_USER,
                password=TRANSMISSION_PASS
            )
            logger.info(f"Connected to Transmission at {host}:{port}")
        except Exception as e:
            logger.error(f"Failed to connect to Transmission: {e}")
            self.client = None
    
    def get_download_dirs(self) -> Dict[str, str]:
        """Get available download directories from Transmission or use defaults"""
        if not self.client:
            logger.warning("Transmission client not available, using default directories")
            return DEFAULT_DOWNLOAD_DIRS
        
        try:
            # Try to get session info for download directories
            session = self.client.get_session()
            download_dir = getattr(session, 'download_dir', '/downloads')
            
            # For now, use default categories with the base download dir
            dirs = {}
            for label, subdir in DEFAULT_DOWNLOAD_DIRS.items():
                dirs[label] = subdir
            
            return dirs
        except Exception as e:
            logger.error(f"Failed to get download directories: {e}")
            return DEFAULT_DOWNLOAD_DIRS
    
    def add_torrent(self, magnet_url: str, download_dir: str) -> Optional['transmission_rpc.Torrent']:
        """Add magnet link to Transmission"""
        if not self.client:
            logger.error("Transmission client not available")
            return None

        try:
            torrent = self.client.add_torrent(magnet_url, download_dir=download_dir)
            logger.info(f"Added torrent: {torrent.name} to {download_dir}")
            return torrent
        except Exception as e:
            logger.error(f"Failed to add torrent: {e}")
            return None


@dataclass
class RuTrackerTorrent:
    topic_id: str
    title: str
    forum: str
    size_bytes: int
    seeds: int
    leeches: int

    @property
    def size_human(self) -> str:
        b = float(self.size_bytes)
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} ПБ"


class RuTrackerClient:
    BASE_URL = "https://rutracker.org/forum/"

    def __init__(self):
        self.session: Optional[http_requests.Session] = None
        self._authed = False

    @staticmethod
    def _is_authed(html: str) -> bool:
        # bb_session cookie is set for guests too, so cookie presence is NOT
        # proof of login. The logout link / non-guest JS flag is reliable.
        low = html.lower()
        return ("href=\"login.php?logout=" in low) or ("выход" in low)

    def _ensure_logged_in(self) -> bool:
        if self.session and self._authed:
            return True
        if not RUTRACKER_USERNAME or not RUTRACKER_PASSWORD:
            logger.warning("RuTracker credentials not configured")
            return False
        self.session = http_requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/109.0",
        })
        try:
            resp = self.session.post(self.BASE_URL + "login.php", data={
                "login_username": RUTRACKER_USERNAME,
                "login_password": RUTRACKER_PASSWORD,
                "login": "Вход",
            }, allow_redirects=True, timeout=60)
        except http_requests.RequestException as exc:
            logger.error(f"RuTracker login request failed: {exc}")
            self.session = None
            return False
        if self._is_authed(resp.text):
            logger.info("RuTracker login successful")
            self._authed = True
            return True
        logger.error("RuTracker login failed (served guest/error page)")
        self.session = None
        return False

    def _drop_session(self) -> None:
        self.session = None
        self._authed = False

    def search(self, query: str) -> List[RuTrackerTorrent]:
        if not self._ensure_logged_in():
            return []
        try:
            resp = self.session.get(self.BASE_URL + "tracker.php",
                                    params={"nm": query}, timeout=60)
        except http_requests.RequestException as exc:
            logger.error(f"RuTracker search request failed: {exc}")
            return []
        # Session may have expired -> served as guest. Re-login once.
        if not self._is_authed(resp.text):
            logger.info("RuTracker session expired, re-logging in")
            self._drop_session()
            if not self._ensure_logged_in():
                return []
            try:
                resp = self.session.get(self.BASE_URL + "tracker.php",
                                        params={"nm": query}, timeout=60)
            except http_requests.RequestException as exc:
                logger.error(f"RuTracker search retry failed: {exc}")
                return []
        resp.encoding = "windows-1251"
        soup = BeautifulSoup(resp.text, "lxml")

        results = []
        for row in soup.select("tr.tCenter.hl-tr"):
            topic_id = row.get("data-topic_id", "")
            forum_el = row.select_one("td.f-name-col a")
            forum = forum_el.get_text(strip=True) if forum_el else "Неизвестно"
            title_el = row.select_one("a.tLink")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not topic_id:
                topic_id = title_el.get("data-topic_id", "")

            size_td = row.select_one("td.tor-size")
            size_bytes = 0
            if size_td:
                try:
                    size_bytes = int(size_td.get("data-ts_text", "0"))
                except ValueError:
                    pass

            seeds = 0
            seeds_el = row.select_one("b.seedmed")
            if seeds_el:
                try:
                    seeds = int(seeds_el.get_text(strip=True))
                except ValueError:
                    pass

            leeches = 0
            leech_el = row.select_one("td.leechmed")
            if leech_el:
                try:
                    leeches = int(leech_el.get_text(strip=True))
                except ValueError:
                    pass

            results.append(RuTrackerTorrent(
                topic_id=topic_id, title=title, forum=forum,
                size_bytes=size_bytes, seeds=seeds, leeches=leeches,
            ))
        return results

    def get_magnet(self, topic_id: str) -> Optional[str]:
        if not self._ensure_logged_in():
            return None
        resp = self.session.get(self.BASE_URL + f"viewtopic.php?t={topic_id}")
        resp.encoding = "windows-1251"
        soup = BeautifulSoup(resp.text, "lxml")
        link = soup.select_one("a.magnet-link")
        return link.get("href") if link else None


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str):
    """Pull the first JSON object/array out of a noisy LLM reply
    (handles <think> blocks, ```json fences, leading prose)."""
    text = _THINK_RE.sub("", text or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    return json.loads(text)


_INTENT_SYSTEM = """\
Ты — парсер поисковых запросов для торрент-трекера RuTracker.
Пользователь пишет что хочет найти на естественном языке (русский/английский).
Извлеки чистую поисковую строку и тип контента.

Верни СТРОГО JSON без пояснений:
{"query": "<чистая строка для поиска>", "category": "<одна из: game, movie, tv, music, book, audiobook, software, other, any>"}

Правила:
- query: убери слова-указатели ("игра", "фильм", "скачать", "сериал"),
  оставь только название/предмет. Сохраняй уточнения (год, часть, платформу).
- category: что именно нужно пользователю. Тип не указан явно — "any".
- Никакого текста кроме JSON."""

_INTENT_FEWSHOT = (
    'Запрос: игра Warcraft III\nОтвет: {"query": "Warcraft III", "category": "game"}\n'
    'Запрос: Фильм ошибка резидента\nОтвет: {"query": "Обитель зла", "category": "movie"}\n'
    'Запрос: Warcraft\nОтвет: {"query": "Warcraft", "category": "any"}\n'
    'Запрос: сериал Чернобыль 2019\nОтвет: {"query": "Чернобыль 2019", "category": "tv"}'
)

_FILTER_SYSTEM = """\
Ты фильтруешь результаты поиска торрент-трекера RuTracker.
Дан желаемый тип контента и список раздач (номер, раздел форума, название).
Определи, какие раздачи реально соответствуют тому, что ищет пользователь.

"game" = только сама игра/репак/портативная версия для запуска.
НЕ игра: саундтреки (Score/OST/Soundtrack), музыка, артбуки, книги,
аудиокниги, фанфики, комиксы, фильмы, видео-прохождения, обои, моды/карты
без самой игры. Аналогично строго для movie/tv/music/book/audiobook/software.
Если тип "any" — оставь всё.
Если сомневаешься, что раздача нужного типа — НЕ включай её.

Верни СТРОГО JSON без пояснений и без лишних чисел:
{"relevant": [<номера подходящих раздач>]}

Только JSON, без текста."""


class LLMClient:
    """Minimal OpenAI-compatible (OpenWebUI/Ollama) client.

    Streams the response so we never hit a hard total-response timeout —
    only a generous gap-before-first-token budget (covers a cold model
    load). keep_alive asks Ollama to keep the model resident so the next
    call is warm (seconds) instead of a cold load (minutes)."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 keep_alive: str):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key
        self.model = model
        self.keep_alive = keep_alive

    def _chat(self, system: str, user: str) -> str:
        resp = http_requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": True,
                "temperature": 0,
                "keep_alive": self.keep_alive,
            },
            stream=True,
            timeout=(10, 300),
        )
        resp.raise_for_status()
        parts: List[str] = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
                delta = obj["choices"][0]["delta"].get("content")
                if delta:
                    parts.append(delta)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        return "".join(parts)

    def parse_intent(self, raw_query: str) -> tuple[str, str]:
        """raw natural-language query -> (clean_query, category)."""
        user = f"{_INTENT_FEWSHOT}\n\nЗапрос: {raw_query}\nОтвет:"
        data = _extract_json(self._chat(_INTENT_SYSTEM, user))
        clean_q = (data.get("query") or raw_query).strip()
        category = (data.get("category") or "any").strip().lower()
        return (clean_q or raw_query), category

    def filter_results(self, query: str, category: str,
                       items: List['RuTrackerTorrent']) -> List[int]:
        """Return indices of items matching the desired category."""
        lines = [f"Пользователь искал: {query}",
                 f"Желаемый тип: {category}", "", "Раздачи:"]
        for i, it in enumerate(items):
            lines.append(f"{i}. [{it.forum}] {it.title}")
        data = _extract_json(self._chat(_FILTER_SYSTEM, "\n".join(lines)))
        rel = data.get("relevant", []) if isinstance(data, dict) else data
        seen = set()
        out = []
        for x in rel:
            if isinstance(x, int) and 0 <= x < len(items) and x not in seen:
                seen.add(x)
                out.append(x)
        return out


# Global clients
transmission_client = TransmissionClient()
rutracker_client = RuTrackerClient()
llm_client = (
    LLMClient(AI_LLM_API_BASE_URL, AI_LLM_API_KEY, AI_LLM_MODEL,
              AI_LLM_KEEP_ALIVE)
    if AI_LLM_ENABLED else None
)


def _get_torrent_job_store(application: Application) -> Dict[int, object]:
    return application.bot_data.setdefault(TORRENT_JOBS_KEY, {})


def _remove_torrent_job(application: Application, torrent_id: Optional[int], active_job=None) -> None:
    if torrent_id is None:
        return
    jobs = application.bot_data.get(TORRENT_JOBS_KEY)
    if not jobs:
        return
    job = jobs.pop(torrent_id, None)
    if job and job is not active_job:
        job.schedule_removal()


def _remove_torrent_task(torrent_id: Optional[int]) -> None:
    """Remove and cancel a torrent monitoring task."""
    if torrent_id is None:
        return
    task = _torrent_monitor_tasks.pop(torrent_id, None)
    if task and not task.done():
        task.cancel()
        logger.debug(f"Cancelled and removed monitor task for torrent {torrent_id}")


def schedule_torrent_monitor(application: Application, torrent_id: Optional[int], chat_id: int,
                             torrent_name: str, download_path: str) -> None:
    """Schedule periodic checks for a torrent completion."""
    if torrent_id is None:
        logger.warning("Cannot schedule monitor: torrent ID is missing")
        return

    # Cancel existing monitoring task if present
    if torrent_id in _torrent_monitor_tasks:
        _torrent_monitor_tasks[torrent_id].cancel()
        logger.debug(f"Cancelled existing monitor task for torrent {torrent_id}")

    # Create a new asyncio task for monitoring
    task = asyncio.create_task(
        _monitor_torrent_loop(
            application=application,
            torrent_id=torrent_id,
            chat_id=chat_id,
            torrent_name=torrent_name,
            download_path=download_path
        )
    )
    _torrent_monitor_tasks[torrent_id] = task
    logger.info(f"Scheduled torrent monitor for torrent {torrent_id}")


async def _monitor_torrent_loop(application: Application, torrent_id: int, chat_id: int,
                                torrent_name: str, download_path: str) -> None:
    """Continuously monitor a torrent until it completes."""
    try:
        while True:
            await asyncio.sleep(TORRENT_POLL_INTERVAL)
            
            if not transmission_client.client:
                logger.debug("Transmission client not connected; will retry later")
                continue

            try:
                torrent = transmission_client.client.get_torrent(torrent_id)
            except TransmissionError as exc:
                if '404: Not Found' in str(exc):
                    logger.info(f"Torrent {torrent_id} appears to be removed before completion")
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=f"⚠️ Torrent removed before completion:\n{torrent_name}"
                        )
                    except Exception as send_exc:
                        logger.error(f"Failed to send removal notification for torrent {torrent_id}: {send_exc}")
                    finally:
                        _remove_torrent_task(torrent_id)
                    break
                else:
                    logger.warning(f"Failed to fetch torrent {torrent_id}: {exc}")
                continue
            except Exception as exc:
                logger.error(f"Unexpected error retrieving torrent {torrent_id}: {exc}")
                continue

            progress = getattr(torrent, 'progress', None)
            percent_done = getattr(torrent, 'percent_done', None)
            status = getattr(torrent, 'status', '').lower()

            is_complete = False
            if progress is not None:
                is_complete = progress >= 100.0
            elif percent_done is not None:
                is_complete = percent_done >= 0.999

            if status in {'seeding', 'seed_pending', 'stopped'}:
                is_complete = True

            if not is_complete:
                continue

            download_dir = getattr(torrent, 'download_dir', None) or download_path

            message_lines = [
                "✅ Torrent finished downloading!",
                f"Name: {torrent_name}"
            ]
            if download_dir:
                message_lines.append(f"Location: {download_dir}")

            try:
                await application.bot.send_message(chat_id=chat_id, text='\n'.join(message_lines))
            except Exception as exc:
                logger.error(f"Failed to send completion notification for torrent {torrent_id}: {exc}")
            finally:
                _remove_torrent_task(torrent_id)
            break
            
    except asyncio.CancelledError:
        logger.debug(f"Torrent monitor task for {torrent_id} was cancelled")
        raise


async def monitor_torrent_completion(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check torrent status and notify the user once it is complete.
    
    This function is kept for backward compatibility with job_queue if it's available,
    but the main monitoring is now done via _monitor_torrent_loop using asyncio tasks.
    """
    job = context.job
    if not job:
        return

    data = job.data or {}
    torrent_id = data.get('torrent_id')
    chat_id = data.get('chat_id')
    torrent_name = data.get('torrent_name', 'Torrent')
    download_path = data.get('download_path')

    if torrent_id is None or chat_id is None:
        logger.warning("Torrent monitor job missing required data, removing job")
        job.schedule_removal()
        if context.application:
            _remove_torrent_job(context.application, torrent_id, active_job=job)
        return

    if not transmission_client.client:
        logger.debug("Transmission client not connected; will retry later")
        return

    try:
        torrent = transmission_client.client.get_torrent(torrent_id)
    except TransmissionError as exc:
        if '404: Not Found' in str(exc):
            logger.info(f"Torrent {torrent_id} appears to be removed before completion")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Torrent removed before completion:\n{torrent_name}"
                )
            except Exception as send_exc:
                logger.error(f"Failed to send removal notification for torrent {torrent_id}: {send_exc}")
            finally:
                job.schedule_removal()
                if context.application:
                    _remove_torrent_job(context.application, torrent_id, active_job=job)
        else:
            logger.warning(f"Failed to fetch torrent {torrent_id}: {exc}")
        return
    except Exception as exc:
        logger.error(f"Unexpected error retrieving torrent {torrent_id}: {exc}")
        return

    progress = getattr(torrent, 'progress', None)
    percent_done = getattr(torrent, 'percent_done', None)
    status = getattr(torrent, 'status', '').lower()

    is_complete = False
    if progress is not None:
        is_complete = progress >= 100.0
    elif percent_done is not None:
        is_complete = percent_done >= 0.999

    if status in {'seeding', 'seed_pending', 'stopped'}:
        is_complete = True

    if not is_complete:
        return

    download_dir = getattr(torrent, 'download_dir', None) or download_path

    message_lines = [
        "✅ Torrent finished downloading!",
        f"Name: {torrent_name}"
    ]
    if download_dir:
        message_lines.append(f"Location: {download_dir}")

    try:
        await context.bot.send_message(chat_id=chat_id, text='\n'.join(message_lines))
    except Exception as exc:
        logger.error(f"Failed to send completion notification for torrent {torrent_id}: {exc}")
    finally:
        job.schedule_removal()
        if context.application:
            _remove_torrent_job(context.application, torrent_id, active_job=job)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    search_hint = "\n📝 Или просто напишите название — поищу на RuTracker!" if RUTRACKER_USERNAME else ""
    welcome_message = (
        "🤖 Welcome to Torrent Bot!\n\n"
        "Send me a magnet link and I'll help you download it via Transmission.\n"
        f"{search_hint}\n\n"
        "Commands:\n"
        "/start - Show this welcome message\n"
        "/help - Show help information\n"
        "/status - Check Transmission connection status"
    )
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    search_section = (
        "\n🔍 Поиск на RuTracker:\n"
        "1. Напишите название (например: Семь самураев)\n"
        "2. Выберите раздел\n"
        "3. Выберите раздачу\n"
        "4. Выберите папку для скачивания\n"
    ) if RUTRACKER_USERNAME else ""
    help_text = (
        "📖 How to use Torrent Bot:\n\n"
        "🧲 Magnet-ссылки:\n"
        "1. Send me a magnet link\n"
        "2. Choose a download category from the buttons\n"
        "3. I'll add it to Transmission for you!\n"
        f"{search_section}\n"
        "Available categories:\n"
        "🎬 Movies\n📺 TV Shows\n📚 Books\n🎵 Music\n🎮 Games\n📁 Other"
    )
    await update.message.reply_text(help_text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check Transmission connection status."""
    try:
        if transmission_client.client:
            session = transmission_client.client.get_session()
            status_text = (
                "✅ Transmission Status: Connected\n"
                f"Version: {session.version}\n"
                f"Download directory: {session.download_dir}\n"
                f"Active torrents: {len(transmission_client.client.get_torrents())}"
            )
        else:
            status_text = "❌ Transmission Status: Disconnected"
    except Exception as e:
        status_text = f"❌ Transmission Status: Error - {str(e)}"
    
    await update.message.reply_text(status_text)


def extract_magnet_links(message_text: str) -> List[str]:
    """Extract magnet links from message text."""
    return MAGNET_PATTERN.findall(message_text)


def _build_download_keyboard() -> InlineKeyboardMarkup:
    download_dirs = transmission_client.get_download_dirs()
    keyboard = [[InlineKeyboardButton(label, callback_data=f"download:{path}")]
                for label, path in download_dirs.items()]
    return InlineKeyboardMarkup(keyboard)


SMART_TOP_LIMIT = 25


async def _show_forum_groups(edit, results: List['RuTrackerTorrent'],
                             context: ContextTypes.DEFAULT_TYPE,
                             note: str = "") -> None:
    """Render the classic 'pick a forum section' screen."""
    groups: Dict[str, List[RuTrackerTorrent]] = defaultdict(list)
    for t in results:
        groups[t.forum].append(t)
    for lst in groups.values():
        lst.sort(key=lambda t: t.seeds, reverse=True)

    context.user_data['rt_results'] = results
    forum_list = list(groups.keys())
    context.user_data['rt_forums'] = forum_list

    header = f"{note}\n\n" if note else ""
    lines = [f"{header}Найдено {len(results)} раздач "
             f"в {len(forum_list)} разделах:\n"]
    keyboard = []
    for i, forum in enumerate(forum_list):
        count = len(groups[forum])
        lines.append(f"{i + 1}. {forum} ({count})")
        keyboard.append([InlineKeyboardButton(
            f"{forum} ({count})", callback_data=f"rt_forum:{i}"
        )])
    keyboard.append([InlineKeyboardButton(
        "📋 Все разделы", callback_data="rt_forum:all")])
    await edit("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_smart_top(edit, selected: List['RuTrackerTorrent'],
                          context: ContextTypes.DEFAULT_TYPE,
                          category: str, total: int) -> None:
    """Render the LLM-filtered flat top list (sorted by seeds)."""
    shown = selected[:SMART_TOP_LIMIT]
    context.user_data['rt_selected'] = shown
    lines = [f"🤖 Отобрано по типу «{category}»: "
             f"{len(selected)} из {total}. Сортировка по сидам:\n"]
    keyboard = []
    for i, t in enumerate(shown):
        seed_str = f"🌱{t.seeds}" if t.seeds > 0 else "💀0"
        lines.append(f"{i + 1}. {t.title[:70]}\n"
                      f"   {t.size_human} | {seed_str} сидов | {t.leeches} личей")
        keyboard.append([InlineKeyboardButton(
            f"{i + 1}. {t.size_human} | {seed_str}",
            callback_data=f"rt_torrent:{i}",
        )])
    if len(selected) > SMART_TOP_LIMIT:
        lines.append(f"\n…показаны топ-{SMART_TOP_LIMIT}.")
    keyboard.append([InlineKeyboardButton(
        "📋 Все разделы (без фильтра)", callback_data="rt_groups")])
    await edit("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages: magnet links or search queries."""
    message_text = update.message.text if update.message.text else ""
    magnet_links = extract_magnet_links(message_text)

    if magnet_links:
        context.user_data['magnet_link'] = magnet_links[0]
        await update.message.reply_text(
            "🔍 Found magnet link!\n\nPlease choose a download location:",
            reply_markup=_build_download_keyboard(),
        )
        return

    if not RUTRACKER_USERNAME or not RUTRACKER_PASSWORD:
        await update.message.reply_text(
            "I didn't find any magnet links in your message. "
            "Please send a valid magnet link starting with 'magnet:?'"
        )
        return

    query = message_text.strip()
    if not query:
        return

    # Without LLM: original behaviour (search verbatim, group by forum).
    if not llm_client:
        msg = await update.message.reply_text(
            f"🔍 Ищу '{query}' на RuTracker...")
        results = await asyncio.to_thread(rutracker_client.search, query)
        if not results:
            await msg.edit_text("Ничего не найдено.")
            return
        await _show_forum_groups(msg.edit_text, results, context)
        return

    # With LLM: parse intent -> search cleaned query -> filter by type.
    msg = await update.message.reply_text("🤖 Разбираю запрос…")
    llm_failed = False
    try:
        clean_q, category = await asyncio.to_thread(
            llm_client.parse_intent, query)
    except Exception as exc:
        logger.error(f"LLM intent parse failed: {exc}")
        clean_q, category, llm_failed = query, "any", True

    await msg.edit_text(f"🔍 Ищу '{clean_q}' на RuTracker…")
    results = await asyncio.to_thread(rutracker_client.search, clean_q)
    if not results:
        await msg.edit_text("Ничего не найдено.")
        return

    note = ("⚠️ Умная фильтрация недоступна — показываю все разделы."
            if llm_failed else "")

    # category "any" => user didn't ask for a type: no filtering, no warning.
    if llm_failed or category == "any":
        await _show_forum_groups(msg.edit_text, results, context, note=note)
        return

    await msg.edit_text(f"🧠 Отбираю подходящее (тип: {category})…")
    try:
        rel = await asyncio.to_thread(
            llm_client.filter_results, clean_q, category, results)
    except Exception as exc:
        logger.error(f"LLM filter failed: {exc}")
        rel = None

    if not rel:
        await _show_forum_groups(
            msg.edit_text, results, context,
            note="⚠️ Умная фильтрация недоступна — показываю все разделы.")
        return

    context.user_data['rt_results'] = results
    selected = sorted((results[i] for i in rel),
                      key=lambda t: t.seeds, reverse=True)
    await _show_smart_top(msg.edit_text, selected, context,
                          category, len(results))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboards."""
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "rt_groups":
        results: List[RuTrackerTorrent] = context.user_data.get(
            'rt_results', [])
        if not results:
            await query.edit_message_text(
                "❌ Результаты поиска устарели. Попробуйте снова.")
            return
        await _show_forum_groups(query.edit_message_text, results, context)
    elif data.startswith("rt_forum:"):
        await _handle_forum_selection(query, context)
    elif data.startswith("rt_torrent:"):
        await _handle_torrent_selection(query, context)
    elif data.startswith("download:"):
        await _handle_download_selection(query, context)
    else:
        await query.edit_message_text("❌ Invalid selection")


async def _handle_forum_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    choice = query.data.replace("rt_forum:", "")
    results: List[RuTrackerTorrent] = context.user_data.get('rt_results', [])
    forum_list: List[str] = context.user_data.get('rt_forums', [])

    if not results:
        await query.edit_message_text("❌ Результаты поиска устарели. Попробуйте снова.")
        return

    if choice == "all":
        selected = sorted(results, key=lambda t: t.seeds, reverse=True)
    else:
        try:
            idx = int(choice)
            forum = forum_list[idx]
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Некорректный выбор.")
            return
        selected = sorted(
            [t for t in results if t.forum == forum],
            key=lambda t: t.seeds, reverse=True,
        )

    context.user_data['rt_selected'] = selected

    lines = []
    keyboard = []
    for i, t in enumerate(selected):
        seed_str = f"🌱{t.seeds}" if t.seeds > 0 else "💀0"
        label = f"{t.size_human} | {seed_str}"
        title_short = t.title[:70]
        lines.append(f"{i + 1}. {title_short}\n   {t.size_human} | {seed_str} сидов | {t.leeches} личей")
        keyboard.append([InlineKeyboardButton(
            f"{i + 1}. {t.size_human} | {seed_str}",
            callback_data=f"rt_torrent:{i}",
        )])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_torrent_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        idx = int(query.data.replace("rt_torrent:", ""))
    except ValueError:
        await query.edit_message_text("❌ Некорректный выбор.")
        return

    selected: List[RuTrackerTorrent] = context.user_data.get('rt_selected', [])
    if idx < 0 or idx >= len(selected):
        await query.edit_message_text("❌ Некорректный выбор.")
        return

    torrent = selected[idx]
    await query.edit_message_text(f"⏳ Получаю magnet-ссылку для:\n{torrent.title[:100]}...")

    magnet = await asyncio.to_thread(rutracker_client.get_magnet, torrent.topic_id)
    if not magnet:
        await query.edit_message_text("❌ Не удалось получить magnet-ссылку.")
        return

    context.user_data['magnet_link'] = magnet
    context.user_data.pop('rt_results', None)
    context.user_data.pop('rt_forums', None)
    context.user_data.pop('rt_selected', None)

    await query.edit_message_text(
        f"🧲 {torrent.title[:100]}\n"
        f"{torrent.size_human} | 🌱 {torrent.seeds} сидов\n\n"
        f"Выберите папку для скачивания:",
        reply_markup=_build_download_keyboard(),
    )


async def _handle_download_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    download_path = query.data.replace("download:", "")
    magnet_link = context.user_data.get('magnet_link')

    if not magnet_link:
        await query.edit_message_text("❌ No magnet link found. Please send a new one.")
        return

    torrent = transmission_client.add_torrent(magnet_link, download_path)

    if torrent:
        chat_id = query.message.chat_id if query.message else query.from_user.id
        torrent_id = getattr(torrent, 'id', None)
        torrent_name = getattr(torrent, 'name', magnet_link)

        if context.application:
            schedule_torrent_monitor(
                context.application, torrent_id, chat_id,
                torrent_name, download_path,
            )

        await query.edit_message_text(
            f"✅ Торрент добавлен в Transmission!\n"
            f"Папка: {download_path}\n\n"
            f"Сообщу когда скачается."
        )
    else:
        await query.edit_message_text(
            "❌ Не удалось добавить торрент в Transmission.\n\n"
            "Проверьте:\n"
            "- Transmission запущен и доступен\n"
            "- Настройки подключения корректны"
        )

    context.user_data.pop('magnet_link', None)


async def telegram_webhook_handler(request):
    """Handle incoming Telegram webhook updates"""
    try:
        # Verify secret token if configured
        if WEBHOOK_SECRET_TOKEN:
            secret_token_header = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
            if secret_token_header != WEBHOOK_SECRET_TOKEN:
                logger.warning(f"Invalid webhook secret token from {request.remote}")
                return web.Response(text='Unauthorized', status=401)
        
        # Get the application from the request
        application = request.app['telegram_application']
        
        # Parse the update from the request body
        data = await request.json()
        update = Update.de_json(data, application.bot)
        
        # Process the update
        await application.process_update(update)
        
        return web.Response(text='OK', status=200)
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return web.Response(text='Error', status=500)


def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required")
        return
    
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    if WEBHOOK_MODE:
        # Webhook mode - run with custom web server
        logger.info("Starting Torrent Bot in webhook mode on port 8080")
        logger.info(f"Webhook URL: {WEBHOOK_URL}")
        
        # Warn if secret token is not set
        if not WEBHOOK_SECRET_TOKEN:
            logger.warning("WEBHOOK_SECRET_TOKEN is not set. Webhook endpoint is not secured!")
            logger.warning("Set WEBHOOK_SECRET_TOKEN environment variable to secure your webhook.")
        
        async def run_webhook():
            # Initialize the application
            await application.initialize()
            await application.start()
            
            # Set webhook
            await application.bot.set_webhook(
                url=WEBHOOK_URL,
                allowed_updates=["message", "callback_query"],
                secret_token=WEBHOOK_SECRET_TOKEN
            )
            logger.info(f"Webhook set to {WEBHOOK_URL}")
            
            # Create aiohttp web application
            app = web.Application()
            app['telegram_application'] = application
            
            # Add routes
            app.router.add_post('/update', telegram_webhook_handler)
            app.router.add_get(HEALTHZ_PATH, healthz_handler)
            app.router.add_get('/status', status_handler)
            
            # Start the web server with custom access logger
            class CustomAccessLogger(web.AccessLogger):
                def log(self, request, response, time):
                    # Skip logging for /healthz endpoint
                    if request.path != HEALTHZ_PATH:
                        super().log(request, response, time)
            
            runner = web.AppRunner(app, access_log_class=CustomAccessLogger)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', 8080)
            await site.start()
            
            logger.info("Web server started on port 8080")
            logger.info("Available endpoints: /update, /healthz, /status")
            
            # Keep running
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Stopping...")
            finally:
                await runner.cleanup()
                await application.stop()
                await application.shutdown()
        
        # Run the webhook server
        asyncio.run(run_webhook())
    else:
        # Polling mode (default)
        logger.info("Starting Torrent Bot in polling mode...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
