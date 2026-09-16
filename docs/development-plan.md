# 개발 계획

## 현재 상태

Issue #3의 초기 통합 이관 범위가 P0부터 P5까지 하나의 구현 단위로 구성돼 있다. 각 단계의
현재 산출물은 다음과 같다.

| 단계 | 구현 | 검증 경계 |
| --- | --- | --- |
| P0 | Repository 구조, CI와 GitHub default CodeQL | Agent entrypoint, workflow와 whitespace 검사 |
| P1 | Canonical scene definition과 frame 계약 | JSON Schema, fixture와 strict parser 검사 |
| P2 | Simulation Core와 sensor 2대 측정 | 결정론, geometry, scenario와 동시 worker test |
| P3 | S2E UDP adapter | 고정 RPLIDAR SDK의 sensor 2대 동시 수락 검사 |
| P4 | Visualizer와 camera | Browser, renderer와 1920 x 1080 30 FPS MJPEG 검사 |
| P5 | ARM64 bridge, 배포와 release | ARM64 build, V4L2 ABI, Compose와 release workflow 검사 |

`scripts/check.sh`가 3개 service의 test 및 runtime image를 대상 architecture로 build하고
runtime probe와 통합 scene 흐름을 검사한다. 물리 V4L2 device의 90 frame 검사는 배포 대상
ARM64 host에서 `deploy/edge/check-90-frames.sh`로 수행한다.

## 변경 순서

후속 기능과 설정 변경은 다음 순서를 따른다.

1. 책임을 가진 계약 또는 service test 갱신.
2. 대상 architecture의 Container build와 test 실행.
3. Server scene, Browser와 camera 통합 흐름 검사.
4. 책임을 가진 정본 문서 갱신.
5. Issue를 닫는 Pull Request 생성.
