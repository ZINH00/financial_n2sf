#!/usr/bin/env bash
set -euo pipefail
MODE="${1:-}"
if [[ "$MODE" != "baseline" && "$MODE" != "proposed" ]]; then
  echo "usage: $0 baseline|proposed" >&2
  echo "  (run 'docker compose -f compose.\$MODE.yml up -d --build' before this script)" >&2
  exit 2
fi

# baseline.rego와 proposed.rego는 둘 다 package financial.access에서 서로 다른
# default decision을 정의하므로, policies/ 디렉터리 전체를 한 번에 opa test하면
# "multiple default rules" 컴파일 오류가 난다(baseline_cds.rego/cds.rego도 동일).
# 따라서 모드별로 실제 그 모드가 사용하는 정책 파일만 명시해서 검사한다.
if [[ "$MODE" == "proposed" ]]; then
  echo "[1/6] Validating policy unit tests (opa test, proposed policy only)"
  docker run --rm -v "$(pwd)/policies:/policies" openpolicyagent/opa:1.4.2-static \
    test /policies/proposed.rego /policies/proposed_test.rego /policies/data.json
else
  echo "[1/6] Skipping opa test for baseline (no baseline-specific unit tests exist; policies/proposed_test.rego targets proposed.rego)"
fi

echo "[2/6] Collecting environment metadata"
python scripts/collect_env.py --results-dir results

echo "[3/6] Running business-flow policy tests (${MODE})"
python scripts/run_policy_tests.py --mode "$MODE" --repeat 30

echo "[4/6] Running S/O transfer CDS tests (${MODE})"
python scripts/run_cds_tests.py --mode "$MODE" --repeat 30

echo "[5/6] Running App+DB reachability tests (${MODE})"
python scripts/run_reachability_tests.py --mode "$MODE"

echo "[6/6] Running performance tests (${MODE}: 30 batches x 200 requests x 4 flows)"
python scripts/run_performance_tests.py --mode "$MODE" --batches 30 --per-batch 200
