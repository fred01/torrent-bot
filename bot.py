#!/usr/bin/env python3
"""
Telegram bot to download magnet links via Transmission RPC API
"""

import os
import re
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse
import asyncio

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

# Webhook configuration
WEBHOOK_MODE = os.getenv('WEBHOOK_MODE', 'false').lower() == 'true'
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://torrent-bot.svc.fred.org.ru/update')
WEBHOOK_LISTEN = os.getenv('WEBHOOK_LISTEN', '0.0.0.0')
WEBHOOK_PORT = 8080  # Fixed port for all endpoints
WEBHOOK_SECRET_TOKEN = os.getenv('WEBHOOK_SECRET_TOKEN')

# Default download directories if not available from Transmission
DEFAULT_DOWNLOAD_DIRS = {
    '🎬 Movies': '/downloads/complete/movies',
    '📺 TV Shows': '/downloads/complete/tvseries',
    '📚 Books': '/downloads/complete/books',
    '🎮 Games': '/downloads/complete/games',
    '📁 Other': '/downloads/complete/soft',
    '📖 Courses': '/downloads/complete/courses'
}

# Magnet link regex pattern
MAGNET_PATTERN = re.compile(r'magnet:\?[^\s]+')


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
        logger.info(f"Starting Torrent Bot in webhook mode on {WEBHOOK_LISTEN}:{WEBHOOK_PORT}")
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
            app.router.add_get('/healthz', healthz_handler)
            app.router.add_get('/status', status_handler)
            
            # Start the web server
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, WEBHOOK_LISTEN, WEBHOOK_PORT)
            await site.start()
            
            logger.info(f"Web server started on {WEBHOOK_LISTEN}:{WEBHOOK_PORT}")
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