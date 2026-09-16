# 배포

## Image 경계

배포 image는 다음 3개다.

| Image | Platform | Dockerfile |
| --- | --- | --- |
| `scrap-monitoring-simulator-server` | `linux/amd64` | `services/simulation-server/Dockerfile` |
| `scrap-monitoring-simulator-visualizer` | `linux/amd64` | `services/visualizer/Dockerfile` |
| `scrap-monitoring-simulator-camera-edge-bridge` | `linux/arm64` | `services/camera-edge-bridge/Dockerfile` |

Release workflow는 `main` 이력에 포함된 commit의 정확한 `vMAJOR.MINOR.PATCH` tag만 받는다. 세
manifest와 각 lock file의 프로젝트 version은 tag에서 `v`를 뺀 값과 같아야 한다. 동일 tag의
GitHub Release가 이미 존재하면 image를 게시하기 전에 workflow가 실패한다.

Release는 `MAJOR.MINOR.PATCH`와 `sha-<full-git-sha>` tag를 같은 OCI manifest에 연결하고
Software Bill of Materials (SBOM)와 provenance를 발행한다. `latest` tag는 만들지 않는다.
배포 설정은 tag가 아니라 manifest digest를 요구한다.

GitHub Release는 image digest 3개를 기록한 `oci-images.txt`와 모든 `deploy/` 파일을 포함한
`scrap-monitoring-simulator-deploy-MAJOR.MINOR.PATCH.tar.gz`를 제공한다.

## Server 배포

`deploy/server/.env.example`을 `.env`로 복사하고 두 image reference의 zero digest를 Release가
발행한 digest로 교체한다. 다음 command가 Simulation server와 Visualizer를 실행한다.

```bash
docker compose --env-file deploy/server/.env \
  --file deploy/server/compose.yml up --detach
```

Browser는 설정한 HTTP port를 사용한다. LiDAR SDK client는 sensor별 UDP port 8089와 8090을
사용한다. Compose는 Visualizer health check가 통과한 뒤 Simulation server를 시작한다.

Server 통합 probe는 설정한 두 release image로 임시 Container를 실행해 scene 수신, Browser
PNG와 1920 x 1080 JPEG stream을 확인한다.

```bash
set -a
. deploy/server/.env
set +a
SCRAP_SIMULATOR_TEST_SERVER_IMAGE="$SCRAP_SIMULATION_SERVER_IMAGE" \
  SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE="$SCRAP_SIMULATOR_VISUALIZER_IMAGE" \
  scripts/check.sh amd64-runtime
```

실제 Browser 수락 검사는 Mac Chrome과 Ubuntu Server의 release Visualizer image 사이에서
Chrome DevTools Protocol (CDP)로 수행한다. Mac에서 별도 임시 profile을 지정해 Chrome을
`--remote-debugging-port=9222 --user-data-dir=<temporary-dir>`로 실행한다. Chrome 창은
최소화하지 않고 전경에 둔 상태로 다음 SSH 연결을 유지한다.

```bash
ssh -N \
  -L 127.0.0.1:18000:127.0.0.1:18000 \
  -R 127.0.0.1:19222:127.0.0.1:9222 \
  "<server-ssh-host>"
```

Server에서 release digest를 설정한 `deploy/server/.env`를 읽고 Browser probe를 실행한다.

```bash
set -a
. deploy/server/.env
set +a
docker run --rm --network host --entrypoint python \
  "$SCRAP_SIMULATOR_VISUALIZER_IMAGE" \
  -m scrap_monitoring_visualizer.browser_probe \
  --cdp-url http://127.0.0.1:19222 \
  --page-url http://127.0.0.1:18000 \
  --duration 60 \
  --minimum-fps 27 \
  --minimum-stable-ratio 0.9
```

Probe는 Browser가 준비된 뒤 5초간 warmup하고 baseline snapshot을 만든 다음 1초 간격으로
60회 측정한다. Probe는 새 target에 `Page.bringToFront`를 요청하고 준비 시점, baseline과 각
표본에서 `document.visibilityState=visible`, `document.hidden=false`,
`document.hasFocus()=true`인지 검증한다. Baseline부터 마지막 snapshot의 `sampled_at_ms`까지
실제 Browser elapsed를 기준으로 계산한 WebSocket 수신 평균과 Browser presentation 평균이
각각 27 FPS 이상이어야 한다.

최소 trailing 5초의 각 안정 창은 source rolling FPS와 WebSocket 수신 및 Browser presentation
cumulative counter delta가 모두 27 FPS 이상일 때 통과한다. 판정 가능한 안정 창 중 90%
이상이 통과하고 decode error delta가 0이어야 한다. Pending decode, decode in-flight와 pending
presentation의 관측 최대값은 각각 1 이하여야 한다. 다른 Mac용 URL로 접속하는 구성은
`--page-url`을 해당 URL로 바꾼다.

CI integration probe는 256-bit Secure Hash Algorithm (SHA-256) digest가 서로 다른 JPEG 15개를
수신하고 `network_unique_fps`가 20 이상이어야 통과한다. `network_unique_frames`와
`network_unique_fps`는 WebSocket 수신 경계를 나타낸다. 같은 JPEG의 반복 전송이나 표시는
고유 FPS로 계산하지 않으며 판정 결과는 개발용 화면에 표시하지 않는다.

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

로컬 검증 명령은 다음 4개 경계를 제공한다.

| 명령 | 검증 경계 |
| --- | --- |
| `scripts/check.sh static` | Repository 구조, Compose, 문서와 workflow |
| `scripts/check.sh amd64` | Simulation server, Visualizer와 통합 probe |
| `scripts/check.sh arm64` | Camera edge bridge build, test와 runtime smoke test |
| `scripts/check.sh` | 두 architecture를 포함한 전체 검사 |

무인자 실행은 static 및 문서 검사와 service 3개의 Bake build를 겹쳐 실행하고, build가 끝나면
독립적인 AMD64 runtime probe와 ARM64 smoke test를 병렬 실행한다. Rust Dockerfile은 manifest,
lock file과 toolchain으로 만든 dependency layer에서 service와 architecture별 Cargo registry 및
target cache를 복원한다. Visualizer Dockerfile은 manifest와 lock file로 생성한 third-party
dependency layer를 source layer와 분리하고 project package만 source 복사 후 설치한다. Rust runtime
image는 검증 stage가 `/artifacts`에 복사한 binary를 사용한다.

Docker Buildx builder는 명령이 요구하는 architecture를 지원해야 한다. AMD64 host에서 전체
검사를 실행할 때는 ARM64 emulation을 사전에 등록한다.

## CI와 CodeQL

GitHub Actions CI는 Static, AMD64와 ARM64 job을 병렬 실행한다. ARM64 job은
`ubuntu-24.04-arm` native runner를 사용하고 각 service는 architecture별 GHA cache를
사용한다. 마지막 `CI` job은 세 결과가 모두 성공했는지 검사한다.

CodeQL은 GitHub 플랫폼 default setup을 사용한다. 이 Repository는 CodeQL workflow 파일을
관리하지 않는다.
