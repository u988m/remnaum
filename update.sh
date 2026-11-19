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
echo "Собираем новый образ приложения..."
docker compose config --quiet
docker compose build app
echo "Проверяем пароль и доступ к PostgreSQL..."
if ! docker compose run --rm --no-deps -T --entrypoint python app -m app.db.check_connection; then
  echo "Обновление остановлено до замены контейнеров. Резервная копия сохранена." >&2
  exit 1
fi
echo "Запускаем контейнеры..."
docker compose up -d
echo "Обновление выполнено; Docker volumes сохранены."
