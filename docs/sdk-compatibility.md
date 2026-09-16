# RPLIDAR SDK 호환 경계

Simulation server의 각 UDP endpoint는 고정 RPLIDAR SDK가 사용하는 다음 동작을 지원한다.

| 동작 | 응답 |
| --- | --- |
| Device information | Sensor identifier 기반 고정 serial과 configuration 지원 firmware |
| Device health | 정상 health |
| Typical scan mode | HQ mode identifier 2 |
| Mode configuration | Sample period, 최대 거리, answer type과 mode name |
| Desired rotation frequency | Simulation measurement 설정 기반 RPM |
| Express scan start | HQ loop response와 capsule stream |
| Stop 및 reset | 현재 client stream 종료 |

HQ capsule은 scan identifier, node 96개와 SDK 방식 CRC32를 포함한다. Endpoint는 sensor별 최신
scan만 유지하며 느린 client 때문에 누락된 scan을 backlog로 전송하지 않는다.

`services/simulation-server/sdk-acceptance`는 고정 SDK source를 checksum과 commit으로 내려받아
두 UDP endpoint에 동시에 연결한다. 수락 검사는 device information, health, typical scan
시작, HQ node 수신, angle 정렬과 실제 거리 node 존재를 확인한다. 이 경계는 전체 S2E
protocol 구현이 아니라 `ajin-edge-platform`의 고정 SDK 호출 경로를 위한 호환 범위다.
