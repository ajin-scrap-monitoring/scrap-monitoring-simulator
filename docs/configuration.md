# 설정

## Simulation server

기본 설정은 `services/simulation-server/config/`의 다음 3개 파일로 구성한다.

| 파일 | 책임 |
| --- | --- |
| `simulation-server.v1.json` | Seed, scenario, measurement와 참조 파일 경로 |
| `environment.v1.json` | 공개 합성 geometry와 sensor 2대 pose |
| `quality-profile.v1.json` | Sensor별 유효 및 무효 거리 quality 분포 |

참조 파일 경로는 주 설정 파일을 기준으로 해석한다. `SCRAP_SIMULATOR_CONFIG`는 주 설정
파일을 선택한다. Runtime endpoint는 다음 환경 변수로 설정한다.

| 환경 변수 | 기본값 | 책임 |
| --- | --- | --- |
| `SCRAP_SIMULATOR_LIDAR_1_BIND` | `0.0.0.0:8089` | 첫 번째 S2E UDP bind |
| `SCRAP_SIMULATOR_LIDAR_2_BIND` | `0.0.0.0:8090` | 두 번째 S2E UDP bind |
| `SCRAP_SIMULATOR_SCENE_HOST` | `visualizer` | Scene stream 수신 host |
| `SCRAP_SIMULATOR_SCENE_PORT` | `17000` | Scene stream TCP port |
| `SCRAP_SIMULATOR_SCENE_INTERVAL_S` | `1.0` | Scene snapshot 간격 |

`check` command는 설정과 참조 파일을 읽고 sensor identifier 대응을 검증한다.

기본 적재면 설정은 다음과 같다.

| 설정 | 기본값 | 책임 |
| --- | --- | --- |
| `cell_size_m` | `0.25` | Canonical height field 격자 간격 |
| `update_interval_s` | `0.5` | 적재면 갱신 간격 |
| `pile_spread_radius_m` | `1.0` | 투입량을 넓게 분산하는 Gaussian profile 반경 |
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

Package 기본 profile은 1920 x 1080, 30 FPS MJPEG와 최대 JPEG 4,194,304 byte를 정의한다.
Camera가 활성화되면 profile의 배경, 바닥, 벽과 적재물 색을 Browser 3D 모델에도 적용한다.
Browser 3D 모델은 높이별 colormap을 사용하지 않는다. 투입 지점 표식은 적재면 교차점까지
수직선으로 연결한다. Browser 페이지는 두 장면의 공통 scene 수치를 영상 아래에 표시하고
runtime 상태는 표시하지 않는다.

## Camera edge bridge

Camera edge bridge는 다음 환경 변수만 읽는다.

| 환경 변수 | 기본값 | 책임 |
| --- | --- | --- |
| `SCRAP_SYNTHETIC_CAMERA_SERVER_URL` | 필수 | `/camera/v1/stream` WebSocket URL |
| `SCRAP_SYNTHETIC_CAMERA_DEVICE` | `/dev/scrap-synthetic-camera` | V4L2 output device |
| `SCRAP_SYNTHETIC_CAMERA_CONNECT_TIMEOUT_MS` | `5000` | TCP connect timeout |
| `SCRAP_SYNTHETIC_CAMERA_IO_TIMEOUT_MS` | `1000` | WebSocket 및 device I/O timeout |
| `SCRAP_SYNTHETIC_CAMERA_RECONNECT_INITIAL_MS` | `500` | 최초 reconnect delay |
| `SCRAP_SYNTHETIC_CAMERA_RECONNECT_MAX_MS` | `30000` | 최대 reconnect delay |

URL은 credential과 query가 없는 `ws` scheme, 명시적 port와 고정 stream path를 사용한다.
