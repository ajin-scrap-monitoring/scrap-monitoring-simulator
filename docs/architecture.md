# 아키텍처

## 상태 소유권

전체 구조는 4개 역할로 구분한다.

| 역할 | 상태와 책임 |
| --- | --- |
| Simulation Core | Canonical scene, simulation clock과 적재 상태 |
| S2E adapter | Scene의 sensor별 광선 측정과 UDP protocol |
| Visual adapter | Scene의 3D Browser frame과 camera frame |
| Edge consumer | 공식 SDK scan과 V4L2 camera 소비 |

```text
                       +-> S2E UDP Adapter -> Edge LiDAR Driver
Simulation Core -------+
                       +-> Scene Stream -> Visual Renderer -> Browser
                                      \-> Camera Stream -> Edge V4L2
```

Simulation Core만 scene과 simulation clock을 변경한다. S2E adapter와 Visual adapter는
같은 불변 snapshot을 소비한다. Scene과 frame은 디스크에 저장하지 않는다.

## Process 경계

Simulation Core와 S2E adapter는 하나의 Rust process에서 실행한다. Sensor별 scan 계산은
독립 worker가 담당하고 sensor별 UDP actor는 별도 socket과 protocol 상태를 소유한다.

Visualizer는 Python server process와 Browser 및 camera용 render worker를 실행한다.
FastAPI는 HTTP와 WebSocket I/O를 담당하고 VTK rendering과 JPEG encoding은 별도 process가
수행한다. Browser용 직교투영 frame과 camera용 원근 MJPEG frame은 같은 scene snapshot에서
생성된다.

두 server process는 bounded latest-only TCP scene stream으로 연결한다. Visualizer가 느리거나
종료돼도 Simulation Core와 S2E UDP endpoint는 계속 동작한다. Visualizer의 render queue와
camera queue도 pending request 1개만 유지한다.

ARM64 Rust bridge는 Visualizer로 outbound WebSocket을 열고 최신 JPEG 1개를 V4L2 output에
기록한다. 느린 network와 device는 backlog를 만들지 않고 지난 출력 주기를 건너뛴다.
V4L2 device가 부여한 sequence와 monotonic EOF timestamp가 consumer의 시간 정렬 기준이다.

## Repository 경계

Container image를 산출하는 3개 service는 같은 위계에 둔다.

```text
services/
|-- simulation-server/
|-- visualizer/
`-- camera-edge-bridge/
```

각 service 디렉터리는 언어별 manifest, source, test, Dockerfile과 전용 notice를 소유한다.
`contracts/`는 process 사이의 versioned data 계약, `deploy/`는 server와 edge 실행 구성,
`scripts/`는 Repository 전체 검증을 소유한다.

`ajin-edge-platform`은 실제 SDK driver와 처리 서비스를 독립적으로 관리한다.
`scrap-monitoring-deployment`는 검증된 image digest와 환경별 실행 설정을 조합한다.
