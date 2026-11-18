#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo ".env не найден. Сначала установите Remnaum." >&2; exit 1; }

# The repository may be private. Pull using the original sudo user's GitHub
# credentials, without making git files owned by root.
git_user() {
  if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    runuser -u "$SUDO_USER" -- git -C "$PWD" "$@"
  else
    git -c safe.directory="$PWD" -C "$PWD" "$@"
  fi
}

branch="$(git_user branch --show-current)"
[[ "$branch" == "main" ]] || { echo "Автообновление доступно в ветке main. Сейчас: $branch" >&2; exit 1; }
if ! grep -q "^COMPOSE_FILE=" .env; then
  echo "Режим установки не задан. Запустите sudo sh install.sh и выберите 1." >&2
  exit 1
fi

echo "Создаём резервную копию базы..."
bash backup.sh
echo "Получаем последнюю версию..."
git_user fetch --tags origin
git_user pull --ff-only origin main
echo "Пересобираем и запускаем контейнеры..."
docker compose config --quiet
docker compose up -d --build
echo "Обновление выполнено; Docker volumes сохранены."
