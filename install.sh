#!/bin/sh
# POSIX entrypoint: install or update through the same interactive wizard.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите: sudo sh install.sh" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "Нужен Python 3. Автоматическая установка поддерживается на Ubuntu/Debian." >&2
        exit 1
    fi
    echo "Устанавливаем Python 3 для запуска мастера..."
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3
fi
exec python3 scripts/install.py "$@"
