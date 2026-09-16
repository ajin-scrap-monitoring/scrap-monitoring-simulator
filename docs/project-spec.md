# Scrap Monitoring Simulator 프로젝트 명세

## 목적

이 프로젝트는 실제 edge platform의 Light Detection and Ranging (LiDAR)과 camera 입력 경계를
검증하는 개발용 simulation system을 제공한다. 하나의 canonical scene과 simulation clock을
모든 합성 출력의 기준으로 사용한다.

운영 Dashboard, Camera Media와 Backend protocol은 이 제품의 범위가 아니다.

## 구성 요소

제품 구성 요소는 같은 위계의 service 3개다.

| 구성 요소 | 경로 | 실행 환경 | 책임 |
| --- | --- | --- | --- |
| Simulation server | `services/simulation-server` | Linux AMD64 server | 적재 상태, S2E UDP endpoint 2개와 scene snapshot |
| Visualizer | `services/visualizer` | Linux AMD64 server | 3D Browser 화면과 합성 camera stream |
| Camera edge bridge | `services/camera-edge-bridge` | Linux ARM64 edge | 합성 camera stream의 V4L2 device 기록 |

실제 RPLIDAR SDK driver와 LiDAR 처리 서비스는 `ajin-edge-platform`의 외부 소비자다.

## 상태와 출력 경계

Simulation Core만 적재면과 simulation clock을 변경한다. S2E adapter와 Visualizer는 같은
불변 scene snapshot을 소비하며 Visualizer는 적재 상태를 독립적으로 생성하지 않는다.

Simulation server는 sensor별 독립 RPLIDAR S2E 호환 UDP endpoint를 제공한다. 실제 edge
driver는 합성 전용 LiDAR bridge 없이 공식 SDK 경로로 endpoint에 연결한다.

적재면은 투입 지점 주위에 넓게 분산되는 완만한 퇴적 profile과 크기가 다른 결정론적 국소
요철을 조합한다. 최대 안식각은 거시 퇴적 형상에 적용하고 국소 요철은 그 이후에 합성한다.
적재량 보존과 scene 높이 경계는 모든 갱신에 적용한다.

Browser는 고정 사선 직교투영 3D 모델과 현장 camera 시점의 live 영상을 한 페이지에
표시한다. 3D 모델은 camera와 같은 색을 사용하고 높이 colormap을 사용하지 않는다. 각 투입
지점은 상단 표식, 적재면까지의 수직선과 교차점 표식으로 나타낸다. 두 영상의 공통 scene
수치는 영상 아래에 표시한다.

합성 camera scene은 world Y축과 평행한 U자 단면의 고정 conveyor와 두 투입 지점 사이를
회전하는 chute를 포함한다. Chute는 표시하지 않는 pivot과 겹침 socket을 기준으로 연결하고
scene 보간 구간에서 부드럽게 회전한다. 끝단 구간만 아래로 꺾인다. 이 machine geometry는
합성 camera에만 적용하며 1 Hz Browser 3D PNG에는 포함하지 않는다.

Camera의 외부 출력 계약은 1920 x 1080, 30 Frames Per Second (FPS) Motion JPEG (MJPEG)다.
CPU renderer는 640 x 360 내부 raster를 만들고 재사용하는 VTK linear scaler로 output 크기까지
확장한 뒤 Pillow로 JPEG를 한 번 encoding한다.
Deadline scheduler는 늦어진 주기를 누적하지 않고 최신 scene만 처리한다. WebSocket은 새
revision의 JPEG만 전송하고 Browser는 decode와 canvas presentation마다 최신 frame 1개만
유지한다. 느린 Browser는 renderer, edge bridge와 S2E UDP scan에 backpressure를 전달하지
않는다.

30 FPS 판정은 서로 다른 revision의 고유 frame을 기준으로 한다. 같은 JPEG를 반복해서
전송하거나 다시 표시한 횟수는 고유 frame에 포함하지 않는다. Source 생성, WebSocket 수신,
Browser decode와 presentation, V4L2 출력 지표는 단계별로 구분한다. 진단값은 status,
integration probe와 Browser diagnostic object에서만 사용하며 개발용 화면에는 표시하지 않는다.

이 제품은 scene record, JPEG, 영상과 replay 파일을 저장하지 않고 live 출력만 제공한다.

## 공개 범위

공개 합성 설정, protocol 호환 코드와 자동 검증 자료만 Repository에 포함한다. 실제 현장
치수, 사설 주소, sensor packet capture, 영상, 운영 log와 자격 증명은 포함하지 않는다.

## 완료 조건

- 하나의 결정론적 scene에서 sensor 2대의 scan과 영상 상태 생성
- 고정 RPLIDAR SDK의 장비 정보, 상태, scan 시작과 HQ scan 수락
- 실제 edge driver를 변경하지 않은 sensor 2대 동시 수집
- 3D Browser 화면과 1920 x 1080 합성 camera live stream 제공
- 고유 frame 기준 30 FPS와 단계별 cadence 및 drop 진단
- ARM64 V4L2의 frame, format, sequence와 timestamp 검증
- Latest-only queue, 장애 격리와 자원 상한 검증
- AMD64 및 ARM64 OCI image의 병렬 CI와 version release
