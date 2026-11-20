#!/bin/sh
# One-command bootstrap when repository is publicly accessible.
# For private repositories, git clone requires the owner's GitHub credentials.
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root: curl ... | sudo sh" >&2
  exit 1
fi
if ! ( : </dev/tty ) 2>/dev/null; then
  echo "Для установки нужен интерактивный терминал." >&2
  exit 1
fi
exec 3</dev/tty
is_project() {
  [ -f "$1/install.sh" ] && [ -f "$1/scripts/install.py" ] && [ -f "$1/compose.yaml" ]
}

DEFAULT=/opt/remnaum
# The launcher contains the actual location selected on a previous installation.
# Read its path as data instead of sourcing the file.
if [ -r /usr/local/bin/remnaum ]; then
  INSTALLED=$(sed -n 's|^cd \(/[A-Za-z0-9_./-]*\)$|\1|p' /usr/local/bin/remnaum)
  if [ -n "$INSTALLED" ] && is_project "$INSTALLED" && [ -f "$INSTALLED/.env" ]; then
    DEFAULT=$INSTALLED
  fi
fi
printf '\n🌊 Remnaum — автоматическая установка\n'
printf 'Папка проекта [%s]: ' "$DEFAULT"
IFS= read -r DEST <&3 || exit 1
DEST=${DEST:-$DEFAULT}
case "$DEST" in
  /*) ;;
  *) echo "Введите абсолютный путь, например /opt/remnaum" >&2; exit 1 ;;
esac
DEST=$(realpath -m -- "$DEST")
case "$DEST" in
  /|/etc|/etc/*|/bin|/bin/*|/usr|/usr/*|/dev|/dev/*|/proc|/proc/*|/sys|/sys/*|*[!A-Za-z0-9_./-]*)
    echo "Выберите отдельный каталог проекта, например /opt/remnaum." >&2
    exit 1 ;;
esac
if is_project "$DEST"; then
  echo "Проект найден: $DEST"
  cd "$DEST"
  exec sh ./install.sh --no-relocate </dev/tty
fi
if [ -e "$DEST" ]; then
  if [ ! -d "$DEST" ] || [ "$(ls -A -- "$DEST")" ]; then
    echo "Каталог уже занят: $DEST. Данные сохранены." >&2
    exit 1
  fi
fi
printf 'Проект не найден. Скачать Remnaum в %s? (Д/н): ' "$DEST"
IFS= read -r ANSWER <&3 || exit 1
case "$ANSWER" in
  ''|д|Д|да|Да|y|Y|yes|YES) ;;
  *) exit 0 ;;
esac
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
  runuser -u "$OWNER" -- git clone https://github.com/u988m/remnaum.git "$TEMP/project" || {
    echo "GitHub не дал скачать проект. Проверьте доступ к репозиторию." >&2
    exit 1
  }
else
  git clone https://github.com/u988m/remnaum.git "$TEMP/project" || exit 1
fi
mkdir -p "$(dirname "$DEST")"
if [ -d "$DEST" ]; then
  rmdir "$DEST"
fi
mv -T "$TEMP/project" "$DEST"
rmdir "$TEMP"
trap - EXIT INT TERM
echo "Проект скачан: $DEST"
cd "$DEST"
exec sh ./install.sh --no-relocate </dev/tty
