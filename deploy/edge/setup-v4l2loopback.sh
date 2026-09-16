#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "run this one-time setup as root" >&2
  exit 1
fi

script_dir="$(
  unset CDPATH
  cd -- "$(dirname -- "$0")"
  pwd
)"
device="/dev/video42"

packages=(v4l-utils v4l2loopback-dkms v4l2loopback-utils)
if [[ ! -d "/lib/modules/$(uname -r)/build" ]]; then
  packages+=("linux-headers-$(uname -r)")
fi
apt-get update
apt-get install -y --no-install-recommends "${packages[@]}"

install -m 0644 \
  "$script_dir/v4l2loopback.conf" \
  /etc/modprobe.d/scrap-synthetic-camera.conf
install -m 0644 \
  "$script_dir/scrap-synthetic-camera.modules-load.conf" \
  /etc/modules-load.d/scrap-synthetic-camera.conf
install -m 0644 \
  "$script_dir/99-scrap-synthetic-camera.rules" \
  /etc/udev/rules.d/99-scrap-synthetic-camera.rules
install -D -m 0755 \
  "$script_dir/configure-v4l2loopback.sh" \
  /usr/local/libexec/scrap-monitoring/configure-v4l2loopback
install -m 0644 \
  "$script_dir/scrap-synthetic-camera-configure.service" \
  /etc/systemd/system/scrap-synthetic-camera-configure.service

modprobe v4l2loopback
udevadm control --reload-rules
udevadm trigger --subsystem-match=video4linux
udevadm settle --timeout=10

if [[ ! -c "$device" ]]; then
  echo "$device was not created; reboot to load the installed module configuration" >&2
  exit 1
fi
device_name="$(< /sys/class/video4linux/video42/name)"
if [[ "$device_name" != "Scrap Synthetic Camera" ]]; then
  echo "$device belongs to an unexpected V4L2 device: $device_name" >&2
  exit 1
fi

if [[ ! -c /dev/scrap-synthetic-camera ]]; then
  echo "udev did not create /dev/scrap-synthetic-camera" >&2
  exit 1
fi

systemctl daemon-reload
systemctl enable scrap-synthetic-camera-configure.service
systemctl restart scrap-synthetic-camera-configure.service
