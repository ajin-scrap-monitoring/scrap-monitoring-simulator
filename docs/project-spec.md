# Scrap Monitoring Simulator 프로젝트 명세

## 목적

이 프로젝트는 실제 edge platform의 Light Detection and Ranging (LiDAR)과 camera 입력 경계를 검증하는 개발용 simulation system을 제공한다. 하나의 canonical scene과 simulation clock을 모든 합성 출력의 기준으로 사용한다.

운영 Dashboard, Camera Media, Backend protocol, 인증, 경보 화면, 영상 저장과 replay는 범위가 아니다.

## 구성 요소

제품 구성 요소는 같은 위계의 service 3개다.

| 구성 요소 | 경로 | 실행 환경 | 책임 |
| --- | --- | --- | --- |
| Simulation server | `services/simulation-server` | Linux AMD64 server | 적재 상태, S2E UDP endpoint 2개와 scene segment |
| Visualizer | `services/visualizer` | Linux AMD64 server | WebGL Browser page와 합성 camera stream |
| Camera edge bridge | `services/camera-edge-bridge` | Linux ARM64 edge | 합성 camera stream의 V4L2 device 기록 |

실제 RPLIDAR SDK driver와 LiDAR 처리 서비스는 `ajin-edge-platform`의 외부 소비자다.

## 상태와 출력 경계

Simulation Core만 적재면과 simulation clock을 변경한다. S2E adapter와 Visualizer는 같은 불변 scene segment를 소비하며 Visualizer는 적재 상태를 독립적으로 생성하지 않는다.

Scene v2는 static definition과 self-contained 인접 keyframe segment로 구성한다. Static definition은 경계와 surface grid를 한 번 전송한다. Dynamic segment는 좌우 sequence, scenario와 전체 높이 배열을 함께 전송한다.

적재면은 넓게 분산되는 완만한 종형 퇴적 profile과 체적 보존 국소 요철을 조합한다. 최대 안식각은 거시 퇴적 형상에 적용하고 국소 요철은 그 이후에 합성한다. 평균 스크랩 크기를 반영한 요철 반경과 높이 범위는 공개 설정으로 관리한다.

Simulation server는 서로 다른 IPv4 주소와 같은 UDP 8089 port로 sensor별 독립 RPLIDAR S2E 호환
endpoint 2개를 제공한다. 각 LiDAR point는 point timestamp 이하인 가장 최근 10 Hz canonical
keyframe을 직접 선택한다. LiDAR는 30 FPS renderer 보간을 사용하지 않는다.

Browser는 WebGL 3D model과 합성 camera를 같은 페이지에서 표시한다. Visualizer는 하나의 30
FPS target에서 보간한 surface와 camera JPEG를 원자적 packet으로 전송한다. WebGL model은
고정 사선 직교투영, 오목한 적재 경계에 맞춘 surface, 바닥, 외벽, 적재물 측면과 높이 눈금을
유지한다. 배경과 바닥, 외벽 및 스크랩의 색상과 Physically Based Rendering (PBR) 재질은 synthetic camera profile을
사용하며 높이 colormap, conveyor와 chute는 표시하지 않는다. 각 투입 지점은 상단 구, 실제
triangle 보간 교차점까지의 수직선과 교차점 구로 나타낸다. 공통 scene 수치는 두 장면 아래에
표시하며 목표 적재율, runtime status와 diagnostics는 표시하지 않는다.

합성 camera scene의 고정 conveyor와 회전 chute는 상부가 열린 U자 단면이다. 고정 conveyor는
world Y축과 평행하며 chute는 두 투입 지점 사이를 최단 polar 경로로 보간한다. Pivot 주변의
연결 구간은 고정 conveyor 방향에서 chute 방향으로 단면과 중심선을 연속적으로 전이하며
끝단 구간만 아래로 꺾인다. 이 machine geometry는 합성 camera에만 적용한다.

Camera의 외부 출력 계약은 1920 x 1080, 30 FPS Motion JPEG (MJPEG)다. CPU renderer는 576 x
324 내부 raster를 만들고 재사용하는 Visualization Toolkit (VTK) linear scaler로 output
크기까지 확장한 뒤 Pillow로 JPEG를 한 번 encoding한다.

Deadline scheduler는 늦어진 주기를 누적하지 않고 최신 target만 처리한다. Browser는 decode 전 packet 1개, decode 중 frame 1개와 presentation 전 pair 1개만 유지한다. Renderer 장애와 느린 Browser는 edge bridge와 S2E UDP scan에 backpressure를 전달하지 않는다.

이 제품은 scene record, JPEG, 영상과 replay 파일을 저장하지 않고 live 출력만 제공한다.

## 공개 범위

공개 합성 설정, protocol 호환 코드와 자동 검증 자료만 Repository에 포함한다. 실제 현장 치수, 사설 주소, sensor packet capture, 영상, 운영 log와 자격 증명은 포함하지 않는다.

## 완료 조건

- 하나의 결정론적 scene에서 sensor 2대의 scan과 paired visual frame 생성
- 서로 다른 IPv4 주소와 같은 UDP 8089 port에서 고정 RPLIDAR SDK의 장비 정보, 상태, scan 시작과 HQ scan 수락
- 실제 edge driver를 변경하지 않은 sensor 2대 동시 수집
- 30 FPS WebGL Browser model과 1920 x 1080 합성 camera live stream 제공
- ARM64 V4L2의 frame, format, sequence와 timestamp 검증
- Latest-only queue, 장애 격리와 자원 상한 검증
- AMD64 및 ARM64 OCI image의 병렬 CI와 version release
