# financial_n2sf — 금융권 N2SF 역할 기반 마이크로세그멘테이션 실험환경

이 저장소는 한국정보보호학회논문지 투고 논문("금융권 N2SF 시스템·업무별 모델 기반 보안통제
설계 및 역할 기반 마이크로세그멘테이션 검증")의 3.4절(실험 환경 및 검증 방법), 4장(실험 결과 및
분석)에 필요한 실험을 재현하기 위한 프로토타입이다. 선행 연구(Track 1)가 이미 제안한 **금융권
N2SF 시스템·업무별 모델**을 정답(ground truth)으로 두고, 그 위에서 역할 기반 마이크로세그멘테이션
정책을 OPA/Rego로 작성·집행하며, 정상 업무흐름의 유지와 비인가 통신·횡적 이동의 차단 효과를
정량적으로 검증한다.

## 목차

1. [연구 범위와 방법론](#1-연구-범위와-방법론)
2. [실험 환경 구성](#2-실험-환경-구성)
3. [정책 모델과 5튜플 권한 테이블](#3-정책-모델과-5튜플-권한-테이블)
4. [HMAC 워크로드 신원 검증](#4-hmac-워크로드-신원-검증)
5. [비교 환경: 2x2 ablation 설계](#5-비교-환경-2x2-ablation-설계)
6. [사전 준비](#6-사전-준비)
7. [실험 실행](#7-실험-실행)
8. [시나리오 및 자산 구성](#8-시나리오-및-자산-구성)
9. [성능 측정 구조와 라운드 설계](#9-성능-측정-구조와-라운드-설계)
10. [분석 및 산출물](#10-분석-및-산출물)
11. [평가 지표 정의](#11-평가-지표-정의)
12. [통계 처리 원칙](#12-통계-처리-원칙)
13. [감사로그 필드](#13-감사로그-필드)
14. [정책 유닛 테스트 및 재현성 메타데이터](#14-정책-유닛-테스트-및-재현성-메타데이터)
15. [해석 시 주의사항](#15-해석-시-주의사항)

## 1. 연구 범위와 방법론

NSDI 2025의 ZTS(Zero Trust Segmentation)가 제안한 **통신 그래프 기반 역할 마이크로세그멘테이션,
역할 수준 정책, 정책 위반 평가** 절차를 연구 목적에 맞게 축약·적용한다.

- ZTS 원 논문은 흐름 텔레메트리로 역할을 추론한다.
- 본 연구는 Track 1에서 업무별 역할과 시스템 경계가 이미 정의되었다고 가정하므로 역할 추론
  단계는 수행하지 않는다.
- 대신 Track 1의 업무 역할을 정답 레이블로 사용해 허용 통신 그래프(5-튜플 permission)를
  작성하고, 이를 OPA/Rego 정책으로 구현·집행한다.
- 따라서 본 실험은 ZTS 전체 알고리즘의 재현이 아니라 **역할 기반 정책 작성·집행·위반 검증
  절차의 적용 연구**다.

이 패키지는 결과 값을 포함하지 않는다. `results/templates/`에는 실험 후 채울 수 있는 빈 헤더만
들어 있고, 실제 raw CSV·감사로그·그래프는 아래 절차로 실행해야 생성된다.

**실험 설계는 baseline/proposed 2조건 비교가 아니라 2×2 ablation이다.** 통제요소가 "①업무별
네트워크 분리"와 "②역할 기반 정책 세분화" 두 가지인데, 2조건 비교만으로는 두 요소가 동시에
바뀌므로 결과가 "스위치 하나로 전부 바뀐" 것처럼 보이는 서사적 약점이 있다. 이를 분해하기 위해
중간 조건(`policy_only`, `segmentation_only`)을 추가한 4모드 구조로 실험한다(§5).

**보안효과 지표는 개별 요청의 allow/deny 비율이 아니라 유효 통신 그래프(Effective Communication
Graph)의 구조적 노출범위로 측정한다(§11).** 접근제어·TCP 도달성 판정 자체는 결정론적이다 —
동일한 시나리오를 반복하면 항상 같은 결과가 나오는 것이 정상이다. 문제는 이를 요약하는 지표를
"몇 %가 allow/deny였는가"로 잡으면 결과가 0%/100%에 몰려 임의로 짜맞춘 것처럼 보일 위험이
있다는 점이다. 이를 해소하기 위해 Basta et al.(NOMS 2022)이 제안한 **AOD(Average Out-Degree)**
/**MPL(Mean shortest Path Length)**/**TINR(Transitive Internal Network Reachability)** 로
"침해 시 도달 가능한 범위가 통제 적용 전후로 얼마나 줄어드는가"를 그래프 구조로 측정한다(§11).
AOD는 Network/Policy/Effective 세 계층 각각에서 따로 계산하므로, Network AOD는 네트워크 분리
축에만, Policy AOD는 정책 세분화 축에만 반응하는 패턴 자체가 "각 통제요소가 자기 역할만 정확히
수행한다"는 근거가 된다 — 축별 평균을 별도로 계산하지 않아도 계층을 나눈 것만으로 같은 서사를
보여준다.

## 2. 실험 환경 구성

가상 금융기관 A의 시스템·업무별 구조(논문 Table 1, Fig. 1)를 반영해 다음 5개 업무를 S영역에
배치한다.

| 업무 | 컨테이너 | 논문상 역할 |
|---|---|---|
| 고객정보 | `customer_app` / `customer_db` | 고객 기본정보 관리 |
| 여신심사 | `loan_app` / `loan_db` | 심사 업무의 시작점(요청 주체) |
| 신용평가 | `credit_app` / `credit_db` | 여신심사 요청에 대한 신용평가 수행 |
| 자금세탁방지 | `aml_app` / `aml_db` | 여신심사 요청에 대한 AML 심사 수행 |
| 승인 | `approval_app` / `approval_db` | 여신심사 결과를 승인·반려 |

O영역에는 공개 서비스(`public_app`)와 외부 생성형 AI 모의 서비스(`external_ai`)를 둔다.

통제 구성요소:

- **PEP / API Gateway (`pep`)**: 정상적인 S영역 업무 간 호출에서 정책판단 및 집행을 수행하는
  통제 경로. Flat 환경(`baseline`/`policy_only`)에서는 PEP를 우회하는 직접 통신경로가 네트워크
  계층에 남아 있을 수 있으며, Segmented 환경(`segmentation_only`/`proposed`)에서는 업무별
  네트워크 분리가 이 직접경로 자체를 제거한다 — 이 우회 가능성 자체를 Network Graph(§11)로
  측정한다.
- **PDP (`opa`)**: 사용자, 역할, 단말 신뢰, 출발/목적 업무, 행위, 목적, 정보등급을 평가한다.
- **Transfer CDS (`transfer_cds`)**: S/O 경계에서 승인·등급·목적·콘텐츠·무결성을 검증한다.
  본 연구의 주 검증대상인 내부 마이크로세그멘테이션에 대한 **보조 검증**이며, 실험 전
  사전검증에만 쓰인다(§8.2).
- **JSONL 감사로그**: PEP·CDS·업무 서비스 각각이 요청/결정/지연시간을 기록한다(§13).

### 포트 번호와 서버 표현

서로 다른 호스트 포트만 부여한 프로세스를 각각 독립 서버라고 표현하지 않는다. 포트 번호는 접속
지점을 구분할 뿐 프로세스·파일시스템·네트워크 이름공간·데이터 저장영역을 분리하지 않기
때문이다. 본 환경은 각 업무시스템과 데이터베이스를 별도 Docker 컨테이너로 실행하고, 업무별
Docker 네트워크를 분리한다. 즉 "단일 물리 호스트에서 컨테이너별 독립 프로세스·파일시스템·
네트워크 이름공간을 부여해 복수의 논리 서버 인스턴스를 구성"한 것으로 취급한다.

호스트에 노출되는 포트:

| 포트 | 대상 | 용도 |
|---|---|---|
| 18080 | `pep` | 정책 집행 지점(모든 업무 간 호출이 통과) |
| 18090 | `transfer_cds` | S/O 경계 전송(사전검증용) |
| 18181 | `opa` | 정책 조회(디버깅용) |
| 18001–18005 | `customer_app`~`approval_app` | 각 업무 워크로드의 `/call`(§4 참고). 실험 하네스가 "실제 출발 업무"로서 요청을 만들기 위한 진입점이며, 운영 환경의 접근경로가 아니다 |

도달성 전수검사(§8.4)는 호스트 포트가 아니라 컨테이너 내부 포트(App 8000, DB 5432)를 컨테이너
네트워크 네임스페이스 안에서 직접 검사하므로 이 표와는 별개다.

## 3. 정책 모델과 5튜플 권한 테이블

논문 3.3절은 정책의 허용 여부를 "**사용자 역할, 출발 업무, 목적 업무, 요청 행위 및 업무목적**"의
관계로 판단한다고 명시한다. `policies/data.json`의 `permissions` 배열이 이 5-튜플의 정답
테이블이고, `policies/proposed.rego`는 요청이 이 튜플 중 하나와 **정확히 일치**할 때만
허용한다(default-deny).

정상 업무흐름 5건(여신심사가 고객정보를 조회하고 신용평가·AML을 요청하며, 심사결과를 승인
업무로 넘기고, 승인자가 심사자료를 조회하는 흐름):

| # | 역할 | 출발 업무 | 목적 업무 | 행위(action) | 목적(purpose) |
|---|---|---|---|---|---|
| 1 | `loan_reviewer` | loan | customer | `read_customer_profile` | `loan_screening` |
| 2 | `loan_reviewer` | loan | credit | `request_credit_assessment` | `loan_screening` |
| 3 | `loan_reviewer` | loan | aml | `request_aml_screening` | `loan_screening` |
| 4 | `loan_reviewer` | loan | approval | `submit_for_approval` | `loan_approval` |
| 5 | `loan_approver` | approval | loan | `read_review_package` | `loan_approval` |

신용평가·AML의 결과가 HTTP 응답으로 loan에 돌아오는 것은 별도의 업무 요청 edge로 모델링하지
않는다(요청과 응답을 중복 모델링하지 않기 위함).

행위(action)는 클라이언트가 자칭하지 않는다. `services/app/main.py`는 `/records`처럼 뭉뚱그린
엔드포인트 대신 업무별 엔드포인트를 노출하고, PEP(`services/pep/main.py`)가 `(HTTP method,
path)` 조합을 정규식 테이블로 매칭해 canonical action을 서버 측에서 결정한다. 이 매핑은
`scenarios/business_endpoints.csv`로도 외부화되어 있어(§8.3), 정책공간 탐색 스크립트가 동일한
소스를 참조한다.

| 엔드포인트 | 행위(action) |
|---|---|
| `GET /customer-profile/{case_id}` | `read_customer_profile` |
| `POST /credit-assessment` | `request_credit_assessment` |
| `POST /aml-screening` | `request_aml_screening` |
| `POST /approval-requests` | `submit_for_approval` |
| `GET /loan-review/{case_id}` | `read_review_package` |

매핑되지 않는 조합(예: `DELETE /customer-profile/{id}`)은 `action=unsupported_action`으로
OPA에 전달되어 어떤 튜플과도 일치하지 않으므로 기본거부로 자연스럽게 막힌다. 모든 요청이 동일한
결정 경로(OPA 질의 → 감사로그)를 지나기 때문에 감사 추적성이 끊기지 않는다.

`policies/baseline.rego`(비교군)는 역할·행위·목적을 구분하지 않고, 인증된 신뢰 단말이면 5개
업무 어디든 광범위하게 허용한다. 제안군의 `role_based_microsegment_policy`와 비교군의
`baseline_broad_authenticated_access`가 이 실험의 핵심 대비축이다.

## 4. HMAC 워크로드 신원 검증

논문은 "사용자와 애플리케이션·서비스의 신원을 기반으로 세분화된 접근정책을 적용하는
제로트러스트 구조"를 전제한다(NIST SP 800-207A 인용). 이를 코드로 닫기 위해, PEP는 클라이언트가
보낸 `x-source-business` 헤더를 **신뢰하지 않는다.**

- 각 업무 컨테이너는 자신만 아는 `WORKLOAD_SECRET`을 환경변수로 갖는다(compose 파일에서 업무별로
  서로 다른 값을 주입).
- 업무 컨테이너의 `POST /call`(실제 워크로드가 다른 업무를 호출하는 유일한 경로)은 자신의
  `SERVICE_NAME`과 타임스탬프를 자신의 비밀키로 HMAC-SHA256 서명해 `x-workload-signature`,
  `x-workload-timestamp`로 PEP에 전달한다. `x-source-service`/`x-source-business`는 호출자가
  넘긴 값이 아니라 **컨테이너 자신의 환경설정에서만** 채워지므로 호출자가 덮어쓸 수 없다.
- PEP는 `WORKLOAD_SECRETS`(5개 업무의 비밀키 매핑)를 갖고, 주장된 출발 업무의 비밀키로 서명을
  재계산해 검증한다(30초 타임스탬프 윈도우로 서명의 유효시간을 확인하여 오래된 서명 요청을
  거부한다 — 동일 서명이 그 윈도우 안에서 재사용되는 것까지 막는 완전한 재전송 방지는
  아니며, 그러려면 서명을 1회용으로 소비하는 nonce 저장소가 추가로 필요하다). 검증에 성공한
  경우에만, 그것도 **서명에 쓰인 업무명으로 대체되어** `source_business`가 OPA 입력에 채워진다.
  검증에 실패하면 OPA를 호출하지도 않고 즉시 거부한다(`unregistered_workload`: 모르는 업무명 /
  `workload_signature_invalid`: 등록된 업무명이지만 서명 불일치·만료).

이 구조는 4개 모드의 PEP 모두에 동일하게 적용된다 — §5의 비교축은 "네트워크가 분리됐는가"와
"Rego 정책이 역할까지 세분화하는가"이지 "신원을 검증하는가"가 아니기 때문이다. 그 결과 신원
위장(스푸핑)이나 미등록 워크로드는 4개 모드 어디에서도 함께 차단되며, 이는 모든 환경이 공유하는
PEP 구조적 방어가 정책 세분화·네트워크 분리와 독립적임을 보여준다.

실험 하네스는 이 구조를 실제로 검증하기 위해, 정책공간 탐색(§8.4)과 사전검증 시나리오(§8.2)
모두 호스트에서 PEP를 직접 두드리지 않고 **실제 출발 업무 컨테이너의 `/call`**을 거친다.
출발 업무 자체를 위장하는 사전검증 시나리오(U07/U08)만 예외적으로 PEP를 직접 호출해
"PEP가 자기선언을 신뢰하지 않는지"를 검증한다.

## 5. 비교 환경: 2x2 ablation 설계

두 통제요소를 각각 독립적으로 켜고 끌 수 있도록 4개의 `compose.<mode>.yml`을 둔다. 모드 이름과
파일명은 논문 본문(Table 4)과 1:1로 대응한다.

| 모드 | compose 파일 | 네트워크 축 | 정책 축 | 비고 |
|---|---|---|---|---|
| `baseline` | `compose.baseline.yml` | flat(`flat_s` 공유망) | broad(`baseline.rego`) | 두 통제요소 모두 미적용(기준 조건) |
| `policy_only` | `compose.policy_only.yml` | flat(`flat_s` 공유망) | finegrained(`proposed.rego`) | 정책 세분화만 적용 |
| `segmentation_only` | `compose.segmentation_only.yml` | segmented(업무별 `*_segment`) | broad(`baseline.rego`) | 네트워크 분리만 적용 |
| `proposed` | `compose.proposed.yml` | segmented(업무별 `*_segment`) | finegrained(`proposed.rego`) | 두 통제요소 결합 |

- **네트워크 축**: flat은 모든 S영역 앱·DB가 `flat_s` 하나의 Docker 네트워크를 공유한다.
  segmented는 업무별 앱·DB·PEP만 공유하는 개별 Docker 네트워크(`*_segment`)로 분리해, 서비스가
  자신의 DB에만 직접 연결할 수 있게 한다(다른 업무의 DB로 가는 네트워크 경로 자체가 없음).
- **정책 축**: broad(`policies/baseline.rego`)는 인증된 신뢰 단말이면 5개 업무 어디든 광범위하게
  허용한다. finegrained(`policies/proposed.rego`)는 role×source×destination×action×purpose
  5-튜플과 정확히 일치해야 허용한다(§3).
- **CDS(S/O 경계 통제)는 2×2의 실험요소(Network, Access Policy)에 포함되지 않는 고정 Security
  Control이다**(논문 Table 3). 네 조건 모두 동일한 `policies/cds.rego`(S 원본 금지, 승인된 O
  파생본만 허용)를 사용하며, 네트워크·정책 축과 무관하게 항상 같은 결과를 낸다 — 비교대상
  이외의 구성요소가 결과에 영향을 주지 않도록 하기 위함이다(§8.2).
- **워크로드 신원 검증(§4)은 4개 모드 모두 동일하게 적용된다** — 비교축은 "네트워크가
  분리됐는가"와 "Rego 정책이 역할까지 세분화하는가"이지 "신원을 검증하는가"가 아니기 때문이다.

각 compose 파일은 호스트 포트(18001–18005/18080/18090/18181)가 동일하므로 항상 하나만 기동한다
(§7).

## 6. 사전 준비

요구사항:

- Docker Engine 또는 Docker Desktop, Docker Compose v2
- Python 3.11 이상
- OPA CLI 또는 Docker(정책 유닛 테스트 실행용 — 로컬에 `opa`가 없으면 `docker run
  openpolicyagent/opa` 이미지로 대체 실행한다)

```bash
cd financial_n2sf
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

실험에는 실제 개인정보나 개인신용정보를 사용하지 않는다. 초기 데이터는
`db/init/00_schema.sql`의 합성 레코드다.

## 7. 실험 실행

### 7.1 정책 유닛 테스트(권장, 실험 전 1회)

정책 파일(`policies/*.rego`)은 실험 내내 고정되어 있으므로 모드별로 반복할 필요 없이 실험 시작
전 한 번만 실행하면 된다.

```bash
docker run --rm -v "$(pwd)/policies:/policies" openpolicyagent/opa:1.4.2-static \
  test /policies/proposed.rego /policies/proposed_test.rego \
       /policies/cds.rego /policies/cds_test.rego /policies/data.json
```

`policies/proposed_test.rego`가 정상 5-튜플 allow와 역할/목적/행위 불일치·미신뢰 단말·반대
방향·무관 업무 조합의 deny를 검증하고, `policies/cds_test.rego`가 S/O 전송 조건과 "S등급이면서
콘텐츠 검사에도 실패하는" 경계조건(두 deny 규칙이 동시에 참이 되어 `eval_conflict_error`가
나지 않는지)을 검증한다. 이 테스트를 통과한 정책 버전만 실험에 사용한다.

`policies/` 디렉터리 전체(`opa test /policies`)를 한 번에 검사하지 않는다 — `baseline.rego`와
`proposed.rego`가 둘 다 `package financial.access`에서 서로 다른 `default decision`을 정의하므로,
함께 로드하면 "multiple default rules" 컴파일 오류가 발생한다. CDS는 네 조건 모두 `cds.rego`
하나만 사용하므로(§5) 이 충돌이 없다(`baseline_cds.rego`는 더 이상 쓰지 않고 `archive/`로
옮겼다).

### 7.2 구조적 보안효과 실험(4.1/4.2) — `run_graph_experiment.py`

```bash
python scripts/run_graph_experiment.py
```

4개 모드를 순서대로 기동·검증·정리한다. 모드마다: `docker compose up -d --build --wait` →
사전검증(`run_policy_tests.py`/`run_cds_tests.py`를 `--fail-on-mismatch`로, §8.2) → 환경
메타데이터 수집(`collect_env.py`) → 90쌍 도달성 전수검사(`run_reachability_tests.py`, §8.4) →
80조합 정책공간 탐색(`run_policy_space.py`, §8.3) → `docker compose down -v`. 사전검증·도달성·
정책공간 중 하나라도 실패하면 스택을 **내리지 않고** 즉시 중단한다 — 원인 조사를 위해 실패
상태를 그대로 보존하기 위함이다. 4개 모드가 모두 끝나면 `build_effective_graph.py`(Network/
Policy/Effective Graph 생성)와 `analyze_graph_metrics.py`(AOD/MPL/TINR 계산)를 자동으로 이어서
실행한다. 대략 30~40분 소요된다(환경에 따라 다름).

개별 단계를 직접 실행할 수도 있다(디버깅용, `--mode`는 `baseline`/`policy_only`/
`segmentation_only`/`proposed` 중 하나):

```bash
docker compose -f compose.proposed.yml up -d --build --wait
python scripts/run_policy_tests.py --mode proposed --repeat 1 --fail-on-mismatch
python scripts/run_cds_tests.py --mode proposed --repeat 1 --fail-on-mismatch
python scripts/run_reachability_tests.py --mode proposed
python scripts/run_policy_space.py --mode proposed
docker compose -f compose.proposed.yml down -v

python scripts/build_effective_graph.py
python scripts/analyze_graph_metrics.py
```

### 7.3 성능 실험(4.3) — `run_experiment.py`

```bash
python scripts/run_experiment.py --rounds 12
```

이미지를 한 번만 빌드한 뒤, 4개 조건을 12개 독립 반복 라운드에 걸쳐 균형화된 순서로(§9)
재기동하며 측정한다. 라운드마다 `docker compose up -d --wait` → `run_performance_tests.py
--round-id <r> --order-position <p>` → `docker compose down -v`를 반복한다. 대략 1.5~2시간
소요되므로(환경에 따라 다름), §7.2의 구조적 보안효과 결과를 먼저 확인한 뒤 실행하는 것을
권장한다.

### 7.4 분석

```bash
python scripts/analyze_graph_metrics.py --results-dir results   # 4.1/4.2 구조적 보안효과 (보통 7.2에서 이미 자동 실행됨)
python scripts/analyze_results.py --results-dir results          # 4.3 성능평가 + 감사로그 추적 검증
```

산출물은 [§10](#10-분석-및-산출물)에 정리했다.

### 7.5 통신 그래프 시각화(선택)

`build_effective_graph.py`가 만드는 `*.graphml` 파일은 Gephi, Cytoscape 또는
`networkx.read_graphml` + matplotlib으로 열어 정성적 그림을 그릴 수 있다(4.1/4.2절 보조 그림).

## 8. 시나리오 및 자산 구성

### 8.1 업무자산 목록 — `scenarios/assets.csv`

논문 3.4.1의 정점집합 V(10개 업무자산 = 5개 업무 × app/db, PDP/PEP 제외)를 그대로 담은
파일이다. 각 행은 `asset_id`, `business`, `tier`(`app`/`db`), `compose_service`(Docker Compose
서비스명), `target_port`(App 8000 / DB 5432)로 구성된다. 도달성 전수검사(§8.4)와 정책공간
탐색(§8.3)의 그래프 구성 스크립트가 모두 이 파일을 노드 정의의 단일 소스로 사용한다.

### 8.2 정책·CDS 사전검증 시나리오 — `authorized_flows.csv`, `unauthorized_flows.csv`, `cds_flows.csv`

A01–A05(정상 5-튜플), U01–U08(위반 8유형), C01–C06(S/O 전송)은 여전히 존재하지만, **더 이상
정량적 보안효과 지표가 아니라 실험 전 사전검증(precondition)** 으로만 쓰인다 — 정책·서비스
구성의 구현 오류가 효과 측정에 혼입되지 않도록 확인하는 단계이며, 이후 4장의 AOD/MPL/TINR·성능
지표에는 포함하지 않는다.

```bash
python scripts/run_policy_tests.py --mode proposed --repeat 1 --fail-on-mismatch
python scripts/run_cds_tests.py --mode proposed --repeat 1 --fail-on-mismatch
```

`--fail-on-mismatch`는 기대값과 다르거나(`matches_expected != true`) PEP<->OPA/목적지 연결
실패 같은 실행 오류가 하나라도 있으면 exit 1로 종료한다. 각 행은 `entry_point` 컬럼으로 요청
경로를 지정한다: `call`은 실제 출발 업무 컨테이너의 `POST /call`(§4)을 거치고, `direct`(U07/U08만
해당)는 PEP를 직접 호출하며 워크로드 서명을 생략(`missing`)하거나 위조(`invalid`)해 "PEP가
자기선언을 신뢰하지 않는지"를 검증한다. `cds_flows.csv`의 `expected`는 네 조건이 공유하는
`cds.rego` 하나를 기준으로 한 단일 컬럼이다(§5) — 정책·네트워크 축과 무관하게 모든 모드에서
같은 값을 기대한다.

### 8.3 업무 엔드포인트 — `scenarios/business_endpoints.csv`

5개 업무가 각각 노출하는 `(method, path, action)`을 담은 파일이다(§3의 행위-엔드포인트 매핑과
동일한 내용을 CSV로 외부화). `action`은 `read_customer_profile`처럼 3.3절의 canonical action
값이며(요청 URL이 아니라 이 값이 raw 정책공간 결과의 `action` 컬럼에 그대로 기록된다),
`run_policy_space.py`가 목적업무별로 어떤 행위를 요청해야 하는지 여기서 조회한다.

### 8.4 정책공간 전수탐색 — `scripts/run_policy_space.py`

논문 3.4.1의 정책경로 구성 방법이다. `policies/data.json`에서 실제 쓰이는 role(2개:
`loan_reviewer`/`loan_approver`)·purpose(2개: `loan_screening`/`loan_approval`) 값을 추출하고,
5개 출발업무 × 4개 목적업무(자기 제외) × role × purpose = **80개 조합**을 전부 실제
`/call → HMAC → PEP → OPA` 경로로 호출한다(§4). 행위(action)는 목적업무가 `business_endpoints.csv`
로 노출하는 값 하나로 고정되므로 자유 변수가 아니다.

```bash
python scripts/run_policy_space.py --mode proposed
```

HTTP status만으로는 정책적 deny와 `opa_transport_error` 같은 실행 오류가 구분되지 않으므로,
PEP 감사로그의 실제 `decision`을 `attempt_id`+`experiment_run_id`로 조인해 최종 판정을 삼는다
(`common.join_audit_decision`, §11). (source_business, destination) 쌍에 대해 80개 조합 중
**하나라도 allow가 있으면** 유효 통신 그래프의 Policy Graph에 간선이 생긴다 — "이 업무에서 저
업무로 갈 수 있는 정당한 업무 맥락이 하나라도 있는가"를 묻는 것이다.

### 8.5 도달성 전수검사 — `scripts/run_reachability_tests.py`

`assets.csv`의 10개 자산 각각을 출발지로, 나머지 9개 자산까지 직접 TCP 연결이 가능한지
**3회씩** 검사한다(총 90개 방향성 관계 × 3회). 자산당 프로브 컨테이너를 1회만 띄워(`docker run
--rm --network container:<source_container_id> <app 이미지> python probe.py <target1> ...`)
나머지 9개 목적지를 한 번에 검사한다 — 출발 자산의 Docker 네트워크 네임스페이스를 프로브
컨테이너가 그대로 공유하므로, DB 컨테이너(Python이 없는 `postgres:17-alpine`)도 이미지를
건드리지 않고 "출발지"로 취급할 수 있다.

```bash
python scripts/run_reachability_tests.py --mode proposed
```

3회 결과가 갈리면(`stable=false`) 스크립트가 실패(exit 1)하며 표준에러에 어느 관계가 불안정한지
출력한다 — 결과를 비율로 뭉개서 덮어쓰지 않고 원인을 조사하도록 강제하기 위함이다(접근제어·TCP
도달성은 결정론적이어야 정상이므로, 불안정한 결과 자체가 조사할 문제다).

## 9. 성능 측정 구조와 라운드 설계

동일한 정책·입력으로 동일 요청을 반복하는 것은 독립 표본이 아니다. 이전 버전은 이를 "배치"로만
해결했지만(한 프로세스에서 여러 배치를 연속 측정), 측정순서·실행환경이 결과에 주는 영향을 줄이기
위해 **네 실험조건을 독립적으로 재기동하는 12개 반복 라운드**로 확장했다. 라운드마다 각 조건이
실행순서의 동일한 위치에 배치되도록 순서를 균형화한다 — `scripts/run_experiment.py`가 cyclic
Latin square(라운드마다 시작 조건을 한 칸씩 돌리는 결정론적 균형화 기법)로 12라운드 순서를
생성하며, 12라운드(=4조건 순환 3바퀴)에서 각 조건은 4개 실행위치(1~4번째)에 정확히 3회씩
배치된다.

각 라운드: 여신심사에서 시작하는 **네 개** 정상 업무흐름(customer/credit/aml/approval; 승인자가
loan을 조회하는 역방향 흐름은 정책·기능 검증에서 이미 다루므로 성능측정은 심사 단계에 집중한다)
마다 20회 워밍업 후 **3개 배치**, 배치당 200회 요청을 측정한다. 각 요청은 `loan_app`의
`/call`(§4)을 통해 이뤄진다.

```bash
python scripts/run_experiment.py --rounds 12 --batches 3 --per-batch 200 --warmup 20
```

**라운드 대표값 산출**: 라운드 내 각 흐름의 배치 median들을 다시 median으로 묶어 "흐름 대표값"을
만들고, 네 흐름의 대표값을 동일 가중으로 다시 median을 취해 "라운드 대표값" 하나를 만든다
(`scripts/analyze_results.py`의 `round_representative`). 12개 라운드 대표값이 최종 통계 단위이며,
배치 내 낱개 요청이나 흐름 하나만을 독립 표본으로 취급하지 않는다(§12).

## 10. 분석 및 산출물

### 10.1 구조적 보안효과(4.1/4.2) — `analyze_graph_metrics.py`

| 파일 | 내용 |
|---|---|
| `{network,policy,effective}_graph_<mode>.graphml` | 모드별 Network(10노드)/Policy(5노드)/Effective(10노드) 통신 그래프(NetworkX GraphML) |
| `{network,policy,effective}_edges_<mode>.csv` | 위 그래프의 간선 목록(`effective_edges_<mode>.csv`는 각 간선이 `direct_tcp`/`policy_mediated` 중 무엇 때문에 존재하는지도 함께 기록) |
| `graph_metrics.csv` | 4개 모드 × 5개 지표(Network/Policy/Effective AOD, Effective MPL, Effective TINR) = 20개 행, 절대값만(`layer`,`metric`,`mode`,`value`,`node_count`,`edge_count`) — Baseline 대비 감소율이나 축별 평균은 3.4.2가 정의한 평가방법이 아니므로 포함하지 않는다(§11) |
| `node_metrics.csv` | Effective Graph 기준 자산×모드별 out-degree, 전이적으로 도달 가능한 노드 수 |
| `network_aod.png`, `policy_aod.png`, `effective_aod.png`, `effective_mpl.png`, `effective_tinr.png` | 지표별 막대그래프(4개 모드) — 계층별 AOD는 정점 수가 달라(10 vs 5) 절대값을 직접 비교하지 않으므로 하나로 합치지 않는다 |
| `raw_network_edges_<mode>_<timestamp>.csv` | 90쌍 도달성 전수검사 원본(§8.5) |
| `raw_policy_space_<mode>_<timestamp>.csv` | 80조합 정책공간 탐색 원본(§8.4) |

### 10.2 성능평가 및 감사로그 추적 검증(4.3) — `analyze_results.py`

| 파일 | 내용 |
|---|---|
| `performance_rounds.csv` | 모드×라운드별 라운드 대표값(`decision_ms`, `total_ms`) |
| `performance_summary.csv` | 모드별 median·IQR·p95·부트스트랩 95% CI(12개 라운드 대표값 기준, 모드당 정확히 12개가 아니면 분석 자체가 실패한다) |
| `policy_decision_latency.png` | Policy Decision Latency(`decision_ms`) 라운드 대표값 분포 박스플롯(4개 모드) |
| `pep_request_latency.png` | PEP-mediated Request Latency(`total_ms`) 라운드 대표값 분포 박스플롯(4개 모드) |
| `raw_performance_<mode>_r<NNN>_<timestamp>.csv` | 라운드별 개별 요청 원본 |

### 10.3 공통

| 파일 | 내용 |
|---|---|
| `experiment_metadata.json` | `collect_env.py`가 수집한 실험장비·소프트웨어 버전·정책/시나리오 해시(§14) |
| `pep_audit_<mode>.jsonl`, `cds_audit_<mode>.jsonl` | 요청 단위 감사로그(§13) — 성공률 지표가 아니라 통신경로·정책판단·성능 측정결과의 추적자료로만 쓴다 |

4개 모드 각각에 고정 색상(baseline=blue, policy_only=orange, segmentation_only=aqua,
proposed=yellow, `scripts/plotting.py`)을 배정해 전 그래프에서 시리즈 식별이 일관되도록 했다.

## 11. 평가 지표 정의

논문 3.4.1/3.4.2를 그대로 구현한다. 세 지표(AOD/MPL/TINR) 모두 N. Basta, M. Ikram, M. A. Kaafar,
A. Walker, "Towards a Zero-Trust Micro-segmentation Network Security Strategy: An Evaluation
Framework," NOMS 2022(논문 references [25])에서 제안한 정의를 그대로 사용한다.

1. **세 계층 그래프**(논문 3.4.2): **Network Graph** `G_m^N`(정점 10개 = 5개 업무 ×
   {app, db}, 두 자산 사이에 직접 TCP 통신이 가능하면 간선, §8.5의 90쌍 전수검사로 산출) /
   **Policy Graph** `G_m^P`(정점 5개 = 업무 App 계층만 — `/call`이 항상 App 컨테이너만을
   대상으로 하므로 정책적으로 허용된 간선은 앱 노드 사이에서만 존재, §8.4의 80조합 정책공간
   탐색으로 산출) / **Effective Graph** `G_m^E`(정점 10개, 두 그래프 간선의 합집합 — DB는
   네트워크 계층에서만(직접 TCP로만) 도달 가능). `scripts/build_effective_graph.py`가 세
   그래프를 모두 생성하며 정점 수를 각각 10/5/10으로 강제 검증한다.
2. **AOD(Average Out-Degree)** = `(1/|V|) * Σ OD(v)` — 자산 하나가 평균적으로 갖는 직접
   통신관계의 크기(식 (1)). **Network/Policy/Effective 세 계층 각각에 대해 별도로 계산**한다
   (`scripts/analyze_graph_metrics.py`의 `aod()`). 계층마다 정점 수(|V|)가 다르므로(10/5/10)
   계층을 넘나드는 절대값 비교는 하지 않는다 — 같은 계층 안에서 4개 조건을 비교한다.
3. **MPL(Mean shortest Path Length)** = `(1/|LSP_C|) * Σ|p|`(식 (2)) — `|p|`는 최단경로 p에
   포함되는 **정점의 수**다. NetworkX의 `shortest_path_length`는 간선 수(hop count)를 반환하므로
   `mpl()`은 여기에 `+1`을 더해 정점 수로 맞춘다. **Effective Graph 기준으로만** 계산한다 — 값이
   클수록 침해 이후 목표 자산까지 도달하는 데 거쳐야 하는 경유지가 많다는(=측면이동이 어렵다는)
   뜻이다.
4. **TINR(Transitive Internal Network Reachability)** = `|A^T|`(식 (3), 전이폐쇄 간선 집합의
   크기) — 값이 작을수록 특정 자산이 침해된 이후 다른 자산을 경유해 연속적으로 도달할 수 있는
   전체 범위가 제한됐다는 뜻이다. **Effective Graph 기준으로만** `nx.transitive_closure(G,
   reflexive=None)`의 간선 수로 계산한다(self-loop 미포함).
5. **비교 방법**: 각 지표를 **동일 계층 내에서 4개 실험조건 간 절대값으로만** 비교한다(논문
   3.4.2). Baseline 대비 상대적 감소율이나 축(Network/Policy) 평균 분해는 논문이 정의한 평가
   방법이 아니므로 산출하지 않는다 — `graph_metrics.csv`는 계층×지표×모드 조합의 절대값
   20행뿐이다(§10.1).
6. **유의성 검정 미적용**: 이들 구조적 보안효과 지표는 정의된 자산관계와 정책조합을
   전수평가(90쌍 도달성 + 80조합 정책공간)하여 산출한 **결정론적 값**이므로 별도의 유의성
   검정을 적용하지 않는다(논문 3.4.2).
7. **Latency** = PEP 감사로그의 `decision_ms`(**Policy Decision Latency**, PEP→PDP 정책결정
   요청·응답 왕복시간 — PEP가 OPA에 HTTP 요청을 보내고 응답을 받기까지의 시간이며,
   네트워크·직렬화/역직렬화를 포함한다. OPA 내부에서 Rego 평가에만 걸린 순수 연산시간이 아니다)
   / `total_ms`(**PEP-mediated Request Latency**, PEP가 요청을 받은 시점부터 목적 workload의
   응답을 받을 때까지 걸린 시간) — §9의 라운드 대표값 12개를 기준으로 median/IQR/p95/부트스트랩
   95% CI.

**감사로그는 더 이상 성공률/완전성 "비율" 지표로 산출하지 않는다**(3.4.2). §8.2의 정책·CDS
사전검증과 §8.4의 정책공간 탐색은 모두 PEP/CDS 감사로그의 `decision`을 `common.join_audit_decision`
으로 조인해 실행 오류(`opa_transport_error`/`opa_error`/`upstream_transport_error`/
`unknown_destination`, `common.STRUCTURAL_ERROR_REASONS`)를 실제 정책적 거부와 구분하지만, 이
조인 결과는 그래프 구성·사전검증 통과/실패 판정에만 쓰이고 4장 본문의 헤드라인 지표로 보고하지
않는다. 감사로그 자체의 역할은 §13/§10.3 참고.

## 12. 통계 처리 원칙

- **구조적 보안효과(AOD/MPL/TINR)**: 90개 자산관계·80개 정책조합을 전수검사한 결정론적 그래프
  지표이므로 유의성 검정을 적용하지 않는다(§11-6). 동일 계층 내 4개 조건 간 절대값으로만
  보고하며, Baseline 대비 상대적 변화나 축별 평균 분해는 산출하지 않는다(§11-5). 도달성 검사는
  3회 반복해 결과가 안정적인지(`stable`) 확인하고, 불안정하면 분석을 중단해 원인을 조사한다
  (비율로 뭉개지 않는다, §8.5).
- **CDS(S/O 경계 통제)**: 2×2 실험요소가 아닌 고정 Security Control이므로(§5, 논문 Table 3)
  통계적 비교 대상이 아니다 — 네 조건 모두 `cds.rego` 하나로 사전검증만 통과하면 된다(§8.2).
- **성능**: 4개 조건 × 12개 독립 반복 라운드 × 4개 흐름 × 3개 측정 배치 × 배치당 200회로
  측정한다. 라운드 대표값(§9) 12개를 통계 단위로 사용해 median, IQR, p95, 2,000회 부트스트랩
  95% CI를 제시한다(`performance_summary.csv`). 모드당 라운드 수가 정확히 12개가 아니면 분석을
  중단한다(`analyze_results.py`). 라운드 순서는 균형화된 cyclic Latin square로 고정하며(§9),
  4-way 전체 조합 간 가설검정(Mann-Whitney U 등)은 수행하지 않는다 — 목적이 "통계적으로 유의한
  차이"가 아니라 "조건별 지연시간 분포와 그 폭"을 보여주는 것이기 때문이다.
- 유의수준, 반복횟수, 라운드/배치 구성은 실험 전에 고정한다(`run_experiment.py`의 기본값:
  12라운드 × 3배치 × 200회).

## 13. 감사로그 필드

PEP(`results/pep_audit_<mode>.jsonl`, `<mode>`는 `baseline`/`policy_only`/`segmentation_only`/
`proposed`)는 매 요청마다 다음 필드를 기록한다(허용/거부 모든 경로에서 동일한 필드 집합을
남긴다). 이 로그는 §11에서 밝힌 대로 성공률 "비율"로 집계하지 않고, 사전검증·정책공간 탐색의
실제 판정 근거(`common.join_audit_decision`)이자 성능 라운드의 배치별 `decision_ms`/`total_ms`
출처, 그리고 감사로그 추적 검증의 정성적 확인 대상으로 쓰인다.
`scenario_id` 필드는 사전검증·정책공간 테스트에서는 시나리오 원래 ID가 아니라 스크립트가
요청마다 생성하는 고유 `attempt_id`(예: `A01-r001`, `PS-loan-customer-loan_reviewer-loan_screening`)
를 담는다 — 동일 시나리오를 반복해도 요청 단위로 감사로그를 정확히 대응시키기 위함이다:

`ts`, `request_id`, `experiment_run_id`, `scenario_id`, `user_role`, `claimed_source_service`,
`verified_workload_identity`, `source_business`, `destination`, `action`, `purpose`,
`data_grade`, `policy_version`, `decision`, `reason`, `decision_ms`, `upstream_ms`, `total_ms`,
`upstream_status`

CDS(`results/cds_audit_<mode>.jsonl`)는 S/O 경계 통제에 특화된 자체 필드를
기록한다: `ts`, `request_id`, `experiment_run_id`, `scenario_id`, `input`(user/role/
device_trust/destination/data_grade/approved/purpose/content_safe/source_object_id를 담은
정책 입력 원문), `content_hash`(SHA-256), `pattern_hits`(정규식 탐지 결과), `decision`,
`reason`, `decision_ms`, `upstream_ms`, `total_ms`, `upstream_status`.

`reason` 필드는 정책 판단 결과(예: `role_purpose_action_mismatch`, `s_grade_direct_transfer_
blocked`)뿐 아니라 PEP/CDS 자체의 구조적 실패도 구분해서 기록한다: `unknown_destination`
(미등록 목적지), `opa_transport_error`/`cds_opa_transport_error`(OPA 연결 실패·타임아웃),
`upstream_transport_error`/`cds_upstream_transport_error`(목적 workload 연결 실패). 이 경로들은
감사로그 없이 FastAPI 기본 500 오류로 빠지지 않고, 다른 거부 사유와 동일한 필드 집합으로
기록된 뒤 4xx/5xx로 실패한다(fail-closed).

`policy_version`은 PEP가 OPA의 `data.financial.model_version`을 조회해 캐시한 값이다
(`policies/data.json`의 `model_version` 필드). docker compose의 짧은 형식 `depends_on`은 OPA가
"시작"됐다는 순서만 보장하고 요청을 받을 준비(healthy)까지는 보장하지 않으므로, PEP는 기동 시
최대 8회(1초 간격) 재시도하고, 그래도 `unknown`으로 남아 있으면 이후 요청이 들어올 때마다 다시
한번 조회해 자연스럽게 회복한다(§6의 healthcheck·`--wait` 설명도 참고). 정책을 바꾸면 이 값을
함께 바꿔 어떤 정책 버전으로 만든 결과인지 로그만으로 추적할 수 있게 한다.

## 14. 정책 유닛 테스트 및 재현성 메타데이터

- `policies/proposed_test.rego`, `policies/cds_test.rego`: §7.1의 명령으로 실행하는 정책 자체의
  유닛 테스트(`opa test policies/`로 디렉터리 전체를 한 번에 검사하지 않는다 — §7.1 참고).
  정상 5-튜플 allow, 역할/목적/행위 불일치, 미신뢰 단말, 반대 방향, 무관 업무 조합의 deny,
  그리고 S/O 전송 정책의 경계조건(S등급+콘텐츠 검사 실패 동시 발생 시 규칙 충돌이 나지
  않는지)을 검증한다. 이 테스트를 통과한 정책 버전만 실험에 사용한다.
- `scripts/collect_env.py`: CPU/코어/RAM/OS, Docker Engine·Compose 버전, OPA·PostgreSQL 이미지
  digest, Python 버전, Git commit SHA, `policies/*.rego`·`data.json`·`scenarios/*.csv`(§8.1/8.3의
  `assets.csv`/`business_endpoints.csv` 포함, 디렉터리 전체를 글롭으로 해싱하므로 별도 수정
  없이 자동 포함된다)의 SHA-256 해시, 실행 시각(UTC)을 `results/experiment_metadata.json`에
  기록한다. 논문 Table 3(실험장비 및 소프트웨어 버전)의 근거자료로 쓸 수 있다.

## 15. 해석 시 주의사항

- Docker 네트워크 분리는 물리적 망분리 장비의 성능이나 보증수준을 재현하지 않는다.
- HMAC 워크로드 서명은 "PEP가 자기선언이 아니라 검증된 신원을 신뢰해야 한다"는 구조적 요구를
  프로토타입 수준에서 보이기 위한 것이며, 운영환경에서는 mTLS/SPIFFE 같은 서비스 메시 신원
  체계로 대체해야 한다. 이 실험의 비밀키는 compose 파일에 평문으로 존재하는 lab 전용 값이다.
- `customer_app`~`approval_app`의 18001–18005 포트는 테스트 하네스가 "실제 출발 업무"로서
  요청을 만들기 위한 진입점이며, 운영환경의 접근경로를 재현하지 않는다. 업무 간 횡적 이동
  가능성은 이 포트가 아니라 §11의 AOD/MPL/TINR로 측정한다.
- 정규식 콘텐츠 검사(Transfer CDS)는 실제 DLP·백신·CDR의 대체물이 아니라 통제 흐름을 재현한
  모의 기능이며, 4장 정량 지표에는 포함하지 않는 사전검증 대상이다(§8.2).
- OPA 입력의 `role`/`device_trust`/`purpose`는 실제 환경의 IdP, MFA, EDR, NAC, IAM/PAM 연동
  결과를 추상화한 것이다. 다만 `source_business`(출발 업무)만은 §4의 HMAC 검증을 거쳐 PEP가
  스스로 확인한 값이다.
- AOD/MPL/TINR은 90개 자산관계·80개 정책조합을 전수검사한 **결정론적** 그래프 지표이며 통계적
  추정치가 아니다(§12) — 신뢰구간·유의성 검정은 성능 지표(라운드 대표값)에만 적용한다.
- 따라서 논문의 결론은 "특정 상용 솔루션의 성능"이 아니라 "시스템·업무 경계와 정책집행지점을
  적용했을 때 통신경로·권한·정보이동 결과가 어떻게 달라지는가"로 한정해야 한다.
