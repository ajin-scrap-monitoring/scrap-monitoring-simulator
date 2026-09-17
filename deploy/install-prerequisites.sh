#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: deploy/install-prerequisites.sh --server|--edge" >&2
}

run_as_root() {
    if (( EUID == 0 )); then
        "$@"
    else
        sudo "$@"
    fi
}

is_package_installed() {
    local package_name="$1"
    [[ "$(dpkg-query --show --showformat='${db:Status-Status}' "$package_name" 2>/dev/null || true)" \
        == "installed" ]]
}

require_minimum_version() {
    local component_name="$1"
    local actual_version="${2#v}"
    local minimum_version="$3"

    if ! dpkg --compare-versions "$actual_version" ge "$minimum_version"; then
        echo "$component_name $minimum_version or newer is required; found $actual_version" >&2
        exit 1
    fi
}

if (( $# != 1 )); then
    usage
    exit 2
fi

case "$1" in
    --server)
        host_role="AMD64 Server"
        required_architecture="amd64"
        ;;
    --edge)
        host_role="ARM64 Edge"
        required_architecture="arm64"
        ;;
    --help)
        usage
        exit 0
        ;;
    *)
        usage
        exit 2
        ;;
esac

if [[ ! -r /etc/os-release ]]; then
    echo "Ubuntu 22.04, 24.04 or 26.04 is required" >&2
    exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release
ubuntu_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "Ubuntu 22.04, 24.04 or 26.04 is required; found ${ID:-unknown}" >&2
    exit 1
fi

case "$ubuntu_codename" in
    jammy|noble|resolute)
        ;;
    *)
        echo "Ubuntu 22.04, 24.04 or 26.04 is required; found $ubuntu_codename" >&2
        exit 1
        ;;
esac

host_architecture="$(dpkg --print-architecture)"
if [[ "$host_architecture" != "$required_architecture" ]]; then
    echo "$host_role requires $required_architecture; found $host_architecture" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemd is required to start Docker Engine" >&2
    exit 1
fi

if (( EUID != 0 )); then
    sudo -v
fi

conflicting_packages=(
    docker.io
    docker-compose
    docker-compose-v2
    docker-doc
    docker-buildx
    podman-docker
    containerd
    runc
)
installed_conflicts=()
for package_name in "${conflicting_packages[@]}"; do
    if is_package_installed "$package_name"; then
        installed_conflicts+=("$package_name")
    fi
done

if (( ${#installed_conflicts[@]} != 0 )); then
    echo "conflicting packages are installed: ${installed_conflicts[*]}" >&2
    echo "remove or migrate them explicitly before installing Docker CE" >&2
    exit 1
fi

run_as_root apt-get update
run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg jq
run_as_root install -m 0755 -d /etc/apt/keyrings
run_as_root curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
run_as_root chmod a+r /etc/apt/keyrings/docker.asc

docker_source=$'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: '"$ubuntu_codename"$'\nComponents: stable\nArchitectures: '"$host_architecture"$'\nSigned-By: /etc/apt/keyrings/docker.asc'
printf '%s\n' "$docker_source" | run_as_root tee /etc/apt/sources.list.d/docker.sources >/dev/null

run_as_root apt-get update
run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
run_as_root systemctl enable --now docker

engine_version="$(run_as_root docker version --format '{{.Server.Version}}')"
compose_version="$(run_as_root docker compose version --short)"
buildx_output="$(run_as_root docker buildx version)"
if [[ ! "$buildx_output" =~ v([0-9]+([.][0-9]+){1,2}([-+][0-9A-Za-z._-]+)?) ]]; then
    echo "cannot determine Docker Buildx version: $buildx_output" >&2
    exit 1
fi
buildx_version="${BASH_REMATCH[1]}"
jq_version="$(jq --version)"
jq_version="${jq_version#jq-}"

require_minimum_version "Docker Engine" "$engine_version" "27"
require_minimum_version "Docker Compose plugin" "$compose_version" "2"
require_minimum_version "Docker Buildx" "$buildx_version" "0.37.1"
require_minimum_version "jq" "$jq_version" "1.6"

if [[ "$1" == "--edge" ]]; then
    repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    run_as_root "$repository_root/deploy/edge/setup-v4l2loopback.sh"
fi

echo "$host_role prerequisites are installed"
