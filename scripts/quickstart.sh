#!/usr/bin/env bash
# Run the app on Docker, with Temporal running on MongoDB via the plugin.
#
#   ./scripts/quickstart.sh up       build and start everything (MongoDB, Temporal, app)
#   ./scripts/quickstart.sh infra    start only MongoDB + Temporal (run the app on the host with uv)
#   ./scripts/quickstart.sh status   what is running, and where
#   ./scripts/quickstart.sh logs [service]
#   ./scripts/quickstart.sh down     stop (keeps data); down -v also deletes the data
#
# The Temporal plugin is built from TEMPORAL_PLUGIN_DIR (default ./.temporal-plugin,
# cloned from GitHub when missing).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

say() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose)"

[ -f .env ] || { cp .env.example .env; say "created .env from .env.example (add your AWS/Voyage keys)"; }

export TEMPORAL_PLUGIN_DIR="${TEMPORAL_PLUGIN_DIR:-./.temporal-plugin}"
if [ ! -f "$TEMPORAL_PLUGIN_DIR/Dockerfile" ]; then
  say "cloning the MongoDB Temporal plugin into $TEMPORAL_PLUGIN_DIR"
  git clone --depth 1 https://github.com/mongodb-partners/mongodb-temporal-plugin.git "$TEMPORAL_PLUGIN_DIR" \
    || die "clone failed; set TEMPORAL_PLUGIN_DIR to a local checkout of mongodb-temporal-plugin"
fi

INFRA=(mongodb schema-init temporal namespace-init temporal-ui)

status() {
  docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  echo
  echo "  Dashboard     http://localhost:8501"
  echo "  API docs      http://localhost:8000/docs"
  echo "  Temporal UI   http://localhost:${TEMPORAL_UI_PORT:-8080}"
  echo "  Temporal gRPC localhost:${TEMPORAL_PORT:-7233}"
  echo "  MongoDB       mongodb://localhost:${MONGO_PORT:-27017}/?directConnection=true"
}

case "${1:-}" in
  up)
    say "building and starting MongoDB, Temporal (MongoDB plugin) and the app (first start takes a few minutes)"
    docker compose up -d --build --wait --wait-timeout 600
    say "creating collections, indexes and seed data"
    docker compose exec -T api python -m scripts.setup_mongodb
    ok "ready"
    status ;;
  infra)
    say "building and starting MongoDB and Temporal (MongoDB plugin)"
    docker compose up -d --build "${INFRA[@]}"
    ok "ready; run the worker/API/dashboard on the host (README, Local Development Setup)" ;;
  status) status ;;
  logs) shift; docker compose logs -f "$@" ;;
  down) shift; docker compose down "$@" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
