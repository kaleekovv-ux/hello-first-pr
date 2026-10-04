#!/bin/bash
# Готовит облачную сессию Claude Code: FFmpeg + пакет autoedit, чтобы
# тесты и демо-рендер работали сразу, без ручной установки в каждой сессии.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Устанавливаю FFmpeg..."
  apt-get update -qq >/dev/null 2>&1 || true
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null 2>&1; then
    echo "Предупреждение: FFmpeg не установился — тесты с реальным рендером будут пропущены." >&2
  fi
fi

PIP=(python3 -m pip install --quiet --disable-pip-version-check --root-user-action=ignore)
"${PIP[@]}" -e ".[export]" || "${PIP[@]}" -e .

echo "Окружение AutoEdit готово: $(ffmpeg -version 2>/dev/null | head -1 || echo 'FFmpeg нет'), $(python3 --version)"
