# Scrap Monitoring Simulator

스크랩 적재 모니터링 개발을 위한 통합 simulation system의 저장소다. 하나의 canonical
scene을 기준으로 RPLIDAR S2E 호환 입력, Browser 시각화와 합성 camera 입력을 제공하는
구조를 정의한다. 실제 edge platform과 운영 서비스는 이 저장소의 범위가 아니다.

## 빠른 시작

Python 3.11 이상이 필요하다. Checkout한 저장소 루트에서 다음 명령으로 현재 구조를
검증한다.

```bash
python3 tools/check_repository.py
```

## 개발 및 검증

문서와 에이전트 진입점을 변경한 뒤 같은 Repository 검사를 실행한다. 제품 구현에 필요한
빌드, 정적 검사와 테스트 명령은 구현이 추가될 때 해당 정본 문서에서 관리한다.

## 문서

| 문서 | 역할 |
| --- | --- |
| [프로젝트 명세](docs/project-spec.md) | 제품 범위, 외부 경계와 완료 조건 |
| [아키텍처](docs/architecture.md) | 상태 소유권과 실행 구성 요소 경계 |
| [개발 계획](docs/development-plan.md) | 현재 구현 상태와 작업 순서 |
| [구현 입력 기준](docs/source-baselines.md) | 이관 입력의 고정 commit과 적용 경계 |
| [프로젝트 작업 지침](.agents/AGENTS.md) | Repository 작업과 검증 규칙 |

## 이용 조건

이 Repository는 코드 검토와 참고를 위해 Public으로 제공하며 프로젝트 소스 코드에 별도
라이선스를 부여하지 않는다.
