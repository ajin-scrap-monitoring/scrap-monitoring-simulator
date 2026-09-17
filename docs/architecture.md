# 아키텍처

## 상태 소유권

전체 구조는 4개 역할로 구분한다.

| 역할 | 상태와 책임 |
| --- | --- |
| Simulation Core | Canonical scene, simulation clock과 적재 상태 |
| S2E adapter | Scene의 sensor별 광선 측정과 UDP protocol |
| Visual adapter | Paired WebGL model과 camera JPEG frame |
| Edge consumer | 공식 SDK scan과 V4L2 camera 소비 |

```text
Simulation Core
|-- S2E UDP Adapter -------------------------------> Edge LiDAR Driver
`-- Latest Scene v2 Segment -> Visualizer
    `-- Target Scheduler -> CPU Raster -> JPEG
        |-- Paired Visual Stream -> WebGL Model + Camera -> Browser
        `-- Raw Camera Stream ----------------------> Edge Bridge -> V4L2
```

Simulation Core만 scene과 simulation clock을 변경한다. S2E adapter와 Visual adapter는 같은 불변 segment를 소비한다. Scene과 frame은 디스크에 저장하지 않는다.

## Process 경계

Simulation Core와 S2E adapter는 하나의 Rust process에서 실행한다. Sensor별 scan 계산은 독립 worker가 담당하고 sensor별 UDP actor는 별도 socket과 protocol 상태를 소유한다.

Visualizer는 Python server process, synthetic camera render worker와 Browser WebGL client로 구성한다. FastAPI는 Hypertext Transfer Protocol (HTTP), WebSocket I/O와 static page 제공을 담당한다. VTK rendering과 JPEG encoding은 별도 process가 수행하고 WebGL은 Browser GPU에서 model surface를 rendering한다.

두 server process는 bounded latest-only Transmission Control Protocol (TCP) scene stream으로 연결한다. Visualizer가 느리거나 종료돼도 Simulation Core와 S2E UDP endpoint는 계속 동작한다. Render worker, visual stream과 Browser decode queue는 각각 최신 항목 1개만 유지한다.

## 시간 경계

Runtime은 5개 시간 경계로 구성한다.

| 경계 | 주기 | 동작 |
| --- | --- | --- |
| Simulation Core | Event와 simulation time 기반 | 시간순 event로 canonical scene 상태 전이 |
| Canonical surface | 10 Hz | 0.1초 간격으로 적재 표면 keyframe 갱신 |
| Sensor별 LiDAR | Scan 10 Hz, ray sample 32,000 samples/s | Point timestamp에서 최신 keyframe을 선택해 S2E UDP scan 생성 |
| Scene v2 stream | Initial definition과 event segment | Static grid와 인접 dynamic keyframe 전달 |
| Visual target | 30 FPS | 같은 target id로 surface 보간과 camera JPEG 생성 |

각 LiDAR point는 sensor별 32,000 samples/s schedule에 따라 timestamp를 가진다. Simulation Core는 point timestamp 이하인 가장 최근 10 Hz keyframe을 선택하므로 한 keyframe 구간의 측정 표면은 piecewise-constant다. LiDAR 측정은 camera와 Browser target 생성에 의존하지 않는다.

Visualizer는 integer target id로 30 FPS target time을 정한다. 새 segment가 도착하면 이전 segment의 마지막 target만 제출하고 과거 target을 따라 생성하지 않는다. Camera renderer가 완료한 JPEG와 같은 target의 Float32 surface 배열은 하나의 binary packet으로 Browser에 전달된다. Browser는 JPEG를 960 x 540 backing canvas 크기로 decode하고 WebGL update와 camera bitmap presentation을 같은 `requestAnimationFrame` callback에서 실행한다.

## Camera와 Browser frame 흐름

실시간 paired visual path는 6개 경계로 구성한다.

| 순서 | 경계 | 처리 |
| --- | --- | --- |
| 1 | Scene source | 인접 keyframe segment 수신 |
| 2 | Target scheduler | 30 FPS target id, deadline과 latest-only 교체 |
| 3 | Camera renderer | 576 x 324 CPU raster, 1920 x 1080 JPEG |
| 4 | Paired visual stream | target metadata, Float32 heights와 JPEG 전송 |
| 5 | Browser | WebGL surface update와 camera bitmap 동시 presentation |
| 6 | Edge V4L2 | Raw MJPEG stream, device sequence와 monotonic EOF timestamp |

Paired visual stream은 Browser 전용이다. `/visual/v1/stream`은 descriptor 뒤에 big-endian JSON metadata 길이, JSON metadata, little-endian Float32 heights와 JPEG를 전송한다. Raw `/camera/v1/stream`은 ARM64 edge bridge의 기존 MJPEG 계약을 유지한다.

Synthetic camera renderer가 1920 x 1080, 30 FPS MJPEG를 생성하며 camera edge bridge는 해상도와 encoding을 변경하지 않고 V4L2 device에 기록한다. `ajin-edge-platform`의 camera-edge는 같은 해상도, FPS와 format을 V4L2에 요청하고 JPEG packet을 변환 없이 전송한다. 실제 camera의 낮은 해상도 profile 생성은 camera 내부 ISP와 encoder의 책임이고 edge platform은 장비가 제공하는 profile을 선택한다. 장비가 제공하지 않는 해상도의 software resize와 re-encode는 이 Repository의 책임이 아니다.

Browser는 하나의 foreground client만 허용한다. Raw camera stream은 edge consumer를 위해 별도 연결 상한을 가진다. Browser가 느리면 최신 packet만 남기며 server rendering과 LiDAR output을 지연시키지 않는다.

## Repository 경계

Container image를 산출하는 service 3개는 같은 위계에 둔다.

```text
services/
|-- simulation-server/
|-- visualizer/
`-- camera-edge-bridge/
```

각 service 디렉터리는 언어별 manifest, source, test, Dockerfile과 전용 notice를 소유한다. `contracts/`는 process 사이의 versioned data 계약, `deploy/`는 server와 edge 실행 구성, `scripts/`는 Repository 전체 검증을 소유한다.
