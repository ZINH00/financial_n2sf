# 금융권 N2SF Track 2 실험환경

## 1. 연구 위치

이 패키지는 선행 논문(Track 1)이 **금융권 N2SF 시스템·업무별 모델**을 이미 제안하였다는 전제에서, 다음을 검증하기 위한 후속 연구용 프로토타입입니다.

1. 가상 금융기관 A의 시스템·업무별 구성요소와 S/O 정보 흐름을 식별합니다.
2. N2SF 부록 2 형식에 따라 위협, 보안 요구사항, 보안통제 항목을 도출합니다.
3. 도출된 통제를 역할 기반 마이크로세그멘테이션과 정책 기반 정보흐름 통제로 구현합니다.
4. 기존의 평면적 내부망·광범위 접근 방식과 제안 방식을 동일 시나리오로 비교합니다.

이 패키지는 결과 값을 포함하지 않습니다. `results/templates`에는 실험 후 채울 수 있는 빈 형식만 포함되어 있습니다.

## 2. 선택한 방법론

NSDI 2025의 ZTS(Zero Trust Segmentation)가 제안한 **통신 그래프 기반 역할 마이크로세그멘테이션, 역할 수준 정책, 정책 위반률 평가**를 연구 목적에 맞게 축약·적용합니다.

- ZTS 원 논문은 흐름 텔레메트리로 역할을 추론합니다.
- 본 연구는 Track 1에서 업무별 역할과 시스템 경계가 이미 정의되었다고 가정하므로 역할 추론 단계는 수행하지 않습니다.
- 대신 Track 1의 업무 역할을 정답 레이블로 사용하여 허용 통신 그래프를 작성하고, 역할 수준 정책을 OPA/Rego로 구현합니다.
- 따라서 본 실험은 ZTS 전체 알고리즘의 재현이 아니라 **역할 기반 정책 작성·집행·위반률 평가 절차의 적용 연구**입니다.

## 3. 포트 번호와 서버 표현

서로 다른 호스트 포트만 부여한 프로세스를 각각 독립 서버라고 표현하는 것은 권장하지 않습니다. 포트 번호는 접속 지점을 구분할 뿐 프로세스, 파일시스템, 네트워크 이름공간, 데이터 저장영역을 분리하지 않기 때문입니다.

본 환경은 각 업무시스템과 데이터베이스를 별도 Docker 컨테이너로 실행하고, 업무별 Docker 네트워크를 분리합니다. 논문에서는 이를 다음과 같이 표현합니다.

> 단일 물리 호스트에서 컨테이너별 독립 프로세스·파일시스템·네트워크 이름공간을 부여하여 복수의 논리 서버 인스턴스를 구성하였다.

각 PostgreSQL 컨테이너는 내부적으로 동일한 `5432` 포트를 사용합니다. 컨테이너와 네트워크가 다르므로 충돌하지 않습니다. 호스트 포트 매핑은 관리·측정이 필요한 PEP(`18080`), Transfer CDS(`18090`), OPA(`18181`)에만 적용합니다.

## 4. 가상 금융기관 A 구성

### S 영역

- 고객정보 업무(`customer_app`, `customer_db`)
- 여신심사 업무(`loan_app`, `loan_db`)
- 신용평가 업무(`credit_app`, `credit_db`)
- 자금세탁방지 업무(`aml_app`, `aml_db`)
- 승인 업무(`approval_app`, `approval_db`)

### O 영역

- 공개 서비스(`public_app`)
- 외부 생성형 AI 모의 서비스(`external_ai`)

### 통제 구성요소

- PEP/API Gateway(`pep`): 모든 승인된 S영역 업무 간 호출을 중계합니다.
- PDP(`opa`): 사용자, 역할, 단말 신뢰, 출발 업무, 도착 업무, 목적, 정보등급을 평가합니다.
- Transfer CDS(`transfer_cds`): S/O 경계에서 승인, 등급, 목적, 콘텐츠, 무결성을 검증합니다.
- JSONL 감사로그: 정책 입력, 허용·거부, 거부 사유, 정책 결정시간을 기록합니다.

## 5. 비교 환경

### 비교군: `compose.baseline.yml`

- 모든 S영역 앱과 DB가 하나의 `flat_s` 네트워크를 공유합니다.
- 인증된 사용자는 S영역 서비스에 광범위하게 접근할 수 있습니다.
- Transfer CDS는 승인 여부만 확인하는 단순 정책을 사용합니다.
- 침해된 업무서비스가 다른 업무 DB에 직접 연결할 가능성이 존재합니다.

### 제안군: `compose.proposed.yml`

