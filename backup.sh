#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
umask 077
if [[ "${EUID}" -ne 0 ]]; then
  exec sudo bash "$0" "$@"
fi
exec python3 scripts/backup_manager.py create "$@"
