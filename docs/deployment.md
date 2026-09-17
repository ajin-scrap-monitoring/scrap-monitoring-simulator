# 배포

## Image 경계

배포 image는 3개다.

| Image | Platform | Dockerfile |
| --- | --- | --- |
| `scrap-monitoring-simulator-server` | `linux/amd64` | `services/simulation-server/Dockerfile` |
| `scrap-monitoring-simulator-visualizer` | `linux/amd64` | `services/visualizer/Dockerfile` |
| `scrap-monitoring-simulator-camera-edge-bridge` | `linux/arm64` | `services/camera-edge-bridge/Dockerfile` |

Release workflow는 `main` 이력의 `vMAJOR.MINOR.PATCH` tag만 받는다. Service manifest, lock file과 tag의 version은 같아야 한다. Release는 version tag와 full Git SHA tag를 같은 OCI manifest에 연결하고 Software Bill of Materials (SBOM)와 provenance를 발행한다. `latest` tag는 만들지 않는다.

GitHub Release는 image digest 3개를 기록한 `oci-images.txt`와 `deploy/` archive를 제공한다. 배포 설정은 tag가 아니라 OCI manifest digest를 사용한다.

## Server 배포

`deploy/server/.env.example`을 `.env`로 복사하고 zero digest를 release digest로 교체한다.

```bash
scripts/check-deployment.sh deploy/server/.env
docker compose --env-file deploy/server/.env \
  --file deploy/server/compose.yml up --detach
```

Browser는 설정한 HTTP port를 사용한다. LiDAR SDK client는 서로 다른 Server IPv4 주소에 공통 UDP
port 8089로 연결한다. `.env.example`의 `127.0.0.2`와 `127.0.0.3`은 공개 loopback 예시이며
외부 SDK client를 연결할 때는 Server interface에 실제로 설정한 두 주소로 교체한다. Compose는
Sensor 1을 내부 UDP 8089에, Sensor 2를 내부 UDP 8090에 연결하고 Visualizer health check가
통과한 뒤 Simulation server를 시작한다. 배포 검사는 두 외부 endpoint가 모두 UDP 8089를
사용하고 두 host IPv4 주소가 서로 다른지 확인한다.

Server 통합 probe는 설정한 release image로 임시 Container를 실행해 scene v2 stream, Browser paired visual stream과 1920 x 1080 raw camera stream을 확인한다.

```bash
set -a
. deploy/server/.env
set +a
SCRAP_SIMULATOR_TEST_SERVER_IMAGE="$SCRAP_SIMULATION_SERVER_IMAGE" \
  SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE="$SCRAP_SIMULATOR_VISUALIZER_IMAGE" \
  scripts/check.sh amd64-runtime
```

실제 Browser 수락 검사는 Mac Chrome과 Ubuntu Server의 release Visualizer image 사이에서 Chrome DevTools Protocol (CDP)로 수행한다. Chrome은 별도 임시 profile, remote debugging port, 전경 window를 사용한다. Server는 release image 실행 전과 실행 중 `scripts/server-health-guard.sh`로 열, load, CPU Pressure Stall Information (PSI), available memory, swap과 검증 대상 container 상태를 기록한다.

```bash
scripts/server-health-guard.sh \
  --container scrap-monitoring-simulator-server \
  --container scrap-monitoring-simulator-visualizer \
  --preflight-seconds 60 \
  --soak-seconds 300 \
  --cooldown-seconds 60 \
  -- <validation-command>
```

Guard는 preflight에서 temperature sensor, load, CPU PSI, memory와 swap의 기준을 확인한다. `SCRAP_SIMULATOR_HEALTH_REQUIRE_TEMPERATURE=false`을 명시하지 않으면 temperature sensor를 읽지 못한 host에서는 수락을 시작하지 않는다. Soak 중 temperature, 지속 load 또는 PSI, memory, swap 증가, tracked container unhealthy, restart와 OOM을 감지하면 guarded process group만 중지한다. Guard는 다른 Container나 process를 중지하지 않는다.

Browser probe는 5분 동안 model과 camera presentation이 각각 27 FPS 이상이고 5초 안정 창의 90 percent 이상이 기준을 만족하는지 확인한다. Target mismatch와 decode error는 0이어야 하고 pending decode, decode in-flight, pending presentation은 각각 1 이하여야 한다. 같은 실행에서 공식 SDK sensor 2대의 HQ scan 수신과 edge V4L2 90 frame 검사를 완료한다.

## Edge 배포

ARM64 host에서 `deploy/edge/setup-v4l2loopback.sh`를 root로 한 번 실행한다. 이 script는 `video_nr=42`, `exclusive_caps=0`인 V4L2 loopback device와 고정 symlink를 구성한다.

`deploy/edge/.env.example`을 `.env`로 복사하고 image digest, Visualizer URL과 host video group identifier를 설정한다.

```bash
docker compose --env-file deploy/edge/.env \
  --file deploy/edge/compose.yml up --detach
```

Stream 연결 후 host에서 90개 frame의 format, cadence, decode, sequence와 monotonic EOF timestamp를 검사한다.

```bash
deploy/edge/check-90-frames.sh /dev/scrap-synthetic-camera
```

## 로컬 검증

| 명령 | 검증 경계 |
| --- | --- |
| `scripts/check.sh static` | Repository 구조, Compose, 문서와 workflow |
| `scripts/check.sh amd64` | Simulation server, Visualizer와 통합 probe |
| `scripts/check.sh arm64` | Camera edge bridge build, test와 runtime smoke test |
| `scripts/check.sh` | 두 architecture를 포함한 전체 검사 |

무인자 실행은 static 검사와 service 3개의 Bake build를 병렬 실행하고, build 완료 뒤 AMD64 runtime probe와 ARM64 smoke test를 병렬 실행한다. Rust Dockerfile은 service와 architecture별 Cargo cache를 사용한다. Visualizer Dockerfile은 Node와 Python dependency layer를 source layer와 분리한다.

## CI와 CodeQL

GitHub Actions Continuous Integration (CI)는 Static, AMD64와 ARM64 job을 병렬 실행한다. ARM64 job은 native runner를 사용하고 각 service는 architecture별 GitHub Actions cache를 사용한다. 마지막 CI job은 세 결과가 모두 성공했는지 검사한다.

CodeQL은 GitHub platform default setup을 사용한다. 이 Repository는 CodeQL workflow file을 관리하지 않는다.
