#!/bin/bash
# rebuild.sh — bezpieczna przebudowa truthbrush_telegram
# Użycie:
#   ./rebuild.sh            # rebuild bez kasowania state
#   ./rebuild.sh --reset-state  # świadomy reset state

set -e

RESET_STATE=0
if [ "${1:-}" = "--reset-state" ]; then
  RESET_STATE=1
elif [ "${1:-}" != "" ]; then
  echo "Użycie: $0 [--reset-state]"
  exit 2
fi

echo "=== 1. Zatrzymuję kontenery ==="
docker compose down --remove-orphans

echo "=== 2. Pobieram najnowszy kod ==="
git pull

if [ "$RESET_STATE" -eq 1 ]; then
  echo "=== 3. Resetuję state na żądanie ==="
  docker run --rm -v truthbrush_telegram_monitor-data:/data alpine rm -f /data/truthbrush_state.json
else
  echo "=== 3. Zachowuję state ==="
fi

echo "=== 4. Usuwam stare obrazy tego projektu ==="
docker images --filter "reference=truthbrush_telegram-*" -q | xargs -r docker rmi -f 2>/dev/null || true

echo "=== 5. Buduję i uruchamiam ==="
docker compose up -d --build

echo "=== Gotowe! Pokazuję logi (Ctrl+C aby wyjść) ==="
docker compose logs -f
