#!/usr/bin/env bash
set -euo pipefail

device="${1:-/dev/video42}"
if [[ ! -c "$device" ]]; then
  echo "$device is not a character device" >&2
  exit 1
fi

device_name="$(< /sys/class/video4linux/video42/name)"
if [[ "$device_name" != "Scrap Synthetic Camera" ]]; then
  echo "$device belongs to an unexpected V4L2 device: $device_name" >&2
  exit 1
fi

v4l2-ctl --device "$device" \
  --set-ctrl=keep_format=0,sustain_framerate=0
v4l2-ctl --device "$device" \
  --set-fmt-video-out=width=1920,height=1080,pixelformat=MJPG
v4l2-ctl --device "$device" --set-output-parm=30
v4l2-ctl --device "$device" \
  --set-ctrl=keep_format=1,sustain_framerate=0

v4l2-ctl --device "$device" --get-fmt-video-out
v4l2-ctl --device "$device" --get-output-parm
v4l2-ctl --device "$device" --get-ctrl=keep_format,sustain_framerate
