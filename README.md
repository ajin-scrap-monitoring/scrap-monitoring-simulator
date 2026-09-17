# Scrap Monitoring Simulator

실제 edge platform의 Light Detection and Ranging (LiDAR)과 camera 입력 경계를 검증하는 개발용
통합 simulation system이다. 하나의 canonical scene에서 RPLIDAR S2E 호환 User Datagram
Protocol (UDP) scan 2개, 30 Frames Per Second (FPS) Browser 시각화와 합성 camera stream을
생성한다.

## 주요 기능

| Service | 경로 | 책임 |
| --- | --- | --- |
| Simulation server | `services/simulation-server` | Canonical scene, 10 Hz 적재면과 S2E UDP endpoint 2개 |
| Visualizer | `services/visualizer` | WebGL 3D model, 합성 camera stream과 Browser page |
| Camera edge bridge | `services/camera-edge-bridge` | 합성 Motion JPEG (MJPEG)를 ARM64 Video4Linux2 (V4L2) device에 기록 |

실제 LiDAR Software Development Kit (SDK) driver와 처리 서비스는 `ajin-edge-platform`이
소유한다.

## 빠른 시작

Docker Engine, Docker Compose plugin, Docker Buildx와 `jq` 1.6 이상이 필요하다. 공개 설정
schema를 사용해 AMD64 server 구성을 실행한다.

```bash
docker buildx bake ci-amd64
SCRAP_SIMULATION_SERVER_IMAGE=scrap-monitoring-simulator-server:local \
  SCRAP_SIMULATOR_VISUALIZER_IMAGE=scrap-monitoring-simulator-visualizer:local \
  docker compose --env-file deploy/server/.env.example \
    --file deploy/server/compose.yml up --detach
```

Browser는 `http://localhost:18000`에서 연다. 전경이고 focus를 가진 Browser page 하나만
visual stream에 연결한다. 실제 실행은 `deploy/server/.env.example`을 복사한 Git 제외
`.env`에 image digest를 설정한다.

## 설정

공개 환경 변수 schema는 `deploy/server/.env.example`과 `deploy/edge/.env.example`이다.
자격 증명과 Docker secret 경계는 [설정 문서](docs/configuration.md)를 사용한다.

## 개발 및 검증

Host 언어 runtime을 설치하지 않고 전체 Container build, 정적 검사와 test를 실행한다.

```bash
scripts/check.sh
```

## 배포

Release는 AMD64 image 2개와 ARM64 image 1개, Open Container Initiative (OCI) manifest digest inventory와 `deploy/`
archive를 제공한다. 실행 구성과 실제 수락 절차는 [배포 문서](docs/deployment.md)를 사용한다.

## 문서

| 문서 | 책임 |
| --- | --- |
| [프로젝트 명세](docs/project-spec.md) | 제품 범위와 완료 조건 |
| [아키텍처](docs/architecture.md) | 상태 소유권, 시간과 process 경계 |
| [개발 계획](docs/development-plan.md) | 현재 구현과 다음 작업 순서 |
| [설정](docs/configuration.md) | 공개 환경 설정과 자격 증명 경계 |
| [배포](docs/deployment.md) | Image, 수락 검사와 release 절차 |
| [의존성](docs/dependencies.md) | 직접 의존성과 notice 경계 |
| [SDK 호환 경계](docs/sdk-compatibility.md) | RPLIDAR SDK 수락 범위 |
| [구현 입력 기준](docs/source-baselines.md) | 이관 입력의 적용 경계 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도
라이선스를 부여하지 않는다.
