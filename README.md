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
5. [비교 환경 baseline과 proposed](#5-비교-환경-baseline과-proposed)
6. [사전 준비](#6-사전-준비)
7. [실험 실행](#7-실험-실행)
8. [시나리오 구성](#8-시나리오-구성)
9. [성능 측정 구조와 배치 설계](#9-성능-측정-구조와-배치-설계)
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

- **PEP / API Gateway (`pep`)**: 모든 S영역 업무 간 호출을 중계하는 유일한 경로. 요청 행위를
  서버 측에서 판정하고, 출발 업무의 워크로드 신원을 검증하며, OPA에 정책을 질의한다.
- **PDP (`opa`)**: 사용자, 역할, 단말 신뢰, 출발/목적 업무, 행위, 목적, 정보등급을 평가한다.
- **Transfer CDS (`transfer_cds`)**: S/O 경계에서 승인·등급·목적·콘텐츠·무결성을 검증한다.
  본 연구의 주 검증대상인 내부 마이크로세그멘테이션에 대한 **보조 검증**으로 취급한다(논문
  3.2.1절 참고).
- **JSONL 감사로그**: PEP·CDS·업무 서비스 각각이 요청/결정/지연시간을 기록한다.

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
| 18090 | `transfer_cds` | S/O 경계 전송 |
| 18181 | `opa` | 정책 조회(디버깅용) |
| 18001–18005 | `customer_app`~`approval_app` | 각 업무 워크로드의 `/call`(§4 참고). 실험 하네스가 "실제 출발 업무"로서 요청을 만들기 위한 진입점이며, 운영 환경의 접근경로가 아니다 |

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
path)` 조합을 정규식 테이블로 매칭해 canonical action을 서버 측에서 결정한다.

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

이 구조는 baseline/proposed 양쪽 PEP에 동일하게 적용된다 — 비교축은 "Rego 정책이 역할까지
세분화하는가"이지 "신원을 검증하는가"가 아니기 때문이다. 그 결과 신원 위장(스푸핑)이나 미등록
워크로드는 baseline에서도 proposed에서도 함께 차단되며, 이는 두 환경이 공유하는 PEP 구조적
방어가 정책 세분화와 독립적임을 보여준다.

실험 하네스는 이 구조를 실제로 검증하기 위해, 정상/역할·목적·행위 위반 시나리오는 호스트에서
PEP를 직접 두드리지 않고 **실제 출발 업무 컨테이너의 `/call`**을 거친다(§8의 `entry_point=call`).
출발 업무 자체를 위장하는 시나리오만 예외적으로 PEP를 직접 호출해(`entry_point=direct`)
"PEP가 자기선언을 신뢰하지 않는지"를 검증한다.

## 5. 비교 환경 baseline과 proposed

### 비교군 — `compose.baseline.yml`

- 모든 S영역 앱과 DB가 하나의 `flat_s` 네트워크를 공유한다.
- `policies/baseline.rego`: 인증된 신뢰 단말이면 5개 업무 어디든 광범위하게 허용.
- Transfer CDS는 승인 여부만 확인하는 단순 정책(`policies/baseline_cds.rego`)을 사용한다.
- 워크로드 신원 검증(§4)은 동일하게 적용된다.

### 제안군 — `compose.proposed.yml`

- 업무별 앱·DB·PEP만 공유하는 개별 Docker 네트워크(`*_segment`, `internal: true`)로 분리한다.
- `policies/proposed.rego`: role×source×destination×action×purpose 5-튜플과 정확히 일치해야
  허용.
- 서비스는 자신의 DB에만 직접 연결할 수 있다(다른 업무의 DB로 가는 네트워크 경로 자체가 없음).
- 외부 전송은 Transfer CDS를 통과하며, S 원본은 금지하고 승인된 O 파생본만 허용한다
  (`policies/cds.rego`).

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

### 7.1 정책 유닛 테스트(선택, 매 실험 전 권장)

```bash
docker run --rm -v "$(pwd)/policies:/policies" openpolicyagent/opa:1.4.2-static \
  test /policies/proposed.rego /policies/proposed_test.rego \
       /policies/cds.rego /policies/cds_test.rego /policies/data.json
```

`policies/proposed_test.rego`가 정상 5-튜플 allow와 역할/목적/행위 불일치·미신뢰 단말·반대
방향·무관 업무 조합의 deny를 검증하고, `policies/cds_test.rego`가 C01–C06에 대응하는 S/O
전송 조건과 "S등급이면서 콘텐츠 검사에도 실패하는" 경계조건(두 deny 규칙이 동시에 참이 되어
`eval_conflict_error`가 나지 않는지)을 검증한다. 이 테스트를 통과한 정책 버전만 실험에
사용한다(재현성 확보 — 결과가 우연히 잘못 작성된 Rego 때문이 아님을 보장).

`policies/` 디렉터리 전체(`opa test /policies`)를 한 번에 검사하지 않는다 — `baseline.rego`와
`proposed.rego`가 둘 다 `package financial.access`에서 서로 다른 `default decision`을 정의하므로
(`baseline_cds.rego`/`cds.rego`도 `package financial.cds`에서 동일하게 충돌), 함께 로드하면
"multiple default rules" 컴파일 오류가 발생한다. `run_all.sh`도 모드별로 필요한 정책 파일만
지정해서 검사한다.

### 7.2 비교군 실행

```bash
docker compose -f compose.baseline.yml up -d --build --wait
./scripts/run_all.sh baseline
docker compose -f compose.baseline.yml down -v
```

### 7.3 제안군 실행

```bash
docker compose -f compose.proposed.yml up -d --build --wait
./scripts/run_all.sh proposed
docker compose -f compose.proposed.yml down -v
```

`--wait`는 Docker Compose가 healthcheck를 정의한 서비스(모든 DB, 업무 App, PEP, `public_app`,
`external_ai`)의 상태가 실제로 `healthy`가 될 때까지 기다렸다가 반환하게 한다. DB→App, App/OPA→
PEP처럼 짧은 형식(short-form) `depends_on`은 의존 서비스가 "시작"됐다는 순서만 보장할 뿐 요청을
받을 준비(healthy)가 됐다는 것까지 보장하지 않으므로, `--wait` 없이 바로 `run_all.sh`를 실행하면
컨테이너가 아직 뜨는 중일 때 첫 요청 몇 건이 연결 실패로 새는 경우가 있다. OPA(`-static` 이미지라
셸이 없어 Docker 헬스체크 자체를 붙일 수 없음)만은 이 대상에서 빠지는데, 대신 PEP/CDS가 OPA
호출을 자체적으로 재시도하도록 구현되어 있다(§4, §13의 `opa_transport_error`).

`run_all.sh`는 다음을 순서대로 실행한다: ① `opa test` ② `collect_env.py`(환경 메타데이터 수집)
③ 업무흐름 정책 테스트(30회 반복) ④ S/O 전송 CDS 테스트(30회 반복) ⑤ App·DB 도달성 테스트(45개
조합) ⑥ 성능 테스트(4개 흐름 × 30배치 × 배치당 200회).

개별 스크립트를 직접 실행할 수도 있다:

```bash
python scripts/collect_env.py --results-dir results
python scripts/run_policy_tests.py --mode proposed --repeat 30
python scripts/run_cds_tests.py --mode proposed --repeat 30
python scripts/run_reachability_tests.py --mode proposed
python scripts/run_performance_tests.py --mode proposed --batches 30 --per-batch 200
```

두 모드를 동시에 실행하면 동일한 호스트 포트를 사용하므로 충돌한다. 반드시 한 환경을 종료한 뒤
다른 환경을 실행한다. `results/pep_audit_baseline.jsonl` 등 감사로그는 호스트 디렉터리
(`./results`)에 바인드 마운트되므로 `docker compose down -v`로 컨테이너/볼륨을 내려도 남아있다
— 같은 모드를 다시 실행하면 이전 로그 뒤에 이어서 append된다(분석 스크립트는
`experiment_run_id`로 최신 실행분만 골라낸다).

### 7.4 분석

```bash
python scripts/analyze_results.py --results-dir results
```

산출물은 [§10](#10-분석-및-산출물)에 정리했다.

### 7.5 통신 그래프(선택, 정성적 그림용)

```bash
python scripts/build_communication_graph.py results/raw_policy_proposed_<timestamp>.csv \
  --allowed-only --output results/proposed_communication_graph.png
```

허용된 요청만으로 방향 그래프를 그려 실제 관측된 업무 간 통신 관계를 시각화한다(정책 표
자체는 §3의 표가 더 정확한 1차 자료이며, 이 그래프는 "관측된 흐름이 설계와 일치하는가"를
보여주는 보조 그림이다).

## 8. 시나리오 구성

### 8.1 업무흐름 정책 시나리오 — `scenarios/authorized_flows.csv`, `unauthorized_flows.csv`

각 행은 `entry_point` 컬럼으로 테스트 하네스가 요청을 만드는 경로를 지정한다.

- **`call`**: `scripts/run_policy_tests.py`가 `source_service`가 가리키는 실제 업무 컨테이너의
  `POST /call`(호스트 포트 18001–18005)을 호출한다. 출발 업무 신원은 컨테이너가 스스로
  서명하므로 위조할 수 없고, `role`/`purpose`/`destination`/`method`/`path`만 시나리오가
  지정한 대로 전달된다.
- **`direct`**: PEP(18080)의 `/proxy/{destination}{path}`를 직접 호출하면서
  `claimed_source_service`/`claimed_source_business`를 자칭하고, `signature_mode`에 따라
  서명을 생략(`missing`)하거나 위조(`invalid`)한다. 오직 신원 위장 자체를 검증하는 두 시나리오
  (U07, U08)만 이 경로를 쓴다.

정상 흐름 A01–A05는 §3의 5-튜플과 1:1 대응한다. 위반 흐름 U01–U08은 각각 다른 위반 축을
검증한다.

| ID | violation_type | 의미 | baseline 기대값 | proposed 기대값 |
|---|---|---|---|---|
| U01 | role_mismatch | loan_approver가 loan_reviewer 전용 행위 시도 | allow | deny |
| U02 | purpose_mismatch | 목적이 튜플과 다름(`loan_approval`을 심사 조회에 사용) | allow | deny |
| U03 | action_mismatch | `submit_for_approval`을 승인이 아닌 customer로 전송 | allow | deny |
| U04 | reverse_direction | customer가 loan을 조회(A01의 역방향) | allow | deny |
| U05 | unrelated_cross_business | approval이 credit에 신용평가 요청 | allow | deny |
| U06 | untrusted_device | 신뢰되지 않은 단말 | deny | deny |
| U07 | unregistered_workload | 등록되지 않은 업무명을 자칭(PEP 직접호출) | deny | deny |
| U08 | identity_spoofing | 등록된 업무(loan)를 사칭하되 서명 위조(PEP 직접호출) | deny | deny |

U06–U08은 PEP의 구조적 방어(단말 신뢰, 워크로드 신원 검증)에 걸리므로 baseline에서도 deny다.
U01–U05는 Rego 정책의 세분화 여부에 따라 baseline/proposed 결과가 갈리는, 이 실험의 핵심
대비 지점이다.

### 8.2 도달성 시나리오 — `scenarios/reachability_matrix.csv`

논문 3.2.1절은 "업무서비스 간 연계"와 "데이터 저장계층에 대한 접근"을 서로 다른 통제지점으로
구분한다. 이를 그대로 반영해 두 계층을 모두 검사한다.

- **DB 도달성**: 5개 업무 × 5개 DB = 25개 조합(자기 DB 5 + 교차 업무 DB 20)
- **업무서비스(App) 도달성**: 5개 업무 앱 사이의 자기 자신을 제외한 5×4=20개 조합

총 45개 조합을 `scripts/run_reachability_tests.py`가 `docker compose exec <container> python
/app/probe.py <target_host> <target_port>`로 TCP 연결 성공 여부만 확인한다(애플리케이션 인증
여부와 무관하게 네트워크 계층 도달성만 측정). App 도달성 조합은 "정책 판단·집행과정을 우회한
업무 간 직접 통신"이 네트워크 수준에서 실제로 불가능한지를 보여준다 — proposed에서는 PEP를
거치지 않고 예컨대 `customer_app`이 `loan_app`에 직접 연결할 네트워크 경로 자체가 없어야 한다.

### 8.3 S/O 전송 CDS 시나리오 — `scenarios/cds_flows.csv`

C01–C06. §1에서 밝힌 대로 본 연구의 주 검증대상은 아니며, S/O 경계에 대한 보조 검증이다.

## 9. 성능 측정 구조와 배치 설계

동일한 정책·입력으로 동일 요청을 반복하는 것은 독립 표본이 아니다. 따라서 성능은 **배치
단위**로 측정한다: `loan`이 시작점인 4개 정상 흐름(customer/credit/aml/approval)마다 warmup
후 `--batches`(기본 30)개의 측정 배치를 만들고, 배치당 `--per-batch`(기본 200)회 요청한다.
각 요청은 `loan_app`의 `/call`(§4의 실제 워크로드 경로)을 통해 이뤄진다. 배치는 통계적으로
독립된 단위로 취급하는 반복 측정 구간일 뿐, 배치마다 프로세스나 컨테이너를 재기동하는 것은
아니다(그런 의미의 "독립 배치"가 아니라는 점에 주의).

```bash
python scripts/run_performance_tests.py --mode proposed --batches 30 --per-batch 200 --warmup 20
```

배치 요약값(median)을 통계 단위로 사용하고, 배치 내 낱개 요청을 독립 표본으로 취급하지 않는다
(§12).

## 10. 분석 및 산출물

`python scripts/analyze_results.py --results-dir results`가 만드는 파일:

| 파일 | 내용 |
|---|---|
| `experiment_summary.csv` | §11의 핵심 지표를 baseline/proposed/relative_change로 요약 |
| `exact_count_summary.csv` | 카테고리별(정상흐름/비인가흐름·정책범위/비인가흐름·구조범위/App도달/DB도달/CDS) **정책판단이 완료된** 요청 수, 기대결과 일치 수, 실행 오류(execution_errors) 건수, 일치율 — Fisher 검정 대신 사용하는 1차 보안·기능 결과표 |
| `blast_radius.csv` | 업무별 Blast Radius(§11) |
| `latency_by_flow.csv` | 흐름×배치별 decision_ms/total_ms median |
| `latency_confidence_intervals.csv` | decision_ms/total_ms의 median·IQR·p95·부트스트랩 95% CI(baseline/proposed) |
| `statistical_tests.csv` | 배치 median 간 Mann-Whitney U 검정(보조 지표) |
| `security_effectiveness.png` | Authorized Flow Success Rate / Unauthorized Flow Block Rate / Cross-Business Service Reachability Rate / Cross-Business DB Reachability Rate 막대그래프 |
| `blast_radius.png` | 업무별 Blast Radius 막대그래프(baseline vs proposed) |
| `latency_boxplot.png` | 배치별 decision_ms median 분포 박스플롯(baseline vs proposed) |
| `experiment_metadata.json` | `collect_env.py`가 수집한 실험장비·소프트웨어 버전·정책/시나리오 해시(§14) |

이 세 PNG가 논문 4장 그림으로 바로 쓸 수 있는 산출물이다. baseline=blue, proposed=orange
색상 배정을 전 그래프에 고정해 시리즈 식별이 일관되도록 했다.

## 11. 평가 지표 정의

Authorized/Unauthorized Flow 지표(1, 2번)는 **정책판단이 실제로 완료된 요청만을 분모로
삼는다.** `run_policy_tests.py`가 기록하는 `actual`(allow/deny)은 HTTP status 2xx 여부만
보므로, PEP→OPA 연결 실패(`opa_transport_error`)·OPA 자체 오류(`opa_error`)·목적
workload 연결 실패(`upstream_transport_error`)·미등록 목적지(`unknown_destination`) 같은
정책과 무관한 실행 오류도 비2xx라서 그대로 "deny"로 섞여 들어갈 수 있다.
`analyze_results.py`는 `attempt_id`+`experiment_run_id`로 각 요청을 PEP 감사로그와 1:1
조인해 실제 `reason`을 확인하고, 위 실행 오류 사유이거나 애초에 매칭되는 감사로그가 없는
요청은 분모·분자에서 제외한 뒤 `exact_count_summary.csv`의 `execution_errors` 컬럼으로
별도 집계한다(신원 미검증 계열 사유인 `unregistered_workload`/`workload_signature_invalid`는
실행 오류가 아니라 U07/U08이 검증하려는 정책적 판단 그 자체이므로 제외 대상이 아니다).

1. **Authorized Flow Success Rate** = A01–A05 중 정책판단이 완료된 요청 가운데 `actual ==
   allow` 비율
2. **Unauthorized Flow Block Rate** = U01–U08 중 정책판단이 완료된 요청 가운데 `actual ==
   deny` 비율. baseline도 U06–U08(단말 미신뢰·미등록 워크로드·신원위장)은 PEP의 구조적
   방어로 차단하므로, 이 값 하나만 보면 baseline의 차단률이 실제보다 높아 보일 수 있다.
   따라서 `exact_count_summary.csv`에 `unauthorized_flows_policy_scope`(U01–U05, Rego 정책
   세분화 여부로 결과가 갈리는 시나리오)와 `unauthorized_flows_structural_scope`(U06–U08,
   두 환경 공통 방어)를 분리한 보조 지표를 함께 제공한다 — 정책 세분화 자체의 효과는
   policy_scope 쪽 차이로 확인한다.
3. **Cross-Business Reachability Rate** = `reachability_matrix.csv` 기준 두 개 하위 지표로
   구성된다(PEP/OPA를 거치지 않는 순수 TCP 도달성 측정이라 실행 오류 제외 로직과는 무관하다).
   - Service(App): `cross_business_app` 20개 조합 중 `reachable == true` 비율
   - DB: `cross_business_db` 20개 조합 중 `reachable == true` 비율
4. **Blast Radius** = 업무 i에서 직접 도달 가능한 **타 업무 DB 수**(`reachable == true`인
   cross_business_db 조합 수, 0~4). `blast_radius.csv`에 업무별 값을 싣고,
   `experiment_summary.csv`에는 평균값(`blast_radius_mean`)과 최댓값(`blast_radius_max`,
   침해 시 노출범위가 가장 큰 업무 기준)을 함께 싣는다. 5개 업무가 서로 동일한 방식으로
   나머지 4개 업무 DB를 검사하므로 평균은 `(M-1) × DB Reachability Rate`(M=5)와 수학적으로
   같은 값이 나온다 — 이 때문에 논문에서 두 지표를 독립적인 별개 효과처럼 나란히 제시하지
   않도록 주의한다. 업무별 격차를 보여주려는 목적이라면 평균보다 `blast_radius_max`나
   `blast_radius.csv`의 업무별 값이 더 적합하다.
5. **Latency** = PEP 감사로그의 `decision_ms`(PEP→PDP 정책결정 요청·응답 왕복시간 — PEP가
   OPA에 HTTP 요청을 보내고 응답을 받기까지의 시간이며, 네트워크·직렬화/역직렬화를 포함한다.
   OPA 내부에서 Rego 평가에만 걸린 순수 연산시간이 아니다) / `total_ms`(PEP가 요청을 받은
   시점부터 목적 workload의 응답을 받을 때까지 걸린 **PEP 처리 지연시간** — 클라이언트가
   체감하는 종단간(end-to-end) 지연시간이 아니라 PEP 내부 처리구간만을 가리킨다) — 배치
   median의 median/IQR/p95/부트스트랩 95% CI
6. **Audit Completeness** = 필수 필드(§13)를 모두 포함하고 요청별 고유 `attempt_id`+
   `experiment_run_id`로 상관관계가 확인된 감사로그 수 / 전체 요청 수. 동일 시나리오를
   `--repeat`로 반복해도 요청마다 서로 다른 attempt_id를 쓰므로, 30번 중 일부만 로그에 남는
   상황을 놓치지 않는다.

## 12. 통계 처리 원칙

- **보안·기능 시나리오(정책·CDS·도달성)**: 동일한 정책·입력으로 동일 요청을 반복하는 것은
  독립 표본이 아니므로 Fisher 정확검정을 적용하지 않는다. 대신 전체 시나리오 수, 기대결과
  일치 수, 성공률/차단률/도달률 같은 **exact count/rate**를 1차 결과로 보고한다
  (`exact_count_summary.csv`). `cross_business_{app,db}_reachability`의 기대값은 모드마다
  다르다는 점에 주의한다 — baseline(flat network)은 설계상 도달 가능한 것이 기대값이고,
  proposed(업무별 segment)는 도달 불가능한 것이 기대값이다.
- **성능**: 4개 흐름 × 30개 측정 배치 × 배치당 200회로 측정하고, 배치 median을 통계 단위로
  사용한다. Median, IQR, p95, 부트스트랩 95% CI를 제시하고(`latency_confidence_intervals.csv`),
  배치 median 간 Mann-Whitney U 검정을 보조 지표로 유지한다(`statistical_tests.csv`).
- 유의수준, 반복횟수, 배치 구성은 실험 전에 고정한다(`run_all.sh`의 기본값: 정책·CDS 30회,
  성능 30배치×200회).

## 13. 감사로그 필드

PEP(`results/pep_audit_{baseline,proposed}.jsonl`)는 매 요청마다 다음 필드를 기록한다(허용/
거부 모든 경로에서 동일한 필드 집합을 남겨 Audit Completeness가 구조적으로 보장되도록 했다).
`scenario_id` 필드는 정책·CDS 테스트에서는 시나리오 원래 ID가 아니라 `run_policy_tests.py`/
`run_cds_tests.py`가 요청마다 생성하는 고유 `attempt_id`(예: `A01-r001`)를 담는다 — 동일
시나리오를 반복해도 요청 단위로 감사로그를 정확히 대응시키기 위함이다:

`ts`, `request_id`, `experiment_run_id`, `scenario_id`, `user_role`, `claimed_source_service`,
`verified_workload_identity`, `source_business`, `destination`, `action`, `purpose`,
`data_grade`, `policy_version`, `decision`, `reason`, `decision_ms`, `upstream_ms`, `total_ms`,
`upstream_status`

CDS(`results/cds_audit_{baseline,proposed}.jsonl`)는 S/O 경계 통제에 특화된 자체 필드를
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
  digest, Python 버전, Git commit SHA, `policies/*.rego`·`data.json`·`scenarios/*.csv`의
  SHA-256 해시, 실행 시각(UTC)을 `results/experiment_metadata.json`에 기록한다. 논문 Table
  3(실험장비 및 소프트웨어 버전)의 근거자료로 쓸 수 있다.

## 15. 해석 시 주의사항

- Docker 네트워크 분리는 물리적 망분리 장비의 성능이나 보증수준을 재현하지 않는다.
- HMAC 워크로드 서명은 "PEP가 자기선언이 아니라 검증된 신원을 신뢰해야 한다"는 구조적 요구를
  프로토타입 수준에서 보이기 위한 것이며, 운영환경에서는 mTLS/SPIFFE 같은 서비스 메시 신원
  체계로 대체해야 한다. 이 실험의 비밀키는 compose 파일에 평문으로 존재하는 lab 전용 값이다.
- `customer_app`~`approval_app`의 18001–18005 포트는 테스트 하네스가 "실제 출발 업무"로서
  요청을 만들기 위한 진입점이며, 운영환경의 접근경로를 재현하지 않는다. 업무 간 횡적 이동
  가능성은 이 포트가 아니라 §11의 Cross-Business Service/DB Reachability Rate·Blast Radius로
  측정한다.
- 정규식 콘텐츠 검사(Transfer CDS)는 실제 DLP·백신·CDR의 대체물이 아니라 통제 흐름을 재현한
  모의 기능이다.
- OPA 입력의 `role`/`device_trust`/`purpose`는 실제 환경의 IdP, MFA, EDR, NAC, IAM/PAM 연동
  결과를 추상화한 것이다. 다만 `source_business`(출발 업무)만은 §4의 HMAC 검증을 거쳐 PEP가
  스스로 확인한 값이다.
- 따라서 논문의 결론은 "특정 상용 솔루션의 성능"이 아니라 "시스템·업무 경계와 정책집행지점을
  적용했을 때 통신경로·권한·정보이동 결과가 어떻게 달라지는가"로 한정해야 한다.
