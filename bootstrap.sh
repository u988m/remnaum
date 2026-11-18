#!/bin/sh
# One-command bootstrap when repository is publicly accessible.
# For private repositories, git clone requires the owner's GitHub credentials.
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root: curl ... | sudo sh" >&2
  exit 1
fi
if [ -r /dev/tty ]; then
  exec 3</dev/tty
else
  echo "Для выбора каталога нужен интерактивный терминал." >&2
  exit 1
fi
printf '\n🌊 Remnaum — автоматическая установка\n'
printf 'Папка для установки [/opt/remnaum]: '
IFS= read -r DEST <&3 || true
DEST=${DEST:-/opt/remnaum}
case "$DEST" in
  /) echo "Нельзя устанавливать в /" >&2; exit 1 ;;
  /*) ;;
  *) echo "Введите абсолютный путь, например /opt/remnaum" >&2; exit 1 ;;
esac
case "$DEST" in
  *' '*|*'..'*|*'%'*|*'"'*|*"'"*) echo "Путь содержит запрещённые символы" >&2; exit 1 ;;
esac
if [ -f "$DEST/install.sh" ] && [ -f "$DEST/scripts/install.py" ]; then
  echo "Проект найден: $DEST"
  cd "$DEST"
  exec sh ./install.sh --no-relocate
fi
if [ -e "$DEST" ] && [ "$(ls -A "$DEST")" ]; then
  echo "Каталог уже занят: $DEST. Данные не тронуты." >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Для установки нужны git и Python3 (автоустановка доступна на Ubuntu/Debian)." >&2
    exit 1
  fi
  echo "Устанавливаем недостающие git/Python3..."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 ca-certificates
fi
TEMP=$(mktemp -d /tmp/remnaum-setup.XXXXXXXX)
trap 'rm -rf "$TEMP"' EXIT INT TERM
OWNER=${SUDO_USER:-root}
if [ "$OWNER" != root ] && id "$OWNER" >/dev/null 2>&1; then
  chown "$OWNER" "$TEMP"
  runuser -u "$OWNER" -- git clone --depth 1 https://github.com/u988m/remnaum.git "$TEMP/project" || {
    echo "GitHub не дал скачать проект. Проверьте доступ к репозиторию." >&2
    exit 1
  }
else
  git clone --depth 1 https://github.com/u988m/remnaum.git "$TEMP/project" || exit 1
fi
mkdir -p "$(dirname "$DEST")"
if [ -d "$DEST" ]; then
  rmdir "$DEST"
fi
mv "$TEMP/project" "$DEST"
echo "Проект скачан: $DEST"
cd "$DEST"
exec sh ./install.sh --no-relocate
