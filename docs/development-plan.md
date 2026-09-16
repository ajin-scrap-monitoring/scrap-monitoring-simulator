# 개발 계획

## 현재 구현 현황

현재 `0.2.1` 구현은 6개 영역으로 구성한다.

| 영역 | 현재 구현 | 검증 경계 |
| --- | --- | --- |
| Scene 계약 | Versioned definition과 frame schema, strict parser | Schema fixture와 계약 test |
| Simulation server | Canonical scene, sensor 2대 측정과 S2E UDP endpoint | 결정론, geometry와 공식 SDK 수락 |
| Browser | 3D model, camera canvas와 공통 scene 수치 | Render와 Browser 통합 probe |
| Synthetic camera | Deadline scheduling, 640 x 360 raster와 revision-only stream | 고유 frame과 단계별 diagnostics |
| Edge bridge | ARM64 WebSocket client와 V4L2 writer | Descriptor, JPEG, cadence와 device 검사 |
| Build와 release | 병렬 AMD64 및 ARM64 build, service별 cache와 OCI 게시 | Container test, digest, SBOM과 provenance |

구성 요소의 현재 동작은 [프로젝트 명세](project-spec.md)와 [아키텍처](architecture.md),
검증 및 릴리스 경계는 [배포 문서](deployment.md), 실행 입력은 [설정 문서](configuration.md)를
정본으로 사용한다.

## 후속 통합 작업 순서

다음 기능 개발은 camera와 3D model을 같은 simulation time에 맞추는 4단계 작업으로 진행한다.

1. Simulation Core의 현재 10 Hz canonical surface keyframe을 timestamp sampling, 시간순 event queue와 scene publisher 계약으로 노출한다. 계약은 camera와 Browser WebGL model이 같은 두 keyframe 사이를 공유 30 Hz target timestamp로 보간하도록 정의한다. LiDAR scan은 point timestamp가 선택한 keyframe을 독립적으로 직접 측정하며 render 경로를 거치지 않는다.
2. Camera scheduler를 timestamp sampling 계약에 연결하고 server-side 3D PNG를 Browser WebGL renderer로 교체한다. 두 renderer는 shared target schedule을 소비한다.
3. Camera frame과 model frame에 target timestamp와 frame identifier를 노출한다. Browser 검증은 두 출력이 같은 target을 표시하는지 판정한다.
4. 실제 4-core Ubuntu Server에서 camera와 model의 Browser presentation이 각각 27 FPS 이상인지 검증한다. 같은 부하에서 공식 SDK LiDAR scan cadence가 유지되고 모든 network, decode와 render queue가 latest-only 크기 1을 넘지 않아야 한다.

## 이후 변경의 반복 검증 순서

후속 변경은 다음 5단계 순서로 통합한다.

1. 책임을 가진 versioned 계약, 설정과 service test 갱신.
2. 변경한 service의 Container 정적 검사, test와 runtime image 검증.
3. Simulation server, Browser와 camera의 latest-only 통합 probe 검증.
4. 영향을 받는 경우 실제 Browser, V4L2와 LiDAR SDK 경계 검증.
5. 정본 문서 갱신과 검증된 `main` commit의 version release.

## 조건부 decision gate

확장 요청이 생길 때만 다음 3개 decision gate를 연다.

| 조건 | 구현 전에 확정할 입력 |
| --- | --- |
| 성능 또는 용량 확장 | 동시 Browser 수, network 조건, 목표 해상도와 FPS, Server CPU 및 GPU 예산 |
| 인증 도입 | Trust boundary, 발급 주체, 저장 위치, 회전과 폐기 책임 |
| 외부 운영 제품 연동 | 계약 소유자, version, endpoint, 장애 격리와 배포 책임 |

인증을 도입하면 Compose가 host의 비밀 파일을 `/run/secrets`에 mount하고 service가 `*_FILE`
경로로 읽는 계약을 먼저 확정한다. Service는 production에서 평문 credential 환경 변수를
거부해야 한다. Credential 값은 `.env`, `.env.example`, image와 Git 이력에 넣지 않는다.
