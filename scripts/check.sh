#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_image="${SCRAP_SIMULATOR_TEST_SERVER_IMAGE:-scrap-monitoring-simulator-server:local}"
visualizer_image="${SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE:-scrap-monitoring-simulator-visualizer:local}"
edge_image="${SCRAP_SIMULATOR_TEST_EDGE_IMAGE:-scrap-monitoring-simulator-camera-edge-bridge:local}"
build_revision="${BUILD_REVISION:-local}"
build_version="${BUILD_VERSION:-0.2.0}"
edge_smoke_log="$(mktemp)"
trap 'rm -f "$edge_smoke_log"' EXIT

cd "$repository_root"

check_static() {
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
    git diff-tree --check --root -r HEAD
    git diff --check HEAD
    scripts/check-deployment.sh
    docker buildx bake docs-lint
    docker run --rm \
        --volume "$repository_root:/repo:ro" \
        --workdir /repo \
        docker.io/rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
}

require_platform() {
    local platform="$1"
    local builder_details
    builder_details="$(docker buildx inspect --bootstrap)"
    if ! grep -Eq "Platforms:.*${platform}" <<<"$builder_details"; then
        echo "Docker Buildx cannot build ${platform}" >&2
        exit 1
    fi
}

build_targets() {
    BUILD_REVISION="$build_revision" \
    BUILD_VERSION="$build_version" \
    SCRAP_SIMULATOR_TEST_SERVER_IMAGE="$server_image" \
    SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE="$visualizer_image" \
    SCRAP_SIMULATOR_TEST_EDGE_IMAGE="$edge_image" \
        docker buildx bake "$@"
}

wait_for_jobs() {
    local status=0
    local process_id
    for process_id in "$@"; do
        if ! wait "$process_id"; then
            status=1
        fi
    done
    return "$status"
}

check_amd64_runtime() {
    test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$server_image")" = \
        "linux/amd64"
    test "$(docker image inspect --format '{{.Config.User}}' "$server_image")" = "10001:10001"
    test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$visualizer_image")" = \
        "linux/amd64"
    test "$(docker image inspect --format '{{.Config.User}}' "$visualizer_image")" = \
        "10001:10001"

    docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
        "$server_image" check &
    local server_probe_id=$!
    docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
        --entrypoint python "$visualizer_image" \
        -m scrap_monitoring_visualizer.runtime_probe --output /tmp/runtime-probe &
    local runtime_probe_id=$!
    docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
        --entrypoint python "$visualizer_image" \
        -m scrap_monitoring_visualizer.rendering.probe --output /tmp/scene-probe &
    local scene_probe_id=$!
    docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
        --entrypoint python "$visualizer_image" \
        -m scrap_monitoring_visualizer.synthetic_camera.probe --output /tmp/camera-probe &
    local camera_probe_id=$!

    wait_for_jobs \
        "$server_probe_id" \
        "$runtime_probe_id" \
        "$scene_probe_id" \
        "$camera_probe_id"

    scripts/check-integration.sh
}

check_arm64_runtime() {
    test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$edge_image")" = \
        "linux/arm64"
    test "$(docker image inspect --format '{{.Config.User}}' "$edge_image")" = "10001:10001"

    if docker run --rm --platform linux/arm64 "$edge_image" 2>"$edge_smoke_log"; then
        echo "edge bridge unexpectedly started without a server URL" >&2
        exit 1
    fi
    grep -Fq "SCRAP_SYNTHETIC_CAMERA_SERVER_URL is required" "$edge_smoke_log"
}

mode="${1:-all}"
case "$mode" in
    static)
        check_static
        echo "static container checks passed"
        ;;
    amd64)
        require_platform linux/amd64
        build_targets ci-amd64
        check_amd64_runtime
        echo "AMD64 container builds and tests passed"
        ;;
    amd64-runtime)
        check_amd64_runtime
        echo "AMD64 runtime probes and integration passed"
        ;;
    arm64)
        require_platform linux/arm64
        build_targets camera-edge-bridge
        check_arm64_runtime
        echo "ARM64 container build and tests passed"
        ;;
    arm64-runtime)
        check_arm64_runtime
        echo "ARM64 runtime smoke test passed"
        ;;
    all)
        require_platform linux/amd64
        require_platform linux/arm64
        check_static &
        static_id=$!
        build_targets ci &
        build_id=$!
        wait_for_jobs "$static_id" "$build_id"
        check_amd64_runtime &
        amd64_id=$!
        check_arm64_runtime &
        arm64_id=$!
        wait_for_jobs "$amd64_id" "$arm64_id"
        echo "all container builds and tests passed"
        ;;
    *)
        echo "usage: scripts/check.sh [all|static|amd64|amd64-runtime|arm64|arm64-runtime]" >&2
        exit 2
        ;;
esac
