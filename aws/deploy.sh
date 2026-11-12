#!/usr/bin/env bash
# Get the latest code from GitHub and restart Job Hunt. Run on the server: bash aws/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Missing .env - copy the keys from Render's Environment tab first."; exit 1; }
git pull --ff-only
docker build -t jobhunt-app .
docker compose -f docker-compose.aws.yml up -d
docker image prune -f >/dev/null
docker compose -f docker-compose.aws.yml ps
