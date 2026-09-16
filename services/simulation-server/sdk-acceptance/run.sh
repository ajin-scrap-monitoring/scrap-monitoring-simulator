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
SCRAP_SIMULATOR_LIDAR_1_BIND=127.0.0.1:8089 \
SCRAP_SIMULATOR_LIDAR_2_BIND=127.0.0.1:8090 \
SCRAP_SIMULATOR_SCENE_HOST=127.0.0.1 \
SCRAP_SIMULATOR_SCENE_PORT=17000 \
"${server_binary}" run >"${server_log}" 2>&1 &
server_pid="$!"

sleep 1
"${acceptance_binary}" 8089 8090

kill -TERM "${server_pid}"
wait "${server_pid}"
server_pid=""

grep -q '"generated_scans"' "${server_log}"
