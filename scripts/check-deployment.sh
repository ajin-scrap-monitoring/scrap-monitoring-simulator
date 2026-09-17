#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_environment="${1:-$repository_root/deploy/server/.env.example}"
edge_environment="${2:-$repository_root/deploy/edge/.env.example}"
server_compose="$repository_root/deploy/server/compose.yml"
edge_compose="$repository_root/deploy/edge/compose.yml"

if (( $# > 2 )); then
    echo "usage: scripts/check-deployment.sh [server-env [edge-env]]" >&2
    exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "jq 1.6 or newer is required" >&2
    exit 1
fi

for environment_file in "$server_environment" "$edge_environment"; do
    if [[ ! -r "$environment_file" ]]; then
        echo "deployment environment is not readable: $environment_file" >&2
        exit 1
    fi
done

validate_server_configuration() {
    jq -e '
        def ipv4:
          type == "string"
          and (split(".") | length == 4)
          and (split(".") | all(.[];
            test("^(0|[1-9][0-9]{0,2})$") and (tonumber <= 255)));
        .services["simulation-server"] as $service
        | [$service.ports[] | select(.name == "lidar-1")] as $lidar_1_matches
        | [$service.ports[] | select(.name == "lidar-2")] as $lidar_2_matches
        | ($lidar_1_matches | length) == 1
          and ($lidar_2_matches | length) == 1
          and ($service.ports | length) == 2
          and ($lidar_1_matches[0].host_ip | ipv4)
          and ($lidar_2_matches[0].host_ip | ipv4)
          and $lidar_1_matches[0].host_ip != $lidar_2_matches[0].host_ip
          and $lidar_1_matches[0].host_ip != "0.0.0.0"
          and $lidar_2_matches[0].host_ip != "0.0.0.0"
          and ($lidar_1_matches[0].published | tostring) == "8089"
          and ($lidar_2_matches[0].published | tostring) == "8089"
          and $lidar_1_matches[0].target == 8089
          and $lidar_2_matches[0].target == 8090
          and $lidar_1_matches[0].protocol == "udp"
          and $lidar_2_matches[0].protocol == "udp"
          and $service.environment.SCRAP_SIMULATOR_LIDAR_1_BIND == "0.0.0.0:8089"
          and $service.environment.SCRAP_SIMULATOR_LIDAR_2_BIND == "0.0.0.0:8090"
    ' >/dev/null
}

server_configuration="$(
    docker compose --env-file "$server_environment" --file "$server_compose" \
        config --format json
)"
docker compose --env-file "$edge_environment" --file "$edge_compose" config --quiet

if ! validate_server_configuration <<<"$server_configuration"; then
    echo "server LiDAR deployment contract is invalid" >&2
    exit 1
fi

for removed_name in SCRAP_SIMULATOR_LIDAR_1_PORT SCRAP_SIMULATOR_LIDAR_2_PORT; do
    removed_error="$(mktemp)"
    if env -u SCRAP_SIMULATOR_LIDAR_1_PORT -u SCRAP_SIMULATOR_LIDAR_2_PORT \
        "$removed_name=8089" \
        docker compose --env-file "$server_environment" --file "$server_compose" \
            config --quiet 2>"$removed_error"; then
        echo "removed deployment variable was accepted: $removed_name" >&2
        rm -f "$removed_error"
        exit 1
    fi
    if ! grep -Fq "$removed_name is removed" "$removed_error"; then
        echo "removed deployment variable did not produce a migration error: $removed_name" >&2
        cat "$removed_error" >&2
        rm -f "$removed_error"
        exit 1
    fi
    rm -f "$removed_error"
done

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
