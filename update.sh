#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo ".env not found. Run ./install.sh first."; exit 1; }
branch="$(git branch --show-current)"
[[ -n "$branch" ]] || { echo "Detached HEAD; refusing automatic update."; exit 1; }
git fetch --tags origin
git pull --ff-only origin "$branch"
docker compose up -d --build
docker image prune -f
echo "Remnaum updated on branch $branch."
