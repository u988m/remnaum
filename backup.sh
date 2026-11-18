#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
umask 077
[[ -f .env ]] || { echo "No .env: installation not configured" >&2; exit 1; }
mkdir -p backups
chmod 700 backups
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="backups/remnaum-$timestamp.sql.gz"
docker compose exec -T postgres pg_dump -U remnaum --no-owner --no-acl remnaum | gzip -9 > "$archive"
[[ -s "$archive" ]] || { rm -f "$archive"; echo "Backup empty" >&2; exit 1; }
echo "Database backup saved at $archive"
echo "IMPORTANT: preserve your .env and APP_SECRET_KEY separately in secure storage."
