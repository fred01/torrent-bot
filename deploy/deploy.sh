#!/bin/bash

# Deployment script for torrent-bot
# Usage: ./deploy.sh <version>
# Example: ./deploy.sh 10

set -e

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

# Base64 encode the secrets
TELEGRAM_BOT_TOKEN_B64=$(echo -n "$TELEGRAM_BOT_TOKEN" | base64 -w 0)
TRANSMISSION_URL_B64=$(echo -n "$TRANSMISSION_URL" | base64 -w 0)
TRANSMISSION_USER_B64=$(echo -n "$TRANSMISSION_USER" | base64 -w 0)
TRANSMISSION_PASS_B64=$(echo -n "$TRANSMISSION_PASS" | base64 -w 0)

# Create temporary files for manifests
TEMP_SECRETS=$(mktemp)
TEMP_DEPLOYMENT=$(mktemp)

# Cleanup function
cleanup() {
    rm -f "$TEMP_SECRETS" "$TEMP_DEPLOYMENT"
}
trap cleanup EXIT

# Replace placeholders in secrets template
sed "s/TELEGRAM_BOT_TOKEN_PLACEHOLDER/$TELEGRAM_BOT_TOKEN_B64/g; \
     s/TRANSMISSION_URL_PLACEHOLDER/$TRANSMISSION_URL_B64/g; \
     s/TRANSMISSION_USER_PLACEHOLDER/$TRANSMISSION_USER_B64/g; \
     s/TRANSMISSION_PASS_PLACEHOLDER/$TRANSMISSION_PASS_B64/g" \
    torrent-bot-secrets.yaml > "$TEMP_SECRETS"

# Replace version placeholder in deployment template
sed "s/VERSION_PLACEHOLDER/$VERSION/g" \
    torrent-bot-k8s.yaml > "$TEMP_DEPLOYMENT"

echo "Applying Kubernetes secrets..."
kubectl apply -f "$TEMP_SECRETS"

echo "Applying Kubernetes deployment..."
kubectl apply -f "$TEMP_DEPLOYMENT"

echo "Deployment completed successfully!"
echo "Checking deployment status..."

# Wait for deployment to be ready
kubectl rollout status deployment/torrent-bot --timeout=300s

echo "Torrent bot version $VERSION has been deployed successfully!"
echo "The bot is running as a Kubernetes deployment and will automatically connect to Telegram."