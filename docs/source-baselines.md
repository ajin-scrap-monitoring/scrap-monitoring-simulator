# 구현 입력 기준

## 고정 입력

구현 검토에 사용하는 고정 입력은 다음 4개다.

| 입력 | Commit | 검토 범위 |
| --- | --- | --- |
| [`scrap-monitoring-lidar-simulator`](https://github.com/ajin-scrap-monitoring/scrap-monitoring-lidar-simulator) | `fedd2ddb4110fce71508acd6a5b339aef63a178f` | Canonical scene, 적재 시나리오, 광선 교차, 측정 오차와 HQ 정수 변환 |
| [`scrap-monitoring-visualizer`](https://github.com/ajin-scrap-monitoring/scrap-monitoring-visualizer) | `2d6fcbfe92cdaa95ed4f8d42fff182aa4f4d4e6f` | VTK scene, camera profile과 보간, MJPEG WebSocket과 Rust V4L2 bridge |
| [`rplidar_sdk`](https://github.com/Slamtec/rplidar_sdk) | `99478e5fb90de3b4a6db0080acacd373f8b36869` | RPLIDAR SDK 2.1.0 API, HQ node와 UDP channel |
| [`ajin-edge-platform`](https://github.com/ajin-scrap-monitoring/ajin-edge-platform) | `666ca6067a3bb86833b74140cb659049025d0dae` | 실제 driver의 SDK 호출, scan 변환과 처리 계약 |

Commit은 이관 대상을 재현할 수 있게 하는 기준이다. 후속 작업에서 upstream을
갱신하면 영향받는 계약과 검증을 같은 Pull Request에서 갱신한다.

## 적용 경계

기존 두 제품 Repository는 읽기 전용 구현 입력이다. 필요한 코드와 계약은 대상 Issue와
Pull Request에서 출처 Repository, commit과 경로를 확인한 뒤 이 Repository로 이관한다.
기존 Repository의 소스, 이력, Release와 배포 상태는 변경하지 않는다.

LiDAR 실행 경계는 기존 gRPC over UDS 출력을 사용하지 않고, 고정한 SDK가
Simulation server의 sensor별 S2E UDP endpoint에 직접 연결하는 구조다. Visualizer는 기존
Observation TCP를 제품 간 계약으로 유지하지 않고 Simulation Core의 불변 scene snapshot을
내부 stream으로 소비한다.

현장 자료, 사설 주소, 운영 설정과 자격 증명은 이관 입력에 포함하지 않는다.
