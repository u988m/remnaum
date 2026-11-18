#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo ".env не найден. Сначала установите Remnaum." >&2; exit 1; }

git_user() {
  if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    runuser -u "$SUDO_USER" -- git -C "$PWD" "$@"
  else
    git -c safe.directory="$PWD" -C "$PWD" "$@"
  fi
}

release="${1:-}"
if (( $# > 1 )) || [[ -n "$release" && ! "$release" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$ ]]; then
  echo "Укажите тег выпуска, например v1.0.0." >&2
  exit 2
fi
branch="$(git_user branch --show-current)"
[[ "$branch" == "main" ]] || { echo "Обновление доступно в ветке main. Сейчас: $branch" >&2; exit 1; }
git_dir="$(git_user rev-parse --absolute-git-dir)"
exec 9>"$git_dir/remnaum-update.lock"
flock -n 9 || { echo "Обновление уже выполняется." >&2; exit 1; }
if [[ -n "$(git_user status --porcelain --untracked-files=no)" ]]; then
  echo "В исходниках есть локальные изменения. Сохраните их перед обновлением." >&2
  exit 1
fi
if ! grep -q "^COMPOSE_FILE=" .env; then
  echo "Режим установки не задан. Запустите sudo sh install.sh и выберите 1." >&2
  exit 1
fi

previous="$(git_user rev-parse HEAD)"
git_user fetch --tags origin refs/heads/main
upstream="$(git_user rev-parse FETCH_HEAD)"
target="$upstream"
if [[ -n "$release" ]]; then
  target="$(git_user rev-parse --verify "refs/tags/$release^{commit}")"
  if ! git_user merge-base --is-ancestor "$target" "$upstream"; then
    echo "Тег $release не входит в историю удалённой ветки main." >&2
    exit 1
  fi
fi
if ! git_user merge-base --is-ancestor "$previous" "$target"; then
  echo "Выбранная версия не является обновлением текущей установки." >&2
  exit 1
fi
if [[ "$previous" == "$target" ]]; then
  echo "Выбранная версия уже установлена."
  exit 0
fi

echo "Создаём резервную копию перед обновлением с ${previous:0:12} до ${target:0:12}."
bash backup.sh

restart_previous=0
rollback() {
  local status=$?
  trap - EXIT
  if (( status != 0 )); then
    echo "Обновление прервано. Возвращаем исходники версии ${previous:0:12}." >&2
    if git_user reset --keep "$previous"; then
      if (( restart_previous )) && ! docker compose up -d --build --wait --wait-timeout 120; then
        echo "Не удалось запустить прежние контейнеры. Проверьте docker compose logs. Резервная копия доступна в backups/." >&2
      fi
    else
      echo "Исходники изменились во время обновления. Резервная копия доступна в backups/." >&2
    fi
  fi
  exit "$status"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

git_user merge --ff-only "$target"
if [[ -f scripts/branding_storage.py ]]; then
  python3 scripts/branding_storage.py
fi
docker compose config --quiet
docker compose build
restart_previous=1
docker compose up -d --wait --wait-timeout 120
trap - EXIT INT TERM
echo "Обновление до ${target:0:12} выполнено."
