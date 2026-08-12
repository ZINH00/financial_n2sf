#!/usr/bin/env bash
set -euo pipefail
MODE="${1:-}"
if [[ "$MODE" != "baseline" && "$MODE" != "proposed" ]]; then
  echo "usage: $0 baseline|proposed" >&2
  echo "  (run 'docker compose -f compose.\$MODE.yml up -d --build' before this script)" >&2
  exit 2
fi

echo "[1/6] Validating policy unit tests (opa test)"
docker run --rm -v "$(pwd)/policies:/policies" openpolicyagent/opa:1.4.2-static test /policies

echo "[2/6] Collecting environment metadata"
python scripts/collect_env.py --results-dir results

echo "[3/6] Running business-flow policy tests (${MODE})"
python scripts/run_policy_tests.py --mode "$MODE" --repeat 30

echo "[4/6] Running S/O transfer CDS tests (${MODE})"
python scripts/run_cds_tests.py --mode "$MODE" --repeat 30

echo "[5/6] Running direct DB reachability tests (${MODE})"
python scripts/run_reachability_tests.py --mode "$MODE"

echo "[6/6] Running performance tests (${MODE}: 30 batches x 200 requests x 4 flows)"
python scripts/run_performance_tests.py --mode "$MODE" --batches 30 --per-batch 200
