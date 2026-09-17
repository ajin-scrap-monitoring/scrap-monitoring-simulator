# 스크랩 모니터링 시뮬레이터

실제 edge platform의 Light Detection and Ranging (LiDAR)과 camera 입력 경계를 검증하는 개발용
통합 simulation system이다. 하나의 canonical scene에서 RPLIDAR S2E 호환 User Datagram
Protocol (UDP) scan 2개, 30 Frames Per Second (FPS) Browser 시각화와 synthetic camera stream을
생성한다.

## 주요 기능

| Service | 경로 | 책임 |
| --- | --- | --- |
| Simulation server | `services/simulation-server` | Canonical scene, 10 Hz 적재면과 S2E UDP endpoint 2개 |
| Visualizer | `services/visualizer` | WebGL 3D model, synthetic camera stream과 Browser page |
| Camera edge bridge | `services/camera-edge-bridge` | synthetic camera MJPEG stream을 ARM64 Video4Linux2 (V4L2) device에 기록 |

## 사전 조건

| 도구 | 지원 버전 | 적용 범위 |
| --- | --- | --- |
| Docker Engine | 27 이상 | Runtime, local image build와 전체 검증 |
| Docker Compose plugin | 2 이상 | Runtime 구성과 deployment 설정 검사 |
| Docker Buildx | 0.37.1 이상 | Local image build와 전체 검증 |
| `jq` | 1.6 이상 | Deployment 설정 검사와 전체 검증 |
| Linux ARM64 host와 V4L2 loopback device | 해당 없음 | Camera edge bridge 배포 |
| FFmpeg | 4.4 이상 | Edge V4L2 90-frame 검사 |

Ubuntu 22.04, 24.04 또는 26.04 host에서 다음 명령으로 대상 hardware의 의존성을 설치한다.
script는 Docker 공식 APT repository를 설정하고 기존 충돌 package는 제거하지 않은 채 중단한다.

```bash
# AMD64 Server
sudo deploy/install-prerequisites.sh --server

# ARM64 Edge
sudo deploy/install-prerequisites.sh --edge
```

## 설정

배포 설정 명령은 Repository root 또는 GitHub Release의 `deploy/` archive를 푼 상위
directory에서 실행한다. `deploy/server`와 `deploy/edge`는 Repository와 archive에 포함되며
`cp`는 각 directory 안의 `.env` 파일만 만든다. 배포 Docker Compose는 하드웨어별 공개
기본값을 `.env.example`에서 읽는다. 아래 `필수` 항목은 배포 전에 직접 설정한다.

### AMD64 Server

```bash
cp deploy/server/.env.example deploy/server/.env
```

| 변수 | 입력할 값 | 적용 |
| --- | --- | --- |
| `SCRAP_SIMULATION_SERVER_IMAGE` | Release의 AMD64 Simulation server OCI digest | 필수 |
| `SCRAP_SIMULATOR_VISUALIZER_IMAGE` | Release의 AMD64 Visualizer OCI digest | 필수 |
| `SCRAP_SIMULATOR_LIDAR_1_HOST` | Server interface에 설정한 Sensor 1 IPv4 주소 | 필수 |
| `SCRAP_SIMULATOR_LIDAR_2_HOST` | Server interface에 설정한 Sensor 2 IPv4 주소 | 필수 |
| `SCRAP_MONITORING_VISUALIZER_CAMERA_BACKEND` | `egl` | NVIDIA GPU와 GPU Compose overlay를 사용하는 경우 |

### ARM64 Edge

```bash
cp deploy/edge/.env.example deploy/edge/.env
```

| 변수 | 입력할 값 | 적용 |
| --- | --- | --- |
| `SCRAP_SYNTHETIC_CAMERA_BRIDGE_IMAGE` | Release의 ARM64 Camera edge bridge OCI digest | 필수 |
| `SCRAP_SYNTHETIC_CAMERA_SERVER_URL` | `ws://<server-address>:<browser-port>/camera/v1/stream` | 필수 |
| `SCRAP_SYNTHETIC_CAMERA_VIDEO_GID` | Edge host의 `/dev/video42` group identifier | 필수 |
| `SCRAP_SYNTHETIC_CAMERA_DEVICE` | Edge consumer가 사용할 V4L2 output path | 기본 경로와 다를 경우 |

전체 설정은 [docs/configuration.md](docs/configuration.md)에서 확인한다.

두 hardware를 함께 실행하기 전에 실제 설정을 검사한다.

```bash
sudo docker compose --env-file deploy/server/.env \
  --file deploy/server/compose.yml config --quiet
sudo docker compose --env-file deploy/edge/.env \
  --file deploy/edge/compose.yml config --quiet
```

## 빠른 시작

사전 조건과 대상 hardware 설정을 마친 뒤 release image를 실행한다.

### AMD64 Server

```bash
sudo docker compose --env-file deploy/server/.env \
  --file deploy/server/compose.yml up --detach
```

Browser는 설정한 Server address와 Browser port로 연다. 기본 URL은
`http://<server-address>:18000`이다. focus를 가진 Browser 하나만 visual stream에 연결한다.

### ARM64 Edge

```bash
sudo docker compose --env-file deploy/edge/.env \
  --file deploy/edge/compose.yml up --detach
sudo deploy/edge/check-90-frames.sh /dev/scrap-synthetic-camera
```

## 개발 및 검증

Container build, 정적 검사와 test를 실행한다.

```bash
sudo scripts/check.sh
```

## 배포

Release는 AMD64 image 2개와 ARM64 image 1개, Open Container Initiative (OCI) manifest digest inventory와 `deploy/`
archive를 제공한다. 실행 구성과 실제 수락 절차는 [docs/deployment.md](docs/deployment.md)를 사용한다.

## 문서

| 문서 | 책임 |
| --- | --- |
| [docs/project-spec.md](docs/project-spec.md) | 제품 범위와 완료 조건 |
| [docs/architecture.md](docs/architecture.md) | 상태 소유권, 시간과 process 경계 |
| [docs/development-plan.md](docs/development-plan.md) | 현재 구현과 다음 작업 순서 |
| [docs/configuration.md](docs/configuration.md) | 공개 환경 설정과 자격 증명 경계 |
| [docs/deployment.md](docs/deployment.md) | Image, 수락 검사와 release 절차 |
| [docs/dependencies.md](docs/dependencies.md) | 직접 의존성과 notice 경계 |
| [docs/sdk-compatibility.md](docs/sdk-compatibility.md) | RPLIDAR SDK 수락 범위 |
| [docs/source-baselines.md](docs/source-baselines.md) | 이관 입력의 적용 경계 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도
라이선스를 부여하지 않는다.

외부 의존성에는 각 저작권자가 정한 license가 적용되며 목록은
[docs/dependencies.md](docs/dependencies.md)에서 관리한다.
