#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo ".env not found. Run sudo bash install.sh first." >&2; exit 1; }
branch="$(git branch --show-current)"
[[ -n "$branch" ]] || { echo "Detached HEAD; refusing automatic update." >&2; exit 1; }
if ! grep -q "^COMPOSE_FILE=" .env; then
  echo "Missing deployment mode. Run sudo sh install.sh to select panel/standalone before updating." >&2
  exit 1
fi
bash backup.sh
git fetch --tags origin
git pull --ff-only origin "$branch"
docker compose config --quiet
docker compose up -d --build
echo "Updated Remnaum on branch $branch. Data volumes were preserved."
