# 설정

## Simulation server

기본 설정은 `services/simulation-server/config/`의 3개 파일로 구성한다.

| 파일 | 책임 |
| --- | --- |
| `simulation-server.v1.json` | Seed, scenario, measurement와 참조 파일 경로 |
| `environment.v1.json` | 공개 합성 geometry와 sensor 2대 pose |
| `quality-profile.v1.json` | Sensor별 유효 및 무효 거리 quality 분포 |

참조 파일 경로는 주 설정 파일을 기준으로 해석한다. `SCRAP_SIMULATOR_CONFIG`는 주 설정 파일을 선택한다.

| 환경 변수 | 기본값 | 책임 |
| --- | --- | --- |
| `SCRAP_SIMULATOR_LIDAR_1_BIND` | `0.0.0.0:8089` | 첫 번째 S2E UDP bind |
| `SCRAP_SIMULATOR_LIDAR_2_BIND` | `0.0.0.0:8090` | 두 번째 S2E UDP bind |
| `SCRAP_SIMULATOR_SCENE_HOST` | `visualizer` | Scene stream 수신 host |
| `SCRAP_SIMULATOR_SCENE_PORT` | `17000` | Scene stream TCP port |
| `SCRAP_SIMULATOR_MEAN_FILL_DURATION_S` | `600` | 평균 적재 주기 override |

`check` command는 설정과 참조 파일을 읽고 sensor identifier 대응을 검증한다.

| 설정 | 기본값 | 책임 |
| --- | --- | --- |
| `mean_fill_duration_s` | `600` | 빠른 추세 확인을 위한 평균 적재 주기 |
| `cell_size_m` | `0.25` | Canonical height field 격자 간격 |
| `update_interval_s` | `0.1` | LiDAR scan 경계와 맞춘 적재면 갱신 간격 |
| `pile_spread_radius_m` | `0.9` | 완만한 종형을 유지하는 Gaussian profile 반경 |
| `roughness_height_range_m` | `[-0.45, 0.45]` | 체적을 보존하는 국소 요철 높이 범위 |
| `roughness_radius_range_m` | `[0.3, 0.65]` | 평균 0.3 m급 스크랩을 표현하는 국소 요철 반경 범위 |

## Visualizer

Visualizer의 `live` command는 TCP scene receiver와 HTTP server를 함께 실행한다.

| 환경 변수 | 배포값 | 책임 |
| --- | --- | --- |
| `SCRAP_MONITORING_VISUALIZER_TCP_HOST` | `0.0.0.0` | Scene stream bind host |
| `SCRAP_MONITORING_VISUALIZER_TCP_PORT` | `17000` | Scene stream bind port |
| `SCRAP_MONITORING_VISUALIZER_HTTP_HOST` | `0.0.0.0` | Browser와 camera bind host |
| `SCRAP_MONITORING_VISUALIZER_HTTP_PORT` | `18000` | Browser와 camera port |
| `SCRAP_MONITORING_VISUALIZER_CAMERA_ENABLED` | `true` | Synthetic camera pipeline 활성화 |
| `SCRAP_MONITORING_VISUALIZER_CAMERA_PROFILE` | Package 기본 profile | Camera profile 경로 |
| `SCRAP_MONITORING_VISUALIZER_CAMERA_BACKEND` | `osmesa` | `auto`, `osmesa` 또는 `egl` renderer |

Camera profile v1의 외부 출력은 1920 x 1080, 30 Frames Per Second (FPS) Motion JPEG (MJPEG)다. CPU camera renderer는 640 x 360 RGB raster를 만들고 재사용하는 Visualization Toolkit (VTK) linear scaler로 output 크기까지 확장한 뒤 Pillow로 JPEG를 한 번 encoding한다.

Browser는 `/visual/v1/stream`에서 camera profile의 배경, 외벽, 스크랩과 chute 색을 descriptor로 받고 WebGL model에 적용한다. Browser 3D model은 높이 colormap과 machine geometry를 사용하지 않는다. 투입 지점 표식은 적재면 교차점까지 수직선으로 연결한다. Browser page는 두 장면의 공통 scene 수치를 영상 아래에 표시하며 runtime status와 diagnostics는 표시하지 않는다.

| 설정 | 기본값 | 책임 |
| --- | --- | --- |
| `conveyor_width_m` | `1.0` | 고정 conveyor 전체 폭 |
| `outlet_width_m` | `0.9` | Chute 끝단 폭 |
| `conveyor_length_m` | `2.0` | Pivot 상류의 고정 conveyor 길이 |
| `conveyor_center_above_wall_m` | `1.25` | 외벽 상단 기준 machine 중심 높이 |
| `conveyor_body_height_m` | `0.24` | 고정 conveyor 전체 단면 높이 |
| `duct_height_m` | `0.24` | 회전 chute의 열린 U자 단면 전체 높이 |
| `tip_fraction` | `0.75` | 끝단 하강이 시작되는 길이 비율 |
| `tip_drop_m` | `0.08` | Chute 끝단 하강 높이 |

`/status`와 `/camera/v1/status`는 camera source cadence와 render 상태를 제공한다. Browser console의 `window.__scrapVisualMetrics.snapshot()`은 source, network, Browser와 queue의 현재 snapshot을 반환한다. `window.__scrapCameraMetrics`는 기존 Browser probe 호환 alias다.

## Camera edge bridge

Camera edge bridge는 다음 환경 변수만 읽는다.

| 환경 변수 | 기본값 | 책임 |
| --- | --- | --- |
| `SCRAP_SYNTHETIC_CAMERA_SERVER_URL` | 필수 | `/camera/v1/stream` WebSocket URL |
| `SCRAP_SYNTHETIC_CAMERA_DEVICE` | `/dev/scrap-synthetic-camera` | V4L2 output device |
| `SCRAP_SYNTHETIC_CAMERA_CONNECT_TIMEOUT_MS` | `5000` | TCP connect timeout |
| `SCRAP_SYNTHETIC_CAMERA_IO_TIMEOUT_MS` | `1000` | WebSocket와 device I/O timeout |
| `SCRAP_SYNTHETIC_CAMERA_RECONNECT_INITIAL_MS` | `500` | 최초 reconnect delay |
| `SCRAP_SYNTHETIC_CAMERA_RECONNECT_MAX_MS` | `30000` | 최대 reconnect delay |

URL은 credential과 query가 없는 `ws` scheme, 명시적 port와 고정 stream path를 사용한다.

## 환경 파일과 자격 증명

`deploy/server/.env.example`과 `deploy/edge/.env.example`은 공개 가능한 image digest, endpoint, device와 timeout 설정의 schema다. 실제 실행값은 Git에서 제외한 `.env`에 둔다. 현재 service가 읽는 runtime credential은 없고 Compose도 Docker secret을 선언하지 않는다.

인증 도입 시 Compose는 host 비밀 파일을 Docker secret으로 `/run/secrets`에 mount한다. Service는 `*_FILE` 경로만 읽고 평문 credential 환경 변수, `.env.example`, image와 Git 이력의 credential 값을 거부한다.
