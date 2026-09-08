#!/bin/sh
set -eu

find_available_postgres_port() {
  candidate="5432"
  if ! nc -z 127.0.0.1 "$candidate" >/dev/null 2>&1; then
    printf '%s\n' "$candidate"
    return
  fi

  candidate="55433"
  while nc -z 127.0.0.1 "$candidate" >/dev/null 2>&1; do
    candidate=$((candidate + 1))
  done
  printf '%s\n' "$candidate"
}

existing_postgres_container=$(docker compose ps -aq postgres 2>/dev/null || true)
existing_postgres_port=""
if [ -n "$existing_postgres_container" ]; then
  existing_postgres_port=$(
    docker inspect \
      --format '{{with (index .HostConfig.PortBindings "5432/tcp")}}{{(index . 0).HostPort}}{{end}}' \
      "$existing_postgres_container" 2>/dev/null || true
  )
fi

export ALCUIN_POSTGRES_PORT="${ALCUIN_POSTGRES_PORT:-${existing_postgres_port:-$(find_available_postgres_port)}}"
export ALCUIN_POSTGRES_USER="${ALCUIN_POSTGRES_USER:-alcuin}"
export ALCUIN_POSTGRES_PASSWORD="${ALCUIN_POSTGRES_PASSWORD:-alcuin}"
export ALCUIN_POSTGRES_DB="${ALCUIN_POSTGRES_DB:-alcuin}"
export ALCUIN_DATABASE_URL="${ALCUIN_DATABASE_URL:-postgresql://${ALCUIN_POSTGRES_USER}:${ALCUIN_POSTGRES_PASSWORD}@127.0.0.1:${ALCUIN_POSTGRES_PORT}/${ALCUIN_POSTGRES_DB}}"

docker compose up -d --wait postgres qdrant searxng
pnpm db:migrate

if [ "${1:-}" = "--prepare-only" ]; then
  exit 0
fi

cleanup() {
  if [ "${ALCUIN_DEV_KEEP_SERVICES:-0}" != "1" ]; then
    docker compose stop
  fi
}
trap cleanup EXIT

pnpm dev:apps
