#!/bin/bash
set -euo pipefail

wait_for() {
  local host="$1"
  local port="$2"
  local name="$3"
  echo "Waiting for $name at $host:$port..."
  until bash -c "cat < /dev/null > /dev/tcp/$host/$port" 2>/dev/null; do
    sleep 1
  done
  echo "$name is up."
}

# Service names below match the docker-compose.yml service names,
# which is what "postgres" / "neo4j" resolve to on the compose network.
wait_for "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "PostgreSQL"
wait_for "${NEO4J_HOST:-neo4j}" "${NEO4J_BOLT_PORT:-7687}" "Neo4j"

exec "$@"