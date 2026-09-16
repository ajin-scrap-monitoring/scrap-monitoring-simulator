# 배포

## Image 경계

배포 image는 다음 3개다.

| Image | Platform | Dockerfile |
| --- | --- | --- |
| `scrap-monitoring-simulator-server` | `linux/amd64` | `services/simulation-server/Dockerfile` |
| `scrap-monitoring-simulator-visualizer` | `linux/amd64` | `services/visualizer/Dockerfile` |
| `scrap-monitoring-simulator-camera-edge-bridge` | `linux/arm64` | `services/camera-edge-bridge/Dockerfile` |

Release는 version tag와 source revision tag를 같은 OCI manifest에 연결하고 Software Bill of
Materials와 provenance를 발행한다. 배포 설정은 tag가 아니라 manifest digest를 요구한다.

## Server 배포

`deploy/server/.env.example`을 `.env`로 복사하고 두 image reference의 zero digest를 Release가
발행한 digest로 교체한다. 다음 command가 Simulation server와 Visualizer를 실행한다.

```bash
docker compose --env-file deploy/server/.env \
  --file deploy/server/compose.yml up --detach
```

Browser는 설정한 HTTP port를 사용한다. LiDAR SDK client는 sensor별 UDP port 8089와 8090을
사용한다. Compose는 Visualizer health check가 통과한 뒤 Simulation server를 시작한다.

## Edge 배포

ARM64 host에서 `deploy/edge/setup-v4l2loopback.sh`를 root로 한 번 실행한다. 이 script는
`video_nr=42`, `exclusive_caps=0`인 V4L2 loopback device와 고정 symlink를 구성한다.

`deploy/edge/.env.example`을 `.env`로 복사하고 image digest, Visualizer URL과 host의 video
group identifier를 설정한다. 다음 command가 bridge를 실행한다.

```bash
docker compose --env-file deploy/edge/.env \
  --file deploy/edge/compose.yml up --detach
```

Stream 연결 후 host에서 다음 command로 90개 frame의 format, cadence, decode, sequence와
monotonic EOF timestamp를 검사한다.

```bash
deploy/edge/check-90-frames.sh /dev/scrap-synthetic-camera
```

## 로컬 검증

`scripts/check.sh`는 AMD64 server image 2개와 ARM64 bridge image를 build하고 모든 service
test, runtime probe, Compose 검사와 end-to-end scene 흐름을 실행한다. ARM64 binfmt가 Docker
Buildx에 등록된 host에서 실행해야 한다.
