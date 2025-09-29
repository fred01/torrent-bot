#!/usr/bin/env python3
"""
Telegram bot to download magnet links via Transmission RPC API
"""

import os
import re
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import transmission_rpc
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

# Default download directories if not available from Transmission
DEFAULT_DOWNLOAD_DIRS = {
    '🎬 Movies': '/downloads/movies',
    '📺 TV Shows': '/downloads/tvshows', 
    '📚 Books': '/downloads/books',
    '🎵 Music': '/downloads/music',
    '🎮 Games': '/downloads/games',
    '📁 Other': '/downloads/other'
}

# Magnet link regex pattern
MAGNET_PATTERN = re.compile(r'magnet:\?[^\s]+')


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks"""
    
    def do_GET(self):
        if self.path == '/healthz':
            # Simple health check - if we can respond, we're healthy
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs to reduce noise
        pass


def start_health_server():
    """Start a simple HTTP server for health checks on port 8080"""
    try:
        server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
        logger.info("Health check server started on port 8080")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start health check server: {e}")


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
    
    def add_torrent(self, magnet_url: str, download_dir: str) -> bool:
        """Add magnet link to Transmission"""
        if not self.client:
            logger.error("Transmission client not available")
            return False
        
        try:
            torrent = self.client.add_torrent(magnet_url, download_dir=download_dir)
            logger.info(f"Added torrent: {torrent.name} to {download_dir}")
            return True
        except Exception as e:
            logger.error(f"Failed to add torrent: {e}")
            return False


# Global transmission client
transmission_client = TransmissionClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    welcome_message = (
        "🤖 Welcome to Torrent Bot!\n\n"
        "Send me a magnet link and I'll help you download it via Transmission.\n\n"
        "Commands:\n"
        "/start - Show this welcome message\n"
        "/help - Show help information\n"
        "/status - Check Transmission connection status"
    )
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = (
        "📖 How to use Torrent Bot:\n\n"
        "1. Send me a magnet link\n"
        "2. Choose a download category from the buttons\n"
        "3. I'll add it to Transmission for you!\n\n"
        "Supported magnet link format:\n"
        "magnet:?xt=urn:btih:...\n\n"
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages and look for magnet links."""
    message_text = update.message.text if update.message.text else ""
    magnet_links = extract_magnet_links(message_text)
    
    if not magnet_links:
        await update.message.reply_text(
            "I didn't find any magnet links in your message. "
            "Please send a valid magnet link starting with 'magnet:?'"
        )
        return
    
    # For now, handle only the first magnet link
    magnet_link = magnet_links[0]
    
    # Store the magnet link in user context
    context.user_data['magnet_link'] = magnet_link
    
    # Get available download directories
    download_dirs = transmission_client.get_download_dirs()
    
    # Create inline keyboard with download options
    keyboard = []
    for label, path in download_dirs.items():
        keyboard.append([InlineKeyboardButton(label, callback_data=f"download:{path}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🔍 Found magnet link!\n\n"
        f"Please choose a download location:",
        reply_markup=reply_markup
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("download:"):
        await query.edit_message_text("❌ Invalid selection")
        return
    
    # Extract download path from callback data
    download_path = query.data.replace("download:", "")
    magnet_link = context.user_data.get('magnet_link')
    
    if not magnet_link:
        await query.edit_message_text("❌ No magnet link found. Please send a new one.")
        return
    
    # Add torrent to Transmission
    success = transmission_client.add_torrent(magnet_link, download_path)
    
    if success:
        await query.edit_message_text(
            f"✅ Success!\n\n"
            f"Torrent added to Transmission\n"
            f"Download location: {download_path}\n\n"
            f"You can check the progress in your Transmission client."
        )
    else:
        await query.edit_message_text(
            f"❌ Failed to add torrent to Transmission.\n\n"
            f"Please check:\n"
            f"- Transmission is running and accessible\n"
            f"- Connection settings are correct\n"
            f"- The magnet link is valid"
        )
    
    # Clear the stored magnet link
    context.user_data.pop('magnet_link', None)


def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required")
        return
    
    # Start health check server in a separate thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Run the bot until the user presses Ctrl-C
    logger.info("Starting Torrent Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()