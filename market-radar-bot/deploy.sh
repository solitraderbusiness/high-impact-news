#!/bin/bash
# Deploy script for Market Radar Bot
# This script is called by the admin panel "Deploy Now" button

set -e

# Change to project directory
cd "$(dirname "$0")"

echo "=== Market Radar Deploy ==="
echo "Timestamp: $(date)"

# Get current branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Current branch: $BRANCH"

# Pull latest changes
echo "Pulling latest changes..."
git fetch origin
git reset --hard origin/$BRANCH

# Restart service (requires sudoers entry - see below)
echo "Restarting service..."
sudo systemctl restart market-radar || sudo systemctl restart radarbot || echo "Warning: Could not restart service automatically"

echo "=== Deploy Complete ==="
echo ""
echo "If service restart failed, run manually:"
echo "  sudo systemctl restart market-radar"
