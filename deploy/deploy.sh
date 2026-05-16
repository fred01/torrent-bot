#!/bin/bash

# Deployment script for torrent-bot
# Usage: ./deploy.sh <version>
# Example: ./deploy.sh 10

set -e

# Portable base64 (BSD base64 on macOS has no -w flag; GNU wraps at 76).
b64() { printf %s "$1" | base64 | tr -d '\n'; }

# Check if version argument is provided
if [ $# -eq 0 ]; then
    echo "Error: Version number is required"
    echo "Usage: $0 <version>"
    echo "Example: $0 10"
    exit 1
fi

VERSION=$1
ENV_FILE="../.env"

# Check if .env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    echo "Please create .env file with required environment variables"
    exit 1
fi

echo "Deploying torrent-bot version $VERSION..."

# Load environment variables from .env file
set -a  # automatically export all variables
source "$ENV_FILE"
set +a  # stop automatically exporting

# Check required environment variables
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "Error: TELEGRAM_BOT_TOKEN not set in .env file"
    exit 1
fi

# Set default values for optional variables
TRANSMISSION_URL=${TRANSMISSION_URL:-"http://localhost:9091"}
TRANSMISSION_USER=${TRANSMISSION_USER:-""}
TRANSMISSION_PASS=${TRANSMISSION_PASS:-""}

# RuTracker search (optional) and LLM smart filtering (optional)
RUTRACKER_USERNAME=${RUTRACKER_USERNAME:-""}
RUTRACKER_PASSWORD=${RUTRACKER_PASSWORD:-""}
AI_LLM_API_BASE_URL=${AI_LLM_API_BASE_URL:-""}
AI_LLM_API_KEY=${AI_LLM_API_KEY:-""}
AI_LLM_MODEL=${AI_LLM_MODEL:-"gemma3:12b"}
AI_LLM_KEEP_ALIVE=${AI_LLM_KEEP_ALIVE:-"30m"}

# Generate WEBHOOK_SECRET_TOKEN if not set
if [ -z "$WEBHOOK_SECRET_TOKEN" ]; then
    echo "WEBHOOK_SECRET_TOKEN not found in .env file, generating a new one..."
    WEBHOOK_SECRET_TOKEN=$(openssl rand -hex 32)
    echo "Generated WEBHOOK_SECRET_TOKEN: $WEBHOOK_SECRET_TOKEN"
    echo "Consider adding this to your .env file for future deployments"
fi

# Base64 encode the secrets
TELEGRAM_BOT_TOKEN_B64=$(b64 "$TELEGRAM_BOT_TOKEN")
TRANSMISSION_URL_B64=$(b64 "$TRANSMISSION_URL")
TRANSMISSION_USER_B64=$(b64 "$TRANSMISSION_USER")
TRANSMISSION_PASS_B64=$(b64 "$TRANSMISSION_PASS")
WEBHOOK_SECRET_TOKEN_B64=$(b64 "$WEBHOOK_SECRET_TOKEN")
RUTRACKER_USERNAME_B64=$(b64 "$RUTRACKER_USERNAME")
RUTRACKER_PASSWORD_B64=$(b64 "$RUTRACKER_PASSWORD")
AI_LLM_API_BASE_URL_B64=$(b64 "$AI_LLM_API_BASE_URL")
AI_LLM_API_KEY_B64=$(b64 "$AI_LLM_API_KEY")
AI_LLM_MODEL_B64=$(b64 "$AI_LLM_MODEL")
AI_LLM_KEEP_ALIVE_B64=$(b64 "$AI_LLM_KEEP_ALIVE")

# Create temporary files for manifests
TEMP_SECRETS=$(mktemp)
TEMP_DEPLOYMENT=$(mktemp)

# Cleanup function
cleanup() {
    rm -f "$TEMP_SECRETS" "$TEMP_DEPLOYMENT"
}
trap cleanup EXIT

# Replace placeholders in secrets template
# Note: '|' as the sed delimiter — base64 can contain '/' but never '|'.
sed "s|{{ TELEGRAM_BOT_TOKEN_PLACEHOLDER }}|$TELEGRAM_BOT_TOKEN_B64|g; \
     s|{{ TRANSMISSION_URL_PLACEHOLDER }}|$TRANSMISSION_URL_B64|g; \
     s|{{ TRANSMISSION_USER_PLACEHOLDER }}|$TRANSMISSION_USER_B64|g; \
     s|{{ TRANSMISSION_PASS_PLACEHOLDER }}|$TRANSMISSION_PASS_B64|g; \
     s|{{ WEBHOOK_SECRET_TOKEN_PLACEHOLDER }}|$WEBHOOK_SECRET_TOKEN_B64|g; \
     s|{{ RUTRACKER_USERNAME_PLACEHOLDER }}|$RUTRACKER_USERNAME_B64|g; \
     s|{{ RUTRACKER_PASSWORD_PLACEHOLDER }}|$RUTRACKER_PASSWORD_B64|g; \
     s|{{ AI_LLM_API_BASE_URL_PLACEHOLDER }}|$AI_LLM_API_BASE_URL_B64|g; \
     s|{{ AI_LLM_API_KEY_PLACEHOLDER }}|$AI_LLM_API_KEY_B64|g; \
     s|{{ AI_LLM_MODEL_PLACEHOLDER }}|$AI_LLM_MODEL_B64|g; \
     s|{{ AI_LLM_KEEP_ALIVE_PLACEHOLDER }}|$AI_LLM_KEEP_ALIVE_B64|g" \
    torrent-bot-secrets.yaml > "$TEMP_SECRETS"

# Replace version placeholder in deployment template
sed "s/{{ VERSION_PLACEHOLDER }}/$VERSION/g" \
    torrent-bot-k8s.yaml > "$TEMP_DEPLOYMENT"

echo "Creating namespace if it doesn't exist..."
kubectl create namespace torrent-bot --dry-run=client -o yaml | kubectl apply -f -

echo "Applying Kubernetes secrets..."
kubectl apply -f "$TEMP_SECRETS"

echo "Applying Kubernetes deployment..."
kubectl apply -f "$TEMP_DEPLOYMENT"

echo "Deployment completed successfully!"
echo "Checking deployment status..."

# Wait for deployment to be ready
kubectl rollout status deployment/torrent-bot -n torrent-bot --timeout=300s

echo "Torrent bot version $VERSION has been deployed successfully!"
echo "The bot is running as a Kubernetes deployment and will automatically connect to Telegram."