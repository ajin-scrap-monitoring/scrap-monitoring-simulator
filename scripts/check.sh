#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_image="${SCRAP_SIMULATOR_TEST_SERVER_IMAGE:-scrap-monitoring-simulator-server:local}"
visualizer_image="${SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE:-scrap-monitoring-simulator-visualizer:local}"
edge_image="${SCRAP_SIMULATOR_TEST_EDGE_IMAGE:-scrap-monitoring-simulator-camera-edge-bridge:local}"
build_revision="${BUILD_REVISION:-local}"
build_version="${BUILD_VERSION:-0.1.0}"
edge_smoke_log="$(mktemp)"
trap 'rm -f "$edge_smoke_log"' EXIT

cd "$repository_root"

test -f .agents/AGENTS.md
test -L AGENTS.md
test "$(readlink AGENTS.md)" = ".agents/AGENTS.md"
test -L GEMINI.md
test "$(readlink GEMINI.md)" = ".agents/AGENTS.md"
test -L .claude/CLAUDE.md
test "$(readlink .claude/CLAUDE.md)" = "../.agents/AGENTS.md"
test ! -e Cargo.toml
test ! -e Dockerfile
test "$(find services -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | tr '\n' ' ')" = \
    "camera-edge-bridge simulation-server visualizer "
cmp contracts/scene/v1/definition.schema.json \
    services/visualizer/src/scrap_monitoring_visualizer/contracts/schema/v1/definition.schema.json
cmp contracts/scene/v1/frame.schema.json \
    services/visualizer/src/scrap_monitoring_visualizer/contracts/schema/v1/frame.schema.json
bash -n scripts/*.sh deploy/edge/*.sh
scripts/check-deployment.sh

if ! docker buildx inspect --bootstrap | grep -Eq 'Platforms:.*linux/arm64'; then
    echo "Docker Buildx requires registered linux/arm64 emulation" >&2
    exit 1
fi

docker buildx build \
    --platform linux/amd64 \
    --file services/simulation-server/Dockerfile \
    --target test \
    --tag scrap-monitoring-simulator-server:test \
    --load \
    .
docker buildx build \
    --platform linux/amd64 \
    --file services/simulation-server/Dockerfile \
    --target runtime \
    --build-arg "BUILD_REVISION=$build_revision" \
    --build-arg "BUILD_VERSION=$build_version" \
    --tag "$server_image" \
    --load \
    .

docker buildx build \
    --platform linux/amd64 \
    --file services/visualizer/Dockerfile \
    --target test \
    --tag scrap-monitoring-simulator-visualizer:test \
    --load \
    .
docker run --rm \
    --volume "$repository_root:/repo:ro" \
    --entrypoint rumdl \
    scrap-monitoring-simulator-visualizer:test \
    check --no-config --no-cache --disable MD013 \
    /repo/docs /repo/services/visualizer/THIRD_PARTY_NOTICES.md
docker buildx build \
    --platform linux/amd64 \
    --file services/visualizer/Dockerfile \
    --target runtime \
    --build-arg "BUILD_REVISION=$build_revision" \
    --build-arg "BUILD_VERSION=$build_version" \
    --tag "$visualizer_image" \
    --load \
    .

docker buildx build \
    --platform linux/arm64 \
    --file services/camera-edge-bridge/Dockerfile \
    --target test \
    --output type=cacheonly \
    .
docker buildx build \
    --platform linux/arm64 \
    --file services/camera-edge-bridge/Dockerfile \
    --target runtime \
    --build-arg "BUILD_REVISION=$build_revision" \
    --build-arg "BUILD_VERSION=$build_version" \
    --tag "$edge_image" \
    --load \
    .

test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$server_image")" = "linux/amd64"
test "$(docker image inspect --format '{{.Config.User}}' "$server_image")" = "10001:10001"
test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$visualizer_image")" = "linux/amd64"
test "$(docker image inspect --format '{{.Config.User}}' "$visualizer_image")" = "10001:10001"
test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$edge_image")" = "linux/arm64"
test "$(docker image inspect --format '{{.Config.User}}' "$edge_image")" = "10001:10001"

docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
    "$server_image" check
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --entrypoint python "$visualizer_image" \
    -m scrap_monitoring_visualizer.runtime_probe --output /tmp/runtime-probe
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --entrypoint python "$visualizer_image" \
    -m scrap_monitoring_visualizer.rendering.probe --output /tmp/scene-probe
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
    --entrypoint python "$visualizer_image" \
    -m scrap_monitoring_visualizer.synthetic_camera.probe --output /tmp/camera-probe

if docker run --rm --platform linux/arm64 "$edge_image" 2>"$edge_smoke_log"; then
    echo "edge bridge unexpectedly started without a server URL" >&2
    exit 1
fi
grep -Fq "SCRAP_SYNTHETIC_CAMERA_SERVER_URL is required" "$edge_smoke_log"

scripts/check-integration.sh

docker run --rm \
    --volume "$repository_root:/repo:ro" \
    --workdir /repo \
    docker.io/rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667

echo "all container builds and tests passed"
