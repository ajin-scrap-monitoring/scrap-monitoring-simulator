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
Simulation Core
|-- S2E UDP Adapter -------------------------------> Edge LiDAR Driver
`-- Latest Scene Stream -> Visualizer
    |-- Browser Renderer -> PNG -------------------> Browser
    `-- Camera Scheduler -> CPU Raster -> JPEG Store
        |-- Revision WebSocket -> Decode -> Canvas -> Browser
        `-- Revision WebSocket --------------------> Edge Bridge -> V4L2
```

Simulation Core만 scene과 simulation clock을 변경한다. S2E adapter와 Visual adapter는
같은 불변 snapshot을 소비한다. Scene과 frame은 디스크에 저장하지 않는다.

## Process 경계

Simulation Core와 S2E adapter는 하나의 Rust process에서 실행한다. Sensor별 scan 계산은
독립 worker가 담당하고 sensor별 UDP actor는 별도 socket과 protocol 상태를 소유한다.

Visualizer는 Python server process와 Browser 및 camera용 render worker를 실행한다.
FastAPI는 Hypertext Transfer Protocol (HTTP)과 WebSocket I/O를 담당하고 Visualization
Toolkit (VTK) rendering과 image encoding은 별도 process가 수행한다. Browser용 직교투영
frame과 camera용 원근 frame은 같은 scene snapshot에서 생성된다.

두 server process는 bounded latest-only Transmission Control Protocol (TCP) scene stream으로
연결한다. Visualizer가 느리거나 종료돼도 Simulation Core와 S2E UDP endpoint는 계속
동작한다. Browser render queue와 camera render queue는 각각 pending request 1개만 유지한다.

## 시간 경계

현재 runtime은 서로 독립적인 5개 시간 경계로 구성한다.

| 경계 | 주기 | 동작 |
| --- | --- | --- |
| Simulation Core | Event와 simulation time 기반 | Frame clock 없이 시간순 event로 canonical scene 상태 전이 |
| Canonical surface | 10 Hz | 0.1초 간격으로 적재 표면 keyframe 갱신 |
| Sensor별 LiDAR | Scan 10 Hz, ray sample 32,000 samples/s | Point별 timestamp에서 surface snapshot을 선택하고 S2E UDP scan 생성 |
| Scene stream과 3D PNG | Revision 1 Hz | 최신 불변 snapshot 발행과 revision별 Browser frame 생성 |
| Synthetic camera | 30 FPS | Scene snapshot 사이의 상태를 downstream scheduler에서 보간해 JPEG 생성 |

각 LiDAR point는 sensor별 32,000 samples/s schedule에 따라 1/32,000초 간격의 timestamp를
가진다. Simulation Core는 point timestamp 이하인 가장 최근 10 Hz canonical surface
keyframe을 선택하므로 한 keyframe 구간의 측정 표면은 piecewise-constant다. LiDAR 측정은
camera frame 생성에 의존하지 않으며 camera scheduler의 지연이나 frame 누락이 scan cadence를
바꾸지 않는다.

Visualizer는 수신한 scene revision마다 server-side 3D PNG를 한 번 생성한다. Browser는
`/status`를 500 ms 간격으로 조회하고 `frame_revision`이 변경된 경우에만 `/frame.png`를
가져온다. 3D model은 30 FPS 영상 stream이 아니라 1 Hz scene revision의 snapshot이다.

## Camera frame 흐름

실시간 camera path는 다음 6개 경계로 구성한다.

| 순서 | 경계 | 처리와 지표 |
| --- | --- | --- |
| 1 | Scene source | 입력 sequence와 interpolation target |
| 2 | Render scheduler | Deadline, 제출, 교체와 누락 frame |
| 3 | Renderer와 encoder | 640 x 360 CPU raster, VTK linear 확대, 1920 x 1080 JPEG와 revision |
| 4 | WebSocket | Revision별 전송과 network 수신 cadence |
| 5 | Browser | 수신, decode와 canvas presentation |
| 6 | Edge V4L2 | Device write, sequence와 monotonic EOF timestamp |

Scheduler는 monotonic deadline을 사용하고 늦어진 주기를 따라잡기 위해 과거 frame을 연속
생성하지 않는다. 새 scene snapshot을 받으면 기존 segment의 마지막 target을 먼저 제출한 뒤
다음 segment를 연다. Renderer는 최신 요청 1개만 처리하고 재사용하는 VTK linear scaler로
output 크기의 RGB를 만든다. Pillow encoder는 JPEG를 한 번 만든다. Latest JPEG store는 직전
JPEG와 byte가 다른 payload에만 증가하는 revision을 부여한다.

Inlet이 바뀌면 renderer는 scene segment의 interpolation alpha에 smoothstep을 적용해 chute를
최단 polar 경로로 회전한다. Pivot 주위의 파생 socket 겹침은 별도 hinge 표식 없이 고정
conveyor와 chute의 연결을 유지한다.

WebSocket handler는 연결 descriptor를 먼저 보내고 store revision이 변경된 경우에만 JPEG를
보낸다. 각 연결 task는 store를 읽고 send timeout을 독립적으로 적용하므로 느린 client가
renderer를 막지 않는다. Browser는 1920 x 1080, 30 FPS descriptor를 검증하고
`createImageBitmap`이 JPEG를 960 x 540 bitmap으로 resize decode한다. Canvas의 intrinsic
크기도 960 x 540이다.

Browser는 decode 전 최신 blob 1개와 decode 완료 bitmap 1개만 유지한다. Ready bitmap이
`requestAnimationFrame` callback에서 소비되기 전에는 다음 decode를 시작하지 않고, 소비한
직후 최신 pending blob의 decode를 시작한다. Callback은 `bitmaprenderer`의
`transferFromImageBitmap`을 우선 사용해 canvas를 교체하고 2D context를 fallback으로 사용한다.

ARM64 Rust bridge는 Visualizer로 outbound WebSocket을 열고 최신 JPEG 1개를 V4L2 output에
기록한다. 느린 network와 device는 backlog를 만들지 않고 지난 출력 주기를 건너뛴다. V4L2
device가 부여한 sequence와 monotonic EOF timestamp가 consumer의 시간 정렬 기준이다.

Source status는 `/camera/v1/status`, WebSocket 수신은 integration probe, Browser 수신,
decode, presentation과 queue는 `window.__scrapCameraMetrics.snapshot()`, V4L2 출력은 edge
검사 script가 각각 측정한다. Browser 화면은 runtime 진단값을 렌더링하지 않는다.

## Repository 경계

Container image를 산출하는 service 3개는 같은 위계에 둔다.

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
