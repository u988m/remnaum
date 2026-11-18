#!/bin/sh
# POSIX wrapper: supports both "sh install.sh" and "bash install.sh".
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ "$(id -u)" -ne 0 ]; then
    echo "Run: sudo sh install.sh" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required. Install python3 before running this installer." >&2
    exit 1
fi
exec python3 scripts/install.py "$@"
