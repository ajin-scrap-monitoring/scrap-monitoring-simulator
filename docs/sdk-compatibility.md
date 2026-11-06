# RPLIDAR SDK 호환 경계

Simulation server의 각 UDP endpoint는 서로 다른 IP와 같은 UDP 8089 port를 사용하며 고정
RPLIDAR SDK가 사용하는 다음 동작을 지원한다.

| 동작 | 응답 |
| --- | --- |
| Device information | Sensor identifier 기반 고정 serial과 configuration 지원 firmware |
| Device health | 정상 health |
| Typical scan mode | HQ mode identifier 2 |
| Mode configuration | Sample period, 최대 거리, answer type과 mode name |
| Desired rotation frequency | Simulation measurement 설정 기반 RPM |
| Express scan start | HQ loop response와 capsule stream |
| Stop 및 reset | 현재 client stream 종료 |

HQ capsule은 첫 node의 scan identifier, 실제 node 96개와 SDK 방식 CRC32를 포함한다. 회전과
패킷 경계는 독립적이며, 회전의 첫 node에 붙는 시작 flag는 패킷 중간에도 올 수 있다.
회전 끝의 미완성 tail은 최대 95개 실제 node만 보관해 다음 연속 회전으로 채운다. 빈칸을 0거리
node로 전송하거나 실제 0거리·0도 node를 제거하지 않는다. 중지·재시작, 수신 peer 변경 또는
scan ID 단절 시 tail을 비워 이전 스트림의 node를 새 스트림에 섞지 않는다. Endpoint는
sensor별 최신 scan만 유지하며 느린 client 때문에 누락된 scan을 복제하거나 backlog로 전송하지 않는다.

`services/simulation-server/sdk-acceptance`는 고정 SDK source를 checksum과 commit으로 내려받아
두 UDP endpoint에 동시에 연결한다. 수락 검사는 device information, health, typical scan
시작, HQ node 수신, angle 정렬, 실제 거리 node 존재와 endpoint별 serial 상이를 확인한다.
공개 기본 설정(32,000 samples/s, 10 rotations/s)에서 각 endpoint는 연속 4회전 수집 후
중지·재시작하여 다시 4회전을 검사하며, 매 회전의 node 수가 정확히 3,200개여야 한다.
수락 입력은 서로 다른 non-wildcard IPv4 주소와 공통 UDP 8089 port만 허용한다. 이 경계는
전체 S2E protocol 구현이 아니라 `ajin-edge-platform`의 고정 SDK 호출 경로를 위한 호환 범위다.
