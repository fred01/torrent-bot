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
- 📊 Built-in status page for monitoring bot and Transmission connection

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
| `WEBHOOK_MODE` | No | `false` | Enable webhook mode (`true` or `false`) |
| `WEBHOOK_URL` | No | `https://torrent-bot.svc.fred.org.ru/update` | Webhook URL for Telegram |
| `WEBHOOK_SECRET_TOKEN` | No | - | Secret token for webhook security (recommended for production) |

### Webhook Security

When using webhook mode in production, it is **strongly recommended** to set a `WEBHOOK_SECRET_TOKEN` to secure your webhook endpoint. This token is used to verify that incoming webhook requests are actually from Telegram.

Generate a secure token:
```bash
openssl rand -hex 32
```

Add it to your `.env` file:
```bash
WEBHOOK_SECRET_TOKEN=your_generated_secret_token_here
```

Without this token, your webhook endpoint will accept any POST requests, which could be a security risk.

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

## Monitoring

The bot includes built-in HTTP endpoints for monitoring and status checking:

### Health Check Endpoint

**URL**: `http://localhost:8080/healthz`

A simple health check endpoint that returns `OK` if the application is running. Useful for:
- Docker health checks
- Kubernetes liveness/readiness probes
- Load balancer health checks

```bash
curl http://localhost:8080/healthz
# Returns: OK
```

### Status Page

**URL**: `http://localhost:8080/status`

An interactive HTML status page that displays:
- **Application Status**: Running state and webhook mode
- **Transmission Connection**: Connection status, version, download directory, and active torrents count
- **Error Messages**: Detailed error information if Transmission connection fails

Access the status page in your browser to view:
- Real-time bot status
- Transmission connectivity status
- Connection error details for troubleshooting

Example URLs for different deployments:
- Local: `http://localhost:8080/status`
- Docker: `http://<container-ip>:8080/status`
- Kubernetes: `https://torrent-bot.svc.fred.org.ru/status`

## Docker Deployment

### Pre-built Images

Docker images are automatically built and published via GitHub Actions:

- **GitHub Container Registry**: `ghcr.io/fred01/torrent-bot:latest`
- **Docker Hub**: `fred01/torrent-bot:latest` (if configured)

Available tags:
- `latest` - Latest stable version from main branch
- `<build_number>` - Specific build version (e.g., `10`, `11`, `12`)
- `v1.0.0`, `v1.0`, `v1` - Semantic version tags (if using git tags)
- `main` - Latest from main branch

### Using Pre-built Images

```bash
# Using GitHub Container Registry
docker run -d \
  --name torrent-bot \
  -e TELEGRAM_BOT_TOKEN="your_token" \
  -e TRANSMISSION_URL="http://transmission:9091" \
  -e TRANSMISSION_USER="username" \
  -e TRANSMISSION_PASS="password" \
  ghcr.io/fred01/torrent-bot:latest

# Using Docker Hub (if available)
docker run -d \
  --name torrent-bot \
  -e TELEGRAM_BOT_TOKEN="your_token" \
  -e TRANSMISSION_URL="http://transmission:9091" \
  -e TRANSMISSION_USER="username" \
  -e TRANSMISSION_PASS="password" \
  fred01/torrent-bot:latest
```

### Building Locally

```bash
docker build -t torrent-bot .
```

### Docker Compose

Two Docker Compose configurations are provided:

#### Production (using published images)
```bash
# Use pre-built images from GitHub Container Registry
cp .env.example .env
# Edit .env with your settings

docker-compose -f docker-compose.prod.yml up -d
```

#### Development (building locally)
```bash
# Build from local source code
cp .env.example .env
# Edit .env with your settings

docker-compose up -d
```

Common commands:
```bash
# View logs
docker-compose logs -f torrent-bot

# Stop services
docker-compose down
```

## Kubernetes Deployment

The project includes Kubernetes deployment files for production deployment to a Kubernetes cluster with webhook support.

### Prerequisites

- Kubernetes cluster with `kubectl` configured
- `.env` file with required environment variables
- TLS certificate for `torrent-bot.svc.fred.org.ru` (stored as `torrent-bot-tls` secret)

### Quick Deployment

1. Configure your environment:
```bash
cp .env.example .env
# Edit .env with your actual values
```

2. Deploy to Kubernetes:
```bash
cd deploy
./deploy.sh <version>
```

Example:
```bash
./deploy.sh 10  # Deploy version 10
```

### What Gets Deployed

- **Deployment**: Single replica torrent-bot container with webhook support
- **Secret**: Environment variables (automatically base64 encoded)
- **Service**: Exposes health check and webhook endpoints
- **Ingress**: Routes traffic from `torrent-bot.svc.fred.org.ru` to the bot

### External Access

The bot is accessible at:
- **Webhook endpoint**: `https://torrent-bot.svc.fred.org.ru/update` (for Telegram webhook updates)
- **Health check**: `https://torrent-bot.svc.fred.org.ru/healthz` (for monitoring)
- **Status page**: `https://torrent-bot.svc.fred.org.ru/status` (for viewing bot and Transmission status)

### Monitoring

```bash
# Check deployment status
kubectl get deployment torrent-bot

# View logs
kubectl logs -l app=torrent-bot -f

# Check rollout status
kubectl rollout status deployment/torrent-bot
```

See [`deploy/README.md`](deploy/README.md) for detailed deployment documentation.

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
├── deploy/               # Kubernetes deployment files
│   ├── deploy.sh         # Deployment script
│   ├── torrent-bot-k8s.yaml      # K8s manifests
│   ├── torrent-bot-secrets.yaml  # Secrets template
│   └── README.md         # Deployment documentation
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
- Use `/status` command in Telegram or visit `http://localhost:8080/status` in your browser to check connection
- Review error messages displayed on the status page

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

## CI/CD and Container Registry

### Automated Docker Builds

This repository includes GitHub Actions workflows that automatically build and publish Docker images:

#### GitHub Container Registry (Default)
- **Workflow**: `.github/workflows/docker-build-push.yml`
- **Registry**: `ghcr.io/fred01/torrent-bot`
- **Authentication**: Uses `GITHUB_TOKEN` (automatic)
- **Multi-architecture**: Supports `linux/amd64` and `linux/arm64`

#### Docker Hub (Optional)
- **Workflow**: `.github/workflows/docker-hub.yml`
- **Registry**: `fred01/torrent-bot`
- **Requirements**: Repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`

### Build Triggers

Images are built automatically on:
- Push to `main` or `develop` branches
- Git tags starting with `v` (e.g., `v1.0.0`)
- Manual workflow dispatch
- Pull requests (build only, no push)

### Setting up Docker Hub (Optional)

To enable Docker Hub publishing, add these repository secrets:

1. Go to repository Settings → Secrets and variables → Actions
2. Add the following secrets:
   - `DOCKERHUB_USERNAME`: Your Docker Hub username
   - `DOCKERHUB_TOKEN`: Docker Hub access token (not password)

## Security Considerations

- The Docker container runs as a non-root user
- Environment variables should never be committed to version control
- Use strong passwords for Transmission authentication
- **Always set `WEBHOOK_SECRET_TOKEN`** when using webhook mode in production to secure the `/update` endpoint
- Consider running behind a reverse proxy for additional security
- The webhook secret token is validated using Telegram's `X-Telegram-Bot-Api-Secret-Token` header

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is open source and available under the MIT License.
