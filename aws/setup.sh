#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server for Job Hunt. Run once: bash aws/setup.sh  (then log out and back in)
set -euo pipefail
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker "$USER"
# 2 GB of swap so building the app never runs out of memory on a small server
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
sudo systemctl enable --now docker
echo "Done. Log out and back in, create .env (and oauth_credentials.json), then run: bash aws/deploy.sh"
