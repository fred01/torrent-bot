# Torrent Bot

A Telegram bot that adds magnet links to Transmission via RPC API. The bot provides an intuitive interface with inline buttons for selecting download locations and handles the complete workflow from receiving magnet links to adding them to Transmission.

## Features

- 🔗 Automatic magnet link detection
- 📁 Interactive download location selection via inline buttons  
- 🚀 Direct integration with Transmission RPC API
- 🐳 Production-ready Docker deployment
- ⚙️ Environment-based configuration
- 📝 Comprehensive logging and error handling
- 🔒 Secure non-root container execution

## Quick Start

### Using Docker Compose (Recommended)

1. Clone the repository:
```bash
git clone https://github.com/fred01/torrent-bot.git
cd torrent-bot
```

2. Copy the environment template and configure:
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. Start the services:
```bash
docker-compose up -d
```

### Manual Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables:
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TRANSMISSION_URL="http://localhost:9091"
export TRANSMISSION_USER="your_username"
export TRANSMISSION_PASS="your_password"
```

3. Run the bot:
```bash
python bot.py
```

## Configuration

The bot uses environment variables for configuration:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Telegram bot token from @BotFather |
| `TRANSMISSION_URL` | No | `http://localhost:9091` | Transmission RPC URL |
| `TRANSMISSION_USER` | No | - | Transmission username (if auth enabled) |
| `TRANSMISSION_PASS` | No | - | Transmission password (if auth enabled) |

## Usage

1. Start a chat with your bot in Telegram
2. Send the `/start` command to see the welcome message
3. Send any message containing a magnet link
4. Choose a download category from the inline buttons:
   - 🎬 Movies
   - 📺 TV Shows  
   - 📚 Books
   - 🎵 Music
   - 🎮 Games
   - 📁 Other
5. The bot will add the torrent to Transmission and confirm success

### Available Commands

- `/start` - Show welcome message
- `/help` - Display help information
- `/status` - Check Transmission connection status

## Download Categories

The bot provides predefined download categories that map to directory paths:

- Movies → `/downloads/movies`
- TV Shows → `/downloads/tvshows`
- Books → `/downloads/books`
- Music → `/downloads/music`
- Games → `/downloads/games`
- Other → `/downloads/other`

These can be customized by modifying the `DEFAULT_DOWNLOAD_DIRS` dictionary in `bot.py`.

## Docker Deployment

### Building the Image

```bash
docker build -t torrent-bot .
```

### Running with Docker

```bash
docker run -d \
  --name torrent-bot \
  -e TELEGRAM_BOT_TOKEN="your_token" \
  -e TRANSMISSION_URL="http://transmission:9091" \
  -e TRANSMISSION_USER="username" \
  -e TRANSMISSION_PASS="password" \
  torrent-bot
```

### Docker Compose

The included `docker-compose.yml` provides a complete setup with both the bot and Transmission:

```bash
# Configure environment
cp .env.example .env
# Edit .env with your settings

# Start services
docker-compose up -d

# View logs
docker-compose logs -f torrent-bot

# Stop services
docker-compose down
```

## Development

### Setup Development Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Configure your .env file
```

### Project Structure

```
torrent-bot/
├── bot.py                 # Main bot application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Docker image definition
├── docker-compose.yml    # Docker Compose setup
├── .env.example         # Environment template
├── .gitignore           # Git ignore rules
└── README.md            # This file
```

## Troubleshooting

### Common Issues

**Bot not responding:**
- Check that `TELEGRAM_BOT_TOKEN` is correctly set
- Verify the bot is running: `docker-compose logs torrent-bot`

**Transmission connection failed:**
- Verify Transmission is running and accessible
- Check `TRANSMISSION_URL` format (include http:// or https://)
- Confirm username/password if authentication is enabled
- Use `/status` command to check connection

**Torrents not downloading:**
- Check Transmission web interface for errors
- Verify download directories exist and are writable
- Ensure magnet links are valid

### Logs

View bot logs:
```bash
# Docker Compose
docker-compose logs -f torrent-bot

# Docker
docker logs -f torrent-bot

# Manual run
# Logs are printed to stdout
```

## Security Considerations

- The Docker container runs as a non-root user
- Environment variables should never be committed to version control
- Use strong passwords for Transmission authentication
- Consider running behind a reverse proxy for additional security

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is open source and available under the MIT License.
