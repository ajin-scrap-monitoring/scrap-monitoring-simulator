#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_environment="$repository_root/deploy/server/.env.example"
edge_environment="$repository_root/deploy/edge/.env.example"
server_compose="$repository_root/deploy/server/compose.yml"
edge_compose="$repository_root/deploy/edge/compose.yml"

docker compose --env-file "$server_environment" --file "$server_compose" config --quiet
docker compose --env-file "$edge_environment" --file "$edge_compose" config --quiet

for environment_file in "$server_environment" "$edge_environment"; do
    while IFS='=' read -r name value; do
        if [[ "$name" != *_IMAGE ]]; then
            continue
        fi
        if [[ ! "$value" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]; then
            echo "deployment image must use a digest: $name" >&2
            exit 1
        fi
    done < "$environment_file"
done

echo "deployment configuration valid"
