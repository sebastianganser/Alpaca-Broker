#!/bin/bash
# Deploy der Review-Fixes (2026-10-05) auf Unraid + Start der Reparatur-Skripte.
#
# Aufruf im Unraid-Web-Terminal:
#   bash /mnt/user/appdata/alpaca-broker/scripts/repair/deploy_review_fixes.sh          # nur Deploy + Dry-Runs
#   bash /mnt/user/appdata/alpaca-broker/scripts/repair/deploy_review_fixes.sh --apply  # Deploy + Reparatur (7-12 h, Hintergrund)
#
# Fortschritt verfolgen:
#   docker exec alpaca-broker sh -c 'tail -f /app/repair_logs/*.log'
set -euo pipefail

REPO_DIR="/mnt/user/appdata/alpaca-broker"
STACK_YML="/boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml"
CONTAINER="alpaca-broker"
APPLY="${1:-}"

echo "==> 1/5 git pull"
cd "$REPO_DIR"
git pull --ff-only

echo "==> 2/5 Image bauen + Container neu starten"
docker compose -f "$STACK_YML" up --build -d

echo "==> 3/5 Warte auf Health-Status 'healthy' (max. 5 min)"
for i in $(seq 1 60); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || echo missing)"
  echo "    [$i] $status"
  if [ "$status" = "healthy" ]; then break; fi
  if [ "$status" = "exited" ] || [ "$status" = "missing" ]; then
    echo "!! Container laeuft nicht. Logs:"; docker logs --tail 80 "$CONTAINER"; exit 1
  fi
  sleep 5
done
if [ "$status" != "healthy" ]; then
  echo "!! Container nach 5 min nicht healthy. Logs:"; docker logs --tail 80 "$CONTAINER"; exit 1
fi

echo "==> 4/5 Migrationsstand (erwartet: 031)"
docker exec "$CONTAINER" .venv/bin/alembic current

if [ "$APPLY" != "--apply" ]; then
  echo "==> 5/5 Dry-Runs (keine Aenderungen)"
  docker exec "$CONTAINER" .venv/bin/python scripts/repair/collectors_refetch_prices.py | tail -n 40
  docker exec "$CONTAINER" .venv/bin/python scripts/repair/features_rebuild.py | tail -n 40
  echo
  echo "Fertig. Fuer die echte Reparatur erneut mit --apply aufrufen."
  exit 0
fi

echo "==> 5/5 Reparatur im Hintergrund starten (Preis-Refetch -> Feature-Rebuild)"
docker exec -u 0 "$CONTAINER" sh -c 'mkdir -p /app/repair_logs && chown 99:100 /app/repair_logs'
docker exec -d "$CONTAINER" sh -c '
  .venv/bin/python scripts/repair/collectors_refetch_prices.py --apply > /app/repair_logs/1_refetch.log 2>&1 &&
  .venv/bin/python scripts/repair/features_rebuild.py --apply > /app/repair_logs/2_rebuild.log 2>&1;
  echo "exit=$?" > /app/repair_logs/done.txt'
echo
echo "Gestartet. Fortschritt:  docker exec $CONTAINER sh -c 'tail -f /app/repair_logs/*.log'"
echo "Abgeschlossen, sobald /app/repair_logs/done.txt existiert:  docker exec $CONTAINER cat /app/repair_logs/done.txt"
echo "Hinweis: Ein Container-Neustart bricht die Reparatur ab. Fortsetzen mit:"
echo "  features_rebuild.py --apply --skip-ta --skip-clusters --start <datum>"