- 업무별 앱·DB·PEP만 공유하는 개별 네트워크를 구성합니다.
- 업무 간 호출은 PEP를 통과하고 OPA 역할 정책을 충족해야 합니다.
- 서비스는 자신의 DB에만 직접 연결할 수 있습니다.
- 외부 전송은 Transfer CDS를 통과하며, S 원본은 금지하고 승인된 O 파생본만 허용합니다.

## 6. 실험 준비

요구사항:

- Docker Engine 또는 Docker Desktop
- Docker Compose v2
- Python 3.11 이상

```bash
cd financial_n2sf_track2
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

실험에는 실제 개인정보나 개인신용정보를 사용하지 않습니다. 초기 데이터는 `db/init/00_schema.sql`의 합성 레코드입니다.

## 7. 비교군 실행

```bash
docker compose -f compose.baseline.yml up -d --build
python scripts/run_policy_tests.py --mode baseline --repeat 30
python scripts/run_cds_tests.py --mode baseline --repeat 30
python scripts/run_reachability_tests.py --mode baseline
python scripts/run_performance_tests.py --mode baseline --requests 1000 --warmup 50
docker compose -f compose.baseline.yml down -v
```

또는 다음 명령을 사용합니다.

```bash
./scripts/run_all.sh baseline
```

## 8. 제안군 실행

```bash
docker compose -f compose.proposed.yml up -d --build
python scripts/run_policy_tests.py --mode proposed --repeat 30
python scripts/run_cds_tests.py --mode proposed --repeat 30
python scripts/run_reachability_tests.py --mode proposed
python scripts/run_performance_tests.py --mode proposed --requests 1000 --warmup 50
docker compose -f compose.proposed.yml down -v
```

또는 다음 명령을 사용합니다.

```bash
./scripts/run_all.sh proposed
```

두 모드를 동시에 실행하면 동일한 호스트 포트를 사용하므로 충돌합니다. 반드시 한 환경을 종료한 뒤 다른 환경을 실행합니다.

## 9. 분석

```bash
python scripts/analyze_results.py --results-dir results
```

생성 예정 산출물:

- `experiment_summary.csv`
- `statistical_tests.csv`
- `security_effectiveness.png`
- `latency_boxplot.png`

통신 그래프는 다음과 같이 생성합니다.

```bash
python scripts/build_communication_graph.py results/raw_policy_proposed_<timestamp>.csv \
  --allowed-only --output results/proposed_communication_graph.png
```

## 10. 평가 지표

- 정상 업무 흐름 성공률
- 비인가 업무 흐름 성공률 및 차단률
- 업무 간 DB 직접 도달률
- 침해 노드의 도달 가능한 업무시스템·DB 수(Blast Radius)
- S 원본의 O 영역 전송 성공률
- 승인된 O 파생본 전송 성공률
- 정책 결정 및 전체 요청의 p50·p95 지연시간
- 감사로그 완전성
- 정책 규칙 수와 업무 변경 시 수정량

정책 위반률은 다음과 같이 계산할 수 있습니다.

`PVR = 정책에 의해 허용되지 않은 관측 통신 간선 수 / 전체 관측 통신 간선 수`

비인가 흐름 차단률은 다음과 같습니다.

`Block Rate = 차단된 비인가 시도 수 / 전체 비인가 시도 수`

## 11. 통계 분석 원칙

- 기능·보안 시나리오는 각 30회 이상 반복합니다.
- 지연시간은 워밍업 이후 1,000회 이상 측정합니다.
- 지연시간은 중앙값, IQR, p95, 95% 부트스트랩 신뢰구간을 제시합니다.
- 비율 차이는 Fisher의 정확검정 또는 카이제곱검정을 사용합니다.
- 지연시간은 정규성을 가정하지 않고 Mann-Whitney U 검정을 우선 사용합니다.
- 유의수준, 반복횟수, 허용 가능한 지연 예산은 실험 전에 고정합니다.

## 12. 해석 시 주의사항

- Docker 네트워크 분리는 물리적 망분리 장비의 성능이나 보증수준을 재현하지 않습니다.
- 정규식 콘텐츠 검사는 실제 DLP·백신·CDR의 대체물이 아니라 통제 흐름을 재현한 모의 기능입니다.
- OPA 헤더는 실제 환경의 IdP, MFA, EDR, NAC, IAM/PAM 연동 결과를 추상화한 것입니다.
- 따라서 논문의 결론은 “특정 상용 솔루션의 성능”이 아니라 “시스템·업무 경계와 정책집행지점을 적용했을 때 통신경로·권한·정보이동 결과가 어떻게 달라지는가”로 한정해야 합니다.
