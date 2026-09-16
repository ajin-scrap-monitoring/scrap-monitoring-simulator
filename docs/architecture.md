# 아키텍처

## 상태 소유권

전체 구조는 4개 역할로 구분한다.

| 역할 | 상태와 책임 |
| --- | --- |
| Simulation Core | Canonical scene, simulation clock과 적재 상태 |
| S2E adapter | Scene의 sensor별 광선 측정과 UDP protocol |
| Visual adapter | Scene의 GPU mesh, Browser frame과 camera frame |
| Edge consumer | 공식 SDK scan과 V4L2 camera 소비 |

```text
                       +-> S2E UDP Adapter -> Edge LiDAR Driver
Simulation Core -------+
                       +-> Scene Stream -> Visual Renderer -> Browser
                                      \-> Camera Stream -> Edge V4L2
```

## Process 경계

Simulation Core와 S2E adapter는 하나의 Rust process에서 실행한다. Sensor별 scan 계산은
독립 worker가 담당하고 sensor별 UDP actor는 별도 socket과 protocol 상태를 소유한다.

Visualizer는 Python process와 별도 render worker에서 실행한다. FastAPI는 HTTP와 WebSocket
I/O만 담당하며 VTK rendering과 JPEG encoding을 요청 event loop에서 수행하지 않는다.

두 server process는 bounded local scene stream으로 연결한다. Visualizer가 느리거나 종료돼도
Simulation Core와 S2E UDP endpoint는 계속 동작한다.

Visual adapter는 Browser용 사선 직교투영 frame과 camera용 원근 MJPEG frame을 같은
scene snapshot에서 만든다. Browser는 두 live 출력을 한 페이지에 표시한다. Camera
WebSocket은 descriptor를 먼저 보내고 완전한 JPEG binary frame을 30 Hz로 보낸다.

ARM64 Rust bridge는 server로 outbound WebSocket을 열고 최신 JPEG 1개를 V4L2 output에
기록한다. 느린 네트워크와 device는 backlog를 만들지 않고 느린 주기를 건너뛴다.
Simulation server는 scene과 frame을 디스크에 저장하지 않는다.

Visual renderer는 NVIDIA EGL을 선택할 수 있고 GPU가 없는 검증 환경에서 software
off-screen renderer로 동작한다. Render 프레임 교체와 bounded queue는 낮은 성능의
GPU에서도 scan 생성이 지연되지 않게 한다.

## Repository 경계

이 Repository는 Simulation Core, S2E adapter, Visualizer와 개발용 camera edge bridge를
같은 version으로 관리한다. 각 실행 구성 요소는 별도 OCI image로 배포한다.

`ajin-edge-platform`은 실제 SDK driver와 처리 서비스를 독립적으로 관리한다.
`scrap-monitoring-deployment`는 검증된 image digest와 환경별 실행 설정을 조합한다.
