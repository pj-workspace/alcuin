#!/bin/sh
set -eu

test_project="alcuin-postgres-test"
export ALCUIN_POSTGRES_USER="${ALCUIN_POSTGRES_USER:-alcuin}"
export ALCUIN_POSTGRES_PASSWORD="${ALCUIN_POSTGRES_PASSWORD:-alcuin-test}"
export ALCUIN_POSTGRES_DB="${ALCUIN_POSTGRES_DB:-alcuin_test}"
export ALCUIN_POSTGRES_PORT="${ALCUIN_POSTGRES_PORT:-55432}"
export ALCUIN_DATABASE_URL="postgresql://${ALCUIN_POSTGRES_USER}:${ALCUIN_POSTGRES_PASSWORD}@127.0.0.1:${ALCUIN_POSTGRES_PORT}/${ALCUIN_POSTGRES_DB}"
export ALCUIN_TEST_POSTGRES_URL="$ALCUIN_DATABASE_URL"

cleanup() {
  docker compose -p "$test_project" down --volumes --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM
cleanup
docker compose -p "$test_project" up -d --wait postgres
uv run --project apps/api alembic \
  -c packages/python/alcuin-storage/alembic.ini upgrade head
"$@"
