# 프로젝트 작업 지침

## 적용 범위

이 파일은 이 저장소의 프로젝트 지침 단일 소스다. 전역 행동 지침을 함께 적용한다.
`AGENTS.md`, `GEMINI.md`와 `.claude/CLAUDE.md`는 이 파일을 가리키는 심링크다.

## 제품 경계

이 저장소는 개발 검증용 통합 simulation system을 관리한다. 하나의 canonical scene에서
RPLIDAR S2E 호환 UDP scan, Browser 시각화와 합성 camera stream을 생성한다. 실제 edge
platform의 SDK driver와 처리 서비스는 `ajin-edge-platform`이 소유한다.

기존 `scrap-monitoring-lidar-simulator`와 `scrap-monitoring-visualizer` 저장소는 동작하는
독립 제품으로 유지한다. 이 저장소 작업은 사용자의 별도 요청 없이 두 기존 저장소를 수정하거나
배포하지 않는다.

## 문서 정본

프로젝트 정본은 다음 5개다.

| 문서 | 책임 |
| --- | --- |
| [README](../README.md) | 제품 개요와 현재 사용 방법 |
| [프로젝트 명세](../docs/project-spec.md) | 제품 범위, 외부 경계와 완료 조건 |
| [아키텍처](../docs/architecture.md) | 상태 소유권, process와 package 경계 |
| [개발 계획](../docs/development-plan.md) | 현재 구현 상태와 작업 순서 |
| [구현 입력 기준](../docs/source-baselines.md) | 이관 입력의 Repository, commit과 적용 경계 |

작업을 시작할 때 프로젝트 명세와 개발 계획을 읽고 아키텍처 경계를 확인한다. 설계나 구현
상태가 달라지면 책임을 가진 정본만 갱신하고 같은 사실을 여러 문서에 복제하지 않는다.

## 조직 운영 규칙

GitHub 작업에는 조직 `.github` 저장소의 최신
[개발 운영 규칙](https://github.com/ajin-scrap-monitoring/.github/blob/main/GOVERNANCE.md),
[기여 절차](https://github.com/ajin-scrap-monitoring/.github/blob/main/CONTRIBUTING.md),
[보안 정책](https://github.com/ajin-scrap-monitoring/.github/blob/main/SECURITY.md),
[PR 템플릿](https://github.com/ajin-scrap-monitoring/.github/blob/main/PULL_REQUEST_TEMPLATE.md)과
[ruleset 적용 절차](https://github.com/ajin-scrap-monitoring/.github/blob/main/rulesets/README.md)를
적용한다. 공통 문서와 template을 이 저장소에 복제하지 않는다.

하나의 목표와 공동 완료 조건으로 검증할 수 있는 관련 변경은 하나의 Issue, 작업 branch와
Pull Request로 묶는다. 단발성 세부 항목마다 별도 Issue나 Pull Request를 만들지 않는다.
최신 `main` 기반 작업 branch, 검증, Pull Request와 squash merge 순서로 처리한다. 원격
`main`에 직접 Push하거나 보호 규칙을 우회하지 않는다.

## 데이터와 구현 경계

Public Repository에는 공개 사양과 공개 합성 입력만 둔다. 실제 사설 주소, 현장 치수, 센서
원본 packet, 영상, 운영 log와 자격 증명을 Commit하지 않는다. 실제 장비 관측 자료가 필요한
검증은 Git에서 제외한 로컬 입력을 사용하고 공개 fixture로 변환하지 않는다.

Simulation Core는 canonical scene과 simulation clock의 유일한 writer다. LiDAR와 renderer는
불변 scene snapshot을 소비하는 adapter다. Renderer 장애와 느린 Browser는 S2E UDP scan 생성에
backpressure를 전달하지 않는다.

## 작업 검증

변경 범위에 해당하는 자동 검사를 실행하고 실행하지 않은 검사를 완료로 표시하지 않는다.
CI는 에이전트 진입점과 변경 whitespace를 확인한다. 제품 구현의 build, 정적 검사와
test는 Container 경계에서 실행하고 Host에 언어 runtime을 사전 의존성으로 추가하지 않는다.
