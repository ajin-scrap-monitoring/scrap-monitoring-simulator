#!/usr/bin/env bash
set -euo pipefail

readonly server_binary="${1:-/work/target/release/scrap-monitoring-simulation-server}"
readonly acceptance_binary="${2:-/work/sdk-build/sdk-acceptance}"
readonly server_log="/tmp/simulation-server.log"

server_pid=""
cleanup() {
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill -TERM "${server_pid}"
    wait "${server_pid}"
  fi
}
trap cleanup EXIT

SCRAP_SIMULATOR_CONFIG=/work/config/simulation-server.v1.json \
SCRAP_SIMULATOR_LIDAR_1_BIND=127.0.0.2:8089 \
SCRAP_SIMULATOR_LIDAR_2_BIND=127.0.0.3:8089 \
SCRAP_SIMULATOR_SCENE_HOST=127.0.0.1 \
SCRAP_SIMULATOR_SCENE_PORT=17000 \
"${server_binary}" run >"${server_log}" 2>&1 &
server_pid="$!"

sleep 1

expect_rejection() {
    local expected="$1"
    shift
    local output
    if output="$("${acceptance_binary}" "$@" 2>&1)"; then
        echo "SDK acceptance unexpectedly accepted invalid endpoints: $*" >&2
        exit 1
    fi
    if ! grep -Fq "${expected}" <<<"${output}"; then
        echo "SDK acceptance rejection did not contain: ${expected}" >&2
        echo "${output}" >&2
        exit 1
    fi
}

expect_rejection \
    "expected two distinct UDP endpoint IPs" \
    127.0.0.2:8089 127.0.0.2:8089
expect_rejection \
    "both UDP endpoints must use port 8089" \
    127.0.0.2:8089 127.0.0.3:8090
expect_rejection \
    "endpoint host must be a non-wildcard IPv4 address" \
    localhost:8089 127.0.0.3:8089

"${acceptance_binary}"

kill -TERM "${server_pid}"
wait "${server_pid}"
server_pid=""

grep -q '"generated_scans"' "${server_log}"
