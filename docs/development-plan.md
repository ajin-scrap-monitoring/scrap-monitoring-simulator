# 개발 계획

## 현재 구현 현황

현재 `0.3.1` 구현은 6개 영역으로 구성한다.

| 영역 | 현재 구현 | 검증 경계 |
| --- | --- | --- |
| Scene 계약 | v2 static definition과 인접 dynamic segment | Schema fixture와 strict parser |
| Simulation server | Canonical scene, 10 Hz surface, sensor 2대와 S2E UDP endpoint | 결정론, geometry와 공식 SDK 수락 |
| Browser | 30 FPS WebGL model, paired camera와 공통 scene 수치 | Web protocol test와 Browser probe |
| Synthetic camera | Integer target scheduler, 640 x 360 raster와 1920 x 1080 MJPEG | 고유 frame, target identity와 latest-only queue |
| Edge bridge | ARM64 raw camera client와 V4L2 writer | Descriptor, JPEG, cadence와 device 검사 |
| Build와 release | 병렬 AMD64 및 ARM64 build, service별 cache와 OCI 게시 | Container test, digest, SBOM과 provenance |

## 수락과 release 순서

`v0.3.1` release는 다음 5단계를 완료 조건으로 사용한다.

1. `scripts/check.sh`로 static, AMD64, ARM64 Container build와 test를 완료한다.
2. Server에서 health guard preflight를 통과한 release image로 Simulation server와 Visualizer를 실행한다.
3. 전경 Chrome Browser에서 5분 동안 model과 camera가 각각 27 FPS 이상이고 target mismatch, decode error와 queue 초과가 없는지 확인한다.
4. 같은 실행에서 공식 SDK sensor 2대의 10 Hz cadence와 ARM64 V4L2 90 frame 검사를 완료한다.
5. Pull Request CI, CodeQL default setup, squash merge와 immutable version release를 완료한다.

## 이후 변경의 반복 순서

후속 변경은 다음 5단계 순서로 통합한다.

1. 책임을 가진 versioned 계약, 설정과 service test를 갱신한다.
2. 변경한 service의 Container 정적 검사, test와 runtime image를 검증한다.
3. Simulation server, paired Browser visual stream과 raw camera 통합 probe를 검증한다.
4. 영향을 받는 경우 실제 Browser, V4L2와 LiDAR SDK 경계를 health guard로 검증한다.
5. 정본 문서 갱신과 검증된 `main` commit의 version release를 수행한다.

## 조건부 decision gate

확장 요청이 생길 때만 다음 3개 decision gate를 연다.

| 조건 | 구현 전에 확정할 입력 |
| --- | --- |
| 성능 또는 용량 확장 | 동시 Browser 수, network 조건, 목표 해상도와 FPS, Server CPU 및 GPU 예산 |
| 인증 도입 | Trust boundary, 발급 주체, 저장 위치, 회전과 폐기 책임 |
| 외부 운영 제품 연동 | 계약 소유자, version, endpoint, 장애 격리와 배포 책임 |

인증을 도입하면 Compose가 host 비밀 파일을 `/run/secrets`에 mount하고 service가 `*_FILE` 경로로 읽는 계약을 먼저 확정한다. Service는 production에서 평문 credential 환경 변수를 거부한다. Credential 값은 `.env`, `.env.example`, image와 Git 이력에 넣지 않는다.
