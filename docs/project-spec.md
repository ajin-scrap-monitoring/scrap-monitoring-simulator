# Scrap Monitoring Simulator 프로젝트 명세

## 목적

이 프로젝트는 실제 edge platform의 LiDAR와 camera 입력 경계를 검증할 수 있는 개발용
simulation system을 제공한다. 하나의 canonical scene과 simulation clock을 모든 합성 출력의
기준으로 사용한다.

## 구성 요소

제품 구성 요소는 다음 3개다.

| 구성 요소 | 실행 환경 | 책임 |
| --- | --- | --- |
| Simulation server | Linux AMD64 server | 적재 상태, S2E UDP endpoint 2개와 scene snapshot |
| Visualizer server | Linux AMD64 server | 3D Browser 화면과 합성 camera stream |
| Camera edge bridge | Linux ARM64 edge | 합성 camera stream의 V4L2 device 기록 |

실제 RPLIDAR SDK driver, LiDAR 처리와 운영 camera service는 `ajin-edge-platform`의 외부
소비자다.

## 상태와 출력 경계

Simulation Core만 적재면과 simulation clock을 변경한다. S2E adapter와 Visualizer는 같은
불변 scene snapshot을 소비한다. Visualizer는 적재 상태를 독립적으로 생성하지 않는다.

Simulation server는 센서별 독립 RPLIDAR S2E 호환 UDP endpoint를 제공한다. 실제 edge
driver는 합성 전용 bridge 없이 공식 SDK 경로로 endpoint에 연결한다.

Visualizer는 정적 scene definition을 한 번 구성하고 동적 scene frame으로 표면 높이와
장면 상태를 갱신한다. Browser와 camera client의 처리 지연은 simulation과 UDP scan을
중단시키지 않는다.

## 공개 범위

공개 합성 설정, protocol 호환 코드와 자동 검증 자료만 Repository에 포함한다. 실제 현장
치수, 사설 주소, 센서 packet capture, 영상, 운영 log와 자격 증명은 포함하지 않는다.

## 완료 조건

- 하나의 결정론적 scene에서 센서 2대의 scan과 영상 상태 생성
- 고정한 RPLIDAR SDK를 통한 장비 정보, 상태, scan 시작과 HQ scan 검증
- 실제 edge driver를 변경하지 않은 센서 2대 동시 수집
- Browser 3D 화면과 합성 camera live stream 제공
- ARM64 V4L2 camera 입력 검증
- bounded queue, 장애 격리와 자원 상한 검증
- AMD64 및 ARM64 OCI image의 CI와 version release
