# 개발 계획

## 현재 상태

Repository bootstrap, 제품 경계와 아키텍처 기준이 구성돼 있다. Simulation, UDP, rendering과
edge bridge 구현은 아직 포함하지 않는다.

## 작업 순서

전체 구현은 다음 6개 단계로 진행한다.

| 단계 | 작업 단위 | 완료 조건 |
| --- | --- | --- |
| P0 | Repository와 CI 기준 | Agent 구조, CI, CodeQL과 ruleset 검증 |
| P1 | Canonical scene 계약 | 정적 definition, 동적 frame과 결정론 검사 |
| P2 | Simulation Core | 적재 상태와 센서 2대 scan 생성 |
| P3 | S2E UDP adapter | 고정 SDK command와 scan 수락 검사 |
| P4 | Visualizer와 camera | Browser 및 camera live 출력 검사 |
| P5 | Edge 및 release | 실제 driver, V4L2, image와 release 검증 |

각 단계는 대상 Repository Issue와 Pull Request로 완료한다. 기존 저장소의 코드는 출처와
현재 동작을 확인한 뒤 필요한 구현만 명시적으로 이관한다.

## 다음 작업

P0가 완료되면 P1 Issue를 시작한다. P1은 [구현 입력 기준](source-baselines.md)의
commit을 검증하고 다음 4개 계약을 먼저 고정한다.

1. World XYZ의 정적 scene definition
2. Simulation clock이 포함된 불변 scene frame
3. Sensor pose와 S2E adapter가 소비하는 scan 입력
4. Visual adapter가 소비하는 latest-only scene stream

P1에서는 network server, VTK renderer와 edge bridge를 구현하지 않는다. 계약과 결정론적
fixture를 검증한 뒤 P2와 P3에서 동일한 frame을 소비한다.
