#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
umask 077
[[ $EUID -eq 0 ]] || { echo "Use sudo bash install.sh"; exit 1; }
command -v openssl >/dev/null || { echo "openssl required"; exit 1; }
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  . /etc/os-release
  [[ "$ID" == ubuntu || "$ID" == debian ]] || { echo "This installer supports Ubuntu/Debian; install Docker+Compose manually." >&2; exit 1; }
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  if ! command -v docker >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker
fi
docker compose version >/dev/null || { echo "Docker Compose v2 required" >&2; exit 1; }
ask() { local name="$1" label="$2" val; read -r -p "$label: " val; [[ -n "$val" ]] || { echo "Required: $name"; exit 1; }; printf -v "$name" '%s' "$val"; }
secret() { local name="$1" label="$2" val; read -r -s -p "$label: " val; echo; [[ -n "$val" ]] || { echo "Required: $name"; exit 1; }; printf -v "$name" '%s' "$val"; }
if [[ -f .env ]]; then
  echo "Existing installation detected: database and secrets preserved."
  read -r -p "Restart using existing settings? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || exit 0
else
  echo "Remnaum installer — Caddy automatic HTTPS and internal Nginx"
  ask public_domain "Webhook domain without https://"
  ask webapp_domain "Mini App/admin domain without https://"
  ask acme_email "TLS certificate email"
  ask bot_username "Bot username without @"
  secret bot_token "Telegram bot token"
  ask admin_ids "Administrator Telegram IDs (comma separated)"
  ask panel_url "Remnawave panel HTTPS URL"
  secret panel_token "Remnawave API token"
  ask squad_ids "Active Remnawave internal squad UUIDs (comma separated)"
  read -r -p "Terms of service HTTPS URL (optional until selling): " terms_url
  read -r -p "Support contact (optional): " support_contact
  [[ "$public_domain" =~ ^[a-zA-Z0-9.-]+$ && "$public_domain" == *.* ]] || { echo "Invalid webhook domain"; exit 1; }
  [[ "$webapp_domain" =~ ^[a-zA-Z0-9.-]+$ && "$webapp_domain" == *.* ]] || { echo "Invalid Mini App domain"; exit 1; }
  [[ "$admin_ids" =~ ^[0-9]+(,[0-9]+)*$ ]] || { echo "Invalid admin IDs"; exit 1; }
  [[ "$bot_username" =~ ^[a-zA-Z0-9_]+$ ]] || { echo "Invalid bot username"; exit 1; }
  [[ "$panel_url" =~ ^https://[^[:space:]]+$ ]] || { echo "Panel must use HTTPS"; exit 1; }
  [[ -z "$terms_url" || "$terms_url" =~ ^https://[^[:space:]]+$ ]] || { echo "Terms must use HTTPS"; exit 1; }
  for v in "$bot_token" "$panel_token" "$support_contact" "$squad_ids"; do [[ "$v" != *$'\n'* && "$v" != *$'\r'* ]] || { echo "Multiline inputs rejected"; exit 1; }; done
  key="$(openssl rand -hex 32)"
  dbpass="$(openssl rand -hex 24)"
  tgsecret="$(openssl rand -hex 32)"
  remsecret="$(openssl rand -hex 32)"
  cat > .env <<ENV
APP_NAME=Remnaum
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8080
APP_SECRET_KEY=$key
ADMIN_API_TOKEN=
LOG_LEVEL=INFO
PUBLIC_DOMAIN=$public_domain
WEBAPP_DOMAIN=$webapp_domain
ACME_EMAIL=$acme_email
POSTGRES_PASSWORD=$dbpass
DATABASE_URL=postgresql+asyncpg://remnaum:$dbpass@postgres:5432/remnaum
REDIS_URL=redis://redis:6379/0
TELEGRAM_BOT_TOKEN=$bot_token
TELEGRAM_BOT_USERNAME=$bot_username
TELEGRAM_WEBHOOK_URL=https://$public_domain/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=$tgsecret
TELEGRAM_USE_WEBHOOK=true
ADMIN_IDS=$admin_ids
REMNAWAVE_BASE_URL=$panel_url
REMNAWAVE_API_TOKEN=$panel_token
REMNAWAVE_ACTIVE_INTERNAL_SQUADS=$squad_ids
REMNAWAVE_WEBHOOK_SECRET=$remsecret
PUBLIC_BASE_URL=https://$webapp_domain
WEBAPP_URL=https://$webapp_domain/app
TELEGRAM_TERMS_URL=$terms_url
SUPPORT_CONTACT=$support_contact
ENV
  chmod 600 .env
fi
# Migrate legacy .env without touching existing customer database volumes.
if ! grep -q '^PUBLIC_DOMAIN=' .env; then
  echo "Legacy config detected; configuring HTTPS domains without resetting data."
  ask public_domain "Webhook domain without https://"
  ask webapp_domain "Mini App/admin domain without https://"
  ask acme_email "TLS certificate email"
  [[ "$public_domain" =~ ^[a-zA-Z0-9.-]+$ && "$public_domain" == *.* ]] || exit 1
  [[ "$webapp_domain" =~ ^[a-zA-Z0-9.-]+$ && "$webapp_domain" == *.* ]] || exit 1
  printf 'PUBLIC_DOMAIN=%s\nWEBAPP_DOMAIN=%s\nACME_EMAIL=%s\n' "$public_domain" "$webapp_domain" "$acme_email" >> .env
  sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$webapp_domain|" .env
  sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=https://$webapp_domain/app|" .env
  sed -i "s|^TELEGRAM_WEBHOOK_URL=.*|TELEGRAM_WEBHOOK_URL=https://$public_domain/telegram/webhook|" .env
  sed -i "s|^TELEGRAM_USE_WEBHOOK=.*|TELEGRAM_USE_WEBHOOK=true|" .env
fi
if ! grep -q '^REMNAWAVE_WEBHOOK_SECRET=' .env; then
  echo "REMNAWAVE_WEBHOOK_SECRET=$(openssl rand -hex 32)" >> .env
fi
chmod 600 .env
mkdir -p data
docker compose config --quiet
docker compose up -d --build
echo "Containers started; verify DNS, HTTPS and /health."
echo "Configure Remnawave outbound webhook as documented in docs/INSTALL.md."
echo "Inspect logs: docker compose logs --tail=100 app caddy"
