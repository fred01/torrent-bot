# Kubernetes Deployment for Torrent Bot

This directory contains Kubernetes deployment files and scripts for deploying the torrent-bot to a Kubernetes cluster.

## Files

- `torrent-bot-k8s.yaml` - Main Kubernetes deployment manifest (Deployment + Service)
- `torrent-bot-secrets.yaml` - Template for Kubernetes secrets
- `deploy.sh` - Deployment script that populates secrets and applies manifests
- `README.md` - This documentation

## Prerequisites

1. `kubectl` configured to connect to your Kubernetes cluster
2. `.env` file in the project root with required environment variables
3. Docker image `ghcr.io/fred01/torrent-bot:<version>` available (built by GitHub Actions)
4. TLS certificate for `torrent-bot.svc.fred.org.ru` (should be available as `torrent-bot-tls` secret in the namespace)

## Environment Variables

Create a `.env` file in the project root directory with the following variables:

```bash
# Required
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Optional (defaults provided)
TRANSMISSION_URL=http://localhost:9091
TRANSMISSION_USER=your_transmission_username
TRANSMISSION_PASS=your_transmission_password

# Webhook Configuration (for production deployment)
WEBHOOK_MODE=true
WEBHOOK_URL=https://torrent-bot.svc.fred.org.ru/update
WEBHOOK_PORT=8443
WEBHOOK_LISTEN=0.0.0.0
```

**Note**: The Kubernetes deployment automatically sets `WEBHOOK_MODE=true` to use webhook mode instead of long polling for better performance and reliability.

## Deployment

1. Ensure your `.env` file is configured with the correct values
2. Run the deployment script with the desired version number:

```bash
cd deploy
./deploy.sh <version>
```

Example:
```bash
./deploy.sh 10
```

This will:
- Read environment variables from `../.env`
- Base64 encode the secrets
- Apply the secrets to Kubernetes
- Deploy the torrent-bot with the specified version
- Wait for the deployment to be ready

## Deployed Resources

The deployment creates the following Kubernetes resources in the `torrent-bot` namespace:

- **Namespace**: `torrent-bot` - Dedicated namespace for the application
- **Deployment**: `torrent-bot` - Runs the bot container with health checks and webhook support
- **Secret**: `torrent-bot-secrets` - Contains environment variables
- **Service**: `torrent-bot-service` - Exposes health check and webhook endpoints
- **Ingress**: `torrent-bot-ingress` - Routes external traffic from `torrent-bot.svc.fred.org.ru` to the bot

## Resource Configuration

The deployment is configured with:
- **Replicas**: 1 (single instance)
- **CPU Request**: 100m
- **CPU Limit**: 200m
- **Memory Request**: 128Mi
- **Memory Limit**: 256Mi
- **Health Checks**: HTTP-based liveness and readiness probes on `/healthz` endpoint
- **Webhook Mode**: Uses Telegram webhooks instead of long polling for better performance
- **External Access**: Available at `https://torrent-bot.svc.fred.org.ru/update` for webhook updates

## Monitoring

The bot includes health checks that verify the Python process is running. You can check the status with:

```bash
# Check deployment status  
kubectl get deployment torrent-bot -n torrent-bot

# Check pod status
kubectl get pods -l app=torrent-bot -n torrent-bot

# View logs
kubectl logs -l app=torrent-bot -n torrent-bot -f

# Check deployment rollout status
kubectl rollout status deployment/torrent-bot -n torrent-bot

# Test health endpoint
kubectl port-forward -n torrent-bot service/torrent-bot-service 8080:8080 &
curl http://localhost:8080/healthz
```

## Updating

To update to a new version:

1. Ensure the new Docker image version exists in the registry
2. Run the deploy script with the new version number:

```bash
./deploy.sh <new_version>
```

This will perform a rolling update of the deployment.

## Troubleshooting

### Common Issues

1. **Missing .env file**: Ensure `.env` exists in the project root directory
2. **Invalid secrets**: Check that all required environment variables are set in `.env`
3. **Image not found**: Verify the version number exists in the container registry
4. **kubectl not configured**: Ensure you have access to the target Kubernetes cluster

### Debug Commands

```bash
# Check pod details
kubectl describe pod -l app=torrent-bot -n torrent-bot

# Check events
kubectl get events -n torrent-bot --sort-by=.metadata.creationTimestamp

# Check secrets
kubectl get secret torrent-bot-secrets -n torrent-bot -o yaml

# Check deployment details
kubectl describe deployment torrent-bot -n torrent-bot
```