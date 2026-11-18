#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
command -v docker >/dev/null 2>&1 || { echo "Docker is required."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin is required."; exit 1; }
if [[ ! -f .env ]]; then
  cp .env.example .env
  secret="$(openssl rand -hex 32)"
  dbpass="$(openssl rand -hex 24)"
  sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$secret|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$dbpass|" .env
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://remnaum:$dbpass@postgres:5432/remnaum|" .env
  echo
  read -r -p "Telegram bot token (leave empty to configure later): " token
  if [[ -n "$token" ]]; then sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$token|" .env; fi
  read -r -p "Telegram admin IDs, comma separated (optional): " admins
  if [[ -n "$admins" ]]; then sed -i "s|^ADMIN_IDS=.*|ADMIN_IDS=$admins|" .env; fi
  read -r -p "Remnawave panel URL (optional): " panel
  if [[ -n "$panel" ]]; then sed -i "s|^REMNAWAVE_BASE_URL=.*|REMNAWAVE_BASE_URL=$panel|" .env; fi
  read -r -p "Remnawave API token (optional): " panel_token
  if [[ -n "$panel_token" ]]; then sed -i "s|^REMNAWAVE_API_TOKEN=.*|REMNAWAVE_API_TOKEN=$panel_token|" .env; fi
  chmod 600 .env
fi
docker compose up -d --build
echo "Remnaum is starting. Check logs with: docker compose logs -f app"
