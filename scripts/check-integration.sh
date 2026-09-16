#!/usr/bin/env bash
set -euo pipefail

server_image="${SCRAP_SIMULATOR_TEST_SERVER_IMAGE:-scrap-monitoring-simulator-server:local}"
visualizer_image="${SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE:-scrap-monitoring-simulator-visualizer:local}"
run_id="${BASHPID:-$$}"
network="scrap-simulator-integration-$run_id"
server_container="scrap-simulator-server-$run_id"
visualizer_container="scrap-simulator-visualizer-$run_id"

cleanup() {
    docker container rm --force "$server_container" "$visualizer_container" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker image inspect "$server_image" >/dev/null
docker image inspect "$visualizer_image" >/dev/null
docker network create "$network" >/dev/null

docker run --detach \
    --name "$visualizer_container" \
    --network "$network" \
    --network-alias visualizer \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --env SCRAP_MONITORING_VISUALIZER_TCP_HOST=0.0.0.0 \
    --env SCRAP_MONITORING_VISUALIZER_TCP_PORT=17000 \
    --env SCRAP_MONITORING_VISUALIZER_HTTP_HOST=0.0.0.0 \
    --env SCRAP_MONITORING_VISUALIZER_HTTP_PORT=18000 \
    --env SCRAP_MONITORING_VISUALIZER_CAMERA_ENABLED=true \
    --env SCRAP_MONITORING_VISUALIZER_CAMERA_BACKEND=osmesa \
    "$visualizer_image" live >/dev/null

docker run --detach \
    --name "$server_container" \
    --network "$network" \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
    --env SCRAP_SIMULATOR_SCENE_HOST=visualizer \
    --env SCRAP_SIMULATOR_SCENE_PORT=17000 \
    --env SCRAP_SIMULATOR_SCENE_INTERVAL_S=1.0 \
    "$server_image" run >/dev/null

if ! docker exec "$visualizer_container" \
    python -m scrap_monitoring_visualizer.integration_probe --timeout 120; then
    docker logs "$server_container" >&2 || true
    docker logs "$visualizer_container" >&2 || true
    exit 1
fi

test "$(docker inspect --format '{{.State.Running}}' "$server_container")" = "true"
test "$(docker inspect --format '{{.State.Running}}' "$visualizer_container")" = "true"
echo "integrated scene, browser and camera flow valid"
