# 구현 입력 기준

## 고정 입력

구현 검토에 사용하는 고정 입력은 다음 4개다.

| 입력 | Commit | 적용 경계 |
| --- | --- | --- |
| [`scrap-monitoring-lidar-simulator`](https://github.com/ajin-scrap-monitoring/scrap-monitoring-lidar-simulator) | `fedd2ddb4110fce71508acd6a5b339aef63a178f` | `services/simulation-server`의 scene, scenario, 광선 측정과 HQ 정수 변환 |
| [`scrap-monitoring-visualizer`](https://github.com/ajin-scrap-monitoring/scrap-monitoring-visualizer) | `2d6fcbfe92cdaa95ed4f8d42fff182aa4f4d4e6f` | `services/visualizer`의 VTK 및 camera 경계와 `services/camera-edge-bridge`의 V4L2 bridge |
| [`rplidar_sdk`](https://github.com/Slamtec/rplidar_sdk) | `99478e5fb90de3b4a6db0080acacd373f8b36869` | `services/simulation-server/sdk-acceptance`의 공식 SDK 수락 client |
| [`ajin-edge-platform`](https://github.com/ajin-scrap-monitoring/ajin-edge-platform) | `666ca6067a3bb86833b74140cb659049025d0dae` | 실제 driver의 SDK 호출, scan 변환과 처리 계약 |

Commit은 이관 대상을 재현하는 기준이다. Upstream 기준을 갱신할 때는 영향받는 계약,
구현과 검증을 같은 Pull Request에서 갱신한다.

## 적용 경계

기존 두 제품 Repository는 읽기 전용 구현 입력이다. 이 Repository는 필요한 source와 계약만
이관하며 기존 Repository의 source, history, Release와 배포 상태를 변경하지 않는다.

LiDAR 실행 경계는 기존 gRPC over UDS 출력을 사용하지 않고 고정 SDK가 Simulation server의
sensor별 S2E UDP endpoint에 직접 연결하는 구조다. Visualizer는 기존 Observation TCP를 제품
간 계약으로 유지하지 않고 Simulation Core의 불변 scene stream을 소비한다.

현장 자료, 사설 주소, 운영 설정과 자격 증명은 이관 입력에 포함하지 않는다.
