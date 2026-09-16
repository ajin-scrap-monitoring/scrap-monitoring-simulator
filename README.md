# Scrap Monitoring Simulator

스크랩 적재 모니터링을 개발하고 검증하는 통합 simulation system이다. 하나의 canonical
scene에서 RPLIDAR S2E 호환 User Datagram Protocol (UDP) scan 2개, Browser 시각화와 합성
camera 입력을 생성한다. 실제 edge platform의 driver와 처리 서비스는 이 저장소의 범위가
아니다.

## 주요 기능

- 결정론적 적재면과 simulation clock을 소유하는 Simulation server
- 같은 scene을 표시하는 3D Browser 화면과 1920 x 1080 camera stream
- 합성 camera stream을 Video4Linux2 (V4L2) device에 기록하는 ARM64 bridge
- 실제 RPLIDAR Software Development Kit (SDK)와 bounded latest-only 경계 검증

## 빠른 시작

Docker Engine, Docker Compose plugin과 Docker Buildx가 필요하다. `linux/amd64` build를
지원하는 환경에서 저장소 루트의 다음 명령으로 Server 구성과 Browser 화면을 실행한다.

```bash
docker buildx bake ci-amd64
SCRAP_SIMULATION_SERVER_IMAGE=scrap-monitoring-simulator-server:local \
  SCRAP_SIMULATOR_VISUALIZER_IMAGE=scrap-monitoring-simulator-visualizer:local \
  docker compose --env-file deploy/server/.env.example \
    --file deploy/server/compose.yml up --detach
```

Browser에서 `http://localhost:18000`에 접속한다.

## 설정

`deploy/server/.env.example`과 `deploy/edge/.env.example`이 공개 실행 설정의 schema 역할을
한다. 전체 설정과 자격 증명 경계는 [설정 문서](docs/configuration.md)에서 확인한다.

## 개발 및 검증

Docker Buildx builder가 `linux/amd64`와 `linux/arm64`를 지원해야 한다. Host에 Rust 또는
Python runtime을 설치하지 않고 다음 명령으로 전체 build, 정적 검사와 test를 실행한다.

```bash
scripts/check.sh
```

## 배포

Release는 AMD64 image 2개와 ARM64 image 1개, image digest inventory와 `deploy/` archive를
제공한다. 배포 구성은 version tag가 아닌 Open Container Initiative (OCI) manifest digest를
사용한다. 자세한 절차는 [배포 문서](docs/deployment.md)에서 확인한다.

## 문서

| 문서 | 역할 |
| --- | --- |
| [프로젝트 명세](docs/project-spec.md) | 제품 범위, 외부 경계와 완료 조건 |
| [아키텍처](docs/architecture.md) | 상태 소유권과 process 경계 |
| [개발 계획](docs/development-plan.md) | 현재 구현 현황과 앞으로의 통합 순서 |
| [설정](docs/configuration.md) | 설정 파일, 환경 변수와 자격 증명 경계 |
| [배포](docs/deployment.md) | Image, 실행과 검증 절차 |
| [의존성](docs/dependencies.md) | 직접 의존성과 외부 고지 |
| [SDK 호환 경계](docs/sdk-compatibility.md) | RPLIDAR SDK 수락 범위 |
| [구현 입력 기준](docs/source-baselines.md) | 이관 입력의 고정 commit과 적용 경계 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도
라이선스를 부여하지 않는다.
