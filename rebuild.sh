#!/bin/bash
# rebuild.sh — przebudowa truthbrush_telegram bez duplikatów
# Użycie: cd /opt/truthbrush_telegram && ./rebuild.sh

set -e

echo "=== 1. Zatrzymuję kontenery ==="
docker compose down --remove-orphans

echo "=== 2. Pobieram najnowszy kod ==="
git pull

echo "=== 3. Czyszczę state (zapobieganie duplikatom) ==="
docker run --rm -v truthbrush_telegram_monitor-data:/data alpine rm -f /data/truthbrush_state.json

echo "=== 4. Usuwam stare obrazy tego projektu ==="
docker images --filter "reference=truthbrush_telegram-*" -q | xargs -r docker rmi -f 2>/dev/null || true

echo "=== 5. Buduję i uruchamiam ==="
docker compose up -d --build

echo "=== Gotowe! Pokazuję logi (Ctrl+C aby wyjść) ==="
docker compose logs -f
